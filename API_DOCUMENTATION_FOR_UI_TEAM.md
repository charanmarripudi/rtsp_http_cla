# 🚀 Complete API Documentation for UI Team

**Backend Base URLs:**
- **Local Network:** `http://192.168.96.36:8080`
- **Public Tunnel:** `https://raspberrypi.tail486a43.ts.net`

All APIs accept and return `Content-Type: application/json` unless otherwise specified (e.g. video streams / image files).

---

## 📌 Quick Summary of Verified Endpoints

| Category | Method | Endpoint | Description |
| :--- | :---: | :--- | :--- |
| **Streams** | `GET` | `/api/streams` | Fetch all configured RTSP streams & HLS URLs |
| **Streams** | `POST` | `/api/streams` | Save/update stream configurations (Smart Location Merge) |
| **Streams** | `DELETE` | `/api/streams` | Permanently remove a camera stream & stop process |
| **Streams** | `GET/POST` | `/api/streams/fetch` | Filter streams by `location` or `location_id` |
| **Locations** | `GET` | `/api/locations` | List all registered location records |
| **Locations** | `POST` | `/api/locations` | Add or update location records |
| **Locations** | `DELETE` | `/api/locations` | Remove location record & its associated streams |
| **PTZ Cameras** | `GET` | `/api/ptz/cameras` | List configured PTZ ONVIF cameras |
| **PTZ Cameras** | `POST` | `/api/ptz/cameras` | Save/update PTZ cameras list |
| **PTZ Cameras** | `DELETE` | `/api/ptz/cameras/{cam_id}` | Remove a PTZ camera by ID or index |
| **PTZ Control** | `POST` | `/api/ptz/move` | Pan / Tilt / Zoom continuous movement |
| **PTZ Control** | `POST` | `/api/ptz/step` | Single step Pan / Tilt / Zoom nudge |
| **PTZ Control** | `POST` | `/api/ptz/stop` | Stop all PTZ motion immediately |
| **PTZ Presets** | `GET` | `/api/ptz/presets` | Get preset positions for active camera |
| **PTZ Presets** | `POST` | `/api/ptz/presets` | Create new preset position |
| **PTZ Presets** | `DELETE` | `/api/ptz/presets/{token}` | Delete preset position by token |
| **Models** | `GET` | `/api/models` | List all available YOLO `.pt` model files |
| **Models** | `GET` | `/api/model-classes` | Get class names for a model (`?model=ppe_new.pt`) |
| **Models** | `GET` | `/api/camera-models` | Get camera-to-model assignment mapping |
| **Models** | `POST` | `/api/camera-models` | Save camera-to-model assignments |
| **Models** | `GET` | `/api/cameras/with-models` | Get full camera list bundled with assigned models |
| **Thresholds** | `POST` | `/api/update-thresholds` | Update Conf & IoU detection thresholds per camera |
| **Controls** | `GET` | `/api/status` | Get status of raw video stream processes |
| **Controls** | `POST` | `/api/start` | Start FFmpeg stream pipeline for a camera |
| **Controls** | `POST` | `/api/stop` | Stop video stream pipeline for a camera |
| **Detection** | `POST` | `/api/detection/start` | Launch AI object detection worker |
| **Detection** | `POST` | `/api/detection/stop` | Stop AI object detection worker |
| **Detection** | `GET` | `/api/detection/status` | Get AI detection worker status |
| **Devices** | `GET` | `/api/devices` | Get edge device hardware status list |
| **Alerts** | `GET` | `/api/alerts` | Get latest AI violation alerts |
| **Alerts** | `GET` | `/api/alerts/images` | Get alert snapshot image filename list |
| **Analytics** | `POST` | `/api/analytics/by-location` | Get alert analytics aggregated by location |
| **Analytics** | `POST` | `/api/analytics/by-usecase` | Get alert analytics aggregated by model usecase |

---

## 1. 🎥 Camera Streams Management

### 1.1 `GET /api/streams`
Fetches all configured camera streams along with their HLS video playback URLs and model configurations.

- **Query Parameters (Optional):**
  - `location_id` (string): Filter by location ID (`loc-1779875782`)
  - `location` (string): Filter by location name (`MEERUT DEPOT`)

- **Example Response (`HTTP 200`):**
```json
[
  {
    "id": 0,
    "label": "Camera 1",
    "location": "Location 14",
    "location_id": "loc-1779965106740",
    "device_id": "RPI-001",
    "device_ip": "192.168.96.36",
    "device_status": "online",
    "rtsp": "rtsp://admin:@192.168.96.30:554/ch0_0.264",
    "conf": 0.40,
    "iou": 0.45,
    "model_configs": {
      "hansung_ppe_violations.pt": {
        "conf": 0.40,
        "iou": 0.45,
        "enabled_classes": ["NO-Hardhat"]
      }
    },
    "hls_live": "/hls/camera/0/playlist.m3u8",
    "hls_raw": "/hls/stream0_raw/playlist.m3u8",
    "hls_detected": "/hls/stream0_detected/playlist.m3u8"
  }
]
```

---

### 1.2 `POST /api/streams`
Saves or updates camera stream configurations.

- **Headers:** `Content-Type: application/json`
- **Request Body Payload Example:**
```json
[
  {
    "rtsp": "rtsp://admin:Alg0M0nit%4070%23@192.168.96.41:554/stream1",
    "location": "MEERUT DEPOT",
    "location_id": "loc-1779875782",
    "conf": 0.40,
    "iou": 0.45
  }
]
```

- **Example Response (`HTTP 200`):**
```json
[
  {
    "id": 0,
    "label": "Camera 1",
    "location": "MEERUT DEPOT",
    "location_id": "loc-1779875782",
    "rtsp": "rtsp://admin:Alg0M0nit%4070%23@192.168.96.41:554/stream1",
    "hls_live": "/hls/camera/0/playlist.m3u8",
    "hls_raw": "/hls/stream0_raw/playlist.m3u8"
  }
]
```

---

### 1.3 `DELETE /api/streams`
Permanently removes a camera stream, stops its background FFmpeg streaming process, and re-indexes remaining stream IDs contiguous from `0`.

> [!IMPORTANT]
> **UI Integration Note:** Issue this `DELETE` request when user clicks "Remove Camera". **Do NOT send a follow-up `POST /api/streams` after deletion**, as `DELETE` handles full server-side removal and re-indexing.

- **Query Parameters (Recommended):**
  - `id` (integer or string): Camera stream ID (`0`, `1`, `2`)
  - `index` (integer): Stream index (`0`, `1`)
  - `rtsp` (string): RTSP stream URL
  - `location_id` (string): Location ID

- **Example Request:**
```http
DELETE /api/streams?id=0&index=0 HTTP/1.1
Host: 192.168.96.36:8080
```

- **Example Response (`HTTP 200`):**
```json
{
  "status": "deleted",
  "message": "Camera removed successfully."
}
```

---

## 2. 📍 Location Management

### 2.1 `GET /api/locations`
Lists all registered locations.

- **Example Response (`HTTP 200`):**
```json
[
  {
    "id": "loc-1779875782",
    "location": "MEERUT DEPOT",
    "device_id": "1987",
    "serial_number": "",
    "device_ip": "192.168.96.36",
    "device_type": "rpi",
    "device_status": "online"
  }
]
```

---

### 2.2 `POST /api/locations`
Adds or updates location records.

- **Request Body Payload Example:**
```json
[
  {
    "id": "loc-1779875782",
    "location": "MEERUT DEPOT",
    "device_id": "1987",
    "device_ip": "192.168.96.36",
    "device_status": "online"
  }
]
```

- **Example Response (`HTTP 200`):**
```json
{
  "status": "success",
  "locations": [ ... ]
}
```

---

### 2.3 `DELETE /api/locations`
Deletes a location record and automatically stops and purges all streams associated with that location.

- **Query Parameters:**
  - `id` or `location_id` (string): `loc-1779875782`
  - `location` (string): `MEERUT DEPOT`

- **Example Response (`HTTP 200`):**
```json
{
  "status": "deleted",
  "message": "Location removed successfully."
}
```

---

## 3. 🕹️ PTZ Camera Control & Management

### 3.1 `GET /api/ptz/cameras`
Fetches all configured ONVIF PTZ cameras.

- **Example Response (`HTTP 200`):**
```json
[
  {
    "id": "ptz-1788529635-0",
    "label": "PTZ Camera 1",
    "rtsp": "rtsp://admin:@192.168.96.30:554/ch0_0.264",
    "ip": "192.168.96.30",
    "port": 8888,
    "username": "admin",
    "password": "",
    "hls_raw": "/hls/streamptz0_raw/playlist.m3u8"
  }
]
```

---

### 3.2 `POST /api/ptz/cameras`
Saves or updates the list of PTZ cameras.

- **Request Body Payload Example:**
```json
[
  {
    "rtsp": "rtsp://admin:@192.168.96.30:554/ch0_0.264",
    "label": "PTZ Camera 1"
  }
]
```

---

### 3.3 `DELETE /api/ptz/cameras/{cam_id}`
Removes a PTZ camera by ID, index, or RTSP URL.

- **Example Request:** `DELETE /api/ptz/cameras/ptz-1788529635-0`
- **Example Response (`HTTP 200`):**
```json
{
  "status": "success",
  "message": "Camera ptz-1788529635-0 removed",
  "cameras": []
}
```

---

### 3.4 `POST /api/ptz/move`
Continuous PTZ movement control.

- **Request Body Payload Example:**
```json
{
  "ip": "192.168.96.30",
  "port": 8888,
  "username": "admin",
  "password": "",
  "pan": 0.5,
  "tilt": 0.0,
  "zoom": 0.0
}
```
*Note: Values for `pan`, `tilt`, `zoom` range from `-1.0` to `1.0`.*

---

### 3.5 `POST /api/ptz/step`
Single step (nudge) PTZ movement.

- **Request Body Payload Example:**
```json
{
  "ip": "192.168.96.30",
  "port": 8888,
  "direction": "up",
  "step": 0.1
}
```
*Directions: `"up"`, `"down"`, `"left"`, `"right"`, `"zoom_in"`, `"zoom_out"`.*

---

### 3.6 `POST /api/ptz/stop`
Stops all PTZ motion immediately.

- **Request Body Payload Example:**
```json
{
  "ip": "192.168.96.30",
  "port": 8888
}
```

---

## 4. 🤖 AI Models & Detection Configurations

### 4.1 `GET /api/models`
Returns list of available YOLO model filenames on the server.

- **Example Response (`HTTP 200`):**
```json
[
  "ppe_new.pt",
  "nik_ppe_best.pt",
  "hf_ppe_detection.pt",
  "keremberke_ppe_gear.pt",
  "hansung_ppe_violations.pt",
  "fire_smoke.pt"
]
```

---

### 4.2 `GET /api/model-classes`
Returns target detection class names for a given model.

- **Query Parameters:**
  - `model` (string, optional): `ppe_new.pt`

- **Example Response (`HTTP 200`):**
```json
{
  "model": "ppe_new.pt",
  "classes": [
    "Cap-Lamp", "Gloves", "Gum-Boots", "Hard-Hat", "Mask", "NO-Mask",
    "No-Cap-Lamp", "No-Gloves", "No-Gum-Boots", "No-Hard-Hat",
    "No-Saftey-Belt", "No-Saftey-Vest", "Saftey-Belt", "Saftey-Vest"
  ]
}
```

---

### 4.3 `GET /api/camera-models` & `POST /api/camera-models`
Get or save camera-to-model assignments dictionary.

- **GET Response Example (`HTTP 200`):**
```json
{
  "0": ["hansung_ppe_violations.pt"],
  "1": ["ppe_new.pt"]
}
```

- **POST Request Body Example:**
```json
{
  "0": ["hansung_ppe_violations.pt"],
  "1": ["ppe_new.pt"]
}
```

---

### 4.4 `POST /api/update-thresholds`
Dynamically updates Confidence & IoU thresholds for a specific camera.

- **Request Body Payload Example:**
```json
{
  "camera_id": 0,
  "conf": 0.35,
  "iou": 0.45
}
```

- **Example Response (`HTTP 200`):**
```json
{
  "status": "ok"
}
```

---

## 5. ⚡ Video Pipeline & Worker Controls

### 5.1 `GET /api/status`
Checks active raw FFmpeg stream processes status.

- **Example Response (`HTTP 200`):**
```json
{
  "running": [0, 1]
}
```

---

### 5.2 `POST /api/start` & `POST /api/stop`
Start or stop raw video stream pipeline for a specific camera ID.

- **Request Body Payload Example:**
```json
{
  "camera_id": 0
}
```

---

### 5.3 `POST /api/detection/start` & `POST /api/detection/stop`
Start or stop the AI Object Detection Worker.

- **Example Response (`HTTP 200`):**
```json
{
  "status": "started",
  "pid": 261877
}
```

---

## 6. 📱 Devices & System Status

### 6.1 `GET /api/devices`
Fetches edge device hardware status.

- **Example Response (`HTTP 200`):**
```json
[
  {
    "id": "loc-1779875782",
    "device_name": "MEERUT DEPOT",
    "device_id": "1987",
    "device_ip": "192.168.96.36",
    "device_type": "rpi",
    "device_status": "online"
  }
]
```

---

### 6.2 `GET /api/alerts` & `GET /api/alerts/images`
Fetch latest violation alert logs and snapshot images.

- **GET `/api/alerts` Response Example:**
```json
[
  {
    "alert_id": "alt-1780001",
    "timestamp": "2026-09-07T10:30:00Z",
    "location": "MEERUT DEPOT",
    "camera_id": 0,
    "violation": "No-Hard-Hat",
    "image_url": "/api/alerts/images/alert_0_1780001.jpg"
  }
]
```

---

## 📋 Critical UI Integration Rules

1. **Camera Removal:**
   - Always call `DELETE /api/streams?id={stream_id}&index={index}` when user clicks "Remove Camera".
   - Do **NOT** send a `POST /api/streams` after `DELETE`, because `DELETE` handles full server-side removal and re-indexing (`0, 1, 2...`).
   - Immediately update client `localStorage` (`offline_streams`) so refreshing the page (`F5`) never restores the deleted camera.

2. **Camera Addition & Location Saving:**
   - When user adds/saves cameras for Location X, send `POST /api/streams` with `location` or `location_id` included in each camera object. The server will update Location X's cameras while preserving all other locations intact.

3. **Stream URLs:**
   - RAW Stream URL: `http://192.168.96.36:8080/hls/stream{id}_raw/playlist.m3u8`
   - AI Detection Stream URL: `http://192.168.96.36:8080/hls/stream{id}_detected/playlist.m3u8`
   - Live Player URL: `http://192.168.96.36:8080/hls/camera/{id}/playlist.m3u8`
