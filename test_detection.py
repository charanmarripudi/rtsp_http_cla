#!/usr/bin/env python3
"""
test_detection.py — Standalone detection tester for RTSP streams

Run this INDEPENDENTLY (no cleanstart.sh, no server.py needed).
Opens an OpenCV window showing live detections from one or more YOLO models.

USAGE:
  # List available streams and models
  python3 test_detection.py --list

  # Test single model on camera 0
  python3 test_detection.py --cam 0 --models ppe_new.pt

  # Test multiple models simultaneously on camera 0
  python3 test_detection.py --cam 0 --models ppe_new.pt fire_smoke.pt

  # Test all models on camera 1 with custom thresholds
  python3 test_detection.py --cam 1 --models ppe_new.pt fire_smoke.pt sand_ext_chocks.pt firehose.pt --conf 0.3 --iou 0.5

  # Use a direct RTSP URL instead of camera index
  python3 test_detection.py --rtsp "rtsp://admin:pass@192.168.1.10:554/stream" --models ppe_new.pt

KEYBOARD SHORTCUTS (in the OpenCV window):
  Q or ESC  — quit
  S         — save current frame as screenshot (saved to test_screenshots/)
  +/-       — increase/decrease confidence threshold by 0.05
"""

import argparse
import os
import sys
import time
import threading
import queue
import cv2
import numpy as np
from datetime import datetime

# ── Resolve paths relative to this script ────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR   = os.path.join(BASE_DIR, "models")
STREAMS_CONF = os.path.join(BASE_DIR, "streams.conf")
SCREENSHOTS  = os.path.join(BASE_DIR, "test_screenshots")


def load_streams():
    if not os.path.exists(STREAMS_CONF):
        return []
    with open(STREAMS_CONF) as f:
        return [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]


def list_available():
    streams = load_streams()
    models  = [f for f in os.listdir(MODELS_DIR) if f.endswith(".pt")] if os.path.exists(MODELS_DIR) else []

    print("\n── STREAMS (from streams.conf) ──────────────────────")
    if streams:
        for i, url in enumerate(streams):
            print(f"  cam {i}: {url}")
    else:
        print("  (no streams.conf found)")

    print("\n── MODELS (from models/) ────────────────────────────")
    if models:
        for m in sorted(models):
            print(f"  {m}")
    else:
        print("  (no .pt files found in models/)")
    print()


# ── YOLO loader ───────────────────────────────────────────────────────────────
def load_models(model_names: list) -> list:
    from ultralytics import YOLO
    loaded = []
    for name in model_names:
        path = os.path.join(MODELS_DIR, name)
        if not os.path.exists(path):
            print(f"[ERROR] Model not found: {path}")
            sys.exit(1)
        print(f"[test]  Loading {name}…", end=" ", flush=True)
        loaded.append((name.replace(".pt", ""), YOLO(path)))
        print("ready")
    return loaded


# ── Multi-model inference on a single frame ───────────────────────────────────
def run_all_models(frame, models, conf, iou):
    """
    Run every model on `frame` and draw ALL bounding boxes simultaneously.
    Each model's boxes use a different colour palette offset.
    Returns annotated frame + dict of detection counts per model.
    """
    try:
        from ultralytics.utils.plotting import Annotator, colors

        annotated = frame.copy()
        ann       = Annotator(annotated, line_width=2)
        counts    = {}

        for model_idx, (model_name, model) in enumerate(models):
            results = model.predict(
                frame,
                conf=conf,
                iou=iou,
                imgsz=640,
                verbose=False,
            )
            r   = results[0]
            cnt = 0
            if r.boxes is not None:
                for box in r.boxes:
                    cls_id   = int(box.cls[0])
                    conf_val = float(box.conf[0])
                    label    = f"[{model_name}] {r.names[cls_id]} {conf_val:.2f}"
                    xyxy     = box.xyxy[0].cpu().numpy().tolist()
                    color    = colors(cls_id + model_idx * 50, True)
                    ann.box_label(xyxy, label, color=color)
                    cnt += 1
            counts[model_name] = cnt

        return ann.result(), counts

    except Exception as e:
        print(f"[test] inference error: {e}")
        return frame, {}


# ── Draw HUD overlay ─────────────────────────────────────────────────────────
def draw_hud(frame, fps, conf, iou, counts, model_names):
    h, w = frame.shape[:2]
    overlay = frame.copy()

    # Semi-transparent top bar
    cv2.rectangle(overlay, (0, 0), (w, 38), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    # FPS + thresholds
    cv2.putText(frame, f"FPS:{fps:.1f}  Conf:{conf:.2f}  IoU:{iou:.2f}",
                (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 160), 2)

    # Per-model detection count (bottom-left)
    y = h - 12
    for name in reversed(model_names):
        cnt  = counts.get(name, 0)
        text = f"{name}: {cnt} det"
        cv2.putText(frame, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0, 200, 140), 1, cv2.LINE_AA)
        y -= 20

    # Keyboard hints (bottom-right)
    hints = "Q=quit  S=screenshot  +/-=conf"
    tw, _ = cv2.getTextSize(hints, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)[0], 0
    cv2.putText(frame, hints, (w - tw[0] - 8, h - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (100, 100, 100), 1, cv2.LINE_AA)

    return frame


# ── Capture thread ────────────────────────────────────────────────────────────
class RTSPCapture:
    def __init__(self, url):
        self.url          = url
        self._latest      = None
        self._lock        = threading.Lock()
        self._stop        = threading.Event()
        self._ok          = True
        self._thread      = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        cap = cv2.VideoCapture(self.url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        while not self._stop.is_set():
            ret, frame = cap.read()
            if not ret:
                print("[test] Stream read failed — reconnecting in 3s…")
                cap.release()
                time.sleep(3)
                cap = cv2.VideoCapture(self.url)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                continue
            with self._lock:
                self._latest = frame
        cap.release()

    def read(self):
        with self._lock:
            return self._latest.copy() if self._latest is not None else None

    def stop(self):
        self._stop.set()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Standalone multi-model YOLO detection tester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--list",   action="store_true", help="List streams and models then exit")
    parser.add_argument("--cam",    type=int, default=0,  help="Camera index from streams.conf (default: 0)")
    parser.add_argument("--rtsp",   type=str, default=None, help="Direct RTSP URL (overrides --cam)")
    parser.add_argument("--models", nargs="+", required=False, help="Model filename(s) e.g. ppe_new.pt fire_smoke.pt")
    parser.add_argument("--conf",   type=float, default=0.25, help="Confidence threshold (default: 0.25)")
    parser.add_argument("--iou",    type=float, default=0.45, help="IoU threshold (default: 0.45)")
    parser.add_argument("--fps",    type=int,   default=15,   help="Target display FPS (default: 15)")
    parser.add_argument("--no-window", action="store_true", help="Run headless (print stats to console only)")

    args = parser.parse_args()

    if args.list:
        list_available()
        return

    if not args.models:
        print("[ERROR] --models is required. Use --list to see available models.")
        parser.print_help()
        sys.exit(1)

    # ── Resolve RTSP URL ──────────────────────────────────────────────────
    if args.rtsp:
        rtsp_url = args.rtsp
    else:
        streams = load_streams()
        if not streams:
            print(f"[ERROR] No streams in {STREAMS_CONF}. Use --rtsp to specify a URL directly.")
            sys.exit(1)
        if args.cam >= len(streams):
            print(f"[ERROR] Camera index {args.cam} out of range (have {len(streams)} stream(s)).")
            sys.exit(1)
        rtsp_url = streams[args.cam]

    print(f"\n[test] Stream  : {rtsp_url}")
    print(f"[test] Models  : {args.models}")
    print(f"[test] Conf={args.conf}  IoU={args.iou}  FPS cap={args.fps}")
    print(f"[test] Window  : {'headless' if args.no_window else 'OpenCV (press Q to quit)'}\n")

    # ── Load models ───────────────────────────────────────────────────────
    models      = load_models(args.models)
    model_names = [n for n, _ in models]

    # ── Start capture ─────────────────────────────────────────────────────
    capture = RTSPCapture(rtsp_url)
    print("[test] Waiting for first frame…")
    while capture.read() is None:
        time.sleep(0.2)
    print("[test] Stream connected. Starting detection loop.\n")

    os.makedirs(SCREENSHOTS, exist_ok=True)

    conf          = args.conf
    iou           = args.iou
    frame_interval = 1.0 / args.fps
    last_t        = 0.0
    fps_counter   = 0
    fps_timer     = time.time()
    current_fps   = 0.0
    last_counts   = {n: 0 for n in model_names}

    window_name = f"RTSP Detection Test — {', '.join(model_names)}"
    if not args.no_window:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, 1280, 720)

    try:
        while True:
            # Rate-limit display
            now = time.time()
            if (now - last_t) < frame_interval:
                time.sleep(0.005)
                continue
            last_t = now

            frame = capture.read()
            if frame is None:
                continue

            # Run all models
            annotated, counts = run_all_models(frame, models, conf, iou)
            last_counts       = counts

            # FPS calc
            fps_counter += 1
            elapsed      = time.time() - fps_timer
            if elapsed >= 1.0:
                current_fps = fps_counter / elapsed
                fps_counter = 0
                fps_timer   = time.time()
                # Console stats (always)
                det_summary = "  ".join(f"{n}:{c}" for n, c in counts.items())
                print(f"[test] FPS:{current_fps:.1f}  Conf:{conf:.2f}  |  {det_summary}")

            if not args.no_window:
                display = draw_hud(annotated, current_fps, conf, iou, last_counts, model_names)
                cv2.imshow(window_name, display)

                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), ord("Q"), 27):   # Q or ESC
                    break
                elif key in (ord("s"), ord("S")):     # Save screenshot
                    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
                    path = os.path.join(SCREENSHOTS, f"detection_{ts}.jpg")
                    cv2.imwrite(path, display)
                    print(f"[test] Screenshot saved: {path}")
                elif key == ord("+"):
                    conf = min(0.99, round(conf + 0.05, 2))
                    print(f"[test] Conf → {conf:.2f}")
                elif key == ord("-"):
                    conf = max(0.01, round(conf - 0.05, 2))
                    print(f"[test] Conf → {conf:.2f}")

    except KeyboardInterrupt:
        print("\n[test] Interrupted.")

    finally:
        capture.stop()
        if not args.no_window:
            cv2.destroyAllWindows()
        print("[test] Done.")


if __name__ == "__main__":
    main()