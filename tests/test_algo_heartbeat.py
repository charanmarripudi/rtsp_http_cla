#!/usr/bin/env python3
"""
Test script to send heartbeat for device "algo"
"""
import requests
import json

BACKEND_URL = "http://localhost:8080/device/heartbeat"
DEVICE_ID = "algo"
DEVICE_IP = "192.168.96.78"

print(f"Testing heartbeat for {DEVICE_ID} @ {DEVICE_IP}")
print()

payload = {
    "device_id": DEVICE_ID,
    "device_ip": DEVICE_IP
}

try:
    response = requests.post(
        BACKEND_URL,
        json=payload,
        timeout=5,
        headers={"Content-Type": "application/json"}
    )
    print(f"POST /device/heartbeat response:")
    print(f"  Status: {response.status_code}")
    print(f"  Body: {json.dumps(response.json(), indent=2)}")
    print()
    
    # Now check status
    status_url = f"http://localhost:8080/device/status?device_id={DEVICE_ID}&device_ip={DEVICE_IP}"
    status_response = requests.get(status_url, timeout=5)
    print(f"GET /device/status response:")
    print(f"  Status: {status_response.status_code}")
    print(f"  Body: {json.dumps(status_response.json(), indent=2)}")
    print()
    
    # Now check locations
    locations_url = "http://localhost:8080/api/locations"
    locations_response = requests.get(locations_url, timeout=5)
    print(f"Checking /api/locations for {DEVICE_ID}:")
    locations = locations_response.json()["locations"]
    for loc in locations:
        if loc.get("device_id") == DEVICE_ID and loc.get("device_ip") == DEVICE_IP:
            print(f"  Found location: {loc['location']} ({loc['id']})")
            print(f"  Status: {loc.get('device_status')}")
            if loc.get('last_communicated_time'):
                print(f"  Last seen: {loc['last_communicated_time']}")
            print()

except Exception as e:
    print(f"Error: {e}")
