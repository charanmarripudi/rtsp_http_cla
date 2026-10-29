
import json
import os
import sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# Let's copy the relevant functions to test
def read_streams_conf():
    STREAMS_CONF = os.path.join(BASE_DIR, "streams.conf")
    if not os.path.exists(STREAMS_CONF): return []
    try:
        with open(STREAMS_CONF, "r") as f:
            return [line.strip() for line in f.readlines() if line.strip()]
    except Exception as e:
        print(f"Error reading streams.conf: {e}")
        return []

def read_streams_metadata():
    STREAMS_JSON = os.path.join(BASE_DIR, "streams.json")
    if not os.path.exists(STREAMS_JSON): return []
    try:
        with open(STREAMS_JSON, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error reading streams.json: {e}")
        return []

def get_cameras_with_models():
    CAMERA_MODELS_JSON = os.path.join(BASE_DIR, "camera_models.json")
    urls = read_streams_conf()
    metadata = read_streams_metadata()
    cm = json.load(open(CAMERA_MODELS_JSON)) if os.path.exists(CAMERA_MODELS_JSON) else {}
    
    running = {}  # dummy for test
    
    result = []
    for i, u in enumerate(urls):
        meta = metadata[i] if i < len(metadata) and isinstance(metadata[i], dict) else {}
        result.append({
            "id": i,
            "label": f"Camera {i+1}",
            "location": meta.get("location", f"Location {i+1}"),
            "location_id": meta.get("location_id", ""),
            "device_id": meta.get("device_id", ""),
            "device_ip": meta.get("device_ip", ""),
            "device_status": meta.get("device_status", "offline"),
            "rtsp": u,
            "models": cm.get(str(i), []),
            "detection_active": False
        })
    return result

print("=== STREAMS.JSON CONTENTS ===")
print(json.dumps(read_streams_metadata(), indent=2))

print("\n=== GET_CAMERAS_WITH_MODELS ===")
cameras = get_cameras_with_models()
print(json.dumps(cameras, indent=2))
