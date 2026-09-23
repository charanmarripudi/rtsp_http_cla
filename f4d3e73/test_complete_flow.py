#!/usr/bin/env python3
"""
Complete test flow: simulate server restart, send heartbeat, check all endpoints
"""
import requests
import time
import json
from datetime import datetime, timezone

BASE_URL = "http://localhost:8080"
DEVICE_ID = "algo"
DEVICE_IP = "192.168.96.78"


def print_separator(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


print_separator("Starting Complete Test Flow")

print(f"Base URL: {BASE_URL}")
print(f"Testing Device: {DEVICE_ID} @ {DEVICE_IP}")

# First, check if server is reachable
try:
    print_separator("Step 1: Check if server is running")
    response = requests.get(f"{BASE_URL}/docs", timeout=5, allow_redirects=False)
    if response.status_code in [200, 404, 307]:
        print(" Server is running!")
    else:
        print(" Server not responding! Please start server.py first!")
        exit(1)
except Exception as e:
    print(f" Error connecting to server: {e}")
    print("Please start server.py first!")
    exit(1)

# Step 2: Send heartbeat
print_separator("Step 2: Send Heartbeat to /device/heartbeat")
heartbeat_payload = {"device_id": DEVICE_ID, "device_ip": DEVICE_IP}
try:
    response = requests.post(
        f"{BASE_URL}/device/heartbeat",
        json=heartbeat_payload,
        timeout=5,
        headers={"Content-Type": "application/json"}
    )
    print(f"Heartbeat response code: {response.status_code}")
    print(f"Heartbeat response: {json.dumps(response.json(), indent=2)}")
    if response.status_code == 200 and response.json()["status"]:
        print(" Heartbeat received!")
    else:
        print(" Heartbeat failed!")
except Exception as e:
    print(f" Error sending heartbeat: {e}")

# Step 3: Check device status
print_separator("Step 3: Check Device Status via /device/status")
try:
    status_response = requests.get(
        f"{BASE_URL}/device/status",
        params={"device_id": DEVICE_ID, "device_ip": DEVICE_IP},
        timeout=5
    )
    print(f"Status response code: {status_response.status_code}")
    print(f"Status response: {json.dumps(status_response.json(), indent=2)}")
    if status_response.status_code == 200 and status_response.json()["device_status"] == "online":
        print(" Device is online!")
    else:
        print(" Device is offline!")
except Exception as e:
    print(f" Error checking device status: {e}")

# Step 4: Check locations (default check_status=False)
print_separator("Step 4: Check Locations via /api/locations")
try:
    locations_response = requests.get(
        f"{BASE_URL}/api/locations",
        timeout=5
    )
    print(f"Locations response code: {locations_response.status_code}")
    locations_data = locations_response.json()
    found_algo = False
    for loc in locations_data["locations"]:
        if loc.get("device_id") == DEVICE_ID and loc.get("device_ip") == DEVICE_IP:
            found_algo = True
            print(f" Found location '{loc['location']}' with device {DEVICE_ID}")
            print(f"   Status: {loc.get('device_status')}")
            if "last_communicated_time" in loc:
                print(f"   Last seen: {loc['last_communicated_time']}")
            if "status_message" in loc:
                print(f"   Status message: {loc['status_message']}")
            print()
    if not found_algo:
        print(" Did not find device {DEVICE_ID} in locations!")
except Exception as e:
    print(f" Error checking locations: {e}")

print_separator("Step 5: Reminder")
print("- We do NOT modify locations.json (status is stored in memory only)")
print("- If you restart the server, heartbeats are lost - need to send again!")
print("- If changes are not showing, RESTART THE SERVER!")
