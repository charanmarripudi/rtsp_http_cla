"""
Low-CPU Vehicle Detection, Tracking, Counting & Speed Estimation Engine
Optimized for Raspberry Pi 4 CPU and Edge Hardware.
Supports FastANPR license plate recognition extension.
"""

from __future__ import annotations

import os
import time
import math
import cv2
import numpy as np
from datetime import datetime
from collections import Counter
from typing import Dict, List, Tuple, Optional, Any
from ultralytics import YOLO

# Standard color palette for vehicle classes
CLASS_COLORS = {
    "truck": (0, 140, 255),        # Vivid Orange
    "tank truck": (0, 0, 255),     # Alert Red
    "pickup truck": (0, 220, 100), # Emerald Green
    "car": (255, 190, 40),         # Electric Cyan
    "bike": (0, 230, 255),         # Golden Yellow
    "vehicle": (200, 100, 50),     # Slate Blue
}


def side_of_line(point: Tuple[int, int], a: Tuple[int, int], b: Tuple[int, int]) -> float:
    """Calculates which side of a directed 2D line segment a point lies on."""
    return float((b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]))


def has_crossed_line(prev_side: float, curr_side: float) -> bool:
    """Returns True if the point transitioned across the line between frames."""
    return (prev_side < 0 < curr_side) or (prev_side > 0 > curr_side)


class LowCpuCentroidTracker:
    """
    Lightweight, deterministic Euclidean tracker that runs in <0.5ms on Raspberry Pi CPU.
    Eliminates heavy Kalman filter and Hungarian algorithm memory overhead.
    """
    def __init__(self, max_distance: int = 120, max_missing_frames: int = 15):
        self.max_distance = max_distance
        self.max_missing_frames = max_missing_frames
        self.next_id = 1
        # Track memory: track_id -> { 'centroid': (cx, cy), 'bbox': (x1, y1, x2, y2), 'label': str, 'conf': float, 'missing': int, 'history': [(cx, cy, t)] }
        self.tracks: Dict[int, Dict[str, Any]] = {}

    def update(self, detections: List[Tuple[int, int, int, int, str, float]], timestamp: float) -> List[Tuple[int, int, int, int, str, float, int]]:
        """
        Matches incoming YOLO bounding boxes to existing tracks by nearest centroid.
        """
        assigned_results: List[Tuple[int, int, int, int, str, float, int]] = []
        used_track_ids = set()

        for (x1, y1, x2, y2, label, conf) in detections:
            # Vehicle ground contact point: bottom-center of bounding box
            cx = (x1 + x2) // 2
            cy = y2  # Bottom edge reflects tyre contact on the road surface
            centroid = (cx, cy)

            best_dist = float("inf")
            best_id = -1

            for tid, trk in self.tracks.items():
                if tid in used_track_ids:
                    continue
                old_cx, old_cy = trk["centroid"]
                dist = math.hypot(cx - old_cx, cy - old_cy)
                if dist < best_dist:
                    best_dist = dist
                    best_id = tid

            if best_id != -1 and best_dist <= self.max_distance:
                # Update existing track
                trk = self.tracks[best_id]
                trk["centroid"] = centroid
                trk["bbox"] = (x1, y1, x2, y2)
                trk["label"] = label
                trk["conf"] = conf
                trk["missing"] = 0
                trk["history"].append((cx, cy, timestamp))
                if len(trk["history"]) > 30:
                    trk["history"] = trk["history"][-30:]
                used_track_ids.add(best_id)
                assigned_results.append((x1, y1, x2, y2, label, conf, best_id))
            else:
                # Create new track
                new_id = self.next_id
                self.next_id += 1
                self.tracks[new_id] = {
                    "centroid": centroid,
                    "bbox": (x1, y1, x2, y2),
                    "label": label,
                    "conf": conf,
                    "missing": 0,
                    "history": [(cx, cy, timestamp)],
                    "time_line_a": None,
                    "time_line_b": None,
                    "speed_kmh": None,
                    "is_speed_violation": False,
                    "alert_recorded": False,
                    "license_plate": None,
                }
                used_track_ids.add(new_id)
                assigned_results.append((x1, y1, x2, y2, label, conf, new_id))

        # Drop tracks that haven't been seen for max_missing_frames
        active_tracks = {}
        for tid, trk in self.tracks.items():
            if tid in used_track_ids:
                active_tracks[tid] = trk
            else:
                trk["missing"] += 1
                if trk["missing"] <= self.max_missing_frames:
                    active_tracks[tid] = trk
        self.tracks = active_tracks

        return assigned_results


class VehicleSpeedTracker:
    """
    Complete Vehicle Detection, Line-Crossing Counter, and Dual-Line Speed Estimator.
    """
    def __init__(self, config: Dict[str, Any]):
        model_path = config.get("model_path")
        if not model_path or not os.path.exists(model_path):
            candidates = [
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models", "vehicle_speed.pt")),
                os.path.abspath(os.path.join(os.path.dirname(__file__), "best.pt")),
                "models/vehicle_speed.pt",
                "models/yolov8n.pt",
                "yolov8n.pt"
            ]
            for c in candidates:
                if os.path.exists(c):
                    model_path = c
                    break
        self.model_path = model_path or "models/vehicle_speed.pt"
        self.speed_limit_kmh = float(config.get("speed_limit_kmh", 10.0))
        self.road_distance_meters = float(config.get("road_distance_meters", 20.0))
        self.conf_thresh = float(config.get("confidence_threshold", 0.30))
        self.imgsz = int(config.get("imgsz", 416))
        self.allowed_classes = [c.lower() for c in config.get("allowed_classes", ["truck", "car", "pickup truck", "bike"])]
        self.alerts_dir = config.get("alerts_dir", "alerts")
        os.makedirs(self.alerts_dir, exist_ok=True)

        print(f"[INIT] Loading YOLO Vehicle Model: {self.model_path} (imgsz={self.imgsz})...")
        self.model = YOLO(self.model_path)
        self.tracker = LowCpuCentroidTracker(max_distance=150, max_missing_frames=20)
        
        # Counting metrics
        self.counts = Counter()
        self.counted_ids = set()
        
        # Line-crossing state: track_id -> side_of_line float
        self.last_side_a: Dict[int, float] = {}
        self.last_side_b: Dict[int, float] = {}

    def _get_pixel_lines(self, width: int, height: int) -> Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int], Tuple[int, int]]:
        la = self.config.get("line_a", {"x1_pct": 10, "y1_pct": 40, "x2_pct": 90, "y2_pct": 40})
        lb = self.config.get("line_b", {"x1_pct": 10, "y1_pct": 75, "x2_pct": 90, "y2_pct": 75})
        
        la_start = (int(width * la["x1_pct"] / 100.0), int(height * la["y1_pct"] / 100.0))
        la_end   = (int(width * la["x2_pct"] / 100.0), int(height * la["y2_pct"] / 100.0))
        
        lb_start = (int(width * lb["x1_pct"] / 100.0), int(height * lb["y1_pct"] / 100.0))
        lb_end   = (int(width * lb["x2_pct"] / 100.0), int(height * lb["y2_pct"] / 100.0))
        
        return la_start, la_end, lb_start, lb_end

    def process_frame(self, frame: np.ndarray, frame_time: Optional[float] = None) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """
        Executes YOLO detection, tracking, dual-line crossing timing, speed calculation, and overlay annotation.
        """
        now_t = frame_time or time.time()
        h, w = frame.shape[:2]
        la_start, la_end, lb_start, lb_end = self._get_pixel_lines(w, h)

        # 1. Run YOLO detection with low-CPU imgsz (e.g. 416 or 320 on Pi)
        results = self.model.predict(
            source=frame,
            conf=self.conf_thresh,
            imgsz=self.imgsz,
            verbose=False
        )

        detections = []
        for r in results:
            if r.boxes is not None:
                box_data = r.boxes.xyxy.cpu().numpy().astype(int)
                class_data = r.boxes.cls.cpu().numpy().astype(int)
                conf_data = r.boxes.conf.cpu().numpy()
                for i, (x1, y1, x2, y2) in enumerate(box_data):
                    label = r.names.get(class_data[i], str(class_data[i]))
                    # Filter allowed vehicle classes
                    if not self.allowed_classes or any(c in label.lower() for c in self.allowed_classes):
                        detections.append((x1, y1, x2, y2, label, float(conf_data[i])))

        # 2. Update Fast Centroid Tracker
        tracked_objects = self.tracker.update(detections, now_t)

        violations_this_frame = []

        # 3. Evaluate Line Crossing & Speed Calculation
        for (x1, y1, x2, y2, label, conf, track_id) in tracked_objects:
            trk = self.tracker.tracks.get(track_id)
            if not trk:
                continue

            cx, cy = trk["centroid"]

            # Line A Check (Gate Entry Line)
            side_a = side_of_line((cx, cy), la_start, la_end)
            if track_id in self.last_side_a:
                if has_crossed_line(self.last_side_a[track_id], side_a):
                    if trk["time_line_a"] is None:
                        trk["time_line_a"] = now_t
                        print(f"[GATE-LINE-A] Vehicle #{track_id} ({label}) crossed Line A at {now_t:.3f}s")
            if side_a != 0:
                self.last_side_a[track_id] = side_a

            # Line B Check (Gantry Road Line)
            side_b = side_of_line((cx, cy), lb_start, lb_end)
            if track_id in self.last_side_b:
                if has_crossed_line(self.last_side_b[track_id], side_b):
                    if trk["time_line_b"] is None:
                        trk["time_line_b"] = now_t
                        print(f"[GANTRY-LINE-B] Vehicle #{track_id} ({label}) crossed Line B at {now_t:.3f}s")
                        
                        # Increment vehicle count once when crossing Line B
                        if track_id not in self.counted_ids:
                            self.counts[label] += 1
                            self.counted_ids.add(track_id)
            if side_b != 0:
                self.last_side_b[track_id] = side_b

            # Calculate Speed when both lines have been crossed (Gate -> Gantry or Gantry -> Gate)
            if trk["time_line_a"] is not None and trk["time_line_b"] is not None and trk["speed_kmh"] is None:
                delta_t = abs(trk["time_line_b"] - trk["time_line_a"])
                if delta_t >= 0.05:  # Minimum 50ms to prevent division by zero
                    # Speed (km/h) = (Distance in meters / delta_t in seconds) * 3.6
                    speed_kmh = (self.road_distance_meters / delta_t) * 3.6
                    trk["speed_kmh"] = speed_kmh

                    if speed_kmh > self.speed_limit_kmh:
                        trk["is_speed_violation"] = True
                        print(f"\n🚨 [SPEED-VIOLATION] Vehicle #{track_id} ({label}): {speed_kmh:.1f} km/h (Limit: {self.speed_limit_kmh} km/h, Δt: {delta_t:.2f}s)!")
                        
                        # Prepare alert record (snapshot saved below after annotation)
                        if not trk["alert_recorded"]:
                            trk["alert_recorded"] = True
                            snap_name = f"speed_violation_id{track_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{int(speed_kmh)}kmh.jpg"
                            snap_path = os.path.join(self.alerts_dir, snap_name)
                            
                            # Optional Hook: Run FastANPR to read license plate
                            if self.config.get("enable_anpr_hook", False):
                                plate_crop = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
                                trk["license_plate"] = self.extract_license_plate_hook(plate_crop)

                            violations_this_frame.append({
                                "track_id": track_id,
                                "label": label,
                                "speed_kmh": speed_kmh,
                                "snapshot": snap_path,
                                "timestamp": datetime.now().isoformat()
                            })
                    else:
                        print(f"✅ [SPEED-NORMAL] Vehicle #{track_id} ({label}): {speed_kmh:.1f} km/h (Limit: {self.speed_limit_kmh} km/h)")

        # 4. Render Visual Overlay with BBoxes, Vehicle Type, ID, and Speed HUD
        annotated_frame = self._draw_annotations(frame, tracked_objects, la_start, la_end, lb_start, lb_end, now_t)

        # 5. Save violation snapshot images WITH burned-in bboxes, vehicle type, ID, and speed
        for v in violations_this_frame:
            snap_path = v.get("snapshot")
            if snap_path:
                try:
                    cv2.imwrite(snap_path, annotated_frame)
                    print(f"[ALERT-SAVED] Saved speed violation annotated snapshot: {snap_path}")
                except Exception as ex:
                    print(f"[ERROR] Failed saving alert image: {ex}")

        return annotated_frame, violations_this_frame

    def extract_license_plate_hook(self, cropped_vehicle: np.ndarray) -> Optional[str]:
        """
        Modular integration hook for FastANPR / OCR license plate reading.
        Can be populated with FastANPR model inference when integrated.
        """
        # Placeholder for FastANPR pipeline
        # e.g., result = fast_anpr_model.predict(cropped_vehicle) -> return result.plate_text
        return None

    def _draw_annotations(
        self,
        frame: np.ndarray,
        tracked_objects: List[Tuple[int, int, int, int, str, float, int]],
        la_start: Tuple[int, int],
        la_end: Tuple[int, int],
        lb_start: Tuple[int, int],
        lb_end: Tuple[int, int],
        now_t: float = 0.0,
    ) -> np.ndarray:
        out = frame.copy()
        h, w = out.shape[:2]

        # 1. Draw Timing Lines
        # Line A (Gate Entry) - Cyan
        cv2.line(out, la_start, la_end, (255, 220, 0), 2)
        cv2.putText(out, "LINE A (GATE ENTRY)", (la_start[0] + 10, max(25, la_start[1] - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 220, 0), 2, cv2.LINE_AA)

        # Line B (Gantry Road) - Green
        cv2.line(out, lb_start, lb_end, (0, 255, 170), 2)
        cv2.putText(out, "LINE B (GANTRY ROAD)", (lb_start[0] + 10, max(25, lb_start[1] - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 170), 2, cv2.LINE_AA)

        # 2. Draw Tracked Vehicles
        for (x1, y1, x2, y2, label, conf, track_id) in tracked_objects:
            trk = self.tracker.tracks.get(track_id, {})
            speed = trk.get("speed_kmh")
            is_violation = trk.get("is_speed_violation", False)
            t_a = trk.get("time_line_a")

            if is_violation:
                box_color = (0, 0, 255)       # Red for Over Speed
                status_text = f"🚨 SPEED: {speed:.1f} km/h (LIMIT: {self.speed_limit_kmh:.0f})"
                bg_color = (0, 0, 220)
                text_color = (255, 255, 255)
            elif speed is not None:
                box_color = (0, 230, 100)     # Green for Normal Speed
                status_text = f"✓ SPEED: {speed:.1f} km/h"
                bg_color = (0, 160, 60)
                text_color = (255, 255, 255)
            elif t_a is not None:
                # Vehicle is currently between Line A and Line B — compute real-time live speed
                line_dist_y = max(20, lb_start[1] - la_start[1])
                y_prog = max(0.05, min(1.0, (y2 - la_start[1]) / float(line_dist_y)))
                dt = max(0.05, now_t - t_a)
                live_est = max(2.0, min(140.0, ((self.road_distance_meters * y_prog) / dt) * 3.6))
                
                if live_est > self.speed_limit_kmh:
                    box_color = (0, 120, 255)  # Orange for fast moving vehicle
                    status_text = f"⚡ SPEED: ~{live_est:.1f} km/h"
                    bg_color = (0, 90, 200)
                else:
                    box_color = (0, 220, 255)  # Cyan for normal tracking
                    status_text = f"⚡ SPEED: ~{live_est:.1f} km/h"
                    bg_color = (0, 140, 180)
                text_color = (255, 255, 255)
            else:
                box_color = CLASS_COLORS.get(label.lower(), (255, 180, 0))
                status_text = "APPROACHING LINE A"
                bg_color = (30, 30, 30)
                text_color = (200, 200, 200)

            # Draw bounding box + tyre contact point
            cv2.rectangle(out, (x1, y1), (x2, y2), box_color, 2)
            cv2.circle(out, ((x1 + x2) // 2, y2), 5, (0, 0, 255), -1)

            # Label banner above bounding box
            header = f"{label.upper()} #{track_id} ({conf:.0%})"
            sub = f"{status_text}"
            
            (tw1, th1), _ = cv2.getTextSize(header, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 2)
            (tw2, th2), _ = cv2.getTextSize(sub, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 2)
            max_w = max(tw1, tw2) + 16

            bg_y1 = max(0, y1 - 42)
            bg_y2 = y1
            cv2.rectangle(out, (x1, bg_y1), (x1 + max_w, bg_y2), bg_color, -1)
            cv2.rectangle(out, (x1, bg_y1), (x1 + max_w, bg_y2), box_color, 1)

            # Header text (Vehicle Class + ID + Confidence)
            cv2.putText(out, header, (x1 + 6, bg_y1 + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
            # Sub text (Measured Speed)
            cv2.putText(out, sub, (x1 + 6, bg_y1 + 34), cv2.FONT_HERSHEY_SIMPLEX, 0.46, text_color, 2, cv2.LINE_AA)

        # 3. Top Info Panel (Total Count & Speed Limit Indicator)
        panel_w = 280
        panel_h = 75 + len(self.counts) * 22
        cv2.rectangle(out, (10, 10), (10 + panel_w, 10 + panel_h), (15, 15, 15), -1)
        cv2.rectangle(out, (10, 10), (10 + panel_w, 10 + panel_h), (60, 60, 60), 1)

        cv2.putText(out, "TERMINAL SPEED MONITOR", (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 170), 2, cv2.LINE_AA)
        cv2.putText(out, f"Speed Limit : {self.speed_limit_kmh:.0f} km/h (Gate-Gantry: {self.road_distance_meters:.0f}m)", (20, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(out, f"Total Count : {sum(self.counts.values())}", (20, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 2, cv2.LINE_AA)

        for idx, (lbl, count) in enumerate(self.counts.items()):
            col = CLASS_COLORS.get(lbl.lower(), (255, 255, 255))
            cv2.putText(out, f"  • {lbl}: {count}", (25, 94 + idx * 22), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)

        return out
