# RTSP-HTTP-CLA API Endpoints Documentation
# This file serves as a reference for the UI team.

# BASE URL: http://<server-ip>:8080

ENDPOINTS = {
    "AI_DETECTION": [
        {
            "name": "Start Detection",
            "endpoint": "POST /api/start",
            "payload": {
                "camera": "0",
                "rtsp": "rtsp://admin:Algo_1212@192.168.96.231:554/stream1",
                "models": ["ppe_new.pt", "spillage.pt"],
                "conf": 0.25,
                "iou": 0.45,
                "location": "Banglore"
            },
            "description": "Starts YOLO inference on the specified camera."
        },
        {
            "name": "Stop Detection",
            "endpoint": "POST /api/stop",
            "payload": {
                "camera": "0"
            },
            "description": "Stops YOLO inference and reverts to the raw stream."
        },
        {
            "name": "Detection Status",
            "endpoint": "GET /api/status",
            "payload": None,
            "description": "Returns a list of cameras currently running detection and their models."
        }
    ],
    "STREAM_CONFIG": [
        {
            "name": "Get Streams",
            "endpoint": "GET /api/streams",
            "payload": null,
            "description": "Returns all configured RTSP streams and their HLS URLs. Supports optional query params: ?location_id=XYZ or ?location=Name"
        },
        {
            "name": "Fetch Filtered Streams (UI Choice)",
            "endpoint": "POST /api/streams/fetch",
            "payload": {
                "location": "Kundapura",
                "location_id": "loc-123"
            },
            "description": "Flexible endpoint to fetch cameras for a specific location using a JSON body. Also supports GET with query params."
        },
        {
            "name": "Save Streams",
            "endpoint": "POST /api/streams",
            "payload": [
                {
                    "rtsp": "rtsp://admin:Algo_1212@192.168.96.231:554/stream1",
                    "location": "Banglore",
                    "location_id": "loc-1779339165181",
                    "device_id": "",
                    "device_ip": "",
                    "device_status": "online"
                }
            ],
            "description": "Updates the list of managed streams."
        },
        {
            "name": "Delete Camera",
            "endpoint": "DELETE /api/streams?rtsp=rtsp://...&location=...&location_id=...",
            "payload": null,
            "description": "Permanently removes a camera stream. Requires matching RTSP URL, location name, and location ID for security."
        },
        {
            "name": "Cameras with Models",
            "endpoint": "GET /api/cameras/with-models",
            "payload": None,
            "description": "Returns cameras enriched with assigned models and detection status."
        }
    ],
    "LOCATIONS_DEVICES": [
        {
            "name": "Get Locations",
            "endpoint": "GET /api/locations",
            "payload": None,
            "description": "Returns all physical site locations."
        },
        {
            "name": "Save Locations",
            "endpoint": "POST /api/locations",
            "payload": [
                {
                    "id": "loc-1779339165181",
                    "location": "Banglore",
                    "device_id": "",
                    "device_ip": "",
                    "device_status": "online"
                }
            ],
            "description": "Updates the full list of physical site locations (Bulk Overwrite)."
        },
        {
            "name": "Add Single Location (URL Params)",
            "endpoint": "POST /api/locations?location=Andhra&device_id=9&device_ip=123.456.789.345",
            "payload": null,
            "description": "Appends a single location to the list using URL query parameters (Safe Append)."
        },
        {
            "name": "Check Device Status (POST)",
            "endpoint": "POST /api/devices/ping",
            "payload": {
                "device_id": "DEV-001",
                "device_ip": "192.168.1.50",
                "location": "Mangalore"
            },
            "description": "Probes a device and updates its status in the system (JSON body)."
        },
        {
            "name": "Check Device Status (GET)",
            "endpoint": "GET /api/devices/ping?device_id=DEV-001&device_ip=192.168.1.50&location=Mangalore",
            "payload": null,
            "description": "Probes a device and updates its status in the system (URL params)."
        }
    ],
    "ALERTS": [
        {
            "name": "Get Alerts (JSON)",
            "endpoint": "GET /api/alerts",
            "payload": None,
            "description": "Fetches recent alert logs from the local JSON store."
        },
        {
            "name": "Get Alerts (DB)",
            "endpoint": "GET /api/alerts/db",
            "payload": None,
            "description": "Fetches the 50 most recent alerts from the PostgreSQL database."
        },
        {
            "name": "Store Alert (DB)",
            "endpoint": "POST /api/alerts/db",
            "payload": {
                "camera_id": "0",
                "location": "Banglore",
                "type_of_alert": "No Mask",
                "image_path": "hls/alerts/cam0_20260520_112123_NO-Mask.jpg"
            },
            "description": "Saves an alert record directly to the database."
        },
        {
            "name": "Get Alert Images",
            "endpoint": "GET /api/alerts/images",
            "payload": None,
            "description": "Lists all alert image filenames and metadata."
        }
    ],
    "HLS_STREAMING": [
        {
            "name": "Smart Playlist",
            "endpoint": "GET /hls/camera/{cam_id}/playlist.m3u8",
            "description": "Automatically switches between AI-detected and Raw streams."
        },
        {
            "name": "Raw Playlist",
            "endpoint": "GET /hls/stream{id}_raw/playlist.m3u8",
            "description": "Direct access to the raw camera feed."
        },
        {
            "name": "Detected Playlist",
            "endpoint": "GET /hls/stream{id}_detected/playlist.m3u8",
            "description": "Direct access to the AI-processed feed with bounding boxes."
        }
    ]
}
