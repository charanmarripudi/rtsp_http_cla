import time
import socket
import requests

BACKEND_URL = "http://127.0.0.1:8000/device/heartbeat"

DEVICE_ID = "RPI001"
LOCATION_ID = "LOC001"


def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


while True:
    payload = {
        "location_id": LOCATION_ID,
        "device_id": DEVICE_ID,
        "device_ip": get_ip()
    }

    try:
        response = requests.post(
            BACKEND_URL,
            json=payload,
            timeout=5
        )

        print("Heartbeat Sent")
        print(response.json())

    except Exception as e:
        print("Heartbeat Failed:", e)

    time.sleep(30)