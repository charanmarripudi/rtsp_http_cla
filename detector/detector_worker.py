import os
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"
os.environ["VECLIB_MAXIMUM_THREADS"] = "2"
os.environ["NUMEXPR_NUM_THREADS"] = "2"
os.environ["TORCH_NUM_THREADS"] = "2"
os.environ["OPENCV_FOR_THREADS_NUM"] = "1"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|max_delay;500000|timeout;5000000"

import cv2, subprocess, time, threading, queue, json, math
import numpy as np
try:
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
except Exception:
    pass
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
    pass

from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Optimize PyTorch CPU threading to 2 dedicated cores on Raspberry Pi 4 CPU
# (Leaves remaining cores free for real-time RTSP decoding & FFmpeg HLS streaming)
try:
    import torch
    torch.set_num_threads(2)
    if hasattr(torch, "set_num_interop_threads"):
        torch.set_num_interop_threads(1)
except Exception:
    pass

import re

from alert_store import DB_DSN, ensure_alerts_schema, insert_alert_db, insert_alert_via_psql

YOLO_CACHE = {}

# ──────────────────────────────────────────────────────────────────────────────
# TUNABLE KNOBS
# ──────────────────────────────────────────────────────────────────────────────
# Shorter track age (1.2s) ensures snappy box movement that tracks moving people
TRACK_MAX_AGE_S = 1.2

# 0.20 EMA gives 80% weight to fresh coordinates so boxes stick to moving objects
BOX_EMA_ALPHA = 0.20

# Minimum IoU overlap to consider two boxes the same detection (same-class NMS)
NMS_SAME_IOU_THRESH = 0.30
NMS_SAME_IO_MIN_THRESH = 0.50

# Minimum IoU overlap to suppress the weaker side of an opposite-class pair
# (e.g.  Safety-Vest vs NO-Safety-Vest on the same person)
NMS_OPP_IOU_THRESH = 0.20
NMS_OPP_IO_MIN_THRESH = 0.30

# Hard floor for YOLO confidence passed to model.predict().
# Keeping this at 0.06 lets us catch low-confidence detections for per-class
# post-filtering, but we apply a stricter per-class gate afterwards.
PREDICT_CONF_FLOOR = 0.10   # raised from 0.06 → fewer ghost boxes fed into NMS

# Scheduler inter-camera sleep (seconds). Allows CPU cooldown and prevents thermal throttling.
SCHEDULER_SLEEP_S = 0.06

# Label rendering
LABEL_FONT_SCALE   = 0.55
LABEL_THICKNESS    = 1
LABEL_BOX_PADDING  = 5

def clean_str(s):
    res = re.sub(r'[^a-z0-9]', '', str(s).lower())
    return res.replace("saftey", "safety")

def extract_negation_and_core(s):
    s_str = str(s).strip()
    if " - " in s_str:
        s_str = s_str.split(" - ")[-1]
    elif ":" in s_str:
        s_str = s_str.split(":")[-1]
    elif "/" in s_str:
        s_str = s_str.split("/")[-1]
        
    cleaned = clean_str(s_str)
    
    # Generic violation keywords
    if cleaned in ("none", "noppe", "noviolation", "violation", "withoutppe"):
        return True, "violation", cleaned

    neg_prefixes = ["no", "without", "non", "un"]
    is_neg = False
    core = cleaned
    for p in neg_prefixes:
        if cleaned.startswith(p):
            is_neg = True
            core = cleaned[len(p):]
            break
    
    synonym_map = {
        "hardhat": "headgear",
        "helmet": "headgear",
        "safetyhelmet": "headgear",
        "headprotection": "headgear",
        "head": "headgear",
        "vest": "bodyvest",
        "safetyvest": "bodyvest",
        "reflectivevest": "bodyvest",
        "jacket": "bodyvest",
        "mask": "facemask",
        "facemask": "facemask",
        "facecover": "facemask",
        "goggles": "eyegoggles",
        "glasses": "eyegoggles",
        "safetyglasses": "eyegoggles",
        "eyewear": "eyegoggles",
        "glove": "handgloves",
        "gloves": "handgloves",
        "handglove": "handgloves",
        "shoe": "footshoes",
        "shoes": "footshoes",
        "boot": "footshoes",
        "boots": "footshoes",
        "safetyshoe": "footshoes",
        "safetyshoes": "footshoes",
        "person": "person",
        "worker": "person",
        "human": "person",
        "man": "person",
        "woman": "person",
        "fire": "fire",
        "flame": "fire",
        "smoke": "smoke",
    }
    mapped_core = synonym_map.get(core, core)
    return is_neg, mapped_core, cleaned

def match_class(box_cls, enabled_cls):
    """
    Robust negation and synonym-aware class matcher.
    Returns True if box_cls matches enabled_cls dynamically across models.
    """
    if not box_cls or not enabled_cls:
        return False
        
    b_neg, b_core, b_clean = extract_negation_and_core(box_cls)
    e_neg, e_core, e_clean = extract_negation_and_core(enabled_cls)
    
    if b_clean == e_clean:
        return True
        
    if b_neg != e_neg:
        return False
        
    # Generic violation matching (e.g. 'none' from construction.pt matches 'NO-Hardhat' / 'NO-Safety Vest')
    if b_core == "violation" or e_core == "violation":
        return True
        
    if b_core == e_core:
        return True
        
    if (b_core in e_core or e_core in b_core) and len(b_core) >= 3 and len(e_core) >= 3:
        return True
        
    return False

INFERENCE_LOCK = threading.Lock()

def get_yolo_model(model_path):
    resolved_path = str(model_path)
    if not os.path.isabs(resolved_path) and not os.path.exists(resolved_path):
        m_dir = os.path.join(str(BASE_DIR), "models")
        if os.path.exists(os.path.join(m_dir, resolved_path)):
            resolved_path = os.path.join(m_dir, resolved_path)
        elif os.path.exists(os.path.join(m_dir, os.path.basename(resolved_path))):
            resolved_path = os.path.join(m_dir, os.path.basename(resolved_path))

    if not os.path.exists(resolved_path):
        # File is not on disk — return None to prevent Ultralytics from freezing with GitHub 429 errors
        return None

    if resolved_path not in YOLO_CACHE:
        with INFERENCE_LOCK:
            if resolved_path not in YOLO_CACHE:
                try:
                    print(f"[CACHE] Loading model weights into memory: {resolved_path}", flush=True)
                    model = YOLO(resolved_path)
                    # Warm up model once with a dummy frame to avoid PyTorch first-pass graph compilation latency
                    try:
                        dummy_frame = np.zeros((480, 854, 3), dtype=np.uint8)
                        if 'torch' in globals() and hasattr(torch, 'inference_mode'):
                            with torch.inference_mode():
                                model.predict(source=dummy_frame, imgsz=480, verbose=False)
                        else:
                            model.predict(source=dummy_frame, imgsz=480, verbose=False)
                        print(f"[CACHE] Model warmed up successfully: {resolved_path}", flush=True)
                    except Exception as we:
                        print(f"[CACHE-WARN] Warmup skipped: {we}", flush=True)
                    YOLO_CACHE[resolved_path] = model
                except Exception as e:
                    print(f"[CACHE-ERR] Could not load model weights {resolved_path}: {e}", flush=True)
                    return None
    return YOLO_CACHE.get(resolved_path)

def get_alerts_base_url():
    try:
        public_url_file = os.path.join(str(BASE_DIR), "hls", "public_url.txt")
        if os.path.exists(public_url_file):
            with open(public_url_file) as f:
                val = f.read().strip()
                if val and not val.startswith("("):
                    return val
    except: pass

def is_opposite_class(cls1, cls2):
    try:
        b_neg, b_core, _ = extract_negation_and_core(cls1)
        e_neg, e_core, _ = extract_negation_and_core(cls2)
        if b_neg != e_neg and b_core == e_core and len(b_core) >= 3:
            return True
    except Exception:
        pass
    return False

DYNAMIC_CLASS_COLOR_MAP = {
    # Violations (NO-PPE) -> Distinct Warning Colors (Red, Orange, Magenta, Pink)
    "no-safety-vest": (0, 0, 255),        # Bright Crimson Alert Red (BGR)
    "no-vest": (0, 0, 255),
    "nosafetyvest": (0, 0, 255),
    "novest": (0, 0, 255),
    "no-saftey-vest": (0, 0, 255),

    "no-hardhat": (0, 100, 255),         # Vivid Amber Orange (BGR)
    "no-helmet": (0, 100, 255),
    "nohardhat": (0, 100, 255),
    "nohelmet": (0, 100, 255),

    "no-mask": (255, 0, 255),            # Electric Magenta / Fuchsia (BGR)
    "nomask": (255, 0, 255),

    "no-gloves": (200, 80, 255),         # Neon Pink (BGR)
    "nogloves": (200, 80, 255),

    "no-goggles": (220, 30, 180),        # Deep Violet (BGR)
    "nogoggles": (220, 30, 180),
    "no-glasses": (220, 30, 180),

    "no-shoes": (0, 140, 220),           # Amber Rust (BGR)
    "noshoes": (0, 140, 220),
    "no-boots": (0, 140, 220),

    # Compliant Equipment -> Emerald Green, Cyan Sky-Blue, Lemon Yellow
    "safety-vest": (255, 220, 0),        # Electric Sky Blue / Cyan (BGR)
    "vest": (255, 220, 0),
    "saftey-vest": (255, 220, 0),

    "hardhat": (0, 230, 80),             # Emerald Lime Green (BGR)
    "helmet": (0, 230, 80),

    "mask": (0, 230, 255),               # Lemon Yellow (BGR)
    "goggles": (255, 120, 30),           # Cobalt Blue (BGR)
    "gloves": (180, 230, 50),            # Turquoise Teal (BGR)
    "shoes": (50, 205, 50),              # Spring Green (BGR)
    "boots": (50, 205, 50),

    # Objects & People
    "person": (200, 100, 50),            # Slate Blue (BGR)
    "worker": (200, 100, 50),
    "human": (200, 100, 50),
    "fire": (0, 0, 255),                 # Pure Red
    "smoke": (180, 180, 180),            # Silver Grey
}

def get_dynamic_class_color(class_name):
    """
    Returns a unique, fixed, high-contrast BGR color for each class name.
    Guarantees NO-Hardhat, NO-Safety Vest, NO-Mask, etc., have completely distinct colors.
    """
    if not class_name:
        return (0, 255, 255)
    
    norm_name = clean_str(class_name).replace("_", "-").replace(" ", "-")
    if norm_name in DYNAMIC_CLASS_COLOR_MAP:
        return DYNAMIC_CLASS_COLOR_MAP[norm_name]
        
    for k, v in DYNAMIC_CLASS_COLOR_MAP.items():
        if k in norm_name or norm_name in k:
            return v
            
    import hashlib
    h = int(hashlib.md5(norm_name.encode('utf-8')).hexdigest(), 16)
    palette = [
        (0, 140, 255),   # Vivid Orange
        (255, 190, 40),  # Electric Cyan
        (30, 45, 255),   # Coral Red
        (0, 230, 115),   # Emerald Green
        (180, 20, 255),  # Magenta
        (0, 215, 255),   # Golden Yellow
        (255, 105, 180), # Neon Pink
        (210, 230, 0),   # Turquoise
        (50, 205, 50),   # Lime Green
        (238, 130, 238), # Violet
        (30, 144, 255),  # Sky Blue
        (255, 215, 0),   # Gold
        (255, 69, 0),    # Red Orange
        (0, 255, 127),   # Spring Green
    ]
    return palette[h % len(palette)]

def get_config_for_model(model_configs, m_name):
    if not isinstance(model_configs, dict) or not model_configs:
        return {}
    m_clean = m_name.replace(".pt", "")
    m_norm = m_clean.lower().replace("_", "-").replace(" ", "-")
    for k, v in model_configs.items():
        if isinstance(v, dict):
            k_clean = str(k).replace(".pt", "")
            k_norm = k_clean.lower().replace("_", "-").replace(" ", "-")
            if k_norm == m_norm or k_clean == m_clean:
                return v
    if "enabled_classes" in model_configs or "class_configs" in model_configs or "conf" in model_configs:
        return model_configs
    return {}

class InferenceScheduler:
    """
    Centralized, Fair Multi-Camera Inference Scheduler.
    Executes AI inference in strict round-robin sequence across all active cameras.
    Guarantees exactly ONE PyTorch inference runs on the Pi CPU at any time,
    eliminating lock contention, overheating, and thread starvation.
    """
    def __init__(self):
        self._workers = {}
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()

    def register_worker(self, worker):
        with self._lock:
            self._workers[worker.cam_id] = worker
            worker._needs_immediate_inference = True
            print(f"[SCHEDULER] Registered Camera {worker.cam_id} into Central Inference Scheduler (Total Active: {len(self._workers)})", flush=True)
        self.ensure_running()

    def unregister_worker(self, worker):
        with self._lock:
            self._workers.pop(worker.cam_id, None)
            print(f"[SCHEDULER] Unregistered Camera {worker.cam_id} from Central Inference Scheduler (Remaining Active: {len(self._workers)})", flush=True)

    def ensure_running(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop_event.clear()
                self._thread = threading.Thread(target=self._run_loop, daemon=True, name="CentralInferenceScheduler")
                self._thread.start()

    def _run_loop(self):
        print("[SCHEDULER] Centralized Multi-Camera Inference Scheduler active", flush=True)
        while not self._stop_event.is_set():
            with self._lock:
                new_workers = [w for w in self._workers.values() if getattr(w, '_needs_immediate_inference', False)]
                other_workers = [w for w in self._workers.values() if not getattr(w, '_needs_immediate_inference', False)]
                active_workers = new_workers + other_workers

            if not active_workers:
                time.sleep(0.05)
                continue

            for worker in active_workers:
                if self._stop_event.is_set():
                    break
                if worker._stop_event.is_set():
                    continue
                try:
                    worker.run_single_inference_cycle()
                    worker._needs_immediate_inference = False
                except Exception as e:
                    print(f"[SCHEDULER-ERR] Camera {worker.cam_id} inference error: {e}", flush=True)

                time.sleep(SCHEDULER_SLEEP_S)

GLOBAL_INFERENCE_SCHEDULER = InferenceScheduler()


class DetectorWorker:
    @property
    def model_configs(self):
        return self._model_configs

    @model_configs.setter
    def model_configs(self, val):
        self._model_configs = val or {}
        new_roi = self._model_configs.get("roi_polygon")
        if new_roi != getattr(self, "roi_polygon", None):
            self.roi_polygon = new_roi
            if hasattr(self, "_box_lock"):
                with self._box_lock:
                    self._tracked_boxes = []
        print(f"[WORKER-ROI-UPDATE] Camera {getattr(self, 'cam_id', '?')} model_configs updated, roi_polygon={self.roi_polygon}", flush=True)

    def __init__(self, rtsp_url, output_dir, model_paths, conf=0.20, iou=0.45, location="Camera", model_configs=None):
        self.roi_polygon = None
        self.rtsp_url, self.output_dir, self.model_paths, self.conf, self.iou, self.location = rtsp_url, output_dir, model_paths, conf, iou, location
        self.model_configs = model_configs or {}
        self.fps, self.width, self.height = 12.0, 854, 480
        self._latest_raw_frame = None
        self._tracked_boxes = []
        self._prev_inference_boxes = []
        self._frame_lock, self._box_lock = threading.Lock(), threading.Lock()
        self._stop_event = threading.Event()
        self._last_frame_time, self._cap_ok = time.time(), True
        self.alert_timers, self.alert_triggered = {}, set()
        self.cam_id = os.path.basename(output_dir).replace("stream", "").replace("_detected", "")
        if isinstance(model_paths, list):
            seen = []
            for p in model_paths:
                if p not in seen: seen.append(p)
            self.model_paths = seen
        else:
            self.model_paths = model_paths
        self.models = None
        self._db_conn = None
        self._start_time = time.time()
        self._models_active_time = None
        self._first_box_logged = False
        # EMA smoothing state: maps a stable track_id → smoothed box coords
        # Each entry: {'box': [x1,y1,x2,y2], 'cls': str, 'color': tuple, 'conf': float, 'last_seen': float}
        self._ema_tracks = {}
        self._ema_next_id = 0
        print(f"[TIMER-START] Camera {self.cam_id} Start request initialized at {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}", flush=True)

    def update_models(self, model_paths, model_configs=None, conf=None, iou=None, location=None):
        if model_paths is not None:
            paths = model_paths if isinstance(model_paths, list) else [model_paths]
            seen = []
            for p in paths:
                if p not in seen: seen.append(p)
            self.model_paths = seen
            self.models = [get_yolo_model(mp) for mp in seen]
            self._models_active_time = time.time()
        if model_configs is not None:
            self.model_configs = model_configs
        if conf is not None:
            self.conf = conf
        if iou is not None:
            self.iou = iou
        if location is not None:
            self.location = location
        if hasattr(self, "_box_lock"):
            with self._box_lock:
                self._tracked_boxes = []
        # Reset EMA tracks when models change so stale boxes from old model don't linger
        self._ema_tracks = {}
        self._ema_next_id = 0
        print(f"[WORKER-DYNAMIC-UPDATE] Camera {getattr(self, 'cam_id', '?')} dynamically updated models to {self.model_paths} in 0ms without restarting RTSP or FFmpeg", flush=True)

    def stop(self):
        self._stop_event.set()
        GLOBAL_INFERENCE_SCHEDULER.unregister_worker(self)

    def _get_db_conn(self):
        if not PSYCOPG2_AVAILABLE:
            return None
        if self._db_conn is None or getattr(self._db_conn, 'closed', 1) != 0:
            try:
                self._db_conn = psycopg2.connect(DB_DSN, connect_timeout=5)
            except Exception as e:
                print(f"[ALERT-DB-CONN-ERR] PostgreSQL connection to {DB_DSN} failed: {e}", flush=True)
                self._db_conn = None
        return self._db_conn

    def _create_ffmpeg(self):
        os.makedirs(self.output_dir, exist_ok=True)
        import glob
        for f in glob.glob(os.path.join(self.output_dir, "*")):
            try:
                if os.path.isfile(f) or os.path.islink(f):
                    os.remove(f)
            except Exception:
                pass

        session_id = int(time.time())
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{self.width}x{self.height}", 
            "-r", str(int(self.fps)), "-i", "-", "-an", "-c:v", "libx264", "-preset", "ultrafast", 
            "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-threads", "1",
            "-profile:v", "baseline", "-level:v", "3.1",
            "-b:v", "350k", "-maxrate", "450k", "-bufsize", "800k",
            "-g", str(int(self.fps)), 
            "-keyint_min", str(int(self.fps)), "-sc_threshold", "0",
            "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
            "-hls_flags", "delete_segments+independent_segments+discont_start+omit_endlist+temp_file", 
            "-hls_segment_filename", os.path.join(self.output_dir, f"segment_{session_id}_%d.ts"), 
            os.path.join(self.output_dir, "playlist.m3u8")
        ]
        # NOTE: hls_list_size=6 keeps 6×2s = 12s of segments for remote viewers to buffer
        # without stuttering over high-latency tunnels (ngrok/cloudflare).
        log = open(os.path.join(self.output_dir, "ffmpeg.log"), "a")
        print(f"[LOG] Camera {self.cam_id} detector stream started with resolution: {self.width}x{self.height}, FPS: {self.fps}, Bitrate: 350k (max 450k)", flush=True)
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log, stdout=subprocess.DEVNULL, bufsize=10*1024*1024)

    # ──────────────────────────────────────────────────────────────────────────
    # NMS HELPERS
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _box_iou_and_io_min(b1, b2):
        """Compute IoU and intersection-over-min-area for two [x1,y1,x2,y2] boxes."""
        x1 = max(b1[0], b2[0]); y1 = max(b1[1], b2[1])
        x2 = min(b1[2], b2[2]); y2 = min(b1[3], b2[3])
        if x2 <= x1 or y2 <= y1:
            return 0.0, 0.0
        inter  = (x2 - x1) * (y2 - y1)
        area1  = max(1.0, (b1[2]-b1[0]) * (b1[3]-b1[1]))
        area2  = max(1.0, (b2[2]-b2[0]) * (b2[3]-b2[1]))
        union  = area1 + area2 - inter
        return inter / max(1.0, union), inter / min(area1, area2)

    @staticmethod
    def _apply_nms(boxes):
        """
        Full multi-class NMS pass over a list of (xyxy, color, conf, cls_name) tuples.
        - Boxes sorted by confidence (high→low).
        - Same-class duplicates removed when IoU >= NMS_SAME_IOU_THRESH or io_min >= NMS_SAME_IO_MIN_THRESH.
        - Opposite-class pairs resolved by keeping the higher-confidence box when
          IoU >= NMS_OPP_IOU_THRESH or io_min >= NMS_OPP_IO_MIN_THRESH.
        """
        if not boxes:
            return []
        boxes = sorted(boxes, key=lambda x: x[2], reverse=True)
        kept = []
        for item in boxes:
            b1, col1, conf1, cls1 = item
            suppress = False
            for k_item in kept:
                b2, col2, conf2, cls2 = k_item
                iou, io_min = DetectorWorker._box_iou_and_io_min(b1, b2)
                is_same = match_class(cls1, cls2)
                is_opp  = is_opposite_class(cls1, cls2)
                if is_same and (iou >= NMS_SAME_IOU_THRESH or io_min >= NMS_SAME_IO_MIN_THRESH):
                    suppress = True; break
                if is_opp and (iou >= NMS_OPP_IOU_THRESH or io_min >= NMS_OPP_IO_MIN_THRESH):
                    suppress = True; break
            if not suppress:
                kept.append(item)
        return kept

    def run_single_inference_cycle(self):
        """
        Executed by the Centralized InferenceScheduler in the background.
        1. Captures the latest live frame ONCE.
        2. Evaluates all assigned models on that identical frame snapshot.
        3. Fuses detections with per-model filtering, negation handling, and multi-model NMS.
        4. Updates the smooth bounding box tracker and checks the 3.0s alert timer.
        """
        if self._stop_event.is_set():
            return

        with self._frame_lock:
            f = self._latest_raw_frame

        if f is None:
            return

        # ── Resolve output canvas size ──────────────────────────────────────
        orig_h, orig_w = f.shape[:2]
        # We run inference on the ORIGINAL raw frame so YOLO gets best quality.
        # We then scale coordinates to the output stream resolution afterward.
        scale_x = float(self.width)  / max(1.0, float(orig_w))
        scale_y = float(self.height) / max(1.0, float(orig_h))

        f_h, f_w = self.height, self.width

        cur_cls, now = set(), time.time()
        raw_boxes = []   # list of (xyxy_in_output_res, color, conf, cls_name)

        if not self.models:
            paths = self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]
            self.models = [get_yolo_model(mp) for mp in paths]
            self._models_active_time = time.time()

        for midx, model in enumerate(self.models):
            if model is None:
                continue
            m_path = self.model_paths[midx] if (isinstance(self.model_paths, list) and midx < len(self.model_paths)) else str(self.model_paths)
            m_name = os.path.basename(m_path)

            m_conf = self.conf
            m_iou  = self.iou
            enabled_classes = None
            default_imgsz   = int(os.getenv("DEFAULT_IMGSZ", "320"))
            m_imgsz         = default_imgsz
            cfg             = get_config_for_model(self.model_configs, m_name)
            class_configs   = cfg.get("class_configs", {}) if isinstance(cfg, dict) else {}
            if cfg and isinstance(cfg, dict):
                m_conf          = float(cfg.get("conf", self.conf))
                m_iou           = float(cfg.get("iou", self.iou))
                enabled_classes = cfg.get("enabled_classes")
                m_imgsz         = int(cfg.get("imgsz", default_imgsz))

            if m_imgsz % 32 != 0:
                m_imgsz = int(math.ceil(m_imgsz / 32.0) * 32)

            filter_classes = enabled_classes if (enabled_classes is not None) else []
            if not filter_classes and class_configs:
                filter_classes = list(class_configs.keys())

            effective_conf = float(m_conf) if (m_conf is not None) else float(self.conf)
            min_class_conf = effective_conf
            if class_configs and isinstance(class_configs, dict):
                for cc in class_configs.values():
                    if isinstance(cc, dict) and "conf" in cc:
                        min_class_conf = min(min_class_conf, float(cc["conf"]))

            predict_kwargs = {
                "source": f,
                "conf":   max(PREDICT_CONF_FLOOR, min(min_class_conf, effective_conf)),
                "iou":    m_iou,
                "imgsz":  m_imgsz,
                "verbose": False
            }

            try:
                t_infer_start = time.time()
                if 'torch' in globals() and hasattr(torch, 'inference_mode'):
                    with torch.inference_mode():
                        results = model.predict(**predict_kwargs)
                else:
                    results = model.predict(**predict_kwargs)
                infer_ms = int((time.time() - t_infer_start) * 1000)

                mod_dets   = []
                all_raw_dets = []
                for r in results:
                    if r.boxes:
                        for b in r.boxes:
                            cls_id   = int(b.cls[0].item())
                            cls_name = r.names.get(cls_id, str(cls_id)) if hasattr(r, 'names') else str(cls_id)
                            conf_val = float(b.conf[0].item())
                            all_raw_dets.append(f"{cls_name} {conf_val:.2f}")
                            if not filter_classes or any(match_class(cls_name, e) for e in filter_classes):
                                mod_dets.append(f"{cls_name} {conf_val:.2f}")

                print(f"[TIMER-INFERENCE] Camera {self.cam_id} model {m_name} (imgsz={m_imgsz}) -> Pure: {infer_ms}ms | Selected: {filter_classes if filter_classes else 'ALL'} | Filtered Match: {mod_dets if mod_dets else 'None'} | Raw YOLO Output: {all_raw_dets if all_raw_dets else 'None'} ({datetime.now().strftime('%H:%M:%S.%f')[:-3]})", flush=True)
            except Exception as pred_err:
                print(f"[PREDICT-ERR] Camera {self.cam_id} model {m_name}: {pred_err}", flush=True)
                continue

            for r in results:
                if r.boxes:
                    for b in r.boxes:
                        cls_id   = int(b.cls[0].item())
                        cls      = r.names.get(cls_id, str(cls_id)) if hasattr(r, 'names') else str(cls_id)
                        conf_val = float(b.conf[0].item())

                        if filter_classes:
                            if not any(match_class(cls, e) for e in filter_classes):
                                continue

                        # Per-class confidence gate (hard reject)
                        req_conf = effective_conf
                        if class_configs:
                            for cc_name, cc_val in class_configs.items():
                                if match_class(cls, cc_name) and isinstance(cc_val, dict) and "conf" in cc_val:
                                    req_conf = float(cc_val["conf"])
                                    break
                        if conf_val < req_conf:
                            continue

                        # Scale coordinates from original raw frame space → output stream resolution
                        box_raw = b.xyxy[0].cpu().numpy().tolist()
                        rx1, ry1, rx2, ry2 = box_raw
                        x1 = max(0, min(self.width  - 1, rx1 * scale_x))
                        y1 = max(0, min(self.height - 1, ry1 * scale_y))
                        x2 = max(0, min(self.width  - 1, rx2 * scale_x))
                        y2 = max(0, min(self.height - 1, ry2 * scale_y))
                        box_xyxy = [x1, y1, x2, y2]
                        
                        bw = max(0, x2 - x1)
                        bh = max(0, y2 - y1)
                        if bw < 5 or bh < 5:
                            continue
                            
                        cx = (x1 + x2) / 2.0
                        cy = (y1 + y2) / 2.0

                        if self.roi_polygon and len(self.roi_polygon) == 2:
                            try:
                                roi_x1 = int(min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                                roi_y1 = int(min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                                roi_x2 = int(max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                                roi_y2 = int(max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                                in_roi = (roi_x1 <= cx <= roi_x2 and roi_y1 <= cy <= roi_y2)
                                if not in_roi:
                                    ix1, iy1 = max(x1, roi_x1), max(y1, roi_y1)
                                    ix2, iy2 = min(x2, roi_x2), min(y2, roi_y2)
                                    if ix2 > ix1 and iy2 > iy1:
                                        in_roi = True
                                if not in_roi:
                                    continue
                            except Exception:
                                pass

                        color_val = get_dynamic_class_color(cls)
                        raw_boxes.append((box_xyxy, color_val, conf_val, cls))

        # ── Multi-Model NMS ───────────────────────────────────────────────────
        kept_items = self._apply_nms(raw_boxes)

        # ── EMA-Smoothed Persistent Track Memory ─────────────────────────────
        # Strategy:
        #   1. For each fresh detection, find the closest existing EMA track of the
        #      same class (by IoU).  If found → update its position with EMA blend.
        #      If not found → create a new EMA track at the raw position.
        #   2. Any EMA track not refreshed within TRACK_MAX_AGE_S is dropped.
        # Result: boxes slide smoothly to the person's new position instead of
        # teleporting, eliminating the "jumping" visual artefact.
        now_t = time.time()
        if not hasattr(self, '_ema_tracks'):
            self._ema_tracks = {}
            self._ema_next_id = 0

        matched_ids = set()

        for b_xyxy, color_val, conf_val, cls_name in kept_items:
            # ── Match to closest same-class EMA track ────────────────────────
            # Use a combined score: IoU for close boxes + centre-distance fallback
            # for boxes that moved significantly (e.g. bending worker).
            bx1, by1, bx2, by2 = b_xyxy
            b_cx = (bx1 + bx2) / 2.0
            b_cy = (by1 + by2) / 2.0
            b_w  = max(1.0, bx2 - bx1)
            b_h  = max(1.0, by2 - by1)

            best_id, best_score = None, 0.0
            for tid, trk in self._ema_tracks.items():
                if tid in matched_ids:
                    continue  # already claimed by an earlier detection this cycle
                if not match_class(trk['cls'], cls_name):
                    continue
                iou_v, io_min_v = DetectorWorker._box_iou_and_io_min(trk['box'], b_xyxy)

                # Centre-distance score: 1.0 when perfectly aligned, 0.0 when > 1 box-width away
                tx1, ty1, tx2, ty2 = trk['box']
                t_cx = (tx1 + tx2) / 2.0
                t_cy = (ty1 + ty2) / 2.0
                dist_x = abs(b_cx - t_cx) / max(b_w, (tx2 - tx1), 1.0)
                dist_y = abs(b_cy - t_cy) / max(b_h, (ty2 - ty1), 1.0)
                dist_score = max(0.0, 1.0 - (dist_x**2 + dist_y**2) ** 0.5)

                # Combined: prioritise IoU, use distance as tiebreaker / fallback
                score = max(iou_v * 1.2, io_min_v * 0.8, dist_score * 0.5)

                if score > best_score and score >= 0.10:
                    best_score = score
                    best_id    = tid

            if best_id is not None:
                # EMA-blend existing track toward new detection
                old_b = self._ema_tracks[best_id]['box']
                a = BOX_EMA_ALPHA
                smoothed = [
                    old_b[0] * a + b_xyxy[0] * (1 - a),
                    old_b[1] * a + b_xyxy[1] * (1 - a),
                    old_b[2] * a + b_xyxy[2] * (1 - a),
                    old_b[3] * a + b_xyxy[3] * (1 - a),
                ]
                
                # Update rolling M-of-N voting history (1 = hit)
                hist = self._ema_tracks[best_id].get('history', [])
                hist.append(1)
                if len(hist) > 5:
                    hist = hist[-5:]

                self._ema_tracks[best_id].update({
                    'box':       smoothed,
                    'color':     color_val,
                    'conf':      conf_val,
                    'cls':       cls_name,
                    'last_seen': now_t,
                    'hit_count': self._ema_tracks[best_id].get('hit_count', 0) + 1,
                    'history':   hist,
                })
                matched_ids.add(best_id)
            else:
                # New detection → new EMA track (starts at raw position)
                new_id = self._ema_next_id
                self._ema_next_id += 1
                self._ema_tracks[new_id] = {
                    'box':             list(b_xyxy),
                    'color':           color_val,
                    'conf':            conf_val,
                    'cls':             cls_name,
                    'last_seen':       now_t,
                    'first_seen':      now_t,
                    'hit_count':       1,
                    'history':         [1],
                    'last_alert_time': 0.0,
                }
                matched_ids.add(new_id)

        # Record a 0 (miss) in history for active tracks not detected in this cycle
        for tid, trk in self._ema_tracks.items():
            if tid not in matched_ids:
                hist = trk.get('history', [])
                hist.append(0)
                if len(hist) > 5:
                    hist = hist[-5:]
                trk['history'] = hist

        # Expire stale tracks
        for tid in list(self._ema_tracks.keys()):
            if now_t - self._ema_tracks[tid].get('last_seen', 0.0) > TRACK_MAX_AGE_S:
                del self._ema_tracks[tid]

        # Build display list from live EMA tracks
        display_boxes = []
        for trk in self._ema_tracks.values():
            age = now_t - trk.get('last_seen', 0.0)
            if age > TRACK_MAX_AGE_S:
                continue
            cur_cls.add(trk['cls'])
            display_boxes.append({
                'box':   trk['box'],
                'label': f"{trk['cls']} {trk['conf']:.2f}",
                'color': trk['color'],
                'cls':   trk['cls'],
                'conf':  trk['conf'],
            })

        with self._box_lock:
            self._tracked_boxes = display_boxes

        if not getattr(self, '_first_box_logged', False) and display_boxes:
            self._first_box_logged = True
            t_start  = getattr(self, '_start_time', None) or now_t
            t_active = getattr(self, '_models_active_time', None) or t_start
            delay_from_start_ms  = int((now_t - t_start)  * 1000)
            delay_from_active_ms = int((now_t - t_active) * 1000)
            detected_labels     = [b['label'] for b in display_boxes]
            all_classes_str     = ", ".join(list(cur_cls)) if cur_cls else "ALL"
            detected_classes_str = ", ".join(detected_labels)
            print(f"\n==================================================================", flush=True)
            print(f"[STREAM-TIMING] Camera {self.cam_id} FIRST BOUNDING BOX DETECTED & RENDERED!", flush=True)
            print(f" -> Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}", flush=True)
            print(f" -> Stream Active Classes   : [{all_classes_str}]", flush=True)
            print(f" -> Stream Detected Classes : [{detected_classes_str}]", flush=True)
            print(f" -> Delay from Click 'Start': {delay_from_start_ms}ms ({delay_from_start_ms/1000.0:.2f}s)", flush=True)
            print(f" -> Delay from Model Active : {delay_from_active_ms}ms ({delay_from_active_ms/1000.0:.2f}s)", flush=True)
            print(f"==================================================================\n", flush=True)

        # ── Snapshot for Alerts ───────────────────────────────────────────────
        frame_snapshot = cv2.resize(f, (self.width, self.height), interpolation=cv2.INTER_LINEAR)
        for t_box in display_boxes:
            try:
                x1, y1, x2, y2 = [int(v) for v in t_box['box']]
                cv2.rectangle(frame_snapshot, (x1, y1), (x2, y2), t_box['color'], 2)
                t_size = cv2.getTextSize(t_box['label'], cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)[0]
                cv2.rectangle(frame_snapshot, (x1, max(0, y1 - t_size[1] - 6)), (x1 + t_size[0] + 6, max(0, y1)), t_box['color'], -1)
                cv2.putText(frame_snapshot, t_box['label'], (x1 + 3, max(t_size[1] + 2, y1 - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
            except:
                pass

        # ── Robust Alert Processing: M-of-N Temporal Voting + Per-Track Cooldown ──
        for tid, trk in list(self._ema_tracks.items()):
            c = trk.get('cls', '')
            if not c:
                continue

            # Non-alert baseline classes (persons/machinery) are not violation alerts
            is_neg, core_type, cleaned_cls = extract_negation_and_core(c)
            if cleaned_cls in ("person", "worker", "human", "man", "woman", "machinery", "vehicle"):
                continue

            # 1. Per-Track Cooldown (30s per unique track instance)
            last_alert_time = trk.get('last_alert_time', 0.0)
            if (now_t - last_alert_time) < 30.0:
                continue

            # 2. Track-Age & M-of-N Voting Gate:
            # Must have at least 2 hits, and >= 2 detections in the last 5 cycles
            votes = sum(trk.get('history', []))
            total_hits = trk.get('hit_count', 0)
            track_age = now_t - trk.get('first_seen', now_t)

            if total_hits >= 2 and (votes >= 2 or track_age >= 1.5):
                trk['last_alert_time'] = now_t
                print(f"[ALERT-VOTING] Triggered verified alert: cam={self.cam_id}, class={c}, track_id={tid}, votes={votes}/5, hits={total_hits}, age={track_age:.1f}s", flush=True)
                self._save_alert(c, frame_snapshot)

    def _save_alert(self, class_name, frame):
        try:
            now_dt = datetime.now()
            ts = now_dt.strftime("%Y%m%d_%H%M%S")
            disp_name = "NO-PPE" if str(class_name).lower() == "none" else str(class_name)
            safe_cls_filename = re.sub(r'[^a-zA-Z0-9_-]', '_', disp_name)
            filename = f"cam{self.cam_id}_{ts}_{safe_cls_filename}.jpg"
            adir = os.path.join(os.path.dirname(self.output_dir), "alerts")
            os.makedirs(adir, exist_ok=True)
            img_saved = cv2.imwrite(os.path.join(adir, filename), frame)
            print(f"[ALERT-IMG] Saved snapshot {filename}, ok={img_saved}", flush=True)

            base_url = get_alerts_base_url()
            if base_url:
                image_path = f"{base_url.rstrip('/')}/hls/alerts/{filename}"
            else:
                image_path = f"/hls/alerts/{filename}"

            type_of_alert_str = f"{disp_name} Detected"

            # 1. Append to alerts.json for immediate UI dashboard update
            alerts_json_file = os.path.join(adir, "alerts.json")
            alert_entry = {
                "id":           int(time.time() * 1000),
                "camera_id":    str(self.cam_id),
                "location":     str(self.location),
                "type_of_alert": type_of_alert_str,
                "image":        image_path,
                "created_at":   now_dt.strftime("%Y-%m-%d %H:%M:%S")
            }
            try:
                data = []
                if os.path.exists(alerts_json_file):
                    with open(alerts_json_file, "r") as jf:
                        try: data = json.load(jf)
                        except: data = []
                if not isinstance(data, list): data = []
                data.insert(0, alert_entry)
                data = data[:100]
                with open(alerts_json_file, "w") as jf:
                    json.dump(data, jf, indent=2)
                print(f"[ALERT-JSON] Appended alert to alerts.json: {type_of_alert_str}", flush=True)
            except Exception as jerr:
                print(f"[ALERT-JSON-ERR] Failed updating alerts.json: {jerr}", flush=True)

            # 2. Store directly into PostgreSQL DB (psycopg2 or psql CLI fallback)
            conn = self._get_db_conn()
            db_stored = False
            if conn:
                try:
                    cur = conn.cursor()
                    ensure_alerts_schema(cur)
                    insert_alert_db(cur, self.cam_id, self.location, type_of_alert_str, image_path, now_dt)
                    conn.commit()
                    cur.close()
                    db_stored = True
                    print(f"[ALERT-DB] Alert stored successfully in DB: cam={self.cam_id}, class={type_of_alert_str}, location={self.location}", flush=True)
                except Exception as dbe:
                    print(f"[ALERT-DB-ERR] DB insert error: {dbe}", flush=True)
                    try: conn.rollback()
                    except: pass
                    try: self._db_conn.close()
                    except: pass
                    self._db_conn = None

            if not db_stored:
                ok = insert_alert_via_psql(self.cam_id, self.location, type_of_alert_str, image_path, now_dt)
                if ok:
                    print(f"[ALERT-DB] Alert stored successfully via psql CLI: cam={self.cam_id}, class={type_of_alert_str}, location={self.location}", flush=True)
        except Exception as e:
            print(f"[ALERT-ERR] Failed to save alert: {e}", flush=True)

    def _get_connecting_frame(self):
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        cv2.putText(frame, "Connecting to Camera...", 
                   (int(self.width*0.2), int(self.height*0.5)), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
        return frame

    def _capture_thread(self, cap, cap_stop_evt):
        consecutive_fails = 0
        while not self._stop_event.is_set() and not cap_stop_evt.is_set():
            try:
                ret, f = cap.read()
                if not ret or f is None:
                    consecutive_fails += 1
                    time.sleep(0.02)
                    if consecutive_fails > 150:
                        print(f"[WARN] Camera {self.cam_id} RTSP stream disconnected, requesting reconnect...", flush=True)
                        break
                    continue
                consecutive_fails = 0
                with self._frame_lock:
                    self._latest_raw_frame = f
                    self._last_frame_time  = time.time()
                    self._cap_ok = True
            except Exception:
                time.sleep(0.02)

    # ──────────────────────────────────────────────────────────────────────────
    # DRAWING HELPER
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _draw_boxes(pf, display_boxes):
        """
        Draw all bounding boxes and labels onto pf (in-place).

        Features:
          • Area-based z-order so small PPE boxes render on top of large person boxes.
          • Thick border (2 px) + black inner glow (1 px outline trick) for visibility.
          • Larger, bolder labels with padded background panels.
          • Vertical label stacking to avoid overlapping label text when two boxes
            share the same top-left corner region.
        """
        if not display_boxes:
            return

        f_h, f_w = pf.shape[:2]

        # Sort: large boxes first (person/body) → drawn below; small boxes last → drawn on top
        sorted_boxes = sorted(
            display_boxes,
            key=lambda b: max(0, b['box'][2] - b['box'][0]) * max(0, b['box'][3] - b['box'][1]),
            reverse=True
        )

        # Track used label regions to stack them vertically
        used_label_slots = []   # list of (lx1, ly1, lx2, ly2)

        def find_free_label_y(lx1, lx2, preferred_y1, preferred_y2, step=2):
            """Shift label upward until it doesn't collide with any occupied slot."""
            ly1, ly2 = preferred_y1, preferred_y2
            h = ly2 - ly1
            for _ in range(60):
                collision = False
                for (ux1, uy1, ux2, uy2) in used_label_slots:
                    # Check horizontal overlap
                    if lx1 < ux2 and lx2 > ux1:
                        # Check vertical overlap
                        if ly1 < uy2 and ly2 > uy1:
                            collision = True
                            break
                if not collision:
                    break
                ly1 = max(0, ly1 - step)
                ly2 = ly1 + h
            return ly1, ly2

        for t_box in sorted_boxes:
            try:
                x1, y1, x2, y2 = [int(v) for v in t_box['box']]
                x1 = max(0, min(f_w - 1, x1))
                y1 = max(0, min(f_h - 1, y1))
                x2 = max(0, min(f_w - 1, x2))
                y2 = max(0, min(f_h - 1, y2))

                label_text = t_box['label']
                color_val  = t_box['color']

                # Draw box: black shadow first (offset 1px) then coloured border
                cv2.rectangle(pf, (x1+1, y1+1), (x2+1, y2+1), (0, 0, 0), 2)      # shadow
                cv2.rectangle(pf, (x1, y1), (x2, y2), color_val, 2)               # main

                # Compute label size
                (tw, th), baseline = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, LABEL_FONT_SCALE, LABEL_THICKNESS)
                pad = LABEL_BOX_PADDING

                # Preferred position: just above the box top edge
                pref_bg_y1 = y1 - th - 2*pad
                pref_bg_y2 = y1
                pref_bg_x1 = x1
                pref_bg_x2 = min(f_w - 1, x1 + tw + 2*pad)

                # If the label would go above frame top, place it inside the box instead
                if pref_bg_y1 < 0:
                    pref_bg_y1 = y1
                    pref_bg_y2 = y1 + th + 2*pad

                # Resolve vertical collision by stacking
                bg_y1, bg_y2 = find_free_label_y(pref_bg_x1, pref_bg_x2, pref_bg_y1, pref_bg_y2)
                bg_x1, bg_x2 = pref_bg_x1, pref_bg_x2
                text_y = bg_y1 + th + pad - 1

                # Label background panel
                cv2.rectangle(pf, (bg_x1, bg_y1), (bg_x2, bg_y2), color_val, -1)
                # Black text for readability
                cv2.putText(pf, label_text, (bg_x1 + pad, text_y),
                            cv2.FONT_HERSHEY_SIMPLEX, LABEL_FONT_SCALE,
                            (0, 0, 0), LABEL_THICKNESS + 1, cv2.LINE_AA)
                cv2.putText(pf, label_text, (bg_x1 + pad, text_y),
                            cv2.FONT_HERSHEY_SIMPLEX, LABEL_FONT_SCALE,
                            (255, 255, 255), LABEL_THICKNESS, cv2.LINE_AA)

                used_label_slots.append((bg_x1, bg_y1, bg_x2, bg_y2))
            except Exception:
                pass

    def run(self):
        ffmpeg, cap = None, None
        cap_stop_evt = threading.Event()
        cap_t = None

        def cleanup_subthreads():
            nonlocal cap, ffmpeg, cap_t
            cap_stop_evt.set()
            if cap_t and cap_t.is_alive():
                cap_t.join(timeout=1.0)
                cap_t = None
            if cap:
                try: cap.release()
                except Exception: pass
                cap = None
            if ffmpeg:
                try: ffmpeg.stdin.close()
                except Exception: pass
                try: ffmpeg.kill(); ffmpeg.wait(timeout=1.0)
                except Exception: pass
                ffmpeg = None

        try:
            GLOBAL_INFERENCE_SCHEDULER.register_worker(self)

            while not self._stop_event.is_set():
                cleanup_subthreads()
                cap_stop_evt.clear()

                if self._stop_event.is_set():
                    break

                try:
                    print(f"[WORKER-TIMER] Camera {self.cam_id} creating FFmpeg process...", flush=True)
                    t_ff_start = time.time()
                    ffmpeg = self._create_ffmpeg()
                    print(f"[WORKER-TIMER] Camera {self.cam_id} FFmpeg process created in {int((time.time() - t_ff_start)*1000)}ms", flush=True)

                    print(f"[WORKER-TIMER] Camera {self.cam_id} connecting to RTSP at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...", flush=True)
                    t_conn_start = time.time()
                    cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)

                    retry_count = 0
                    while not cap.isOpened() and retry_count < 10 and not self._stop_event.is_set():
                        time.sleep(0.5)
                        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                        retry_count += 1

                    if self._stop_event.is_set():
                        break

                    print(f"[WORKER-TIMER] Camera {self.cam_id} RTSP connected in {int((time.time() - t_conn_start)*1000)}ms at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

                    cap_t = threading.Thread(target=self._capture_thread, args=(cap, cap_stop_evt), daemon=True)
                    cap_t.start()

                    f_int          = 1.0 / self.fps
                    next_frame_time = time.time()

                    while not self._stop_event.is_set():
                        now = time.time()
                        if now < next_frame_time:
                            time.sleep(max(0.001, next_frame_time - now))
                            continue
                        next_frame_time += f_int
                        if now - next_frame_time > 0.3:
                            next_frame_time = now + f_int

                        if time.time() - getattr(self, '_last_frame_time', now) > 15.0 and self._latest_raw_frame is not None:
                            print(f"[WARN] Camera {self.cam_id} frame timeout (>15s), reconnecting...", flush=True)
                            break

                        with self._frame_lock:
                            raw_frame = self._latest_raw_frame

                        if raw_frame is None:
                            pf = self._get_connecting_frame()
                        else:
                            pf = cv2.resize(raw_frame, (self.width, self.height), interpolation=cv2.INTER_LINEAR)
                        f_h, f_w = pf.shape[:2]

                        # Draw ROI boundary if configured
                        if self.roi_polygon and len(self.roi_polygon) == 2:
                            try:
                                min_x = min(self.roi_polygon[0][0], self.roi_polygon[1][0])
                                max_x = max(self.roi_polygon[0][0], self.roi_polygon[1][0])
                                min_y = min(self.roi_polygon[0][1], self.roi_polygon[1][1])
                                max_y = max(self.roi_polygon[0][1], self.roi_polygon[1][1])
                                rx1, ry1 = int(min_x * f_w), int(min_y * f_h)
                                rx2, ry2 = int(max_x * f_w), int(max_y * f_h)
                                cv2.rectangle(pf, (rx1, ry1), (rx2, ry2), (0, 255, 0), 2)
                            except: pass

                        # Draw latest tracked bounding boxes from Central Inference Scheduler
                        with self._box_lock:
                            display_boxes = list(self._tracked_boxes)

                        DetectorWorker._draw_boxes(pf, display_boxes)

                        if ffmpeg.poll() is not None:
                            break

                        try:
                            ffmpeg.stdin.write(pf.tobytes())
                            ffmpeg.stdin.flush()
                        except:
                            break
                except:
                    import traceback
                    traceback.print_exc()
        finally:
            cleanup_subthreads()
            GLOBAL_INFERENCE_SCHEDULER.unregister_worker(self)
