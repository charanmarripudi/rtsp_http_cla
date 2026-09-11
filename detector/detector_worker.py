import os
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|sync;ext|max_delay;500000|timeout;5000000"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["TORCH_NUM_THREADS"] = "1"

import cv2, subprocess, time, threading, queue, json
try:
    cv2.setNumThreads(1)
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

# Optimize PyTorch CPU threading to prevent CPU starvation on Raspberry Pi
try:
    import torch
    torch.set_num_threads(1)
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
    cleaned = clean_str(s)
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
    
    b_clean = clean_str(box_cls)
    e_clean = clean_str(enabled_cls)
    
    if b_clean == e_clean:
        return True
        
    b_neg, b_core, _ = extract_negation_and_core(box_cls)
    e_neg, e_core, _ = extract_negation_and_core(enabled_cls)
    
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
DYNAMIC_CLASS_COLOR_MAP = {
    # Violations - Bright High-Contrast Distinct Colors
    "no-hardhat": (0, 50, 255),          # Bright Coral Red / Orange-Red
    "no-helmet": (0, 50, 255),
    "nohardhat": (0, 50, 255),
    "nohelmet": (0, 50, 255),
    
    "no-safety-vest": (255, 230, 0),     # Electric Neon Cyan / Turquoise
    "no-vest": (255, 230, 0),
    "nosafetyvest": (255, 230, 0),
    "novest": (255, 230, 0),
    "no-saftey-vest": (255, 230, 0),
    
    "no-mask": (255, 0, 255),            # Bright Magenta / Neon Pink
    "nomask": (255, 0, 255),
    
    "no-goggles": (0, 215, 255),         # Vivid Golden Yellow
    "nogoggles": (0, 215, 255),
    "no-glasses": (0, 215, 255),
    
    "no-gloves": (255, 105, 180),        # Light Neon Pink
    "nogloves": (255, 105, 180),
    
    "no-shoes": (0, 140, 255),           # Vivid Tangerine Orange
    "noshoes": (0, 140, 255),
    "no-boots": (0, 140, 255),
    
    # Positive Equipment - Green / Sky Blue / Lime
    "hardhat": (0, 220, 100),            # Emerald Green
    "helmet": (0, 220, 100),
    
    "safety-vest": (255, 140, 0),        # Deep Sky Blue
    "vest": (255, 140, 0),
    "saftey-vest": (255, 140, 0),
    
    "mask": (180, 220, 0),               # Bright Lime Green
    "goggles": (255, 190, 40),           # Electric Cyan
    "gloves": (200, 100, 255),           # Lavender Violet
    "shoes": (50, 205, 50),              # Spring Green
    "boots": (50, 205, 50),
    
    # Objects & People
    "person": (30, 45, 255),             # Coral Red
    "worker": (30, 45, 255),
    "human": (30, 45, 255),
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
        self.fps, self.width, self.height = 5.0, 1280, 720
        self._latest_raw_frame = None
        self._latest_boxes = []
        self._latest_box_time = 0.0
        self._tracked_boxes = []
        self._frame_lock, self._box_lock = threading.Lock(), threading.Lock()
        self._stop_event = threading.Event()
        self._frame_queue, self._result_queue = queue.Queue(maxsize=1), queue.Queue(maxsize=1)
        self._last_frame_time, self._cap_ok = time.time(), True
        self.alert_timers, self.alert_triggered = {}, set()
        self.cam_id = os.path.basename(output_dir).replace("stream", "").replace("_detected", "")
        self.model_paths = model_paths
        self.models = None
        self._db_conn = None

    def update_models(self, model_paths, model_configs=None, conf=None, iou=None, location=None):
        if model_paths is not None:
            paths = model_paths if isinstance(model_paths, list) else [model_paths]
            self.model_paths = paths
            self.models = [get_yolo_model(mp) for mp in paths]
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
        print(f"[WORKER-DYNAMIC-UPDATE] Camera {getattr(self, 'cam_id', '?')} dynamically updated models to {self.model_paths} in 0ms without restarting RTSP or FFmpeg", flush=True)

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
            "-r", str(self.fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "ultrafast", 
            "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-threads", "2",
            "-profile:v", "baseline", "-level:v", "3.1",
            "-b:v", "500k", "-maxrate", "700k", "-bufsize", "1M",
            "-g", str(max(1, int(self.fps * 2))), 
            "-keyint_min", str(max(1, int(self.fps * 1))), "-sc_threshold", "0",
            "-f", "hls", "-hls_time", "2", "-hls_list_size", "5",
            "-hls_flags", "delete_segments+independent_segments+discont_start+omit_endlist+temp_file", 
            "-hls_segment_filename", os.path.join(self.output_dir, f"segment_{session_id}_%d.ts"), 
            os.path.join(self.output_dir, "playlist.m3u8")
        ]
        log = open(os.path.join(self.output_dir, "ffmpeg.log"), "a")
        print(f"[LOG] Camera {self.cam_id} detector stream started with resolution: {self.width}x{self.height}, FPS: {self.fps}, Bitrate: 500k (max 700k)", flush=True)
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
        # Maximum Size Bounds (Rejects 80% full-screen hallucinations)
        if box_area > 0.85 * f_area or bh > 0.95 * f_h or bw > 0.95 * f_w:
            return False
        return True

    def _run_all_models(self, f):
        try:
            cur_cls, now = set(), time.time()
            raw_boxes = []
            f_h, f_w = f.shape[:2]
            f_area = f_w * f_h

            # Collect union of all active enabled classes across all models for this camera
            all_camera_enabled_classes = []
            if isinstance(self.model_configs, dict):
                for k, v in self.model_configs.items():
                    if isinstance(v, dict) and "enabled_classes" in v and isinstance(v["enabled_classes"], list):
                        for ec in v["enabled_classes"]:
                            if ec not in all_camera_enabled_classes:
                                all_camera_enabled_classes.append(ec)
                    elif k == "enabled_classes" and isinstance(v, list):
                        for ec in v:
                            if ec not in all_camera_enabled_classes:
                                all_camera_enabled_classes.append(ec)

            if hasattr(self, "streams_metadata") and isinstance(self.streams_metadata, list):
                try:
                    cid = str(self.cam_id)
                    if cid.isdigit() and int(cid) < len(self.streams_metadata) and isinstance(self.streams_metadata[int(cid)], dict):
                        saved_mc = self.streams_metadata[int(cid)].get("model_configs") or {}
                        if isinstance(saved_mc, dict):
                            for k, v in saved_mc.items():
                                if isinstance(v, dict) and "enabled_classes" in v and isinstance(v["enabled_classes"], list):
                                    for ec in v["enabled_classes"]:
                                        if ec not in all_camera_enabled_classes:
                                            all_camera_enabled_classes.append(ec)
                                elif k == "enabled_classes" and isinstance(v, list):
                                    for ec in v:
                                        if ec not in all_camera_enabled_classes:
                                            all_camera_enabled_classes.append(ec)
                except Exception:
                    pass

            for midx, model in enumerate(self.models):
                m_path = self.model_paths[midx] if (isinstance(self.model_paths, list) and midx < len(self.model_paths)) else str(self.model_paths)
                m_name = os.path.basename(m_path)
                m_clean = m_name.replace(".pt", "")
                
                m_conf = self.conf
                m_iou = self.iou
                enabled_classes = None
                m_imgsz = 640
                cfg = get_config_for_model(self.model_configs, m_name)
                if cfg and isinstance(cfg, dict):
                    m_conf = float(cfg.get("conf", self.conf))
                    m_iou = float(cfg.get("iou", self.iou))
                    enabled_classes = cfg.get("enabled_classes")
                    m_imgsz = int(cfg.get("imgsz", 640))

                if enabled_classes is None and hasattr(self, "streams_metadata") and isinstance(self.streams_metadata, list):
                    try:
                        cid = str(self.cam_id)
                        if cid.isdigit() and int(cid) < len(self.streams_metadata) and isinstance(self.streams_metadata[int(cid)], dict):
                            saved_mc = self.streams_metadata[int(cid)].get("model_configs") or {}
                            saved_cfg = get_config_for_model(saved_mc, m_name)
                            if isinstance(saved_cfg, dict) and saved_cfg.get("enabled_classes"):
                                enabled_classes = saved_cfg.get("enabled_classes")
                    except Exception:
                        pass

                # Combine model-specific enabled_classes with camera-wide union so any assigned model detects all requested classes instantly
                filter_classes = list(all_camera_enabled_classes) if all_camera_enabled_classes else (enabled_classes if enabled_classes else [])

                detected_this_model = []
                # Predict at conf 0.05 to capture all moving, distant, and close objects instantly
                with INFERENCE_LOCK:
                    results = model.predict(f, conf=0.05, iou=m_iou, imgsz=m_imgsz, verbose=False)
                for r in results:
                    if r.boxes:
                        for b in r.boxes:
                            cls = r.names[int(b.cls[0])]
                            conf_val = float(b.conf[0])
                            detected_this_model.append((cls, conf_val))

                            if filter_classes:
                                matched = any(match_class(cls, e) for e in filter_classes)
                                if not matched:
                                    continue

                            box_xyxy = b.xyxy[0].cpu().numpy().tolist()
                            x1, y1, x2, y2 = box_xyxy
                            bw = max(0, x2 - x1)
                            bh = max(0, y2 - y1)
                            box_area = bw * bh

                            # Load class-specific conf thresholds dynamically
                            cls_conf = m_conf
                            if cfg and isinstance(cfg, dict):
                                class_configs = cfg.get("class_configs")
                                if class_configs and isinstance(class_configs, dict):
                                    c_cfg = None
                                    for k, val in class_configs.items():
                                        if match_class(cls, k):
                                            c_cfg = val
                                            break
                                    if c_cfg and isinstance(c_cfg, dict) and "conf" in c_cfg:
                                        cls_conf = float(c_cfg.get("conf", m_conf))

                            # Automatically set detection threshold floor for enabled violation classes to 0.15 max so all cameras detect across the full area
                            if filter_classes:
                                cls_conf = min(cls_conf, 0.15)
                            elif not cls_conf or cls_conf < 0.05:
                                cls_conf = 0.15

                            # Hysteresis: if this object's place is already tracked on screen, allow retention down to conf 0.08
                            is_tracked_place = False
                            for t_box in getattr(self, '_tracked_boxes', []):
                                if match_class(t_box.get('cls'), cls):
                                    tx1, ty1, tx2, ty2 = t_box['box']
                                    t_area = max(0, tx2 - tx1) * max(0, ty2 - ty1)
                                    ix1, iy1 = max(x1, tx1), max(y1, ty1)
                                    ix2, iy2 = min(x2, tx2), min(y2, ty2)
                                    if ix2 > ix1 and iy2 > iy1:
                                        inter = (ix2 - ix1) * (iy2 - iy1)
                                        union = box_area + t_area - inter
                                        if inter / max(1.0, union) > 0.20:
                                            is_tracked_place = True
                                            break

                            effective_conf = 0.08 if is_tracked_place else cls_conf

                            # Validate Box using effective confidence threshold
                            if not self._is_valid_box(conf_val, effective_conf, bw, bh, box_area, f_w, f_h, f_area):
                                continue

                            # Apply ROI rectangle filter if configured
                            if self.roi_polygon and len(self.roi_polygon) == 2:
                                try:
                                    fh, fw = f.shape[:2]
                                    rx1 = int(min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * fw)
                                    ry1 = int(min(self.roi_polygon[0][1], self.roi_polygon[1][0]) * fh)
                                    rx2 = int(max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * fw)
                                    ry2 = int(max(self.roi_polygon[0][1], self.roi_polygon[1][0]) * fh)
                                    cx = int((x1 + x2) / 2)
                                    cy = int((y1 + y2) / 2)
                                    inside = (rx1 <= cx <= rx2 and ry1 <= cy <= ry2)
                                    if not inside:
                                        continue
                                except Exception:
                                    pass

                            label_text = f"{cls} {conf_val:.2f}"
                            color_val = get_dynamic_class_color(cls)
                            raw_boxes.append((box_xyxy, label_text, color_val, conf_val, cls))
                if detected_this_model:
                    m_name = os.path.basename(self.model_paths[midx])
                    print(f"[DEBUG] Camera {self.cam_id} {m_name}: raw_detected={len(detected_this_model)}, filter={enabled_classes}, kept={len(raw_boxes)} boxes", flush=True)

            # Strict Non-Maximum Suppression (NMS) to guarantee single clean bounding boxes per object
            boxes_data = []
            kept_items = []
            if raw_boxes:
                raw_boxes.sort(key=lambda x: x[3], reverse=True)
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
                            
                            is_same_cls = match_class(cls1_name, cls2_name)
                            # Strict IoU: 0.35 for same/equivalent class to eliminate duplicate boxes on same object
                            iou_thresh = 0.35 if is_same_cls else 0.70
                            if iou >= iou_thresh:
                                suppress = True
                                break
                                
                    if not suppress:
                        kept_items.append(item)

            # ── TEMPORAL BOX PERSISTENCE & SMOOTHING ──
            new_tracked = []
            matched_indices = set()

            for item in kept_items:
                b1_xyxy, l1_text, c1_color, conf1_val, cls1_name = item
                x1_1, y1_1, x2_1, y2_1 = b1_xyxy
                area1 = max(0, x2_1 - x1_1) * max(0, y2_1 - y1_1)
                
                best_match_idx = None
                best_match_iou = 0.0

                for t_idx, t_box in enumerate(getattr(self, '_tracked_boxes', [])):
                    if t_idx in matched_indices:
                        continue
                    if not match_class(t_box.get('cls'), cls1_name):
                        continue
                    
                    x1_2, y1_2, x2_2, y2_2 = t_box['box']
                    area2 = max(0, x2_2 - x1_2) * max(0, y2_2 - y1_2)
                    ix1, iy1 = max(x1_1, x1_2), max(y1_1, y1_2)
                    ix2, iy2 = min(x2_1, x2_2), min(y2_1, y2_2)
                    if ix2 > ix1 and iy2 > iy1:
                        inter = (ix2 - ix1) * (iy2 - iy1)
                        union = area1 + area2 - inter
                        iou_val = inter / max(1.0, union)
                        if iou_val > 0.30 and iou_val > best_match_iou:
                            best_match_iou = iou_val
                            best_match_idx = t_idx

                if best_match_idx is not None:
                    matched_indices.add(best_match_idx)
                    prev_box = self._tracked_boxes[best_match_idx]['box']
                    smooth_box = [
                        0.80 * x1_1 + 0.20 * prev_box[0],
                        0.80 * y1_1 + 0.20 * prev_box[1],
                        0.80 * x2_1 + 0.20 * prev_box[2],
                        0.80 * y2_1 + 0.20 * prev_box[3]
                    ]
                    new_tracked.append({
                        'box': smooth_box,
                        'label': l1_text,
                        'color': c1_color,
                        'cls': cls1_name,
                        'conf': conf1_val,
                        'ttl': 3
                    })
                else:
                    new_tracked.append({
                        'box': b1_xyxy,
                        'label': l1_text,
                        'color': c1_color,
                        'cls': cls1_name,
                        'conf': conf1_val,
                        'ttl': 3
                    })

            # Carry over active tracked boxes whose ttl > 1
            for t_idx, t_box in enumerate(getattr(self, '_tracked_boxes', [])):
                if t_idx not in matched_indices:
                    new_ttl = t_box['ttl'] - 1
                    if new_ttl > 0:
                        t_copy = dict(t_box)
                        t_copy['ttl'] = new_ttl
                        new_tracked.append(t_copy)

            self._tracked_boxes = new_tracked

            boxes_data = []
            for t_box in self._tracked_boxes:
                boxes_data.append((t_box['box'], t_box['label'], t_box['color']))
                cur_cls.add(t_box['cls'])

            # Render alert image snapshot with green ROI and identical color-coded boxes
            snap_img = f.copy()
            if self.roi_polygon and len(self.roi_polygon) == 2:
                try:
                    fh, fw = snap_img.shape[:2]
                    rx1 = int(min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * fw)
                    ry1 = int(min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * fh)
                    rx2 = int(max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * fw)
                    ry2 = int(max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * fh)
                    cv2.rectangle(snap_img, (rx1, ry1), (rx2, ry2), (0, 255, 0), 2)
                except:
                    pass

            for b_xyxy, label_text, c_color in boxes_data:
                try:
                    x1, y1, x2, y2 = [int(v) for v in b_xyxy]
                    cv2.rectangle(snap_img, (x1, y1), (x2, y2), c_color, 2)
                    t_size = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)[0]
                    cv2.rectangle(snap_img, (x1, max(0, y1 - t_size[1] - 6)), (x1 + t_size[0] + 6, max(0, y1)), c_color, -1)
                    cv2.putText(snap_img, label_text, (x1 + 3, max(t_size[1] + 2, y1 - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
                except:
                    pass
            res = snap_img

            # Persistent alert timing: 3.0s initial trigger + 30.0s repeat interval for ongoing violations
            for c in cur_cls:
                if c not in self.alert_timers:
                    self.alert_timers[c] = {'start': now, 'last_seen': now, 'last_alert': 0.0}
                else:
                    self.alert_timers[c]['last_seen'] = now

                duration = now - self.alert_timers[c]['start']
                last_alert_time = self.alert_timers[c].get('last_alert', 0.0)

                # Initial trigger after 3.0s continuous detection, or repeat every 30s for continuous violations
                if duration >= 3.0:
                    if last_alert_time == 0.0 or (now - last_alert_time) >= 30.0:
                        self.alert_timers[c]['last_alert'] = now
                        self.alert_triggered.add(c)
                        print(f"[ALERT] Triggering alert: cam={self.cam_id}, class={c}, duration={duration:.1f}s, is_repeat={(last_alert_time > 0)}", flush=True)
                        self._save_alert(c, res)

            # Cleanup expired classes (absent for > 1.5s)
            for c in list(self.alert_timers.keys()):
                if now - self.alert_timers[c]['last_seen'] > 1.5:
                    del self.alert_timers[c]
                    if c in self.alert_triggered:
                        self.alert_triggered.remove(c)

            return res, boxes_data
        except Exception as err:
            print(f"[ERROR] _run_all_models error: {err}", flush=True)
            return f, []

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

    def _inference_thread(self):
        print(f"[LOG] Camera {self.cam_id} inference thread started", flush=True)
        while not self._stop_event.is_set():
            try:
                f = self._frame_queue.get(timeout=0.2)
            except:
                continue
            try:
                ann_frame, boxes = self._run_all_models(f)
            except Exception as e:
                print(f"[INFERENCE-ERR] Camera {self.cam_id} inference error: {e}", flush=True)
                ann_frame, boxes = f, []
            with self._box_lock:
                self._latest_boxes = boxes
                self._latest_box_time = time.time()
                self._latest_ann_frame = ann_frame
            if hasattr(self, '_result_queue'):
                if self._result_queue.full():
                    try:
                        self._result_queue.get_nowait()
                    except:
                        pass
                self._result_queue.put(ann_frame)

    def _capture_thread(self, cap):
        while not self._stop_event.is_set():
            t_start = time.time()
            if not cap.grab():
                time.sleep(0.005)
                continue
            
            # Drain buffer: discard old frames queued in socket buffer to reach live edge
            grab_count = 0
            while grab_count < 30 and (time.time() - t_start) < 0.005:
                t_start = time.time()
                if not cap.grab():
                    break
                grab_count += 1
            
            ret, f = cap.retrieve()
            if not ret or f is None:
                time.sleep(0.005)
                continue
                
            with self._frame_lock:
                self._latest_raw_frame = f
                self._cap_ok = True
                self._last_frame_time = time.time()

    def run(self):
        ffmpeg, cap, inf_t, cap_t = None, None, None, None
        try:
            if self.models is None:
                paths = self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]
                print(f"[WORKER-TIMER] Camera {self.cam_id} background model loading started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
                t_load_start = time.time()
                self.models = [get_yolo_model(mp) for mp in paths]
                print(f"[WORKER-TIMER] Camera {self.cam_id} models loaded in {int((time.time() - t_load_start)*1000)}ms at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {paths}", flush=True)

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
                        cap_t = threading.Thread(target=self._capture_thread, args=(cap,), daemon=True)
                        cap_t.start()

                    # Wait for first real raw frame from camera
                    t_frame_start = time.time()
                    while time.time() - t_frame_start < 5.0 and self._latest_raw_frame is None and not self._stop_event.is_set():
                        time.sleep(0.05)
                    
                    if self._stop_event.is_set():
                        break
                    
                    print(f"[WORKER-TIMER] Camera {self.cam_id} first raw frame received in {int((time.time() - t_frame_start)*1000)}ms at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
                    
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

                        # Overlay latest active tracked boxes onto fresh live frame pf at 15 FPS
                        with self._box_lock:
                            cur_tracked = list(getattr(self, '_tracked_boxes', []))
                        
                        for t_box in cur_tracked:
                            try:
                                b_xyxy = t_box['box']
                                label_text = t_box['label']
                                color_val = t_box['color']
                                x1, y1, x2, y2 = [int(v) for v in b_xyxy]
                                cv2.rectangle(pf, (x1, y1), (x2, y2), color_val, 2)
                                t_size = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)[0]
                                cv2.rectangle(pf, (x1, max(0, y1 - t_size[1] - 6)), (x1 + t_size[0] + 6, max(0, y1)), color_val, -1)
                                cv2.putText(pf, label_text, (x1 + 3, max(t_size[1] + 2, y1 - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
                            except: pass

                        if ffmpeg.poll() is not None: break
                        try:
                            ffmpeg.stdin.write(pf.tobytes())
                            ffmpeg.stdin.flush()
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
