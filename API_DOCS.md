# RTSP-HTTP-CLA API Integration Guide

This document contains all API endpoints, their purposes, and real-world payload examples for the UI team to integrate with the backend.

**Base URL:** `http://<server-ip>:8080`

---

## 1. AI Detection Control
Manage YOLO inference processes for specific cameras.

### Start Detection
*   **Method:** `POST`
*   **Endpoint:** `/api/start` (or `/api/detection/start`)
*   **Payload:**
    ```json
    {
      "camera": "0",
      "rtsp": "rtsp://admin:Algo_1212@192.168.96.231:554/stream1",
      "models": ["ppe_new.pt", "spillage.pt"],
      "conf": 0.25,
      "iou": 0.45,
      "location": "Banglore"
    }
    ```

### Stop Detection
*   **Method:** `POST`
*   **Endpoint:** `/api/stop` (or `/api/detection/stop`)
*   **Payload:**
    ```json
    {
      "camera": "0"
    }
    ```

### Active Status
*   **Method:** `GET`
*   **Endpoint:** `/api/status` (or `/api/detection/status`)
*   **Response:**
    ```json
    {
      "active": ["0"],
      "models": {
        "0": ["ppe_new.pt", "spillage.pt"]
      }
    }
    ```

---

## 2. Stream & Camera Management
Configure RTSP sources and default detection models.

### Get All Streams
*   **Method:** `GET`
*   **Endpoint:** `/api/streams`
*   **Response Example:**
    ```json
    [
      {
        "id": 0,
        "label": "Camera 1",
        "location": "Banglore",
        "location_id": "loc-1779339165181",
        "device_ip": "192.168.96.231",
        "device_status": "online",
        "rtsp": "rtsp://admin:Algo_1212@192.168.96.231:554/stream1",
        "hls_live": "/hls/camera/0/playlist.m3u8"
      }
    ]
    ```

### Save Stream List
*   **Method:** `POST`
*   **Endpoint:** `/api/streams`
*   **Payload:** An array of stream objects (same format as GET response).

### Get Cameras with Assigned Models
*   **Method:** `GET`
*   **Endpoint:** `/api/cameras/with-models`
*   **Description:** Returns cameras enriched with their saved default models.

### Get/Set Camera Default Models
*   **GET** `/api/camera-models` - Returns all mappings.
*   **POST** `/api/camera-models` - Saves mappings. Payload: `{"0": ["ppe_new.pt"], "1": ["fire.pt"]}`

---

## 3. Location & Device Management

### List Locations
*   **Method:** `GET`
*   **Endpoint:** `/api/locations`

### Ping/Health Check
*   **Method:** `POST`
*   **Endpoint:** `/api/devices/ping`
*   **Payload:**
    ```json
    {
      "device_id": "DEV-0",
      "device_ip": "192.168.96.231",
      "location": "Banglore"
    }
    ```

---

## 4. Alerts & History

### Fetch Alerts (JSON)
*   **Method:** `GET`
*   **Endpoint:** `/api/alerts`

### Store Alert (PostgreSQL)
*   **Method:** `POST`
*   **Endpoint:** `/api/alerts/db`
*   **Payload:**
    ```json
    {
      "camera_id": "0",
      "location": "Banglore",
      "type_of_alert": "No Mask",
      "image_path": "cam0_20260520_112123_NO-Mask.jpg"
    }
    ```

### List Alert Images
*   **Method:** `GET`
*   **Endpoint:** `/api/alerts/images`
*   **Response:** Lists all JPGs in the `/hls/alerts/` directory with metadata.

---

## 5. Streaming & Assets

### HLS Playback (Video Player)
Use these URLs in your HLS player (e.g., HLS.js):
*   **Smart Stream:** `/hls/camera/{id}/playlist.m3u8` (Auto-switches AI/Raw)
*   **Raw Only:** `/hls/stream{id}_raw/playlist.m3u8`
*   **AI Only:** `/hls/stream{id}_detected/playlist.m3u8`

### System Models
*   **Endpoint:** `GET /api/models` - Returns all available `.pt` files.
