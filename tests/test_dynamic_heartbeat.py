#!/usr/bin/env python3
"""
Test dynamic heartbeat: send every 10 seconds
"""
import requests
import time
from datetime import datetime, timezone

BACKEND_URL = "http://localhost:8080/device/heartbeat"
DEVICE_ID = "algo"
DEVICE_IP = "192.168.96.78"
INTERVAL = 10  # seconds

print("=" * 60)
print(f"Dynamic Heartbeat Test for {DEVICE_ID}")
print(f"Backend: {BACKEND_URL}")
print(f"Interval: {INTERVAL} seconds")
print("=" * 60)
print()

try:
    while True:
        payload = {"device_id": DEVICE_ID, "device_ip": DEVICE_IP}
        print(f"[{datetime.now(timezone.utc)}] Sending heartbeat...")
        
        try:
            response = requests.post(BACKEND_URL, json=payload, timeout=5)
            print(f"  Response: {response.status_code} - {response.json()['message']}")
        except Exception as e:
            print(f"  Error: {e}")
        
        time.sleep(INTERVAL)
except KeyboardInterrupt:
    print("\nStopping...")
