
import time
import requests
from datetime import datetime, timezone

BACKEND_URL = "http://localhost:8080/api/rpi-heartbeat"

payload = {
    "location_id": "loc-1780038850556",
    "device_id": "RPI-001",
    "device_ip": "192.168.96.26",
    "status": "online",
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "cpu": 10.5,
    "memory": 45.2
}

print("Sending test heartbeat...")
print(f"Payload: {payload}\n")

try:
    response = requests.post(
        BACKEND_URL,
        json=payload,
        timeout=5,
        headers={"Content-Type": "application/json"}
    )

    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
except Exception as e:
    print(f"Error: {e}")

# Now check /api/locations to verify status updated!
print("\n\nChecking /api/locations to verify status...")
try:
    get_response = requests.get("http://localhost:8080/api/locations", timeout=5)
    locations = get_response.json().get("locations", [])
    for loc in locations:
        if loc.get("id") == "loc-1780038850556":
            print(f"\nLocation 20 Status: {loc.get('device_status')}")
            print(f"Status Message: {loc.get('status_message')}")
except Exception as e:
    print(f"Error checking locations: {e}")
