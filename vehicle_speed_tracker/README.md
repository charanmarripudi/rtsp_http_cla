# 🚛 Terminal Tank Truck Speed & Count Tracking Engine

A low-CPU, high-efficiency vehicle detection, tracking, counting, and **10 km/h speed limit estimation** module designed for terminal gate-to-gantry monitoring on Raspberry Pi 4 and edge systems.

---

## 📌 Architecture & Features

```
[Camera Stream (Gate -> Gantry)]
           │
           ▼
[1. Low-CPU YOLO Detection] ──────────► Detects Trucks, Tank Trucks, Cars, Pickups (imgsz=416/320)
           │
           ▼
[2. Centroid Ground Tracker] ─────────► Tracks tyre ground contact point (x, y) across frames (<0.5ms CPU)
           │
           ▼
[3. Dual-Line Timing Gate] ───────────► Line A (Gate Entry @ t1) ──► Line B (Gantry Road @ t2)
           │
           ▼
[4. Speed Calculation] ───────────────► Speed (km/h) = (Distance in meters / (t2 - t1)) × 3.6
           │
           ├── Speed > 10.0 km/h ────► 🚨 OVER SPEED (Red Box + Snapshot saved to /alerts)
           └── Speed <= 10.0 km/h ───► ✅ NORMAL SPEED (Green Box)
```

---

## ⚙️ Configuration (`config.json`)

```json
{
  "rtsp_url": "rtsp://192.168.96.72:8554/petrolbunk",
  "model_path": "../Vehicle_Detector_Tracking_Counter/best.pt",
  "speed_limit_kmh": 10.0,
  "road_distance_meters": 20.0,
  "confidence_threshold": 0.30,
  "imgsz": 416,
  "line_a": {
    "name": "Gate Entry",
    "x1_pct": 15, "y1_pct": 40,
    "x2_pct": 85, "y2_pct": 40
  },
  "line_b": {
    "name": "Gantry Road",
    "x1_pct": 15, "y1_pct": 75,
    "x2_pct": 85, "y2_pct": 75
  },
  "allowed_classes": ["truck", "car", "pickup truck", "bike", "tank truck", "vehicle"],
  "alerts_dir": "alerts",
  "enable_anpr_hook": false
}
```

---

## 🚀 How to Run

### 1. Run on an RTSP Stream:
```bash
python3 run_speed_tracker.py --source rtsp://192.168.96.72:8554/petrolbunk --limit 10.0 --distance 20.0
```

### 2. Run on a Test Video File:
```bash
python3 run_speed_tracker.py --source /path/to/tank_truck_video.mp4 --limit 10.0 --distance 25.0
```

### 3. Run Headless (on Raspberry Pi background service):
```bash
python3 run_speed_tracker.py --source rtsp://192.168.96.72:8554/petrolbunk --no-display
```

---

## 🏎️ Low-CPU Optimization on Raspberry Pi 4

1. **Lightweight Centroid Tracker**:
   - Replaced heavy DeepSORT / Kalman filter tracking with sub-millisecond Euclidean centroid tracking.
2. **Adaptive `imgsz`**:
   - Default `imgsz=416` or `320` keeps inference CPU time under ~350ms on Pi 4 CPU cores.
3. **Pure Mathematical Speed Timing**:
   - Zero floating point GPU overhead: computes exact timestamp deltas ($\Delta t = t_2 - t_1$) on line crossings.

---

## 🔍 FastANPR Integration Hook

In [speed_tracker.py](file:///Users/algofusion/Documents/dnc_backend_v2/orchestrator/Delta/Go/vehicle_speed_tracker/speed_tracker.py#L210), when a vehicle commits a speed violation, the `extract_license_plate_hook` function is called:

```python
def extract_license_plate_hook(self, cropped_vehicle: np.ndarray) -> Optional[str]:
    # Hook for FastANPR inference
    # import fast_anpr
    # return fast_anpr.read_plate(cropped_vehicle)
    return None
```
