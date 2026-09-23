#!/usr/bin/env python3
"""
Simple Raspberry Pi Heartbeat Agent
Sends heartbeats to /device/heartbeat every 30 seconds
"""
import time
import socket
import requests
from datetime import datetime, timezone

# ---------------------- CONFIG ----------------------
# Set these based on your setup
BACKEND_URL = "http://charan.tail486a43.ts.net:8080/device/heartbeat"
DEVICE_ID = "algo"  # Must match device_id in locations.json
OVERRIDE_IP = None  # Change to "192.168.96.78" if you want to force this specific IP
# -------------------------------------------------


def get_local_ip():
    """Get the local IP of this Raspberry Pi"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        print(f"Could not get local IP: {e}")
        return "127.0.0.1"


def send_heartbeat():
    """Send a heartbeat to the backend"""
    device_ip = OVERRIDE_IP if OVERRIDE_IP else get_local_ip()
    payload = {
        "device_id": DEVICE_ID,
        "device_ip": device_ip
    }

    try:
        print(f"[{datetime.now(timezone.utc)}] Sending heartbeat to {BACKEND_URL}")
        print(f"Payload: {payload}")

        response = requests.post(
            BACKEND_URL,
            json=payload,
            timeout=5,
            headers={"Content-Type": "application/json"}
        )

        response.raise_for_status()
        print(f"[{datetime.now(timezone.utc)}] Heartbeat sent successfully! Status: {response.status_code}")
        return True

    except requests.exceptions.RequestException as e:
        print(f"[{datetime.now(timezone.utc)}] Failed to send heartbeat: {str(e)}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Raspberry Pi Simple Heartbeat Agent Starting...")
    print(f"Device ID: {DEVICE_ID}")
    print(f"Backend URL: {BACKEND_URL}")
    print("=" * 60)
    print()

    while True:
        try:
            send_heartbeat()
        except KeyboardInterrupt:
            print("\nStopping Heartbeat Agent...")
            break
        except Exception as e:
            print(f"Unexpected error: {e}")

        time.sleep(30)  # Send every 30 seconds
