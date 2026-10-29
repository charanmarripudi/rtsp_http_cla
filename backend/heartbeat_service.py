from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
import uvicorn

app = FastAPI()

DEVICES = {}


class Heartbeat(BaseModel):
    location_id: str
    device_id: str
    device_ip: str


@app.post("/device/heartbeat")
def heartbeat(data: Heartbeat):
    key = f"{data.location_id}:{data.device_id}"

    DEVICES[key] = {
        "location_id": data.location_id,
        "device_id": data.device_id,
        "device_ip": data.device_ip,
        "last_communicated_time": datetime.now(timezone.utc),
    }

    return {
        "status": True,
        "message": "heartbeat received"
    }


@app.get("/device/status")
def device_status(location_id: str, device_id: str, device_ip: str):
    key = f"{location_id}:{device_id}"

    device = DEVICES.get(key)

    if not device:
        return {
            "status": "offline",
            "reason": "device not found"
        }

    if device["device_ip"] != device_ip:
        return {
            "status": "offline",
            "reason": "device_ip mismatch"
        }

    last_seen = device["last_communicated_time"]

    is_online = (
        datetime.now(timezone.utc) - last_seen
    ) <= timedelta(seconds=90)

    return {
        "location_id": location_id,
        "device_id": device_id,
        "device_ip": device_ip,
        "status": "online" if is_online else "offline",
        "last_communicated_time": last_seen
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)