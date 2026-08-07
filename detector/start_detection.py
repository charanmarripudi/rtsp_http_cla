#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detector_worker import DetectorWorker

if len(sys.argv) < 4:
    print("Usage: start_detection.py <idx> <rtsp> <models> [conf] [iou] [location]")
    sys.exit(1)

camera_idx = sys.argv[1]
rtsp_url = sys.argv[2]
model_names = [m.strip() for m in sys.argv[3].split(",") if m.strip()]
conf = float(sys.argv[4]) if len(sys.argv) > 4 else 0.25
iou = float(sys.argv[5]) if len(sys.argv) > 5 else 0.45
location = sys.argv[6] if len(sys.argv) > 6 else f"Camera {camera_idx}"

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
model_paths = [os.path.join(base, "models", m) for m in model_names]
output_dir = os.path.join(base, "hls", f"stream{camera_idx}_detected")

worker = DetectorWorker(rtsp_url, output_dir, model_paths, conf=conf, iou=iou, location=location)
worker.run()
