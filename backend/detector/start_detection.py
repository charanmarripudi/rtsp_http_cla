import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detector_worker import DetectorWorker

if len(sys.argv) < 4:
    print("Usage: start_detection.py <idx> <rtsp> <models> [conf] [iou] [location] [model_configs_json]")
    sys.exit(1)

camera_idx = sys.argv[1]
rtsp_url = sys.argv[2]
model_names = [m.strip() for m in sys.argv[3].split(",") if m.strip()]
conf = float(sys.argv[4]) if len(sys.argv) > 4 else 0.40
iou = float(sys.argv[5]) if len(sys.argv) > 5 else 0.45
location = sys.argv[6] if len(sys.argv) > 6 else f"Camera {camera_idx}"

model_configs = {}
if len(sys.argv) > 7 and sys.argv[7].strip():
    try:
        model_configs = json.loads(sys.argv[7])
    except Exception as e:
        print(f"[WARN] Failed to parse model_configs: {e}")

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
model_paths = [os.path.join(base, "models", m) for m in model_names]
output_dir = os.path.join(base, "hls", f"stream{camera_idx}_detected")

worker = DetectorWorker(rtsp_url, output_dir, model_paths, conf=conf, iou=iou, location=location, model_configs=model_configs)
worker.run()
