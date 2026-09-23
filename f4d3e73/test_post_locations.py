import requests
import json

BASE_URL = "http://localhost:8080"

print("=== TEST 1: POST via Query Params (Single Location) ===")
# Test adding via query params
params = {
    "location": "TestQueryParamLocation",
    "device_id": "TEST-QP-001",
    "device_ip": "192.168.1.55"
}
res = requests.post(
    f"{BASE_URL}/api/locations",
    params=params
)
print(f"Status code: {res.status_code}")
try:
    print(f"Response: {json.dumps(res.json(), indent=2)}")
except Exception as e:
    print(f"Error parsing JSON: {e}")
    print(f"Raw response: {res.text}")
print("\n" + "="*50 + "\n")

print("=== TEST 2: POST via JSON Body (All Locations) ===")
# First get current locations
res = requests.get(f"{BASE_URL}/api/locations")
current = res.json()["locations"]
print(f"Got {len(current)} current locations")

# Add a new location
new_loc = {
    "id": "loc-test-json-001",
    "location": "TestJSONBodyLocation",
    "device_id": "TEST-JSON-001",
    "device_ip": "192.168.1.66"
}
payload = current + [new_loc]

# Send POST
res = requests.post(
    f"{BASE_URL}/api/locations",
    json=payload,
    headers={"Content-Type": "application/json"}
)
print(f"Status code: {res.status_code}")
try:
    print(f"Response: {json.dumps(res.json(), indent=2)}")
except Exception as e:
    print(f"Error parsing JSON: {e}")
    print(f"Raw response: {res.text}")
print("\n")
