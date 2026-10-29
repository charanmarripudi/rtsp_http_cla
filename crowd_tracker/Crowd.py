from datetime import datetime
import os
import sys
from pathlib import Path
import time
import math
import argparse
from collections import defaultdict, deque
import cv2
import numpy as np
import requests
from ultralytics import YOLO

try:
    from class_rtsp_stream import RTSPStream
except ImportError:
    try:
        from crowd_tracker.class_rtsp_stream import RTSPStream
    except ImportError:
        RTSPStream = None


def resolve_crowd_model(model_path="yolov8n.pt"):
    """Finds model file across candidate paths."""
    if os.path.exists(model_path):
        return model_path
    base_dir = Path(__file__).resolve().parent.parent
    candidates = [
        base_dir / "models" / os.path.basename(model_path),
        base_dir / "models" / "yolov8n.pt",
        Path(model_path)
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return model_path


class CrowdMonitoring:
    """Crowd Detection and Multi-Person Tracking System with Interactive ROI.
    
    Allows the user to interactively draw or specify a Region of Interest (ROI).
    Tracks individuals and triggers alerts when the count of people
    gathered inside that specific ROI meets or exceeds the user-configured threshold.
    """

    def __init__(
        self,
        model_path="yolov8n.pt",
        crowd_threshold=4,
        group_distance_px=180,
        dwell_time_seconds=0.0,  # 0.0 = instant alert when threshold reached
        alert_cooldown_seconds=5.0,
        roi_coords=None,
        interactive_roi=False,
        source=None,
        enable_trails=True
    ):
        resolved_model = resolve_crowd_model(model_path)
        print(f"[CrowdMonitoring] Loading YOLO tracking model: {resolved_model}...")
        self.model = YOLO(resolved_model)
        self.crowd_threshold = int(crowd_threshold)
        self.group_distance_px = int(group_distance_px)
        self.dwell_time_seconds = float(dwell_time_seconds)
        self.alert_cooldown_seconds = float(alert_cooldown_seconds)
        self.roi_coords = roi_coords
        self.interactive_roi = interactive_roi and (roi_coords is None)
        self.source = source
        self.enable_trails = enable_trails

        if source and RTSPStream is not None:
            self.stream = RTSPStream(source)
        else:
            self.stream = None

        # Tracking state
        self.track_history = defaultdict(lambda: deque(maxlen=25))
        self.violation_start_time = None
        self.last_alert_time = 0.0

        # Fallback tracker memory: { track_id: {"centroid": (cx, cy), "last_t": float} }
        self._fallback_tracks = {}
        self._next_fallback_id = 1

    def prompt_and_draw_roi(self, frame):
        """Prompt user to interactively draw an ROI rectangle using OpenCV mouse selection."""
        print("\n" + "=" * 65)
        print(">>> DRAW REGION OF INTEREST (ROI) <<<")
        print("1. In the popup window, CLICK & DRAG mouse to draw the ROI box.")
        print("2. Press ENTER or SPACE to confirm your selection.")
        print("3. Press 'c' to cancel and use the full video frame.")
        print("=" * 65 + "\n")

        disp_frame = frame.copy()
        h, w = disp_frame.shape[:2]

        # Top instruction banner
        cv2.rectangle(disp_frame, (0, 0), (w, 50), (25, 25, 25), -1)
        cv2.putText(
            disp_frame,
            "CLICK & DRAG to Draw ROI  |  ENTER to Confirm  |  'c' to Cancel/Full Frame",
            (20, 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 255),
            2,
            cv2.LINE_AA
        )

        win_name = "Draw ROI - Crowd Monitoring"
        cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win_name, min(1280, w), min(720, h))

        # Built-in OpenCV interactive ROI selection
        rect = cv2.selectROI(win_name, disp_frame, fromCenter=False, showCrosshair=True)
        cv2.destroyWindow(win_name)

        x, y, rw, rh = rect
        if rw > 15 and rh > 15:
            self.roi_coords = (int(x), int(y), int(x + rw), int(y + rh))
            print(f"[ROI Confirmed]: (x1={self.roi_coords[0]}, y1={self.roi_coords[1]}, "
                  f"x2={self.roi_coords[2]}, y2={self.roi_coords[3]})\n")
        else:
            self.roi_coords = None
            print("[ROI]: No valid area drawn (or cancelled). Monitoring full frame.\n")

    def is_inside_roi(self, point):
        """Check if a point (x, y) is inside the selected ROI rectangle."""
        if self.roi_coords is None:
            return True
        x1_roi, y1_roi, x2_roi, y2_roi = self.roi_coords
        x, y = point
        return x1_roi <= x <= x2_roi and y1_roi <= y <= y2_roi

    def cluster_people_in_proximity(self, people):
        """Group people who are physically close together in the ROI."""
        n = len(people)
        if n == 0:
            return []
        if n == 1:
            return [people]

        adj = {i: [] for i in range(n)}
        for i in range(n):
            for j in range(i + 1, n):
                gx1, gy1 = people[i]["ground"]
                gx2, gy2 = people[j]["ground"]
                dist = math.hypot(gx1 - gx2, gy1 - gy2)
                if dist <= self.group_distance_px:
                    adj[i].append(j)
                    adj[j].append(i)

        visited = set()
        groups = []
        for i in range(n):
            if i not in visited:
                cluster = []
                queue = [i]
                visited.add(i)
                while queue:
                    curr = queue.pop(0)
                    cluster.append(people[curr])
                    for neighbor in adj[curr]:
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append(neighbor)
                groups.append(cluster)

        groups.sort(key=lambda g: len(g), reverse=True)
        return groups

    def draw_hud(self, frame, total_tracked, in_roi_count, groups, is_alert):
        """Draw an informational heads-up display dashboard banner."""
        h, w = frame.shape[:2]
        banner_h = 70

        # Semi-transparent dark banner
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, banner_h), (20, 20, 20), -1)
        cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)

        # Status Badge
        if is_alert or in_roi_count >= self.crowd_threshold:
            badge_color = (0, 0, 255)  # Red
            badge_text = f"CROWD ALERT: {in_roi_count} IN ROI"
        else:
            badge_color = (0, 200, 0)  # Green
            badge_text = "STATUS: NORMAL"

        cv2.rectangle(frame, (15, 12), (340, 58), badge_color, -1)
        cv2.putText(
            frame,
            badge_text,
            (25, 43),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
            cv2.LINE_AA
        )

        # Metrics text
        stats_text = (
            f"Total Visible: {total_tracked}  |  In ROI: {in_roi_count}/{self.crowd_threshold}  |  "
            f"Clusters: {len(groups)}  |  [Press 'r' to Redraw ROI]"
        )
        cv2.putText(
            frame,
            stats_text,
            (360, 42),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (220, 220, 220),
            2,
            cv2.LINE_AA
        )

    def trigger_alert(self, frame, in_roi_people):
        """Log crowd alert event (alerts are captured and stored in PostgreSQL by the detector worker)."""
        group_ids = [int(p["id"]) for p in in_roi_people]
        person_count = len(in_roi_people)

        print("\n" + "=" * 65)
        print(f"[CROWD ALERT IN ROI] at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Persons Inside ROI: {person_count} (Threshold: {self.crowd_threshold})")
        print(f"Track IDs in ROI: {group_ids}")
        print(f"ROI Boundary: {self.roi_coords}")
        print("=" * 65 + "\n")

    def _update_fallback_tracker(self, boxes, timestamp):
        """Lightweight Euclidean tracker when lap/ByteTrack is not installed."""
        assigned_ids = []
        used_ids = set()
        for box in boxes:
            x1, y1, x2, y2 = map(int, box)
            cx = (x1 + x2) // 2
            cy = y2
            best_id = None
            best_dist = 140.0
            for tid, trk in self._fallback_tracks.items():
                if tid in used_ids:
                    continue
                tcx, tcy = trk["centroid"]
                dist = math.hypot(cx - tcx, cy - tcy)
                if dist < best_dist:
                    best_dist = dist
                    best_id = tid
            if best_id is not None:
                self._fallback_tracks[best_id] = {"centroid": (cx, cy), "last_t": timestamp}
                used_ids.add(best_id)
                assigned_ids.append(best_id)
            else:
                new_id = self._next_fallback_id
                self._next_fallback_id += 1
                self._fallback_tracks[new_id] = {"centroid": (cx, cy), "last_t": timestamp}
                used_ids.add(new_id)
                assigned_ids.append(new_id)
        # Clean stale tracks (> 2.0s)
        stale = [tid for tid, trk in self._fallback_tracks.items() if (timestamp - trk["last_t"]) > 2.0]
        for tid in stale:
            del self._fallback_tracks[tid]
        return assigned_ids

    def process_frame(self, frame, return_metrics=False):
        """Process frame: track people, check against ROI & threshold, render visual feedback."""
        current_time = time.time()

        boxes = []
        ids = []
        try:
            results = self.model.track(
                frame,
                persist=True,
                classes=[0],
                conf=0.30,
                verbose=False
            )[0]
            if results.boxes is not None and len(results.boxes) > 0:
                boxes = results.boxes.xyxy.cpu().numpy()
                ids = (
                    results.boxes.id.cpu().numpy().astype(int)
                    if results.boxes.id is not None
                    else list(range(1, len(boxes) + 1))
                )
        except Exception:
            # Fallback to model.predict + lightweight Centroid Tracker if lap library is missing
            results = self.model.predict(
                frame,
                classes=[0],
                conf=0.30,
                verbose=False
            )[0]
            if results.boxes is not None and len(results.boxes) > 0:
                boxes = results.boxes.xyxy.cpu().numpy()
                ids = self._update_fallback_tracker(boxes, current_time)

        in_roi_people = []
        out_roi_people = []

        if len(boxes) > 0:

            for box, track_id in zip(boxes, ids):
                x1, y1, x2, y2 = map(int, box)
                cx = int((x1 + x2) / 2)
                cy = int((y1 + y2) / 2)
                gx = cx
                gy = y2  # ground contact point

                self.track_history[track_id].append((cx, cy))

                person = {
                    "id": track_id,
                    "bbox": (x1, y1, x2, y2),
                    "center": (cx, cy),
                    "ground": (gx, gy)
                }

                # Person is in ROI if center or foot ground point is within ROI
                if self.is_inside_roi((cx, cy)) or self.is_inside_roi((cx, gy)):
                    in_roi_people.append(person)
                else:
                    out_roi_people.append(person)

        in_roi_count = len(in_roi_people)
        has_crowd = (in_roi_count >= self.crowd_threshold)

        # Alert trigger logic
        is_active_alert = False
        should_save_alert = False
        if has_crowd:
            is_active_alert = True
            if (current_time - self.last_alert_time) >= self.alert_cooldown_seconds:
                should_save_alert = True
                self.last_alert_time = current_time

        # Render Trajectory Trails
        if self.enable_trails:
            for p in in_roi_people:
                pts = self.track_history[p["id"]]
                trail_color = (0, 0, 255) if has_crowd else (0, 220, 255)
                for i in range(1, len(pts)):
                    cv2.line(frame, pts[i - 1], pts[i], trail_color, 2)

        # Draw ROI Boundary Zone
        if self.roi_coords is not None:
            x1_roi, y1_roi, x2_roi, y2_roi = self.roi_coords
            roi_border_color = (0, 0, 255) if has_crowd else (255, 120, 0)
            roi_thickness = 3 if has_crowd else 2

            cv2.rectangle(frame, (x1_roi, y1_roi), (x2_roi, y2_roi), roi_border_color, roi_thickness)

            roi_label = (
                f"ROI CROWD ALERT: {in_roi_count}/{self.crowd_threshold} Persons"
                if has_crowd
                else f"ROI Zone: {in_roi_count}/{self.crowd_threshold} Persons"
            )
            cv2.putText(
                frame,
                roi_label,
                (x1_roi + 8, max(20, y1_roi - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                roi_border_color,
                2,
                cv2.LINE_AA
            )

        # Cluster people inside the ROI for proximity visualization
        groups = self.cluster_people_in_proximity(in_roi_people)

        # Draw persons OUTSIDE ROI in dim gray (excluded from alert)
        for p in out_roi_people:
            x1, y1, x2, y2 = p["bbox"]
            cv2.rectangle(frame, (x1, y1), (x2, y2), (90, 90, 90), 1)
            cv2.putText(
                frame,
                f"ID:{p['id']}",
                (x1, max(15, y1 - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (120, 120, 120),
                1,
                cv2.LINE_AA
            )

        # Draw persons INSIDE ROI
        for p in in_roi_people:
            x1, y1, x2, y2 = p["bbox"]
            tid = p["id"]

            if has_crowd:
                box_color = (0, 0, 255)  # Red for crowd
                label = f"ID:{tid} [CROWD]"
            else:
                box_color = (0, 230, 0)  # Green for normal
                label = f"ID:{tid}"

            cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
            cv2.circle(frame, p["ground"], 4, box_color, -1)

            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, max(0, y1 - th - 6)), (x1 + tw + 6, y1), box_color, -1)
            cv2.putText(
                frame,
                label,
                (x1 + 3, max(th, y1 - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                1,
                cv2.LINE_AA
            )

        # Draw connecting lines between clustered members in ROI
        for g in groups:
            if len(g) > 1:
                line_color = (0, 0, 255) if has_crowd else (0, 200, 255)
                for i in range(len(g)):
                    for j in range(i + 1, len(g)):
                        pt1 = g[i]["ground"]
                        pt2 = g[j]["ground"]
                        if math.hypot(pt1[0] - pt2[0], pt1[1] - pt2[1]) <= self.group_distance_px:
                            cv2.line(frame, pt1, pt2, line_color, 2, cv2.LINE_AA)

                if has_crowd and len(g) >= 3:
                    pts = np.array([p["ground"] for p in g], dtype=np.int32)
                    hull = cv2.convexHull(pts)
                    cv2.polylines(frame, [hull], True, (0, 0, 255), 2, cv2.LINE_AA)

        # Draw top HUD dashboard
        total_tracked = len(in_roi_people) + len(out_roi_people)
        self.draw_hud(frame, total_tracked, in_roi_count, groups, is_active_alert)

        # Save annotated snapshot (showing ROI, bounding boxes, and tracking IDs)
        if should_save_alert:
            self.trigger_alert(frame, in_roi_people)

        if return_metrics:
            metrics = {
                "in_roi_count": in_roi_count,
                "total_visible": total_tracked,
                "threshold": self.crowd_threshold,
                "clusters_count": len(groups),
                "has_crowd": has_crowd,
                "is_alert": is_active_alert,
                "people_in_roi": in_roi_people
            }
            return frame, metrics

        return frame

    def run(self):
        """Run the main detection loop with OpenCV display window."""
        # Grab first frame to allow user to draw ROI
        frame = None
        while frame is None:
            frame = self.stream.read()
            time.sleep(0.02)

        # Ask user to draw ROI interactively on startup if enabled
        if self.interactive_roi:
            self.prompt_and_draw_roi(frame)

        window_title = f"Crowd Monitoring in ROI (Threshold >= {self.crowd_threshold})"
        cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_title, 1280, 720)

        print("\n--- Starting Crowd Monitoring ---")
        print(f"Source: {self.source}")
        print(f"Selected ROI: {self.roi_coords if self.roi_coords else 'Full Frame'}")
        print(f"Crowd Count Threshold: >= {self.crowd_threshold} persons in ROI")
        print(f"Dwell Time Threshold: {self.dwell_time_seconds}s | Cooldown: {self.alert_cooldown_seconds}s")
        print("Controls:")
        print("  - Press 'r': Redraw ROI with mouse anytime")
        print("  - Press 'q': Quit application\n")

        while True:
            frame = self.stream.read()
            if frame is None:
                time.sleep(0.01)
                continue

            annotated_frame = self.process_frame(frame)
            cv2.imshow(window_title, annotated_frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('r'):
                # Allow user to redraw ROI on current live frame
                self.prompt_and_draw_roi(frame)
                cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)

        self.stream.release()
        cv2.destroyAllWindows()


def parse_arguments():
    parser = argparse.ArgumentParser(description="Crowd & Group Detection in User-Drawn ROI with ByteTrack")
    parser.add_argument(
        "--source",
        type=str,
        default="crowd_video1.mp4" if os.path.exists("crowd_video1.mp4") else "rtsp://admin:Algo12345678@192.168.96.41:554/stream1",
        help="Video file path or RTSP stream URL"
    )
    parser.add_argument(
        "--threshold", "-t",
        type=int,
        default=4,
        help="Crowd count threshold (alert if >= this many people in ROI)"
    )
    parser.add_argument(
        "--roi",
        type=str,
        default=None,
        help="Predefined ROI coordinates as x1,y1,x2,y2 (if omitted, prompts user to draw ROI with mouse)"
    )
    parser.add_argument(
        "--no-roi",
        action="store_true",
        help="Skip ROI drawing and monitor entire video frame"
    )
    parser.add_argument(
        "--distance", "-d",
        type=int,
        default=180,
        help="Proximity pixel distance to group people together (default: 180px)"
    )
    parser.add_argument(
        "--dwell",
        type=float,
        default=0.0,
        help="Minimum seconds group must stay gathered before alert triggers (default: 0.0s for instant alert)"
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=5.0,
        help="Cooldown in seconds between repeated alert dispatches (default: 5.0s)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="yolov8n.pt",
        help="YOLO model path (default: yolov8n.pt)"
    )
    parser.add_argument(
        "--output-dir", "--image-path",
        type=str,
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "crowd_alerts"),
        help="Directory to save alert snapshot images (default: crowd_alerts folder)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()

    # Parse fixed ROI if passed as string "x1,y1,x2,y2"
    fixed_roi = None
    if args.roi:
        try:
            fixed_roi = tuple(map(int, args.roi.split(",")))
            assert len(fixed_roi) == 4, "ROI must contain 4 coordinates"
        except Exception as e:
            print(f"Invalid --roi format: {e}. Format must be: x1,y1,x2,y2")
            sys.exit(1)

    interactive = (not args.no_roi) and (fixed_roi is None)

    monitor = CrowdMonitoring(
        model_path=args.model,
        crowd_threshold=args.threshold,
        group_distance_px=args.distance,
        dwell_time_seconds=args.dwell,
        alert_cooldown_seconds=args.cooldown,
        roi_coords=fixed_roi,
        interactive_roi=interactive,
        source=args.source,
        image_path=args.output_dir
    )
    monitor.run()
