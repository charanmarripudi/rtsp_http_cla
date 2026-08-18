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
    def __init__(self, rtsp_url, output_dir, model_paths, fps=12, conf=0.40, iou=0.45, location=None):
        self.rtsp_url, self.output_dir, self.fps, self.conf, self.iou = rtsp_url, output_dir, fps, conf, iou
        self.location = location or f"Camera {os.path.basename(output_dir).replace('stream', '').replace('_detected', '')}"
        self.width, self.height = 1280, 720
        self._stop_event, self._frame_queue, self._result_queue = threading.Event(), queue.Queue(maxsize=2), queue.Queue(maxsize=2)
        self._latest_raw_frame, self._frame_lock, self._cap_ok, self._last_frame_time = None, threading.Lock(), True, time.time()
        self._latest_boxes, self._box_lock = [], threading.Lock()
        self.alert_timers, self.alert_triggered = {}, set()
        self.cam_id = os.path.basename(output_dir).replace("stream", "").replace("_detected", "")
        self.models = [YOLO(mp) for mp in (model_paths if isinstance(model_paths, list) else [model_paths])]
        self._db_conn = None

        # Check if any model requires person body anchoring (PPE / Violation models)
        self.person_detector = None
        has_ppe = False
        ppe_keywords = ["vest", "helmet", "hat", "boot", "glove", "mask", "goggle", "belt", "harness", "lamp", "fall"]
        for m in self.models:
            for cname in m.names.values():
                if any(k in str(cname).lower() for k in ppe_keywords):
                    has_ppe = True
                    break
        if has_ppe:
            person_model_path = os.path.join(str(BASE_DIR), "models", "yolov8n.pt")
            if not os.path.exists(person_model_path):
                person_model_path = "yolov8n.pt"
            try:
                self.person_detector = YOLO(person_model_path)
                print(f"[LOG] Camera {self.cam_id}: Loaded Human Body Anchor model (yolov8n.pt) for high-precision PPE validation", flush=True)
            except Exception as e:
                print(f"[WARN] Could not load person detector: {e}", flush=True)

    def _get_db_conn(self):
        if not PSYCOPG2_AVAILABLE: return None
        if self._db_conn is None or self._db_conn.closed:
            try:
                self._db_conn = psycopg2.connect(DB_DSN, connect_timeout=5)
            except: self._db_conn = None
        return self._db_conn

    def _create_ffmpeg(self):
        os.makedirs(self.output_dir, exist_ok=True)
        # Software encoder (libx264 ultrafast) — 100% reliable on both Mac and Raspberry Pi
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{self.width}x{self.height}", 
            "-r", str(self.fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "ultrafast", 
            "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-threads", "1",
            "-profile:v", "main", "-level:v", "4.0",
            "-b:v", "800k", "-maxrate", "1000k", "-bufsize", "2M",
            "-g", str(int(self.fps * 2)), # GOP = 2s * FPS (24 frames)
            "-keyint_min", str(int(self.fps * 2)), "-sc_threshold", "0",
            "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
            "-hls_flags", "delete_segments+independent_segments+discont_start+omit_endlist+temp_file", 
            "-hls_segment_filename", os.path.join(self.output_dir, "segment_%d.ts"), 
            os.path.join(self.output_dir, "playlist.m3u8")
        ]
        log = open(os.path.join(self.output_dir, "ffmpeg.log"), "a")
        print(f"[LOG] Camera {self.cam_id} detector stream started with resolution: {self.width}x{self.height}, FPS: {self.fps}, Speed: 1.4x real-time (GOP 24), Bitrate: 800k (max 1000k)", flush=True)
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log, stdout=subprocess.DEVNULL)

    def _letterbox(self, f):
        h, w = f.shape[:2]
        s = min(self.width/w, self.height/h)
        nw, nh = int(w*s), int(h*s)
        res = cv2.resize(f, (nw, nh))
        return cv2.copyMakeBorder(res, (self.height-nh)//2, (self.height-nh+1)//2, (self.width-nw)//2, (self.width-nw+1)//2, cv2.BORDER_CONSTANT, value=[0,0,0])

    def _is_gear_on_person(self, box, cls_lower, person_boxes):
        if not person_boxes:
            # If no persons detected at all in the entire room, wearable gear cannot float in empty air
            return False
        bx1, by1, bx2, by2 = box
        bw, bh = max(0, bx2 - bx1), max(0, by2 - by1)
        b_area = bw * bh
        if b_area <= 0: return False
        
        for px1, py1, px2, py2 in person_boxes:
            pw, ph = max(0, px2 - px1), max(0, py2 - py1)
            if pw <= 0 or ph <= 0: continue

            # 1. Scale constraint: gear cannot be 2x larger than the person wearing it
            if any(k in cls_lower for k in ["helmet", "hat", "mask", "goggle", "lamp"]):
                if bw > pw * 1.3 or bh > ph * 0.65: continue
            elif any(k in cls_lower for k in ["vest", "belt", "harness"]):
                if bw > pw * 1.5 or bh > ph * 1.1: continue
            elif any(k in cls_lower for k in ["boot", "shoe"]):
                if bw > pw * 1.3 or bh > ph * 0.55: continue

            # 2. Overlap constraint with human body region
            exp_px1 = px1 - pw * 0.25
            exp_px2 = px2 + pw * 0.25
            exp_py1 = py1 - ph * 0.30
            exp_py2 = py2 + ph * 0.30
            
            ix1 = max(bx1, exp_px1)
            iy1 = max(by1, exp_py1)
            ix2 = min(bx2, exp_px2)
            iy2 = min(by2, exp_py2)
            
            if ix2 > ix1 and iy2 > iy1:
                inter_area = (ix2 - ix1) * (iy2 - iy1)
                # If at least 15% of the box overlaps with the person area, it is valid
                if inter_area / b_area >= 0.15:
                    return True
        return False

    def _is_valid_box(self, cls_lower, conf_val, bw, bh, box_area, f_w, f_h, f_area, crop_img=None):
        # Respect user confidence setting
        if conf_val < self.conf:
            return False

        # 1. Absolute Minimum Size (Rejects micro-noise and single-pixel compression artifacts)
        if bw < 15 or bh < 15 or box_area < 300:
            return False

        # 2. Aspect Ratios
        aspect_w_to_h = bw / max(1.0, bh)
        aspect_h_to_w = bh / max(1.0, bw)

        # 3. Small Gear (Helmets, Masks, Boots, Gloves, Goggles, Caps)
        if any(k in cls_lower for k in ["boot", "glove", "helmet", "hat", "mask", "goggle", "lamp", "choke", "bucket"]):
            if bw > 0.25 * f_w or bh > 0.35 * f_h or box_area > 0.06 * f_area:
                return False
            if aspect_w_to_h > 3.0 or aspect_h_to_w > 3.5:
                return False

        # 4. Torso Gear (Safety Vests, Harnesses, Belts)
        elif any(k in cls_lower for k in ["vest", "belt", "harness"]):
            if bw > 0.35 * f_w or bh > 0.50 * f_h or box_area > 0.12 * f_area:
                return False
            if aspect_w_to_h > 2.8 or aspect_h_to_w > 4.0:
                return False

        # 5. Fire & Smoke
        elif "fire" in cls_lower or "smoke" in cls_lower:
            if box_area < 500:
                return False

        # 6. General / Full-Body Classes (Person, Vehicle, etc.)
        else:
            if bw > 0.75 * f_w or bh > 0.95 * f_h or box_area > 0.50 * f_area:
                return False

        # 7. Flat-Space / Empty Background Filter (Only rejects completely uniform flat textures)
        if crop_img is not None and crop_img.size > 0:
            try:
                import numpy as np
                if np.std(crop_img) < 8.0:
                    return False
            except:
                pass

        return True

    def _run_all_models(self, f):
        try:
            cur_cls, now = set(), time.time()
            boxes_data = []
            f_h, f_w = f.shape[:2]
            f_area = f_w * f_h

            # Detect persons in frame to anchor human-dependent PPE classes
            person_boxes = []
            if self.person_detector is not None:
                try:
                    p_res = self.person_detector.predict(f, classes=[0], conf=min(0.25, self.conf), imgsz=640, verbose=False)
                    if p_res and p_res[0].boxes:
                        for pb in p_res[0].boxes:
                            person_boxes.append(pb.xyxy[0].cpu().numpy().tolist())
                except: pass

            ppe_keywords = ["vest", "helmet", "hat", "boot", "glove", "mask", "goggle", "belt", "harness", "lamp", "fall"]

            for midx, model in enumerate(self.models):
                for r in model.predict(f, conf=self.conf, iou=self.iou, imgsz=640, verbose=False):
                    if r.boxes:
                        for b in r.boxes:
                            box_xyxy = b.xyxy[0].cpu().numpy().tolist()
                            x1, y1, x2, y2 = box_xyxy
                            bw = max(0, x2 - x1)
                            bh = max(0, y2 - y1)
                            box_area = bw * bh
                            cls = r.names[int(b.cls[0])]
                            cls_lower = str(cls).strip().lower().replace("_", "-")
                            conf_val = float(b.conf[0])

                            # Anchor PPE and Violation classes to an actual human body
                            is_ppe = any(k in cls_lower for k in ppe_keywords)
                            if is_ppe:
                                if self.person_detector is not None and not self._is_gear_on_person(box_xyxy, cls_lower, person_boxes):
                                    continue

                            # Crop the detected region for texture verification
                            crop = None
                            try:
                                ix1, iy1, ix2, iy2 = max(0, int(x1)), max(0, int(y1)), min(f_w, int(x2)), min(f_h, int(y2))
                                if ix2 > ix1 and iy2 > iy1:
                                    crop = f[iy1:iy2, ix1:ix2]
                            except: pass

                            # Dynamic validation across all models
                            if not self._is_valid_box(cls_lower, conf_val, bw, bh, box_area, f_w, f_h, f_area, crop):
                                continue

                            cur_cls.add(cls)
                            label_text = f"{cls} {conf_val:.2f}"
                            color_val = colors(int(b.cls[0])+midx*50, True)
                            boxes_data.append((box_xyxy, label_text, color_val))

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
            ret, f = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            with self._frame_lock:
                self._latest_raw_frame = f
                self._cap_ok = True
                self._last_frame_time = time.time()

    def run(self):
        ffmpeg, cap, inf_t, cap_t = None, None, None, None
        while True:
            self._stop_event.set()
            if inf_t: inf_t.join(timeout=5)
            if cap: cap.release()
            if ffmpeg:
                try: ffmpeg.stdin.close()
                except: pass
                ffmpeg.kill(); ffmpeg.wait()
            self._stop_event.clear()
            self._frame_queue = queue.Queue(maxsize=1)
            self._result_queue = queue.Queue(maxsize=1)
            self._latest_raw_frame = None
            self._cap_ok = True

            try:
                self.width, self.height = 1280, 720
                cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                
                retry_count = 0
                while not cap.isOpened() and retry_count < 10:
                    time.sleep(0.5)
                    cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                    retry_count += 1
                
                if cap and cap.isOpened():
                    cap_t = threading.Thread(target=self._capture_thread, args=(cap,), daemon=True)
                    cap_t.start()

                # Wait up to 5s for the first real frame from camera before starting FFmpeg
                t0 = time.time()
                while time.time() - t0 < 5.0 and self._latest_raw_frame is None:
                    time.sleep(0.05)
                
                ffmpeg = self._create_ffmpeg()
                inf_t = threading.Thread(target=self._inference_thread, daemon=True)
                inf_t.start()
                
                f_int = 1.0 / self.fps
                next_frame_time = time.time()
                
                while True:
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
