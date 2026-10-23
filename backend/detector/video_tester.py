"""
video_tester.py - Edge AI Video Testing Engine for RTSP & Edge Server.
Supports both Instant Live Stream Mode (real-time MJPEG detection playback with live slider tuning)
and Offline Web-Compatible H.264 MP4 export.
"""

import os
import sys
import time
import math
import uuid
import shutil
import logging
import threading
import subprocess
import re
import hashlib
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np

# Ensure root directory is on path
_curr = Path(__file__).resolve()
BASE_DIR = None
for p in [_curr.parent, _curr.parent.parent, _curr.parent.parent.parent]:
    if (p / "models").is_dir() and (p / "backend").is_dir():
        BASE_DIR = p
        break
if BASE_DIR is None:
    BASE_DIR = _curr.parents[2] if len(_curr.parents) > 2 else _curr.parent.parent

BACKEND_DIR = BASE_DIR / "backend"
DETECTOR_DIR = BACKEND_DIR / "detector"

for p in [str(DETECTOR_DIR), str(BACKEND_DIR), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

try:
    from detector_worker import (
        get_yolo_model,
        get_dynamic_class_color,
        match_class,
        clean_str,
        extract_negation_and_core
    )
except ImportError:
    try:
        from detector.detector_worker import (
            get_yolo_model,
            get_dynamic_class_color,
            match_class,
            clean_str,
            extract_negation_and_core
        )
    except ImportError:
        YOLO_CACHE = {}
        def get_yolo_model(model_path):
            if model_path not in YOLO_CACHE:
                YOLO_CACHE[model_path] = YOLO(model_path)
            return YOLO_CACHE[model_path]

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
        if not box_cls or not enabled_cls:
            return False
        b_neg, b_core, b_clean = extract_negation_and_core(box_cls)
        e_neg, e_core, e_clean = extract_negation_and_core(enabled_cls)
        if b_clean == e_clean: return True
        if b_neg != e_neg: return False
        if b_core == e_core: return True
        if (b_core in e_core or e_core in b_core) and len(b_core) >= 3 and len(e_core) >= 3: return True
        return False

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
        if not class_name:
            return (0, 255, 255)
        is_neg, core, cleaned = extract_negation_and_core(class_name)
        is_hazard = is_neg or any(w in cleaned for w in ("fire", "smoke", "fall", "danger", "hazard", "violation", "unauthorized"))
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

logger = logging.getLogger("VideoTester")
logger.setLevel(logging.INFO)

STORAGE_DIR = BASE_DIR / "test_videos"
RAW_DIR = STORAGE_DIR / "raw"
RESULTS_DIR = STORAGE_DIR / "results"

RAW_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def resolve_model_path(model_name: str) -> str:
    """Resolve model file path across models directory or project root."""
    if not model_name or str(model_name).lower() in ("none", "raw", "preview", ""):
        return ""
    if os.path.isabs(model_name) and os.path.exists(model_name):
        return model_name
    basename = os.path.basename(model_name)
    candidates = [
        BASE_DIR / "models" / basename,
        BASE_DIR / "models" / model_name,
        BASE_DIR / "backend" / "models" / basename,
        BASE_DIR / basename,
        Path(model_name)
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return str(BASE_DIR / "models" / basename)


def resolve_raw_path(raw_filename: str) -> str:
    """Resolve raw uploaded video path across candidate storage directories or return RTSP URL directly."""
    if not raw_filename:
        return ""
    if raw_filename.startswith("rtsp://") or raw_filename.startswith("rtsps://") or raw_filename.startswith("http://") or raw_filename.startswith("https://"):
        return raw_filename
    if os.path.isabs(raw_filename) and os.path.exists(raw_filename):
        return raw_filename
    basename = os.path.basename(raw_filename)
    candidates = [
        RAW_DIR / basename,
        BASE_DIR / "test_videos" / "raw" / basename,
        BASE_DIR / "backend" / "test_videos" / "raw" / basename,
        Path(raw_filename)
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return str(RAW_DIR / basename)


# In-memory Job Store
JOBS = {}
JOBS_LOCK = threading.Lock()

# In-memory Live Streamers Store
LIVE_STREAMERS = {}
LIVE_STREAMERS_LOCK = threading.Lock()


# =========================================================================
# [LIVE] INSTANT LIVE VIDEO DETECTION STREAMER (REAL-TIME MJPEG)
# =========================================================================
class LiveVideoTestStreamer:
    def __init__(self, session_id: str, raw_filename: str, model_name: str, conf=0.35, iou=0.45, imgsz=640, enabled_classes=None, frame_step=1):
        self.session_id = session_id
        self.raw_filename = raw_filename
        self.raw_path = resolve_raw_path(raw_filename)
        self.model_name = model_name
        self.model_path = resolve_model_path(model_name)
        self.conf = float(conf)
        self.iou = float(iou)
        self.imgsz = int(imgsz)
        self.enabled_classes = enabled_classes or []
        self.frame_step = max(1, int(frame_step))
        
        self.running = False
        self.paused = False
        self.loop = True
        self.thread = None
        
        self.current_frame = None
        self.current_jpeg_bytes = None
        self.latest_detections = []
        self.active_classes_count = {}
        self.fps = 25.0
        self.processed_fps = 0.0
        self.lock = threading.Lock()
        self.model = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True, name=f"LiveStreamer-{self.session_id}")
        self.thread.start()

    def stop(self):
        self.running = False

    def update_params(self, conf=None, iou=None, imgsz=None, enabled_classes=None, model_name=None, frame_step=None):
        with self.lock:
            if conf is not None:
                self.conf = float(conf)
            if iou is not None:
                self.iou = float(iou)
            if imgsz is not None:
                self.imgsz = int(imgsz)
            if enabled_classes is not None:
                self.enabled_classes = enabled_classes
            if frame_step is not None:
                self.frame_step = max(1, int(frame_step))
            if model_name is not None and model_name != self.model_name:
                self.model_name = model_name
                self.model_path = resolve_model_path(model_name)
                try:
                    self.model = get_yolo_model(self.model_path)
                except Exception as e:
                    logger.error(f"Error loading new model {model_name}: {e}")

    def get_jpeg_frame(self):
        with self.lock:
            return self.current_jpeg_bytes

    def _run_loop(self):
        cap = cv2.VideoCapture(self.raw_path)
        if not cap.isOpened():
            alt_path = resolve_raw_path(self.raw_filename)
            if alt_path != self.raw_path:
                self.raw_path = alt_path
                cap = cv2.VideoCapture(self.raw_path)

        if not cap.isOpened():
            logger.error(f"Cannot open video or RTSP stream for streaming: {self.raw_path}")
            self.running = False
            return

        is_rtsp = self.raw_path.startswith("rtsp://") or self.raw_path.startswith("rtsps://") or self.raw_path.startswith("http://") or self.raw_path.startswith("https://")
        if is_rtsp:
            self.loop = False

        native_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if native_fps <= 0 or math.isnan(native_fps) or native_fps > 120:
            native_fps = 25.0
        self.fps = native_fps
        target_frame_time = 1.0 / native_fps
        frame_idx = 0

        # Publish initial raw frame IMMEDIATELY so stream never starts on black screen
        ret, initial_frame = cap.read()
        if ret and initial_frame is not None:
            ret_j, jpeg = cv2.imencode('.jpg', initial_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if ret_j:
                with self.lock:
                    self.current_jpeg_bytes = jpeg.tobytes()

        if self.model_path:
            try:
                self.model = get_yolo_model(self.model_path)
                if self.model is None:
                    logger.warning(f"LiveStreamer: Model returned None for path {self.model_path}")
                else:
                    logger.info(f"LiveStreamer: Successfully loaded model {self.model_name} from {self.model_path}")
            except Exception as e:
                logger.error(f"Failed to load model on live start: {e}")

        logger.info(f"LiveStreamer: Started streaming {self.raw_filename} at native {native_fps:.1f} FPS (pace: {target_frame_time*1000:.1f}ms/frame)")

        while self.running:
            if self.paused:
                time.sleep(0.05)
                continue

            t_frame_start = time.time()

            ret, frame = cap.read()
            if not ret or frame is None:
                if self.loop and not is_rtsp:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    frame_idx = 0
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        time.sleep(0.05)
                        continue
                elif is_rtsp:
                    time.sleep(0.02)
                    continue
                else:
                    break

            frame_idx += 1

            with self.lock:
                c_conf = self.conf
                c_iou = self.iou
                c_imgsz = self.imgsz
                c_model = self.model
                m_name = self.model_name
                c_classes = list(self.enabled_classes)
                c_step = self.frame_step

            # If model was not loaded initially, attempt lazy reload
            if c_model is None and self.model_path:
                try:
                    c_model = get_yolo_model(self.model_path)
                    if c_model is not None:
                        with self.lock:
                            self.model = c_model
                except Exception:
                    pass

            dets = []
            dt_infer = 0.001

            # Run YOLO directly on the exact frame being shown
            if c_model is not None:
                try:
                    t_infer_0 = time.time()
                    results = c_model.predict(
                        source=frame,
                        imgsz=c_imgsz,
                        conf=c_conf,
                        iou=c_iou,
                        verbose=False
                    )
                    dt_infer = max(0.001, time.time() - t_infer_0)

                    if results and len(results) > 0:
                        r = results[0]
                        if r.boxes is not None and len(r.boxes) > 0:
                            for b in r.boxes:
                                cls_id = int(b.cls[0].item())
                                conf_val = float(b.conf[0].item())
                                cls_name = c_model.names.get(cls_id, str(cls_id)) if hasattr(c_model, 'names') else str(cls_id)

                                if c_classes:
                                    if not any(match_class(cls_name, fc) for fc in c_classes):
                                        continue

                                xyxy = b.xyxy[0].cpu().numpy()
                                color = get_dynamic_class_color(cls_name)
                                dets.append({
                                    "box": xyxy,
                                    "label": cls_name,
                                    "conf": conf_val,
                                    "color": color
                                })
                except Exception as e:
                    logger.error(f"Live infer error on frame #{frame_idx}: {e}")

            h, w = frame.shape[:2]

            # Draw exact bounding boxes on this frame
            for det in dets:
                x1, y1, x2, y2 = map(int, det["box"])
                x1 = max(0, min(w - 1, x1))
                y1 = max(0, min(h - 1, y1))
                x2 = max(0, min(w - 1, x2))
                y2 = max(0, min(h - 1, y2))
                
                color = det["color"]
                label = det["label"]
                conf = det["conf"]

                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                tag_text = f"{label} {conf:.2f}"
                (tw, th), _ = cv2.getTextSize(tag_text, cv2.FONT_HERSHEY_SIMPLEX, 0.50, 1)
                
                # Position label tag: above box if space permits, else inside top of box
                if y1 - th - 6 > 0:
                    bg_y1 = y1 - th - 6
                    bg_y2 = y1
                    text_y = y1 - 4
                else:
                    bg_y1 = y1
                    bg_y2 = min(h - 1, y1 + th + 6)
                    text_y = y1 + th + 2
                    
                bg_x2 = min(w - 1, x1 + tw + 6)
                cv2.rectangle(frame, (x1, bg_y1), (bg_x2, bg_y2), (18, 20, 24), -1)
                cv2.rectangle(frame, (x1, bg_y1), (bg_x2, bg_y2), color, 1)
                cv2.putText(frame, tag_text, (x1 + 3, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (255, 255, 255), 1, cv2.LINE_AA)

            p_fps = 1.0 / dt_infer if dt_infer > 0 else 0.0

            # Encode to JPEG and publish to stream
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 75]
            ret, jpeg = cv2.imencode('.jpg', frame, encode_param)
            if ret:
                with self.lock:
                    self.current_jpeg_bytes = jpeg.tobytes()
                    self.latest_detections = dets
                    self.processed_fps = p_fps

            # Frame skipping if requested (advances video faster across time)
            if c_step > 1:
                for _ in range(c_step - 1):
                    if not cap.grab():
                        break
                    frame_idx += 1

            # Real-time FPS Pacing: enforce 1.0x native playback speed
            t_frame_end = time.time()
            elapsed = t_frame_end - t_frame_start
            sleep_needed = target_frame_time - elapsed
            if sleep_needed > 0.002:
                time.sleep(sleep_needed)

        cap.release()
        self.running = False


def get_or_create_live_streamer(session_id: str, raw_filename: str, model_name: str, conf=0.35, iou=0.45, imgsz=640, enabled_classes=None, frame_step=1):
    with LIVE_STREAMERS_LOCK:
        if session_id in LIVE_STREAMERS:
            s = LIVE_STREAMERS[session_id]
            if s.running and s.raw_filename == raw_filename:
                s.update_params(conf=conf, iou=iou, imgsz=imgsz, enabled_classes=enabled_classes, model_name=model_name, frame_step=frame_step)
                return s
            else:
                s.stop()
                del LIVE_STREAMERS[session_id]

        streamer = LiveVideoTestStreamer(
            session_id=session_id,
            raw_filename=raw_filename,
            model_name=model_name,
            conf=conf,
            iou=iou,
            imgsz=imgsz,
            enabled_classes=enabled_classes,
            frame_step=frame_step
        )
        LIVE_STREAMERS[session_id] = streamer
        streamer.start()
        return streamer


def get_live_streamer(session_id: str):
    with LIVE_STREAMERS_LOCK:
        return LIVE_STREAMERS.get(session_id)


def stop_live_streamer(session_id: str):
    with LIVE_STREAMERS_LOCK:
        s = LIVE_STREAMERS.pop(session_id, None)
        if s:
            s.stop()
            return True
    return False


# =========================================================================
# [BATCH] BATCH JOB ENGINE (EXPORT H.264 MP4)
# =========================================================================
class VideoTestJob:
    def __init__(self, job_id, raw_filename, model_name, conf=0.35, iou=0.45, imgsz=640, enabled_classes=None, frame_skip=1):
        self.job_id = job_id
        self.raw_filename = raw_filename
        self.raw_path = resolve_raw_path(raw_filename)
        self.result_filename = f"{job_id}_annotated.mp4"
        self.result_path = str(RESULTS_DIR / self.result_filename)
        
        self.model_name = model_name
        self.model_path = resolve_model_path(model_name)
        self.conf = float(conf)
        self.iou = float(iou)
        self.imgsz = int(imgsz)
        self.enabled_classes = enabled_classes or []
        self.frame_skip = max(1, int(frame_skip))
        
        self.status = "queued"
        self.progress = 0.0
        self.current_frame = 0
        self.total_frames = 0
        self.video_fps = 25.0
        self.video_width = 0
        self.video_height = 0
        self.duration_sec = 0.0
        
        self.processing_fps = 0.0
        self.start_time = None
        self.elapsed_sec = 0.0
        self.eta_sec = 0.0
        
        self.detections_total = 0
        self.class_counts = {}
        self.error = None
        self.cancel_requested = False

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "raw_filename": self.raw_filename,
            "result_filename": self.result_filename,
            "status": self.status,
            "progress": round(self.progress, 1),
            "current_frame": self.current_frame,
            "total_frames": self.total_frames,
            "video_fps": round(self.video_fps, 2),
            "video_resolution": f"{self.video_width}x{self.video_height}" if self.video_width else "N/A",
            "duration_sec": round(self.duration_sec, 2),
            "processing_fps": round(self.processing_fps, 1),
            "elapsed_sec": round(self.elapsed_sec, 1),
            "eta_sec": round(self.eta_sec, 1),
            "model_name": self.model_name,
            "conf": self.conf,
            "iou": self.iou,
            "imgsz": self.imgsz,
            "enabled_classes": self.enabled_classes,
            "frame_skip": self.frame_skip,
            "detections_total": self.detections_total,
            "class_counts": self.class_counts,
            "result_url": f"/api/video-test/result/{self.job_id}",
            "raw_url": f"/api/video-test/raw/{self.job_id}",
            "error": self.error,
        }


def get_job(job_id: str) -> VideoTestJob:
    with JOBS_LOCK:
        return JOBS.get(job_id)


def create_job(raw_filename: str, model_name: str, conf=0.35, iou=0.45, imgsz=640, enabled_classes=None, frame_skip=1) -> VideoTestJob:
    job_id = str(uuid.uuid4())[:8]
    job = VideoTestJob(
        job_id=job_id,
        raw_filename=raw_filename,
        model_name=model_name,
        conf=conf,
        iou=iou,
        imgsz=imgsz,
        enabled_classes=enabled_classes,
        frame_skip=frame_skip
    )
    with JOBS_LOCK:
        JOBS[job_id] = job
    return job


def delete_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.pop(job_id, None)
    if job:
        job.cancel_requested = True
        try:
            if os.path.exists(job.raw_path):
                os.remove(job.raw_path)
            if os.path.exists(job.result_path):
                os.remove(job.result_path)
        except Exception as e:
            logger.error(f"Error removing files for job {job_id}: {e}")
        return True
    return False


def _draw_detection_overlay(frame, detections, job: VideoTestJob, frame_idx: int, total_frames: int):
    h, w = frame.shape[:2]
    
    for det in detections:
        box = det["box"]
        label = det["label"]
        conf = det["conf"]
        color = det["color"]
        
        x1, y1, x2, y2 = map(int, box)
        x1 = max(0, min(w - 1, x1))
        y1 = max(0, min(h - 1, y1))
        x2 = max(0, min(w - 1, x2))
        y2 = max(0, min(h - 1, y2))
        
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        tag_text = f"{label} {conf:.2f}"
        (tw, th), baseline = cv2.getTextSize(tag_text, cv2.FONT_HERSHEY_SIMPLEX, 0.50, 1)
        
        if y1 - th - 6 > 0:
            bg_y1 = y1 - th - 6
            bg_y2 = y1
            text_y = y1 - 4
        else:
            bg_y1 = y1
            bg_y2 = min(h - 1, y1 + th + 6)
            text_y = y1 + th + 2
            
        bg_x2 = min(w - 1, x1 + tw + 6)
        cv2.rectangle(frame, (x1, bg_y1), (bg_x2, bg_y2), (18, 20, 24), -1)
        cv2.rectangle(frame, (x1, bg_y1), (bg_x2, bg_y2), color, 1)
        cv2.putText(frame, tag_text, (x1 + 3, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (255, 255, 255), 1, cv2.LINE_AA)
    
    return frame


def _process_video_worker(job: VideoTestJob):
    job.status = "processing"
    job.start_time = time.time()
    
    temp_avi_path = str(RESULTS_DIR / f"{job.job_id}_temp.avi")
    
    try:
        if not os.path.exists(job.model_path):
            raise FileNotFoundError(f"YOLO model not found at: {job.model_path}")
            
        model = get_yolo_model(job.model_path)
        cap = cv2.VideoCapture(job.raw_path)
        if not cap.isOpened():
            raise RuntimeError(f"Unable to open uploaded video file: {job.raw_path}")
            
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        video_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        job.total_frames = total_frames
        job.video_fps = video_fps
        job.video_width = width
        job.video_height = height
        job.duration_sec = total_frames / video_fps if video_fps > 0 else 0.0
        
        filter_classes = [c.strip() for c in job.enabled_classes if c.strip()]
        target_class_ids = []
        if hasattr(model, 'names') and isinstance(model.names, dict):
            if filter_classes:
                for cid, cname in model.names.items():
                    for fc in filter_classes:
                        if match_class(cname, fc):
                            target_class_ids.append(cid)
                            break
        
        fourcc = cv2.VideoWriter_fourcc(*'MJPG')
        out_writer = cv2.VideoWriter(temp_avi_path, fourcc, video_fps, (width, height))
        if not out_writer.isOpened():
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            out_writer = cv2.VideoWriter(temp_avi_path, fourcc, video_fps, (width, height))
            
        frame_idx = 0
        last_detections = []
        
        while cap.isOpened():
            if job.cancel_requested:
                job.status = "cancelled"
                break
                
            ret, frame = cap.read()
            if not ret or frame is None:
                break
                
            frame_idx += 1
            run_detection = ((frame_idx - 1) % job.frame_skip == 0)
            current_frame_detections = []
            
            if run_detection:
                predict_kwargs = {
                    "source": frame,
                    "imgsz": job.imgsz,
                    "conf": job.conf,
                    "iou": job.iou,
                    "verbose": False,
                }
                if target_class_ids:
                    predict_kwargs["classes"] = target_class_ids
                    
                results = model.predict(**predict_kwargs)
                
                if results and len(results) > 0:
                    r = results[0]
                    if r.boxes is not None and len(r.boxes) > 0:
                        for b in r.boxes:
                            cls_id = int(b.cls[0].item())
                            conf_val = float(b.conf[0].item())
                            cls_name = model.names.get(cls_id, str(cls_id)) if hasattr(model, 'names') else str(cls_id)
                            
                            if filter_classes:
                                matched = any(match_class(cls_name, fc) for fc in filter_classes)
                                if not matched:
                                    continue
                                    
                            xyxy = b.xyxy[0].cpu().numpy()
                            color = get_dynamic_class_color(cls_name)
                            
                            current_frame_detections.append({
                                "box": xyxy,
                                "label": cls_name,
                                "conf": conf_val,
                                "color": color
                            })
                            
                            job.detections_total += 1
                            job.class_counts[cls_name] = job.class_counts.get(cls_name, 0) + 1
                            
                last_detections = current_frame_detections
            else:
                current_frame_detections = last_detections
                
            annotated_frame = _draw_detection_overlay(frame, current_frame_detections, job, frame_idx, total_frames)
            out_writer.write(annotated_frame)
            
            job.current_frame = frame_idx
            if total_frames > 0:
                job.progress = (frame_idx / total_frames) * 100.0
                
            now = time.time()
            job.elapsed_sec = now - job.start_time
            if job.elapsed_sec > 0:
                job.processing_fps = frame_idx / job.elapsed_sec
                remaining_frames = max(0, total_frames - frame_idx)
                job.eta_sec = remaining_frames / job.processing_fps if job.processing_fps > 0 else 0.0

        cap.release()
        out_writer.release()
        
        if job.status == "cancelled":
            if os.path.exists(temp_avi_path):
                os.remove(temp_avi_path)
            return

        logger.info(f"[VideoTester] Remuxing job {job.job_id} to H.264 MP4 via FFmpeg...")
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", temp_avi_path,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            job.result_path
        ]
        
        res = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            logger.warning(f"[VideoTester] FFmpeg libx264 conversion failed: {res.stderr.decode('utf-8', errors='ignore')}")
            shutil.copyfile(temp_avi_path, job.result_path)
            
        if os.path.exists(temp_avi_path):
            try: os.remove(temp_avi_path)
            except: pass
            
        job.progress = 100.0
        job.status = "completed"
        logger.info(f"[VideoTester] Job {job.job_id} successfully completed in {job.elapsed_sec:.1f}s.")
        
    except Exception as e:
        logger.error(f"[VideoTester] Job {job.job_id} failed with error: {e}", exc_info=True)
        job.status = "failed"
        job.error = str(e)
        if os.path.exists(temp_avi_path):
            try: os.remove(temp_avi_path)
            except: pass


def start_video_test(job_id: str):
    job = get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
    t = threading.Thread(target=_process_video_worker, args=(job,), daemon=True, name=f"VideoTestWorker-{job_id}")
    t.start()
    return job
