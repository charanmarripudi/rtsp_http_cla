"""
video_tester.py — Edge AI Video Testing Engine for RTSP & Edge Server.
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
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np

# Ensure root directory is on path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

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
                from ultralytics import YOLO
                YOLO_CACHE[model_path] = YOLO(model_path)
            return YOLO_CACHE[model_path]

    DYNAMIC_CLASS_COLOR_MAP = {
        "no-hardhat": (0, 50, 255),
        "no-helmet": (0, 50, 255),
        "no-safety-vest": (255, 230, 0),
        "no-vest": (255, 230, 0),
        "no-mask": (255, 0, 255),
        "hardhat": (0, 220, 100),
        "safety-vest": (255, 140, 0),
        "person": (30, 45, 255),
    }

    def clean_str(s):
        return "".join(c for c in str(s).lower().strip() if c.isalnum() or c in ("-", "_")).replace("_", "-")

    def extract_negation_and_core(name):
        c = clean_str(name)
        is_neg = False
        for prefix in ["no-", "without-", "non-"]:
            if c.startswith(prefix):
                is_neg = True
                c = c[len(prefix):]
                break
        return is_neg, c, clean_str(name)

    def match_class(b_cls, e_cls):
        return clean_str(b_cls) == clean_str(e_cls)

    def get_dynamic_class_color(cname):
        clean = clean_str(cname)
        return DYNAMIC_CLASS_COLOR_MAP.get(clean, (0, 220, 100))

logger = logging.getLogger("VideoTester")
logger.setLevel(logging.INFO)

STORAGE_DIR = BASE_DIR / "test_videos"
RAW_DIR = STORAGE_DIR / "raw"
RESULTS_DIR = STORAGE_DIR / "results"

RAW_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# In-memory Job Store
JOBS = {}
JOBS_LOCK = threading.Lock()

# In-memory Live Streamers Store
LIVE_STREAMERS = {}
LIVE_STREAMERS_LOCK = threading.Lock()


# =========================================================================
# 🔴 INSTANT LIVE VIDEO DETECTION STREAMER (REAL-TIME MJPEG)
# =========================================================================
class LiveVideoTestStreamer:
    def __init__(self, session_id: str, raw_filename: str, model_name: str, conf=0.35, iou=0.45, imgsz=640, enabled_classes=None, frame_step=1):
        self.session_id = session_id
        self.raw_filename = raw_filename
        self.raw_path = str(RAW_DIR / raw_filename)
        self.model_name = model_name
        self.model_path = str(BASE_DIR / "models" / model_name) if not os.path.isabs(model_name) else model_name
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
                self.model_path = str(BASE_DIR / "models" / model_name) if not os.path.isabs(model_name) else model_name
                try:
                    self.model = get_yolo_model(self.model_path)
                except Exception as e:
                    logger.error(f"Error loading new model {model_name}: {e}")

    def get_jpeg_frame(self):
        with self.lock:
            return self.current_jpeg_bytes

    def _run_loop(self):
        try:
            self.model = get_yolo_model(self.model_path)
        except Exception as e:
            logger.error(f"Failed to load model on live start: {e}")

        cap = cv2.VideoCapture(self.raw_path)
        if not cap.isOpened():
            logger.error(f"Cannot open video for streaming: {self.raw_path}")
            self.running = False
            return

        native_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        self.fps = native_fps
        frame_idx = 0

        while self.running:
            if self.paused:
                time.sleep(0.05)
                continue

            ret, frame = cap.read()
            if not ret or frame is None:
                if self.loop:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    frame_idx = 0
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        time.sleep(0.1)
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
# 📁 BATCH JOB ENGINE (EXPORT H.264 MP4)
# =========================================================================
class VideoTestJob:
    def __init__(self, job_id, raw_filename, model_name, conf=0.35, iou=0.45, imgsz=640, enabled_classes=None, frame_skip=1):
        self.job_id = job_id
        self.raw_filename = raw_filename
        self.raw_path = str(RAW_DIR / raw_filename)
        self.result_filename = f"{job_id}_annotated.mp4"
        self.result_path = str(RESULTS_DIR / self.result_filename)
        
        self.model_name = model_name
        self.model_path = str(BASE_DIR / "models" / model_name) if not os.path.isabs(model_name) else model_name
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
