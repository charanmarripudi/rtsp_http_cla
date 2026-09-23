import cv2, subprocess, os, time, threading, queue, json
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
    torch.set_num_threads(2)
except Exception as e:
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
    def __init__(self, rtsp_url, output_dir, model_paths, fps=15, conf=0.25, iou=0.45, location=None):
        self.rtsp_url, self.output_dir, self.fps, self.conf, self.iou = rtsp_url, output_dir, fps, conf, iou
        self.location = location or f"Camera {os.path.basename(output_dir).replace('stream', '').replace('_detected', '')}"
        self.width, self.height = 1280, 720
        self._stop_event, self._frame_queue, self._result_queue = threading.Event(), queue.Queue(maxsize=2), queue.Queue(maxsize=2)
        self._latest_raw_frame, self._frame_lock, self._cap_ok, self._last_frame_time = None, threading.Lock(), True, time.time()
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
        import platform
        is_rpi_sys = platform.system() == "Linux" and platform.machine() in ["aarch64", "armv7l"]
        
        if is_rpi_sys:
            # Raspberry Pi Hardware H.264 Encoder (extremely low CPU usage)
            cmd = [
                "ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{self.width}x{self.height}", 
                "-r", str(self.fps), "-i", "-", "-an", "-c:v", "h264_v4l2m2m", "-pix_fmt", "yuv420p", 
                "-b:v", "600k", "-maxrate", "800k",
                "-g", str(int(self.fps * 2)),
                "-keyint_min", str(int(self.fps * 2)), 
                "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
                "-hls_flags", "delete_segments+independent_segments+discont_start+omit_endlist", 
                "-hls_segment_filename", os.path.join(self.output_dir, "segment_%d.ts"), 
                os.path.join(self.output_dir, "playlist.m3u8")
            ]
        else:
            # Software encoder for Mac/PC
            cmd = [
                "ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{self.width}x{self.height}", 
                "-r", str(self.fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "ultrafast", 
                "-tune", "zerolatency", "-pix_fmt", "yuv420p", 
                "-profile:v", "main", "-level:v", "4.0",
                "-b:v", "600k", "-maxrate", "800k", "-bufsize", "1.5M",
                "-g", str(int(self.fps * 2)), # GOP = 2s * FPS
                "-keyint_min", str(int(self.fps * 2)), 
                "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
                "-hls_flags", "delete_segments+independent_segments+discont_start+omit_endlist", 
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
            ann, cur_cls, now = Annotator(f.copy(), line_width=2), set(), time.time()
            for midx, model in enumerate(self.models):
                for r in model.predict(f, conf=self.conf, iou=self.iou, imgsz=640, verbose=False):
                    if r.boxes:
                        for b in r.boxes:
                            cls = r.names[int(b.cls[0])]
                            cur_cls.add(cls)
                            ann.box_label(b.xyxy[0].cpu().numpy().tolist(), f"{cls} {float(b.conf[0]):.2f}", color=colors(int(b.cls[0])+midx*50, True))
            res = ann.result()
            for c in cur_cls:
                if c not in self.alert_timers: self.alert_timers[c] = now
                elif now - self.alert_timers[c] >= 3.0 and c not in self.alert_triggered:
                    self.alert_triggered.add(c); self._save_alert(c, res)
            for c in list(self.alert_timers):
                if c not in cur_cls:
                    del self.alert_timers[c]
                    if c in self.alert_triggered: self.alert_triggered.remove(c)
            return res
        except: return f

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
                except: 
                    if self._db_conn: self._db_conn.rollback()

            # Local JSON Store
            try:
                adir_root = os.path.dirname(os.path.dirname(self.output_dir))
                alog = os.path.join(adir_root, "hls", "alerts", "alerts.json")
                os.makedirs(os.path.dirname(alog), exist_ok=True)
                data = json.load(open(alog)) if os.path.exists(alog) else []
                data.insert(0, {"camera": self.cam_id, "time": now_dt.strftime("%Y-%m-%d %H:%M:%S"), "event": f"{class_name} Detected", "image": image_path})
                json.dump(data, open(alog, "w"), indent=4)
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
            try: f = self._frame_queue.get(timeout=1.0)
            except: continue
            ann = self._run_all_models(f)
            if self._result_queue.full():
                try: self._result_queue.get_nowait()
                except: pass
            self._result_queue.put(ann)

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
                self.width, self.height = 854, 480
                
                cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
                
                retry_count = 0
                while not cap.isOpened() and retry_count < 10:
                    time.sleep(2)
                    cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                    retry_count +=1
                if not cap.isOpened(): 
                    # If we can't open camera, still run FFmpeg with fallback frame
                    h, w = 480, 854
                else:
                    h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                    w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                    if not h or not w: h, w = 480, 854
                
                self.width = 854; self.height = int(int(h) * (854 / int(w)))
                self.height += self.height % 2; ffmpeg = self._create_ffmpeg()
                inf_t = threading.Thread(target=self._inference_thread, daemon=True); inf_t.start()
                if cap and cap.isOpened():
                    cap_t = threading.Thread(target=self._capture_thread, args=(cap,), daemon=True); cap_t.start()
                
                f_int, last_w, last_ann = 1.0 / self.fps, 0.0, None
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

                    try: last_ann = self._result_queue.get_nowait()
                    except: pass
                    
                    if now - last_w < f_int: continue
                    last_w = now; out = last_ann if last_ann is not None else pf
                    if ffmpeg.poll() is not None: break
                    try: 
                        ffmpeg.stdin.write(out.tobytes())
                        ffmpeg.stdin.flush()
                    except: break
            except: 
                import traceback
                traceback.print_exc()
            time.sleep(3)




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
