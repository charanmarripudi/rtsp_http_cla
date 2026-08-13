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

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;30000000|reorder_queue_size;30|probesize;10000000|analyzeduration;10000000"

class DetectorWorker:
    def __init__(self, rtsp_url, output_dir, model_paths, fps=15, conf=0.40, iou=0.45, location=None):
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
            "-b:v", "600k", "-maxrate", "800k", "-bufsize", "1.5M",
            "-g", str(int(self.fps * 2)), # GOP = 2s * FPS
            "-keyint_min", str(int(self.fps * 2)), 
            "-f", "hls", "-hls_time", "2", "-hls_list_size", "10",
            "-hls_flags", "delete_segments+independent_segments+discont_start+omit_endlist+temp_file", 
            "-hls_segment_filename", os.path.join(self.output_dir, "segment_%d.ts"), 
            os.path.join(self.output_dir, "playlist.m3u8")
        ]
        log = open(os.path.join(self.output_dir, "ffmpeg.log"), "a")
        print(f"[LOG] Camera {self.cam_id} detector stream started with resolution: {self.width}x{self.height}, FPS: {self.fps}, Bitrate: 600k (max 800k)", flush=True)
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log, stdout=subprocess.DEVNULL)

    def _letterbox(self, f):
        h, w = f.shape[:2]
        s = min(self.width/w, self.height/h)
        nw, nh = int(w*s), int(h*s)
        res = cv2.resize(f, (nw, nh))
        return cv2.copyMakeBorder(res, (self.height-nh)//2, (self.height-nh+1)//2, (self.width-nw)//2, (self.width-nw+1)//2, cv2.BORDER_CONSTANT, value=[0,0,0])

    def _run_all_models(self, f):
        try:
            cur_cls, now = set(), time.time()
            boxes_data = []
            f_h, f_w = f.shape[:2]
            f_area = f_w * f_h

            # Strict realistic bounding box limits for PPE categories
            # Small gear (boots, shoes, gloves, helmet, mask, goggles) should NEVER span across rooms/desks
            small_gear_classes = {
                "gum-boots", "no-gum-boots", "boots", "no_boots", 
                "gloves", "no-gloves", "no_gloves", 
                "helmet", "hard-hat", "no-hard-hat", "no_helmet", 
                "mask", "no-mask", "goggles", "no_goggles", 
                "cap-lamp", "no-cap-lamp", "wheel-choke", "sand-bucket"
            }
            torso_gear_classes = {
                "saftey-vest", "no-saftey-vest", "safety vest", "no_safety_vest", 
                "saftey-belt", "no-saftey-belt", "harness", "no_harness", "vest"
            }

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

                            # 1. Reject small gear if bounding box is absurdly large (e.g. spanning multiple tables/people)
                            if any(k in cls_lower for k in ["boot", "glove", "helmet", "hat", "mask", "goggle", "lamp", "choke", "bucket"]):
                                if bw > 0.28 * f_w or bh > 0.35 * f_h or box_area > 0.07 * f_area:
                                    continue
                            # 2. Reject torso gear if bounding box exceeds realistic human upper-body scale
                            elif any(k in cls_lower for k in ["vest", "belt", "harness"]):
                                if bw > 0.40 * f_w or bh > 0.55 * f_h or box_area > 0.16 * f_area:
                                    continue
                            # 3. General ceiling for full-body / environment detections
                            else:
                                if bw > 0.50 * f_w or bh > 0.80 * f_h or box_area > 0.28 * f_area:
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
            try: f = self._frame_queue.get(timeout=0.5)
            except: continue
            boxes = self._run_all_models(f)
            with self._box_lock:
                self._latest_boxes = boxes
            # Pacing: 50ms pause ensures CPU cores are shared gracefully across all cameras
            time.sleep(0.05)

    def _capture_thread(self, cap):
        retry_count = 0
        max_retries = 10
        while not self._stop_event.is_set():
            ret, f = cap.read()
            if not ret:
                retry_count +=1
                if retry_count > max_retries:
                    self._cap_ok = False
                    break
                time.sleep(2)
                continue
            retry_count =0 # Reset retry count on success
            with self._frame_lock:
                self._latest_raw_frame, self._cap_ok, self._last_frame_time = f, True, time.time()

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
            self._stop_event.clear(); self._frame_queue, self._result_queue, self._latest_raw_frame, self._cap_ok = queue.Queue(maxsize=2), queue.Queue(maxsize=2), None, True
            try:
                # Initialize default dimensions early for fallback frame
                self.width, self.height = 1280, 720
                
                cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
                
                retry_count = 0
                while not cap.isOpened() and retry_count < 10:
                    time.sleep(2)
                    cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                    retry_count +=1
                
                ffmpeg = self._create_ffmpeg()
                inf_t = threading.Thread(target=self._inference_thread, daemon=True); inf_t.start()
                if cap and cap.isOpened():
                    cap_t = threading.Thread(target=self._capture_thread, args=(cap,), daemon=True); cap_t.start()
                
                f_int, last_w = 1.0 / self.fps, 0.0
                while True:
                    if cap and cap.isOpened():
                        if not self._cap_ok or time.time() - self._last_frame_time > 15.0: break
                    with self._frame_lock: f = self._latest_raw_frame
                    
                    # Use fallback connecting frame if no frame available
                    if f is None: 
                        f = self._get_connecting_frame()
                    
                    now = time.time()
                    # Only letterbox if we are going to use it for FFmpeg OR if inference queue is empty
                    if now - last_w >= f_int or not self._frame_queue.full():
                        pf = self._letterbox(f)
                        if not self._frame_queue.full() and cap and cap.isOpened(): 
                            self._frame_queue.put(pf)
                    else:
                        time.sleep(0.005)
                        continue
                    
                    if now - last_w < f_int: continue
                    last_w = now
                    
                    with self._box_lock:
                        cur_boxes = list(self._latest_boxes)
                    
                    if cur_boxes:
                        for b_xyxy, label_text, color_val in cur_boxes:
                            try:
                                x1, y1, x2, y2 = [int(v) for v in b_xyxy]
                                if isinstance(color_val, (list, tuple)) and len(color_val) >= 3:
                                    c = (int(color_val[0]), int(color_val[1]), int(color_val[2]))
                                else:
                                    c = (0, 255, 128)
                                cv2.rectangle(pf, (x1, y1), (x2, y2), c, 1)
                                t_size = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)[0]
                                cv2.rectangle(pf, (x1, max(0, y1 - t_size[1] - 4)), (x1 + t_size[0] + 4, max(0, y1)), c, -1)
                                cv2.putText(pf, label_text, (x1 + 2, max(t_size[1] + 2, y1 - 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 1, cv2.LINE_AA)
                            except: pass
                    out = pf

                    if ffmpeg.poll() is not None: break
                    try: 
                        ffmpeg.stdin.write(out.tobytes())
                        ffmpeg.stdin.flush()
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
