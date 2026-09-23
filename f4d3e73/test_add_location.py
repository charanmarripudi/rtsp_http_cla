import requests
import json

BASE_URL = "http://localhost:8080"

# Step 1: Get current locations
print("=== Step 1: Getting current locations ===")
res = requests.get(f"{BASE_URL}/api/locations")
current_locs = res.json()["locations"]
print(json.dumps(current_locs, indent=2))
print("\n")

# Step 2: Add a new location to the payload
new_loc = {
    "id": "loc-test-123",
    "location": "New Test Location",
    "device_id": "TEST001",
    "device_ip": "192.168.1.50",
    "device_status": "offline"
}
payload = current_locs + [new_loc]

print("=== Step 2: Sending POST /api/locations with payload ===")
print(json.dumps(payload, indent=2))
print("\n")

# Step 3: Send request
res = requests.post(
    f"{BASE_URL}/api/locations",
    json=payload,
    headers={"Content-Type": "application/json"}
)
print("=== Step 3: Response ===")
print(f"Status code: {res.status_code}")
print(f"Response body: {json.dumps(res.json(), indent=2)}")
