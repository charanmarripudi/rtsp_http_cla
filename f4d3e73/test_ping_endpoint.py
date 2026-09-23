import requests
import json

BASE_URL = "http://localhost:8080"

# Test POST /api/devices/ping for akola depot
test_payload = {
    "location": "akola depot",
    "device_id": "algo",
    "device_ip": "192.168.96.78"
}

print("📡 Testing POST /api/devices/ping...")
print(f"Payload: {json.dumps(test_payload, indent=2)}")

try:
    response = requests.post(f"{BASE_URL}/api/devices/ping", json=test_payload, timeout=10)
    print(f"\n Status code: {response.status_code}")
    print(f"\n Response: {json.dumps(response.json(), indent=2)}")
    
    # Now check locations.json
    with open('/Users/algofusion/Documents/dnc_backend_v2/orchestrator/Delta/Go/rtsp_http_cla/locations.json', 'r') as f:
        locs = json.load(f)
        for loc in locs:
            if loc['location'] == 'akola depot':
                print(f"\n akola depot status in locations.json: {loc['device_status']}")
                break

except Exception as e:
    print(f" Error: {e}")
