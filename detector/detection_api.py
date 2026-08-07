"""
detection_api.py  — standalone detection FastAPI sub-app.

BUG FIXED: previously passed str(cam) as the rtsp_url argument,
which is just the camera index, not the actual RTSP URL.
Now the caller must supply the rtsp_url in the request body.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import subprocess

BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(BASE_DIR, "models")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# camera_id+model -> process
active_processes = {}


@app.get("/api/models")
def get_models():
    if not os.path.exists(MODEL_DIR):
        return []
    return [f for f in os.listdir(MODEL_DIR) if f.endswith(".pt")]


@app.post("/api/detection/start")
def start_detection(data: dict):
    cam      = data["camera"]
    model    = data["model"]
    rtsp_url = data["rtsp"]        # ← FIXED: caller supplies the RTSP URL

    key = f"{cam}_{model}"

    if key in active_processes:
        return {"status": "already_running", "key": key}

    cmd = [
        "python3",
        os.path.join(BASE_DIR, "detector/start_detection.py"),
        str(cam),
        rtsp_url,   # ← FIXED: was str(cam) before
        model,
    ]

    p = subprocess.Popen(cmd)
    active_processes[key] = p

    return {"status": "started", "key": key}


@app.post("/api/detection/stop")
def stop_detection(data: dict):
    cam   = data["camera"]
    model = data["model"]
    key   = f"{cam}_{model}"

    if key in active_processes:
        try:
            active_processes[key].terminate()
        except Exception:
            pass
        del active_processes[key]

    return {"status": "stopped", "key": key}


@app.get("/api/detection/status")
def detection_status():
    return {"active": list(active_processes.keys())}