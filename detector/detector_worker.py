import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["TORCH_NUM_THREADS"] = "1"

import cv2, subprocess, time, threading, queue, json
from datetime import datetime
from ultralytics import YOLO
from ultralytics.utils.plotting import Annotator, colors
try:
    import psycopg2
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

# Load .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # If dotenv not installed, just use system env vars

from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Optimize PyTorch CPU threading to prevent CPU starvation on Raspberry Pi
try:
    import torch
    torch.set_num_threads(1)
    if hasattr(torch, "set_num_interop_threads"):
        torch.set_num_interop_threads(1)
except Exception:
    pass

from alert_store import DB_DSN, ensure_alerts_schema, insert_alert_db

YOLO_CACHE = {}

def get_yolo_model(model_path):
    if model_path not in YOLO_CACHE:
        print(f"[CACHE] Loading model weights into memory: {model_path}", flush=True)
        YOLO_CACHE[model_path] = YOLO(model_path)
    return YOLO_CACHE[model_path]

def get_alerts_base_url():
    try:
        public_url_file = os.path.join(str(BASE_DIR), "hls", "public_url.txt")
        if os.path.exists(public_url_file):
            with open(public_url_file) as f:
                val = f.read().strip()
                if val and not val.startswith("("):
                    return val
    except: pass
    return os.getenv("ALERTS_BASE_URL", "")

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

class DetectorWorker:
    @property
    def model_configs(self):
        return self._model_configs

    @model_configs.setter
    def model_configs(self, val):
        self._model_configs = val or {}
        self.roi_polygon = self._model_configs.get("roi_polygon")

    def __init__(self, rtsp_url, output_dir, model_paths, conf=0.40, iou=0.45, location="Camera", model_configs=None):
        self.rtsp_url, self.output_dir, self.model_paths, self.conf, self.iou, self.location = rtsp_url, output_dir, model_paths, conf, iou, location
        self.model_configs = model_configs or {}
        self.fps, self.width, self.height = 5.0, 1280, 720
        self._latest_raw_frame = None
        self._latest_boxes = []
        self._frame_lock, self._box_lock = threading.Lock(), threading.Lock()
        self._stop_event = threading.Event()
        self._frame_queue, self._result_queue = queue.Queue(maxsize=1), queue.Queue(maxsize=1)
        self._last_frame_time, self._cap_ok = time.time(), True
        self.alert_timers, self.alert_triggered = {}, set()
        self.cam_id = os.path.basename(output_dir).replace("stream", "").replace("_detected", "")
        # Load YOLO models synchronously during worker startup (runs in subprocess, doesn't block main server)
        paths = model_paths if isinstance(model_paths, list) else [model_paths]
        print(f"[WORKER-TIMER] Camera {self.cam_id} model loading started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
        t_load_start = time.time()
        self.models = [get_yolo_model(mp) for mp in paths]
        print(f"[WORKER-TIMER] Camera {self.cam_id} models loaded in {int((time.time() - t_load_start)*1000)}ms at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {paths}", flush=True)
        self._db_conn = None

    def stop(self):
        self._stop_event.set()

    def _get_db_conn(self):
        if not PSYCOPG2_AVAILABLE: return None
        if self._db_conn is None or self._db_conn.closed:
            try:
                self._db_conn = psycopg2.connect(DB_DSN, connect_timeout=5)
            except: self._db_conn = None
        return self._db_conn

    def _create_ffmpeg(self):
        os.makedirs(self.output_dir, exist_ok=True)
        session_id = int(time.time())
        # Use optimized software libx264 to ensure browser MSE compatibility (SPS/PPS parameters)
        # while keeping RPi CPU low via ultrafast preset, zerolatency tuning, and auto-threading.
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{self.width}x{self.height}", 
            "-r", str(self.fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "ultrafast", 
            "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-threads", "2",
            "-profile:v", "baseline", "-level:v", "3.1",
            "-b:v", "400k", "-maxrate", "500k", "-bufsize", "1M",
            "-g", str(int(self.fps * 2)), 
            "-keyint_min", str(int(self.fps * 2)), "-sc_threshold", "0",
            "-f", "hls", "-hls_time", "2", "-hls_list_size", "8",
            "-hls_flags", "delete_segments+independent_segments+discont_start+omit_endlist+temp_file", 
            "-hls_segment_filename", os.path.join(self.output_dir, f"segment_{session_id}_%d.ts"), 
            os.path.join(self.output_dir, "playlist.m3u8")
        ]
        log = open(os.path.join(self.output_dir, "ffmpeg.log"), "a")
        print(f"[LOG] Camera {self.cam_id} detector stream started with resolution: {self.width}x{self.height}, FPS: {self.fps}, Bitrate: 400k (max 500k)", flush=True)
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log, stdout=subprocess.DEVNULL)

    def _letterbox(self, f):
        h, w = f.shape[:2]
        s = min(self.width/w, self.height/h)
        nw, nh = int(w*s), int(h*s)
        res = cv2.resize(f, (nw, nh))
        return cv2.copyMakeBorder(res, (self.height-nh)//2, (self.height-nh+1)//2, (self.width-nw)//2, (self.width-nw+1)//2, cv2.BORDER_CONSTANT, value=[0,0,0])

    def _is_valid_box(self, conf_val, m_conf, bw, bh, box_area, f_w, f_h, f_area):
        if conf_val < m_conf:
            return False
        # 1. Absolute Minimum Size Bounds (Rejects single-pixel noise only)
        # Relaxed to allow small/distant detections in 480p/720p scaled streams
        if bw < 4 or bh < 4 or box_area < 20:
            return False
        # 2. Maximum Size Bounds (Rejects 80% full-screen hallucinations)
        if box_area > 0.85 * f_area or bh > 0.95 * f_h or bw > 0.95 * f_w:
            return False
        return True

    def _run_all_models(self, f):
        try:
            cur_cls, now = set(), time.time()
            raw_boxes = []
            f_h, f_w = f.shape[:2]
            f_area = f_w * f_h

            for midx, model in enumerate(self.models):
                m_path = self.model_paths[midx] if (isinstance(self.model_paths, list) and midx < len(self.model_paths)) else str(self.model_paths)
                m_name = os.path.basename(m_path)
                m_clean = m_name.replace(".pt", "")
                
                m_conf = self.conf
                m_iou = self.iou
                enabled_classes = None
                m_imgsz = 960
                if isinstance(self.model_configs, dict):
                    cfg = self.model_configs.get(m_name) or self.model_configs.get(m_clean) or self.model_configs.get(m_name.lower())
                    if cfg and isinstance(cfg, dict):
                        m_conf = float(cfg.get("conf", self.conf))
                        m_iou = float(cfg.get("iou", self.iou))
                        enabled_classes = cfg.get("enabled_classes")
                        m_imgsz = int(cfg.get("imgsz", 960))

                # Dynamically set YOLO predict confidence to the minimum of active class sliders
                detect_conf = m_conf
                if enabled_classes is not None and isinstance(enabled_classes, list) and len(enabled_classes) > 0:
                    min_cls_conf = m_conf
                    if cfg and isinstance(cfg, dict):
                        class_configs = cfg.get("class_configs")
                        if class_configs and isinstance(class_configs, dict):
                            for cls_name in enabled_classes:
                                c_cfg = class_configs.get(cls_name)
                                if not c_cfg:
                                    # Fallback to normalized lookup
                                    norm_cls = cls_name.lower().replace("_", "-").replace(" ", "-")
                                    for k, val in class_configs.items():
                                        if k.lower().replace("_", "-").replace(" ", "-") == norm_cls:
                                            c_cfg = val
                                            break
                                if c_cfg and isinstance(c_cfg, dict) and "conf" in c_cfg:
                                    min_cls_conf = min(min_cls_conf, float(c_cfg["conf"]))
                    detect_conf = min_cls_conf

                detected_this_model = []
                # Predict at a very low confidence (0.01) to capture distant or low-confidence boxes,
                # then filter on the Python side using the slider threshold.
                for r in model.predict(f, conf=0.01, iou=m_iou, imgsz=m_imgsz, verbose=False):
                    if r.boxes:
                        for b in r.boxes:
                            cls = r.names[int(b.cls[0])]
                            conf_val = float(b.conf[0])
                            detected_this_model.append((cls, conf_val))
                            
                            box_xyxy = b.xyxy[0].cpu().numpy().tolist()
                            x1, y1, x2, y2 = box_xyxy
                            bw = max(0, x2 - x1)
                            bh = max(0, y2 - y1)
                            box_area = bw * bh

                            # Filter by enabled classes (robust match handling dashes/underscores/spaces/doubles)
                            if enabled_classes is not None and isinstance(enabled_classes, list):
                                import re
                                norm_cls = re.sub(r'[-_\s]+', '-', cls.lower())
                                matched = False
                                for e in enabled_classes:
                                    norm_e = re.sub(r'[-_\s]+', '-', e.lower())
                                    if norm_cls == norm_e:
                                        matched = True
                                        break
                                if not matched:
                                    continue

                            # Load class-specific conf thresholds
                            cls_conf = m_conf
                            if cfg and isinstance(cfg, dict):
                                class_configs = cfg.get("class_configs")
                                if class_configs and isinstance(class_configs, dict):
                                    import re
                                    norm_cls = re.sub(r'[-_\s]+', '-', cls.lower())
                                    c_cfg = None
                                    for k, val in class_configs.items():
                                        if re.sub(r'[-_\s]+', '-', k.lower()) == norm_cls:
                                            c_cfg = val
                                            break
                                    if c_cfg and isinstance(c_cfg, dict):
                                        cls_conf = float(c_cfg.get("conf", m_conf))

                            # Validate Box using class-specific confidence
                            if not self._is_valid_box(conf_val, cls_conf, bw, bh, box_area, f_w, f_h, f_area):
                                continue

                             # Apply ROI rectangle filter if configured
                            if self.roi_polygon and len(self.roi_polygon) == 2:
                                try:
                                    fh, fw = f.shape[:2]
                                    rx1 = int(self.roi_polygon[0][0] * fw)
                                    ry1 = int(self.roi_polygon[0][1] * fh)
                                    rx2 = int(self.roi_polygon[1][0] * fw)
                                    ry2 = int(self.roi_polygon[1][1] * fh)
                                    cx = int((x1 + x2) / 2)
                                    cy = int((y1 + y2) / 2)
                                    if not (rx1 <= cx <= rx2 and ry1 <= cy <= ry2):
                                        continue
                                except Exception as e:
                                    print(f"[ROI] Filter error: {e}", flush=True)

                            label_text = f"{cls} {conf_val:.2f}"
                            color_val = colors(int(b.cls[0])+midx*50, True)
                            raw_boxes.append((box_xyxy, label_text, color_val, conf_val, cls))
                if detected_this_model:
                    m_name = os.path.basename(self.model_paths[midx])
                    print(f"[DEBUG] Camera {self.cam_id} {m_name}: raw_detected={detected_this_model}, filter={enabled_classes}, kept={len(raw_boxes)} boxes", flush=True)

            # Universal Cross-Class Non-Maximum Suppression (NMS) across ALL models and ALL classes
            boxes_data = []
            if raw_boxes:
                raw_boxes.sort(key=lambda x: x[3], reverse=True)
                kept_items = []
                
                for item in raw_boxes:
                    b1_xyxy, l1_text, c1_color, conf1_val, cls1_name = item
                    x1_1, y1_1, x2_1, y2_1 = b1_xyxy
                    area1 = max(0, x2_1 - x1_1) * max(0, y2_1 - y1_1)
                    
                    suppress = False
                    for k_item in kept_items:
                        b2_xyxy, l2_text, c2_color, conf2_val, cls2_name = k_item
                        x1_2, y1_2, x2_2, y2_2 = b2_xyxy
                        area2 = max(0, x2_2 - x1_2) * max(0, y2_2 - y1_2)
                        
                        # Compute Intersection over Union (IoU)
                        ix1 = max(x1_1, x1_2)
                        iy1 = max(y1_1, y1_2)
                        ix2 = min(x2_1, x2_2)
                        iy2 = min(y2_1, y2_2)
                        
                        if ix2 > ix1 and iy2 > iy1:
                            inter = (ix2 - ix1) * (iy2 - iy1)
                            union = area1 + area2 - inter
                            iou = inter / max(1.0, union)
                            
                            # Universal IoU Threshold (0.35 overlap suppresses duplicate/conflicting boxes)
                            if iou >= 0.35:
                                suppress = True
                                break
                                
                    if not suppress:
                        kept_items.append(item)
                        boxes_data.append((b1_xyxy, l1_text, c1_color))
                        cur_cls.add(cls1_name)

            # Draw ROI rectangle outline if configured
            if self.roi_polygon and len(self.roi_polygon) == 2:
                try:
                    fh, fw = f.shape[:2]
                    rx1 = int(self.roi_polygon[0][0] * fw)
                    ry1 = int(self.roi_polygon[0][1] * fh)
                    rx2 = int(self.roi_polygon[1][0] * fw)
                    ry2 = int(self.roi_polygon[1][1] * fh)
                    cv2.rectangle(f, (rx1, ry1), (rx2, ry2), color=(0, 255, 255), thickness=2)
                except:
                    pass

            # Render alert image snapshot with bounding boxes
            ann = Annotator(f.copy(), line_width=2)
            for b_xyxy, label_text, color_val in boxes_data:
                ann.box_label(b_xyxy, label_text, color=color_val)
            res = ann.result()

            for c in cur_cls:
                if c not in self.alert_timers: self.alert_timers[c] = now
                elif now - self.alert_timers[c] >= 3.0 and c not in self.alert_triggered:
                    self.alert_triggered.add(c); self._save_alert(c, res)
            for c in list(self.alert_timers):
                if c not in cur_cls:
                    del self.alert_timers[c]
                    if c in self.alert_triggered: self.alert_triggered.remove(c)
            return boxes_data
        except: return []

    def _save_alert(self, class_name, frame):
        try:
            now_dt = datetime.now()
            ts = now_dt.strftime("%Y%m%d_%H%M%S")
            filename = f"cam{self.cam_id}_{ts}_{class_name}.jpg"
            adir = os.path.join(os.path.dirname(self.output_dir), "alerts")
            os.makedirs(adir, exist_ok=True)
            cv2.imwrite(os.path.join(adir, filename), frame)

            # Create full image path
            base_url = get_alerts_base_url()
            if base_url:
                image_path = f"{base_url.rstrip('/')}/hls/alerts/{filename}"
            else:
                image_path = f"/hls/alerts/{filename}"

            # Direct DB Store using provided logic and custom location
            conn = self._get_db_conn()
            if conn:
                try:
                    cur = conn.cursor()
                    ensure_alerts_schema(cur)
                    insert_alert_db(cur, self.cam_id, self.location, f"{class_name} Detected", image_path, now_dt)
                    conn.commit()
                    cur.close()
                except: pass
        except: pass

    def _get_connecting_frame(self):
        # Create a fallback frame with message
        import numpy as np
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        cv2.putText(frame, "Connecting to Camera...", 
                   (int(self.width*0.2), int(self.height*0.5)), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
        return frame

    def _inference_thread(self):
        print(f"[LOG] Camera {self.cam_id} inference thread started", flush=True)
        while not self._stop_event.is_set():
            try:
                f = self._frame_queue.get(timeout=0.2)
            except:
                continue
            boxes = self._run_all_models(f)
            with self._box_lock:
                self._latest_boxes = boxes
            time.sleep(0.03)

    def _capture_thread(self, cap):
        while not self._stop_event.is_set():
            t_start = time.time()
            if not cap.grab():
                time.sleep(0.01)
                continue
            
            # Flush queue: if the grab was instant, keep discarding old frames to catch up to live edge
            grab_count = 0
            while (time.time() - t_start) < 0.004 and grab_count < 15:
                t_start = time.time()
                if not cap.grab():
                    break
                grab_count += 1
            
            ret, f = cap.retrieve()
            if not ret or f is None:
                time.sleep(0.01)
                continue
                
            with self._frame_lock:
                self._latest_raw_frame = f
                self._cap_ok = True
                self._last_frame_time = time.time()

    def run(self):
        ffmpeg, cap, inf_t, cap_t = None, None, None, None
        try:
            while not self._stop_event.is_set():
                if inf_t: inf_t.join(timeout=1)
                if cap: cap.release(); cap = None
                if ffmpeg:
                    try: ffmpeg.stdin.close()
                    except: pass
                    ffmpeg.kill(); ffmpeg.wait()
                    ffmpeg = None
                
                if self._stop_event.is_set():
                    break

                self._frame_queue = queue.Queue(maxsize=1)
                self._result_queue = queue.Queue(maxsize=1)
                self._latest_raw_frame = None
                self._cap_ok = True

                try:
                    print(f"[WORKER-TIMER] Camera {self.cam_id} connecting to RTSP at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...", flush=True)
                    t_conn_start = time.time()
                    cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    
                    retry_count = 0
                    while not cap.isOpened() and retry_count < 10 and not self._stop_event.is_set():
                        time.sleep(0.5)
                        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                        retry_count += 1
                    
                    if self._stop_event.is_set():
                        break
                    
                    print(f"[WORKER-TIMER] Camera {self.cam_id} RTSP connected in {int((time.time() - t_conn_start)*1000)}ms at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
                    
                    if cap and cap.isOpened():
                        cap_t = threading.Thread(target=self._capture_thread, args=(cap,), daemon=True)
                        cap_t.start()

                    # Wait up to 5s for the first real frame from camera before starting FFmpeg
                    print(f"[WORKER-TIMER] Camera {self.cam_id} waiting for first raw frame...", flush=True)
                    t_frame_start = time.time()
                    while time.time() - t_frame_start < 5.0 and self._latest_raw_frame is None and not self._stop_event.is_set():
                        time.sleep(0.05)
                    
                    if self._stop_event.is_set():
                        break
                    
                    print(f"[WORKER-TIMER] Camera {self.cam_id} first raw frame received in {int((time.time() - t_frame_start)*1000)}ms at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
                    
                    print(f"[WORKER-TIMER] Camera {self.cam_id} creating FFmpeg process...", flush=True)
                    t_ff_start = time.time()
                    ffmpeg = self._create_ffmpeg()
                    print(f"[WORKER-TIMER] Camera {self.cam_id} FFmpeg process created in {int((time.time() - t_ff_start)*1000)}ms", flush=True)
                    
                    inf_t = threading.Thread(target=self._inference_thread, daemon=True)
                    inf_t.start()
                    
                    f_int = 1.0 / self.fps
                    next_frame_time = time.time()
                    
                    while not self._stop_event.is_set():
                        if cap and cap.isOpened():
                            if not self._cap_ok or time.time() - self._last_frame_time > 15.0: break
                        
                        now = time.time()
                        if now < next_frame_time:
                            time.sleep(max(0.001, next_frame_time - now))
                            continue
                        next_frame_time += f_int
                        if now - next_frame_time > 0.3:
                            next_frame_time = now + f_int
                        
                        with self._frame_lock:
                            f = self._latest_raw_frame
                        
                        if f is None:
                            continue
                        
                        # Ultra-fast in-place resize to 720p HD
                        pf = cv2.resize(f, (self.width, self.height), interpolation=cv2.INTER_LINEAR)
                        
                        # Send copy to inference thread if ready
                        if not self._frame_queue.full():
                            try:
                                self._frame_queue.put_nowait(pf.copy())
                            except:
                                pass
                        
                        # Draw latest bounding boxes
                        with self._box_lock:
                            cur_boxes = list(self._latest_boxes)
                        
                        if cur_boxes:
                            for b_xyxy, label_text, color_val in cur_boxes:
                                try:
                                    x1, y1, x2, y2 = [int(v) for v in b_xyxy]
                                    c = tuple(color_val) if isinstance(color_val, (list, tuple)) and len(color_val) >= 3 else (0, 255, 128)
                                    cv2.rectangle(pf, (x1, y1), (x2, y2), c, 2)
                                    t_size = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)[0]
                                    cv2.rectangle(pf, (x1, max(0, y1 - t_size[1] - 6)), (x1 + t_size[0] + 4, max(0, y1)), c, -1)
                                    cv2.putText(pf, label_text, (x1 + 2, max(t_size[1] + 2, y1 - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
                                except: pass
                        
                        if ffmpeg.poll() is not None: break
                        try:
                            ffmpeg.stdin.write(pf)
                        except: break
                except:
                    import traceback
                    traceback.print_exc()
        finally:
            if cap: cap.release()
            if ffmpeg:
                try: ffmpeg.stdin.close()
                except: pass
                ffmpeg.kill(); ffmpeg.wait()
# import cv2, subprocess, os, time, threading, queue, json
# from datetime import datetime
# from ultralytics import YOLO
# from ultralytics.utils.plotting import Annotator, colors
# try:
#     import psycopg2
#     PSYCOPG2_AVAILABLE = True
# except ImportError:
#     PSYCOPG2_AVAILABLE = False

# # Load .env file
# try:
#     from dotenv import load_dotenv
#     load_dotenv()
# except ImportError:
#     pass  # If dotenv not installed, just use system env vars

# from pathlib import Path
# import sys

# BASE_DIR = Path(__file__).resolve().parents[1]
# if str(BASE_DIR) not in sys.path:
#     sys.path.insert(0, str(BASE_DIR))

# from alert_store import DB_DSN, ensure_alerts_schema, insert_alert_db

# ALERTS_BASE_URL = os.getenv("ALERTS_BASE_URL", "")

# os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;30000000|reorder_queue_size;30|probesize;10000000|analyzeduration;10000000"

# class DetectorWorker:
#     def __init__(self, rtsp_url, output_dir, model_paths, fps=15, conf=0.25, iou=0.45, location=None):
#         self.rtsp_url, self.output_dir, self.fps, self.conf, self.iou = rtsp_url, output_dir, fps, conf, iou
#         self.location = location or f"Camera {os.path.basename(output_dir).replace('stream', '').replace('_detected', '')}"
#         self.width, self.height = 1280, 720
#         self._stop_event, self._frame_queue, self._result_queue = threading.Event(), queue.Queue(maxsize=2), queue.Queue(maxsize=2)
#         self._latest_raw_frame, self._frame_lock, self._cap_ok, self._last_frame_time = None, threading.Lock(), True, time.time()
#         self.alert_timers, self.alert_triggered = {}, set()
#         self.cam_id = os.path.basename(output_dir).replace("stream", "").replace("_detected", "")
#         self.models = [YOLO(mp) for mp in (model_paths if isinstance(model_paths, list) else [model_paths])]
#         self._db_conn = None

#     def _get_db_conn(self):
#         if not PSYCOPG2_AVAILABLE: return None
#         if self._db_conn is None or self._db_conn.closed:
#             try:
#                 self._db_conn = psycopg2.connect(DB_DSN, connect_timeout=5)
#             except: self._db_conn = None
#         return self._db_conn

#     def _create_ffmpeg(self):
#         os.makedirs(self.output_dir, exist_ok=True)
#         # Optimized for Remote/VPN (Tailscale) viewing:
#         # - Set hls_time to 2s for lower latency
#         # - Increased quality and bitrate (500k) for crisp visual inspection
#         # - Added ultra-fast preset and zerolatency tune for minimal latency
#         # - Added temp_file flag to prevent incomplete segment reading
#         cmd = [
#             "ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{self.width}x{self.height}", 
#             "-r", str(self.fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "ultrafast", 
#             "-tune", "zerolatency", "-pix_fmt", "yuv420p", 
#             "-b:v", "500k", "-maxrate", "600k", "-bufsize", "1000k",
#             "-g", str(int(self.fps * 2)), # GOP = 2 * FPS for 2s segments
#             "-keyint_min", str(int(self.fps * 2)), 
#             "-f", "hls", "-hls_time", "2", "-hls_list_size", "8",
#             "-hls_flags", "delete_segments+independent_segments+discont_start+temp_file", 
#             "-hls_segment_filename", os.path.join(self.output_dir, "segment_%d.ts"), 
#             os.path.join(self.output_dir, "playlist.m3u8")
#         ]
#         log = open(os.path.join(self.output_dir, "ffmpeg.log"), "a")
#         return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log, stdout=subprocess.DEVNULL)

#     def _letterbox(self, f):
#         h, w = f.shape[:2]
#         s = min(self.width/w, self.height/h)
#         nw, nh = int(w*s), int(h*s)
#         res = cv2.resize(f, (nw, nh))
#         return cv2.copyMakeBorder(res, (self.height-nh)//2, (self.height-nh+1)//2, (self.width-nw)//2, (self.width-nw+1)//2, cv2.BORDER_CONSTANT, value=[0,0,0])

#     def _run_all_models(self, f):
#         try:
#             ann, cur_cls, now = Annotator(f.copy(), line_width=2), set(), time.time()
#             for midx, model in enumerate(self.models):
#                 for r in model.predict(f, conf=self.conf, iou=self.iou, imgsz=640, verbose=False):
#                     if r.boxes:
#                         for b in r.boxes:
#                             cls = r.names[int(b.cls[0])]
#                             cur_cls.add(cls)
#                             ann.box_label(b.xyxy[0].cpu().numpy().tolist(), f"{cls} {float(b.conf[0]):.2f}", color=colors(int(b.cls[0])+midx*50, True))
#             res = ann.result()
#             for c in cur_cls:
#                 if c not in self.alert_timers: self.alert_timers[c] = now
#                 elif now - self.alert_timers[c] >= 3.0 and c not in self.alert_triggered:
#                     self.alert_triggered.add(c); self._save_alert(c, res)
#             for c in list(self.alert_timers):
#                 if c not in cur_cls:
#                     del self.alert_timers[c]
#                     if c in self.alert_triggered: self.alert_triggered.remove(c)
#             return res
#         except: return f

#     def _save_alert(self, class_name, frame):
#         try:
#             now_dt = datetime.now()
#             ts = now_dt.strftime("%Y%m%d_%H%M%S")
#             filename = f"cam{self.cam_id}_{ts}_{class_name}.jpg"
#             adir = os.path.join(os.path.dirname(self.output_dir), "alerts")
#             os.makedirs(adir, exist_ok=True)
#             cv2.imwrite(os.path.join(adir, filename), frame)

#             # Create full image path
#             if ALERTS_BASE_URL:
#                 image_path = f"{ALERTS_BASE_URL.rstrip('/')}/hls/alerts/{filename}"
#             else:
#                 image_path = f"/hls/alerts/{filename}"

#             # Direct DB Store using provided logic and custom location
#             conn = self._get_db_conn()
#             if conn:
#                 try:
#                     cur = conn.cursor()
#                     ensure_alerts_schema(cur)
#                     insert_alert_db(cur, self.cam_id, self.location, f"{class_name} Detected", image_path, now_dt)
#                     conn.commit()
#                     cur.close()
#                 except: 
#                     if self._db_conn: self._db_conn.rollback()

#             # Local JSON Store
#             try:
#                 adir_root = os.path.dirname(os.path.dirname(self.output_dir))
#                 alog = os.path.join(adir_root, "hls", "alerts", "alerts.json")
#                 os.makedirs(os.path.dirname(alog), exist_ok=True)
#                 data = json.load(open(alog)) if os.path.exists(alog) else []
#                 data.append({"camera": self.cam_id, "time": now_dt.strftime("%Y-%m-%d %H:%M:%S"), "event": f"{class_name} Detected", "image": image_path})
#                 json.dump(data, open(alog, "w"), indent=4)
#             except: pass
#         except: pass

#     def _get_connecting_frame(self):
#         # Create a fallback frame with message
#         import numpy as np
#         frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
#         cv2.putText(frame, "Connecting to Camera...", 
#                    (int(self.width*0.2), int(self.height*0.5)), 
#                    cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
#         return frame

#     def _inference_thread(self):
#         while not self._stop_event.is_set():
#             try: f = self._frame_queue.get(timeout=1.0)
#             except: continue
#             ann = self._run_all_models(f)
#             if self._result_queue.full():
#                 try: self._result_queue.get_nowait()
#                 except: pass
#             self._result_queue.put(ann)

#     def _capture_thread(self, cap):
#         retry_count = 0
#         max_retries = 10
#         while not self._stop_event.is_set():
#             ret, f = cap.read()
#             if not ret:
#                 retry_count +=1
#                 if retry_count > max_retries:
#                     self._cap_ok = False
#                     break
#                 time.sleep(2)
#                 continue
#             retry_count =0 # Reset retry count on success
#             with self._frame_lock:
#                 self._latest_raw_frame, self._cap_ok, self._last_frame_time = f, True, time.time()

#     def run(self):
#         ffmpeg, cap, inf_t, cap_t = None, None, None, None
#         while True:
#             self._stop_event.set()
#             if inf_t: inf_t.join(timeout=5)
#             if cap: cap.release()
#             if ffmpeg:
#                 try: ffmpeg.stdin.close()
#                 except: pass
#                 ffmpeg.kill(); ffmpeg.wait()
#             self._stop_event.clear(); self._frame_queue, self._result_queue, self._latest_raw_frame, self._cap_ok = queue.Queue(maxsize=2), queue.Queue(maxsize=2), None, True
#             try:
#                 # Initialize default dimensions early for fallback frame
#                 self.width, self.height = 854, 480
                
#                 cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
#                 cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
                
#                 retry_count = 0
#                 while not cap.isOpened() and retry_count < 10:
#                     time.sleep(2)
#                     cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
#                     retry_count +=1
#                 if not cap.isOpened(): 
#                     # If we can't open camera, still run FFmpeg with fallback frame
#                     h, w = 480, 854
#                 else:
#                     h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
#                     w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
#                     if not h or not w: h, w = 480, 854
                
#                 self.width = 854; self.height = int(int(h) * (854 / int(w)))
#                 self.height += self.height % 2; ffmpeg = self._create_ffmpeg()
#                 inf_t = threading.Thread(target=self._inference_thread, daemon=True); inf_t.start()
#                 if cap and cap.isOpened():
#                     cap_t = threading.Thread(target=self._capture_thread, args=(cap,), daemon=True); cap_t.start()
                
#                 f_int, last_w, last_ann = 1.0 / self.fps, 0.0, None
#                 while True:
#                     if cap and cap.isOpened():
#                         if not self._cap_ok or time.time() - self._last_frame_time > 15.0: break
#                     with self._frame_lock: f = self._latest_raw_frame
                    
#                     # Use fallback connecting frame if no frame available
#                     if f is None: 
#                         f = self._get_connecting_frame()
                    
#                     now = time.time()
#                     # Only letterbox if we are going to use it for FFmpeg OR if inference queue is empty
#                     if now - last_w >= f_int or not self._frame_queue.full():
#                         pf = self._letterbox(f)
#                         if not self._frame_queue.full() and cap and cap.isOpened(): 
#                             self._frame_queue.put(pf)
#                     else:
#                         time.sleep(0.005)
#                         continue

#                     try: last_ann = self._result_queue.get_nowait()
#                     except: pass
                    
#                     if now - last_w < f_int: continue
#                     last_w = now; out = last_ann if last_ann is not None else pf
#                     if ffmpeg.poll() is not None: break
#                     try: 
#                         ffmpeg.stdin.write(out.tobytes())
#                         ffmpeg.stdin.flush()
#                     except: break
#             except: 
#                 import traceback
#                 traceback.print_exc()
#             time.sleep(3)
