import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["TORCH_NUM_THREADS"] = "1"
os.environ["OPENCV_FOR_THREADS_NUM"] = "1"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|sync;ext|max_delay;500000|timeout;5000000"

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
    pass  # If dotenv not installed, just use system env vars

from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Optimize PyTorch CPU threading to 2 threads (balanced with centralized InferenceScheduler)
try:
    import torch
    torch.set_num_threads(2)
    if hasattr(torch, "set_num_interop_threads"):
        torch.set_num_interop_threads(1)
except Exception:
    pass

import re

from alert_store import DB_DSN, ensure_alerts_schema, insert_alert_db

YOLO_CACHE = {}

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
        
    if b_core == e_core:
        return True
        
    if (b_core in e_core or e_core in b_core) and len(b_core) >= 3 and len(e_core) >= 3:
        return True
        
    return False

INFERENCE_LOCK = threading.Lock()

def get_yolo_model(model_path):
    if model_path not in YOLO_CACHE:
        with INFERENCE_LOCK:
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

def is_opposite_class(cls1, cls2):
    try:
        b_neg, b_core, _ = extract_negation_and_core(cls1)
        e_neg, e_core, _ = extract_negation_and_core(cls2)
        if b_neg != e_neg and b_core == e_core and len(b_core) >= 3:
            return True
    except Exception:
        pass
    return False

def get_dynamic_class_color(class_name):
    """
    Fully dynamic, zero-hardcoded color assignment that works for ANY current and future models.
    - Violations (NO-*, without-*, non-*, fire, danger) -> Alert Red / Orange / Magenta (BGR).
    - Compliant gear & Objects -> High-contrast Safe Palette (Lime Green, Cyan Sky-Blue, Yellow, Teal, Violet).
    """
    if not class_name:
        return (0, 255, 255)
        
    is_neg, core, cleaned = extract_negation_and_core(class_name)
    is_hazard = is_neg or any(w in cleaned for w in ("fire", "smoke", "fall", "danger", "hazard", "violation", "unauthorized"))
    
    import hashlib
    if is_hazard:
        hazard_palette = [
            (0, 0, 255),      # Bright Alert Red (BGR)
            (0, 90, 255),     # Vivid Amber Orange (BGR)
            (255, 0, 255),    # Electric Magenta / Fuchsia (BGR)
            (180, 50, 255),   # Hot Crimson Pink (BGR)
            (0, 140, 255),    # Deep Tangerine (BGR)
            (220, 20, 180),   # Deep Purple Violet (BGR)
        ]
        core_h = int(hashlib.md5(core.encode('utf-8')).hexdigest(), 16)
        return hazard_palette[core_h % len(hazard_palette)]
    else:
        safe_palette = [
            (255, 215, 0),    # Bright Cyan Sky Blue (BGR)
            (0, 230, 80),     # Emerald Lime Green (BGR)
            (0, 230, 255),    # Golden Lemon Yellow (BGR)
            (180, 230, 50),   # Mint Teal (BGR)
            (255, 120, 180),  # Soft Lavender Violet (BGR)
            (50, 205, 50),    # Spring Green (BGR)
            (200, 100, 50),   # Slate Blue (BGR)
            (255, 140, 0),    # Deep Cobalt Blue (BGR)
        ]
        core_h = int(hashlib.md5(core.encode('utf-8')).hexdigest(), 16)
        return safe_palette[core_h % len(safe_palette)]

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
    eliminating lock contention and thread starvation.
    """
    def __init__(self):
        self._workers = {}
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()

    def register_worker(self, worker):
        with self._lock:
            self._workers[worker.cam_id] = worker
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
                active_workers = list(self._workers.values())

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
                except Exception as e:
                    print(f"[SCHEDULER-ERR] Camera {worker.cam_id} inference error: {e}", flush=True)

                # Ultra-fast pacing pause (5ms) between camera turns
                time.sleep(0.005)

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
                    self._latest_boxes = []
        print(f"[WORKER-ROI-UPDATE] Camera {getattr(self, 'cam_id', '?')} model_configs updated, roi_polygon={self.roi_polygon}", flush=True)

    def __init__(self, rtsp_url, output_dir, model_paths, conf=0.20, iou=0.45, location="Camera", model_configs=None):
        self.roi_polygon = None
        self.rtsp_url, self.output_dir, self.model_paths, self.conf, self.iou, self.location = rtsp_url, output_dir, model_paths, conf, iou, location
        self.model_configs = model_configs or {}
        self.fps, self.width, self.height = 12.0, 854, 480
        self._latest_raw_frame = None
        self._latest_boxes = []
        self._latest_box_time = 0.0
        self._tracked_boxes = []
        self._prev_inference_boxes = []
        self._tracked_objects = []
        self._next_track_id_counter = 1
        self._frame_lock, self._box_lock = threading.Lock(), threading.Lock()
        self._stop_event = threading.Event()
        self._frame_queue, self._result_queue = queue.Queue(maxsize=1), queue.Queue(maxsize=1)
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
        print(f"[TIMER-START] Camera {self.cam_id} Start request initialized at {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}", flush=True)

    def _get_next_track_id(self):
        tid = getattr(self, '_next_track_id_counter', 1)
        self._next_track_id_counter = (tid % 9999) + 1
        return tid

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
                self._latest_boxes = []
                self._tracked_boxes = []
                self._tracked_objects = []
        print(f"[WORKER-DYNAMIC-UPDATE] Camera {getattr(self, 'cam_id', '?')} dynamically updated models to {self.model_paths} in 0ms without restarting RTSP or FFmpeg", flush=True)

    def stop(self):
        self._stop_event.set()
        GLOBAL_INFERENCE_SCHEDULER.unregister_worker(self)

    def _get_db_conn(self):
        if not PSYCOPG2_AVAILABLE: return None
        if self._db_conn is None or self._db_conn.closed:
            try:
                self._db_conn = psycopg2.connect(DB_DSN, connect_timeout=5)
            except: self._db_conn = None
        return self._db_conn

    def _create_ffmpeg(self):
        os.makedirs(self.output_dir, exist_ok=True)
        # Clean stale HLS segments & playlist from previous session so player never loops old streams
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
            "-f", "hls", "-hls_time", "1", "-hls_list_size", "3",
            "-hls_flags", "delete_segments+independent_segments+discont_start+omit_endlist+temp_file", 
            "-hls_segment_filename", os.path.join(self.output_dir, f"segment_{session_id}_%d.ts"), 
            os.path.join(self.output_dir, "playlist.m3u8")
        ]
        log = open(os.path.join(self.output_dir, "ffmpeg.log"), "a")
        print(f"[LOG] Camera {self.cam_id} detector stream started with resolution: {self.width}x{self.height}, FPS: {self.fps}, Bitrate: 350k (max 450k)", flush=True)
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log, stdout=subprocess.DEVNULL, bufsize=10*1024*1024)

    def _letterbox(self, f):
        h, w = f.shape[:2]
        s = min(self.width/w, self.height/h)
        nw, nh = int(w*s), int(h*s)
        res = cv2.resize(f, (nw, nh))
        return cv2.copyMakeBorder(res, (self.height-nh)//2, (self.height-nh+1)//2, (self.width-nw)//2, (self.width-nw+1)//2, cv2.BORDER_CONSTANT, value=[0,0,0])

    def _is_valid_box(self, conf_val, m_conf, bw, bh, box_area, f_w, f_h, f_area):
        if conf_val < m_conf:
            return False
        # Absolute Minimum Size Bounds (Rejects single-pixel noise only)
        if bw < 3 or bh < 3 or box_area < 10:
            return False
        # Maximum Size Bounds (Rejects 85% full-screen hallucinations)
        if box_area > 0.85 * f_area or bh > 0.95 * f_h or bw > 0.95 * f_w:
            return False
        # Aspect Ratio Filter: Rejects thin vertical stripes (> 4.5) & flat horizontal stripes (< 0.12)
        aspect = bh / max(1.0, bw)
        if aspect > 4.5 or aspect < 0.12:
            return False
        return True

    def run_single_inference_cycle(self):
        """
        Executed by the Centralized InferenceScheduler.
        1. Captures the latest live frame ONCE (Same-Frame Multi-Model Snapshot).
        2. Evaluates all assigned models on that identical frame snapshot.
        3. Fuses detections with per-model filtering and validation.
        4. Updates the Time-Aware Multi-Object Tracker.
        """
        if self._stop_event.is_set():
            return

        with self._frame_lock:
            f = self._latest_raw_frame

        if f is None:
            return

        # Capture original raw camera frame for maximum feature extraction & long-range detection
        orig_h, orig_w = f.shape[:2]
        scale_x = float(self.width) / max(1.0, float(orig_w))
        scale_y = float(self.height) / max(1.0, float(orig_h))

        frame_snapshot = cv2.resize(f, (self.width, self.height), interpolation=cv2.INTER_LINEAR)
        f_h, f_w = self.height, self.width

        cur_cls, now = set(), time.time()
        raw_boxes = []

        if not self.models:
            paths = self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]
            self.models = [get_yolo_model(mp) for mp in paths]
            self._models_active_time = time.time()

        for midx, model in enumerate(self.models):
            m_path = self.model_paths[midx] if (isinstance(self.model_paths, list) and midx < len(self.model_paths)) else str(self.model_paths)
            m_name = os.path.basename(m_path)

            m_conf = self.conf
            m_iou = self.iou
            enabled_classes = None
            default_imgsz = int(os.getenv("DEFAULT_IMGSZ", "640"))
            m_imgsz = default_imgsz
            cfg = get_config_for_model(self.model_configs, m_name)
            if cfg and isinstance(cfg, dict):
                m_conf = float(cfg.get("conf", self.conf))
                m_iou = float(cfg.get("iou", self.iou))
                enabled_classes = cfg.get("enabled_classes")
                m_imgsz = int(cfg.get("imgsz", default_imgsz))

            if m_imgsz % 32 != 0:
                m_imgsz = int(math.ceil(m_imgsz / 32.0) * 32)

            filter_classes = enabled_classes if (enabled_classes is not None) else []
            effective_conf = float(m_conf) if (m_conf is not None) else float(self.conf)

            class_configs = cfg.get("class_configs", {}) if isinstance(cfg, dict) else {}
            min_class_conf = effective_conf
            if class_configs and isinstance(class_configs, dict):
                for cc in class_configs.values():
                    if isinstance(cc, dict) and "conf" in cc:
                        min_class_conf = min(min_class_conf, float(cc["conf"]))

            # Direct Native Prediction on full-res frame (Identical to Video Lab)
            predict_kwargs = {
                "source": f,
                "conf": min_class_conf,
                "iou": m_iou,
                "imgsz": m_imgsz,
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

                mod_dets = []
                for r in results:
                    if r.boxes:
                        for b in r.boxes:
                            cls_id = int(b.cls[0].item())
                            cls_name = r.names.get(cls_id, str(cls_id)) if hasattr(r, 'names') else str(cls_id)
                            conf_val = float(b.conf[0].item())
                            
                            if not filter_classes or any(match_class(cls_name, e) for e in filter_classes):
                                mod_dets.append(f"{cls_name} {conf_val:.2f}")

                print(f"[TIMER-INFERENCE] Camera {self.cam_id} model {m_name} (imgsz={m_imgsz}) -> Pure: {infer_ms}ms | Selected: {filter_classes if filter_classes else 'ALL'} | Detected: {mod_dets if mod_dets else 'None'} ({datetime.now().strftime('%H:%M:%S.%f')[:-3]})", flush=True)
            except Exception as pred_err:
                print(f"[PREDICT-ERR] Camera {self.cam_id} model {m_name}: {pred_err}", flush=True)
                continue

            for r in results:
                if r.boxes:
                    for b in r.boxes:
                        cls_id = int(b.cls[0].item())
                        cls = r.names.get(cls_id, str(cls_id)) if hasattr(r, 'names') else str(cls_id)
                        conf_val = float(b.conf[0].item())

                        if filter_classes:
                            if not any(match_class(cls, e) for e in filter_classes):
                                continue

                        # Per-class confidence filter
                        req_conf = effective_conf
                        if class_configs:
                            for cc_name, cc_val in class_configs.items():
                                if match_class(cls, cc_name) and isinstance(cc_val, dict) and "conf" in cc_val:
                                    req_conf = float(cc_val["conf"])
                                    break
                        if conf_val < req_conf:
                            continue

                        # Scale coordinates from raw frame to output stream resolution
                        box_raw = b.xyxy[0].cpu().numpy().tolist()
                        rx1, ry1, rx2, ry2 = box_raw
                        x1 = max(0, min(self.width - 1, rx1 * scale_x))
                        y1 = max(0, min(self.height - 1, ry1 * scale_y))
                        x2 = max(0, min(self.width - 1, rx2 * scale_x))
                        y2 = max(0, min(self.height - 1, ry2 * scale_y))
                        box_xyxy = [x1, y1, x2, y2]
                        
                        bw = max(0, x2 - x1)
                        bh = max(0, y2 - y1)
                        if bw < 3 or bh < 3:
                            continue
                            
                        cx = (x1 + x2) / 2.0
                        cy = (y1 + y2) / 2.0

                        # Apply ROI rectangle filter if configured
                        if self.roi_polygon and len(self.roi_polygon) == 2:
                            try:
                                roi_x1 = int(min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                                roi_y1 = int(min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                                roi_x2 = int(max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                                roi_y2 = int(max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                                if not (roi_x1 <= cx <= roi_x2 and roi_y1 <= cy <= roi_y2):
                                    continue
                            except Exception:
                                pass

                        color_val = get_dynamic_class_color(cls)
                        raw_boxes.append((box_xyxy, color_val, conf_val, cls))

        # Multi-Model NMS
        kept_items = []
        if raw_boxes:
            raw_boxes.sort(key=lambda x: x[2], reverse=True)
            for item in raw_boxes:
                b1_xyxy, c1_color, conf1_val, cls1_name = item
                x1_1, y1_1, x2_1, y2_1 = b1_xyxy
                area1 = max(0, x2_1 - x1_1) * max(0, y2_1 - y1_1)

                suppress = False
                for k_item in kept_items:
                    b2_xyxy, c2_color, conf2_val, cls2_name = k_item
                    x1_2, y1_2, x2_2, y2_2 = b2_xyxy
                    area2 = max(0, x2_2 - x1_2) * max(0, y2_2 - y1_2)

                    ix1 = max(x1_1, x1_2)
                    iy1 = max(y1_1, y1_2)
                    ix2 = min(x2_1, x2_2)
                    iy2 = min(y2_1, y2_2)

                    if ix2 > ix1 and iy2 > iy1:
                        inter = (ix2 - ix1) * (iy2 - iy1)
                        union = area1 + area2 - inter
                        iou = inter / max(1.0, union)
                        min_area = max(1.0, min(area1, area2))
                        io_min = inter / min_area

                        # Suppress duplicate detections of the same class or conflicting positive/negative opposites on the same object
                        is_same_cls = match_class(cls1_name, cls2_name)
                        is_opp_cls = is_opposite_class(cls1_name, cls2_name)
                        if (is_same_cls and (iou >= 0.35 or io_min >= 0.50)) or (is_opp_cls and (iou >= 0.40 or io_min >= 0.55)):
                            suppress = True
                            break

                if not suppress:
                    kept_items.append(item)

        # Smooth Sub-Frame Motion Vector Extraction
        render_boxes = []
        new_prev_boxes = []
        now_cycle = time.time()
        
        for b_xyxy, color_val, conf_val, cls_name in kept_items:
            cx = (b_xyxy[0] + b_xyxy[2]) / 2.0
            cy = (b_xyxy[1] + b_xyxy[3]) / 2.0
            
            vx, vy = 0.0, 0.0
            best_prev = None
            min_dist = 90.0  # Search radius in pixels for moving person match
            
            for prev in getattr(self, '_prev_inference_boxes', []):
                if match_class(cls_name, prev['cls']):
                    d = math.hypot(cx - prev['cx'], cy - prev['cy'])
                    if d < min_dist:
                        min_dist = d
                        best_prev = prev
                        
            if best_prev is not None:
                dt = max(0.02, now_cycle - best_prev['t'])
                raw_vx = (cx - best_prev['cx']) / dt
                raw_vy = (cy - best_prev['cy']) / dt
                raw_vx = max(-300.0, min(300.0, raw_vx))
                raw_vy = max(-300.0, min(300.0, raw_vy))
                # Exponential moving average filter
                vx = 0.5 * raw_vx + 0.5 * best_prev.get('vx', 0.0)
                vy = 0.5 * raw_vy + 0.5 * best_prev.get('vy', 0.0)
                
            new_prev_boxes.append({
                'cls': cls_name,
                'cx': cx,
                'cy': cy,
                't': now_cycle,
                'vx': vx,
                'vy': vy
            })
            
            render_boxes.append({
                'box': b_xyxy,
                'label': f"{cls_name} {conf_val:.2f}",
                'color': color_val,
                'cls': cls_name,
                'conf': conf_val,
                't': now_cycle,
                'vx': vx,
                'vy': vy
            })
            cur_cls.add(cls_name)
            
        self._prev_inference_boxes = new_prev_boxes

        # Direct Frame Box Rendering (Identical to Video Lab video_tester.py)
        # Eliminates class switching, ghost boxes, and tracker latency completely
        display_boxes = render_boxes

        with self._box_lock:
            self._tracked_boxes = display_boxes

        # Precision calculation and print for first detection box appear time
        if not getattr(self, '_first_box_logged', False) and display_boxes:
            self._first_box_logged = True
            now_t = time.time()
            t_start = getattr(self, '_start_time', None) or now_t
            t_active = getattr(self, '_models_active_time', None) or t_start
            delay_from_start_ms = int((now_t - t_start) * 1000)
            delay_from_active_ms = int((now_t - t_active) * 1000)
            detected_labels = [b['label'] for b in display_boxes]
            all_classes_str = ", ".join(list(cur_cls)) if cur_cls else "ALL"
            detected_classes_str = ", ".join(detected_labels)
            print(f"\n==================================================================", flush=True)
            print(f"[STREAM-TIMING] Camera {self.cam_id} FIRST BOUNDING BOX DETECTED & RENDERED!", flush=True)
            print(f" -> Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}", flush=True)
            print(f" -> Stream Active Classes   : [{all_classes_str}]", flush=True)
            print(f" -> Stream Detected Classes : [{detected_classes_str}]", flush=True)
            print(f" -> Delay from Click 'Start': {delay_from_start_ms}ms ({delay_from_start_ms/1000.0:.2f}s)", flush=True)
            print(f" -> Delay from Model Active : {delay_from_active_ms}ms ({delay_from_active_ms/1000.0:.2f}s)", flush=True)
            print(f"==================================================================\n", flush=True)

        # Snapshot for Alerts
        snap_img = frame_snapshot.copy()
        for t_box in display_boxes:
            try:
                x1, y1, x2, y2 = [int(v) for v in t_box['box']]
                cv2.rectangle(snap_img, (x1, y1), (x2, y2), t_box['color'], 2)
                t_size = cv2.getTextSize(t_box['label'], cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)[0]
                cv2.rectangle(snap_img, (x1, max(0, y1 - t_size[1] - 6)), (x1 + t_size[0] + 6, max(0, y1)), t_box['color'], -1)
                cv2.putText(snap_img, t_box['label'], (x1 + 3, max(t_size[1] + 2, y1 - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
            except:
                pass

        # Persistent Alert Processing: 3.0s continuous trigger + 30.0s repeat interval
        for c in cur_cls:
            if c not in self.alert_timers:
                self.alert_timers[c] = {'start': now, 'last_seen': now, 'last_alert': 0.0}
            else:
                self.alert_timers[c]['last_seen'] = now

            duration = now - self.alert_timers[c]['start']
            last_alert_time = self.alert_timers[c].get('last_alert', 0.0)

            if duration >= 3.0:
                if last_alert_time == 0.0 or (now - last_alert_time) >= 30.0:
                    self.alert_timers[c]['last_alert'] = now
                    self.alert_triggered.add(c)
                    print(f"[ALERT] Triggering alert: cam={self.cam_id}, class={c}, duration={duration:.1f}s, is_repeat={(last_alert_time > 0)}", flush=True)
                    self._save_alert(c, snap_img)

        # Cleanup expired alert classes (absent for > 2.0s)
        for c in list(self.alert_timers.keys()):
            if now - self.alert_timers[c]['last_seen'] > 2.0:
                del self.alert_timers[c]
                if c in self.alert_triggered:
                    self.alert_triggered.remove(c)

    def _save_alert(self, class_name, frame):
        try:
            now_dt = datetime.now()
            ts = now_dt.strftime("%Y%m%d_%H%M%S")
            filename = f"cam{self.cam_id}_{ts}_{class_name}.jpg"
            adir = os.path.join(os.path.dirname(self.output_dir), "alerts")
            os.makedirs(adir, exist_ok=True)
            img_saved = cv2.imwrite(os.path.join(adir, filename), frame)
            print(f"[ALERT-IMG] Saved snapshot {filename}, ok={img_saved}", flush=True)

            base_url = get_alerts_base_url()
            if base_url:
                image_path = f"{base_url.rstrip('/')}/hls/alerts/{filename}"
            else:
                image_path = f"/hls/alerts/{filename}"

            conn = self._get_db_conn()
            if conn:
                try:
                    cur = conn.cursor()
                    ensure_alerts_schema(cur)
                    insert_alert_db(cur, self.cam_id, self.location, f"{class_name} Detected", image_path, now_dt)
                    conn.commit()
                    cur.close()
                    print(f"[ALERT-DB] Alert stored successfully in DB: cam={self.cam_id}, class={class_name}, location={self.location}", flush=True)
                except Exception as dbe:
                    print(f"[ALERT-DB-ERR] DB insert error: {dbe}", flush=True)
            else:
                print(f"[ALERT-DB-WARN] No DB connection available (PSYCOPG2={PSYCOPG2_AVAILABLE})", flush=True)
        except Exception as e:
            print(f"[ALERT-ERR] Failed to save alert: {e}", flush=True)

    def _get_connecting_frame(self):
        import numpy as np
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        cv2.putText(frame, "Connecting to Camera...", 
                   (int(self.width*0.2), int(self.height*0.5)), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
        return frame

    def _capture_thread(self, cap, cap_stop_evt):
        while not self._stop_event.is_set() and not cap_stop_evt.is_set():
            try:
                ret, f = cap.read()
                if not ret or f is None:
                    time.sleep(0.005)
                    continue
                with self._frame_lock:
                    self._latest_raw_frame = f
                    self._cap_ok = True
                    self._last_frame_time = time.time()
            except Exception:
                break

    def _infer_loop(self, infer_stop_evt):
        while not self._stop_event.is_set() and not infer_stop_evt.is_set():
            try:
                if self._latest_raw_frame is not None:
                    self.run_single_inference_cycle()
                else:
                    time.sleep(0.01)
            except Exception as e:
                print(f"[INFER-ERR] Camera {self.cam_id}: {e}", flush=True)
            time.sleep(0.005)

    def run(self):
        ffmpeg, cap, cap_t = None, None, None
        cap_stop_evt = None
        infer_t, infer_stop_evt = None, None

        def cleanup_subthreads():
            nonlocal cap, cap_t, cap_stop_evt, infer_t, infer_stop_evt, ffmpeg
            if infer_stop_evt: infer_stop_evt.set()
            if infer_t and infer_t.is_alive(): infer_t.join(timeout=1.0)
            if cap_stop_evt: cap_stop_evt.set()
            if cap_t and cap_t.is_alive(): cap_t.join(timeout=1.0)
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
            if self.models is None:
                paths = self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]
                t_load_start = time.time()
                self.models = [get_yolo_model(mp) for mp in paths]
                load_ms = int((time.time() - t_load_start) * 1000)
                from_start_ms = int((time.time() - getattr(self, '_start_time', time.time())) * 1000)
                print(f"[TIMER-MODELS-ACTIVE] Camera {self.cam_id} models loaded & ACTIVE at {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} (Load time: {load_ms}ms, Elapsed from Start: {from_start_ms}ms)", flush=True)

            while not self._stop_event.is_set():
                cleanup_subthreads()
                
                if self._stop_event.is_set():
                    break

                self._latest_raw_frame = None
                self._cap_ok = True

                try:
                    print(f"[WORKER-TIMER] Camera {self.cam_id} creating FFmpeg process...", flush=True)
                    t_ff_start = time.time()
                    ffmpeg = self._create_ffmpeg()
                    print(f"[WORKER-TIMER] Camera {self.cam_id} FFmpeg process created in {int((time.time() - t_ff_start)*1000)}ms", flush=True)

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
                        cap_stop_evt = threading.Event()
                        cap_t = threading.Thread(target=self._capture_thread, args=(cap, cap_stop_evt), daemon=True)
                        cap_t.start()

                    # Wait for first real raw frame from camera
                    t_frame_start = time.time()
                    while time.time() - t_frame_start < 5.0 and self._latest_raw_frame is None and not self._stop_event.is_set():
                        time.sleep(0.05)
                    
                    if self._stop_event.is_set():
                        break
                    
                    print(f"[WORKER-TIMER] Camera {self.cam_id} first raw frame received in {int((time.time() - t_frame_start)*1000)}ms at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
                    
                    infer_stop_evt = threading.Event()
                    infer_t = threading.Thread(target=self._infer_loop, args=(infer_stop_evt,), daemon=True, name=f"InferWorker-{self.cam_id}")
                    infer_t.start()
                    
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
                        
                        pf = cv2.resize(f, (self.width, self.height), interpolation=cv2.INTER_LINEAR)

                        # Draw ROI boundary if active
                        if self.roi_polygon and len(self.roi_polygon) == 2:
                            try:
                                fh, fw = pf.shape[:2]
                                min_x = min(self.roi_polygon[0][0], self.roi_polygon[1][0])
                                max_x = max(self.roi_polygon[0][0], self.roi_polygon[1][0])
                                min_y = min(self.roi_polygon[0][1], self.roi_polygon[1][1])
                                max_y = max(self.roi_polygon[0][1], self.roi_polygon[1][1])
                                rx1, ry1 = int(min_x * fw), int(min_y * fh)
                                rx2, ry2 = int(max_x * fw), int(max_y * fh)
                                cv2.rectangle(pf, (rx1, ry1), (rx2, ry2), (0, 255, 0), 2)
                            except: pass

                        # Overlay latest active tracked boxes with sub-frame motion interpolation onto live frame
                        with self._box_lock:
                            cur_tracked = list(getattr(self, '_tracked_boxes', []))
                        
                        pf_h, pf_w = pf.shape[:2]
                        now_render = time.time()
                        for t_box in cur_tracked:
                            try:
                                b_xyxy = t_box['box']
                                label_text = t_box['label']
                                color_val = t_box['color']
                                t_infer = t_box.get('t', now_render)
                                vx = t_box.get('vx', 0.0)
                                vy = t_box.get('vy', 0.0)
                                
                                # Sub-frame motion extrapolation: glides box smoothly between inference intervals
                                dt = min(0.35, max(0.0, now_render - t_infer))
                                dx = max(-25.0, min(25.0, vx * dt))
                                dy = max(-25.0, min(25.0, vy * dt))
                                
                                x1 = max(0, min(pf_w - 1, int(b_xyxy[0] + dx)))
                                y1 = max(0, min(pf_h - 1, int(b_xyxy[1] + dy)))
                                x2 = max(0, min(pf_w - 1, int(b_xyxy[2] + dx)))
                                y2 = max(0, min(pf_h - 1, int(b_xyxy[3] + dy)))
                                
                                cv2.rectangle(pf, (x1, y1), (x2, y2), color_val, 2)
                                (tw, th), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
                                
                                if y1 - th - 6 > 0:
                                    bg_y1 = y1 - th - 6
                                    bg_y2 = y1
                                    text_y = y1 - 4
                                else:
                                    bg_y1 = y1
                                    bg_y2 = min(pf_h - 1, y1 + th + 6)
                                    text_y = y1 + th + 2
                                    
                                bg_x2 = min(pf_w - 1, x1 + tw + 6)
                                cv2.rectangle(pf, (x1, bg_y1), (bg_x2, bg_y2), (18, 20, 24), -1)
                                cv2.rectangle(pf, (x1, bg_y1), (bg_x2, bg_y2), color_val, 1)
                                cv2.putText(pf, label_text, (x1 + 3, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)
                            except:
                                pass

                        if ffmpeg.poll() is not None: break
                        try:
                            ffmpeg.stdin.write(pf.tobytes())
                            ffmpeg.stdin.flush()
                        except: break

                except Exception:
                    import traceback
                    traceback.print_exc()
        finally:
            cleanup_subthreads()
