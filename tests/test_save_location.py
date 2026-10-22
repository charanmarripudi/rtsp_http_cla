
import requests

BASE_URL = "http://localhost:8080"

# Test 1: Save a new Raspberry Pi location (query params mode)
print("Test 1: Saving Raspberry Pi location via query params")
params = {
    "id": "loc-test-01",
    "location": "Test Pi Location",
    "device_id": "RPI-TEST-01",
    "device_ip": "192.168.96.78",
    "device_type": "Raspberry Pi"
}

res = requests.post(f"{BASE_URL}/api/locations", params=params, timeout=10)
print(f"Save response status: {res.status_code}")
print(f"Save response body: {res.text}\n")

# Test 2: Get locations and check if our test location is there
print("Test 2: Checking if saved location exists")
get_res = requests.get(f"{BASE_URL}/api/locations", timeout=10)
locations = get_res.json().get("locations")
found = False
for loc in locations:
    if loc.get("id") == "loc-test-01":
        found = True
        print("Found test location:")
        print(f"  ID: {loc.get('id')}")
        print(f"  Location: {loc.get('location')}")
        print(f"  Device ID: {loc.get('device_id')}")
        print(f"  Device Type: {loc.get('device_type')}")
        print(f"  Device Status: {loc.get('device_status')}")
        print(f"  Status Message: {loc.get('status_message')}")
if not found:
    print("Test location not found!\n")
    
# Test 3: Send heartbeat and verify status updates
print("\nTest 3: Sending heartbeat to test location")
heartbeat_payload = {
    "location_id": "loc-test-01",
    "device_id": "RPI-TEST-01",
    "device_ip": "192.168.96.78",
    "status": "online"
}
heartbeat_res = requests.post(f"{BASE_URL}/api/rpi-heartbeat", json=heartbeat_payload, timeout=10)
print(f"Heartbeat status: {heartbeat_res.status_code}")
print(f"Heartbeat response: {heartbeat_res.text}")

# Test 4: Get locations again to verify status is ONLINE now
print("\nTest 4: Checking status after heartbeat")
get_after_res = requests.get(f"{BASE_URL}/api/locations", timeout=10)
locations_after = get_after_res.json().get("locations")
for loc in locations_after:
    if loc.get("id") == "loc-test-01":
        print("Updated test location:")
        print(f"  Status: {loc.get('device_status')}")
        print(f"  Message: {loc.get('status_message')}")
