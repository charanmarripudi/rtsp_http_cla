"""
Vehicle Speed & Counting Runner Script
Supports:
  1. Live RTSP Streams (e.g. rtsp://192.168.96.72:8554/petrolbunk)
  2. Local Video Files (e.g. .mp4, .avi, .mov, .mkv) with optional auto-loop
  3. Live Webcams (e.g. index 0)

Usage:
    # 1. Test with RTSP Stream:
    python3 run_speed_tracker.py --source rtsp://192.168.96.72:8554/petrolbunk

    # 2. Test with Local Video File (with continuous looping):
    python3 run_speed_tracker.py --source /path/to/tank_truck_video.mp4 --loop

    # 3. Test using parameters from config.json:
    python3 run_speed_tracker.py
"""

import os
import sys
import json
import argparse
import time
import cv2
from pathlib import Path

# Add current dir to path
CURR_DIR = Path(__file__).resolve().parent
if str(CURR_DIR) not in sys.path:
    sys.path.insert(0, str(CURR_DIR))

from speed_tracker import VehicleSpeedTracker


def load_config(config_path: str) -> dict:
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    return {}


def is_rtsp_stream(src: str) -> bool:
    s = str(src).lower().strip()
    return s.startswith("rtsp://") or s.startswith("rtsps://") or s.startswith("http://") or s.startswith("https://")


def main():
    parser = argparse.ArgumentParser(description="Terminal Vehicle Speed & Counting Monitor (Video & RTSP)")
    parser.add_argument("--source", type=str, default=None, help="RTSP URL, video file path (.mp4/.avi/.mov), or camera index")
    parser.add_argument("--config", type=str, default=str(CURR_DIR / "config.json"), help="Path to config.json")
    parser.add_argument("--model", type=str, default=None, help="YOLO model weights path")
    parser.add_argument("--limit", type=float, default=None, help="Speed limit in km/h (default: 10.0)")
    parser.add_argument("--distance", type=float, default=None, help="Distance between Line A and Line B in meters (default: 20.0)")
    parser.add_argument("--imgsz", type=int, default=None, help="Inference resolution (default: 416 for Pi CPU)")
    parser.add_argument("--skip", type=int, default=None, help="YOLO frame skipping factor (e.g. 2 = run inference every 2nd frame for low CPU)")
    parser.add_argument("--loop", action="store_true", help="Loop video continuously if testing with a video file")
    parser.add_argument("--output", type=str, default=None, help="Optional output video file to save result (.mp4)")
    parser.add_argument("--no-display", action="store_true", help="Run in headless mode without cv2.imshow")

    args = parser.parse_args()
    cfg = load_config(args.config)

    # 1. Resolve Input Source (CLI -> config.json -> fallback)
    if args.source:
        source = args.source
    elif cfg.get("source_type") == "video" and cfg.get("video_file_path"):
        source = cfg.get("video_file_path")
    else:
        source = cfg.get("rtsp_url", "0")

    # 2. Resolve Model Weights
    if args.model:
        cfg["model_path"] = args.model
    elif not os.path.exists(cfg.get("model_path", "")):
        candidate_paths = [
            str(CURR_DIR.parent / "models" / "vehicles.pt"),
            str(CURR_DIR / "vehicles.pt"),
            "/home/algo/rtsp_http_cla/models/vehicles.pt",
            str(CURR_DIR.parent / "models" / "vehicle_speed.pt"),
            str(CURR_DIR / "models" / "vehicle_speed.pt"),
            "/home/algo/rtsp_http_cla/models/vehicle_speed.pt",
            str(CURR_DIR.parent / "Vehicle_Detector_Tracking_Counter" / "best.pt"),
            str(CURR_DIR.parent / "models" / "yolov8n.pt")
        ]
        for cp in candidate_paths:
            if os.path.exists(cp):
                cfg["model_path"] = cp
                break

    # 3. Apply CLI Overrides
    if args.limit is not None:
        cfg["speed_limit_kmh"] = args.limit
    if args.distance is not None:
        cfg["road_distance_meters"] = args.distance
    if args.imgsz is not None:
        cfg["imgsz"] = args.imgsz
    if args.skip is not None:
        cfg["frame_skip"] = args.skip

    should_loop = args.loop or cfg.get("loop_video", False)
    is_live_stream = is_rtsp_stream(source)

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  TERMINAL TANK TRUCK SPEED & COUNT MONITOR")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Mode                 : {'LIVE RTSP STREAM' if is_live_stream else 'VIDEO FILE / WEBCAM'}")
    print(f"  Source Input         : {source}")
    print(f"  Auto-Loop Video      : {'ENABLED' if (should_loop and not is_live_stream) else 'DISABLED'}")
    print(f"  YOLO Model Weights   : {cfg.get('model_path')}")
    print(f"  Speed Limit          : {cfg.get('speed_limit_kmh')} km/h")
    print(f"  Gate-Gantry Distance : {cfg.get('road_distance_meters')} meters")
    print(f"  Inference Image Size : {cfg.get('imgsz')} px (Low-CPU optimized)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    tracker_engine = VehicleSpeedTracker(cfg)

    def open_capture():
        if str(source).isdigit():
            return cv2.VideoCapture(int(source))
        elif is_live_stream:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|max_delay;500000|timeout;5000000"
            c = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
            c.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return c
        else:
            return cv2.VideoCapture(source)

    cap = open_capture()
    if not cap.isOpened():
        print(f"❌ [ERROR] Could not open source: {source}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if fps <= 0 or fps > 120:
        fps = 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"[STREAM] Connected. Resolution: {width}x{height} @ {fps:.1f} FPS" + (f" ({total_frames} total frames)" if total_frames > 0 else ""))
    print("💡 [CONTROLS] Press 'Q' or ESC to exit | 'P' to pause/resume | 'S' to save snapshot\n")

    writer = None
    if args.output:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(args.output, fourcc, fps, (width, height))
        print(f"[RECORD] Saving annotated video output to: {args.output}")

    frame_count = 0
    t_start = time.time()
    paused = False

    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and not args.no_display

    try:
        while True:
            if not paused:
                ret, frame = cap.read()
                if not ret or frame is None:
                    if should_loop and not is_live_stream:
                        print("\n[LOOP] Video reached end, looping back to start...")
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    else:
                        print("[STREAM] Stream finished or disconnected.")
                        break

                frame_count += 1
                
                # For video files: compute exact video time from frame index / FPS to be 100% independent of CPU speed
                # For RTSP live streams: use real system wall-clock time
                if is_live_stream:
                    curr_time = time.time()
                else:
                    curr_time = frame_count / fps

                # Process frame with speed tracker engine
                annotated_frame, violations = tracker_engine.process_frame(frame, curr_time)

                # Overlay live FPS stats
                elapsed = time.time() - t_start
                proc_fps = frame_count / max(0.001, elapsed)
                cv2.putText(annotated_frame, f"FPS: {proc_fps:.1f}", (width - 110, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2, cv2.LINE_AA)

                if writer:
                    writer.write(annotated_frame)

            if has_display:
                try:
                    cv2.imshow("Terminal Vehicle Speed & Count Monitor", annotated_frame)
                    key = cv2.waitKey(1 if not paused else 30) & 0xFF
                    if key == ord("q") or key == 27:
                        print("[USER] Quit requested.")
                        break
                    elif key == ord("p"):
                        paused = not paused
                        print(f"[USER] {'PAUSED' if paused else 'RESUMED'}")
                    elif key == ord("s"):
                        snap_name = f"manual_snapshot_{int(time.time())}.jpg"
                        cv2.imwrite(snap_name, annotated_frame)
                        print(f"[SNAPSHOT] Saved manual screenshot: {snap_name}")
                except Exception:
                    has_display = False
            else:
                # In headless / SSH mode, sleep minimally so CPU doesn't busy-wait on pause
                if paused:
                    time.sleep(0.05)
    finally:
        cap.release()
        if writer:
            writer.release()
        try:
            if has_display:
                cv2.destroyAllWindows()
        except Exception:
            pass
        print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"  FINAL SUMMARY: Total Vehicles Counted = {sum(tracker_engine.counts.values())}")
        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        for lbl, cnt in tracker_engine.counts.items():
            print(f"  • {lbl}: {cnt}")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")


if __name__ == "__main__":
    main()
