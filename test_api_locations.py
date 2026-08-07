import sys
import os
import json
import requests

sys.path.insert(0, '/Users/algofusion/Documents/dnc_backend_v2/orchestrator/Delta/Go/rtsp_http_cla')
BASE_URL = "http://localhost:8080"

# Test 1: Read locations.json directly
with open('/Users/algofusion/Documents/dnc_backend_v2/orchestrator/Delta/Go/rtsp_http_cla/locations.json', 'r') as f:
    locs_json = json.load(f)
print(" locations.json direct read:")
for loc in locs_json:
    print(f"  - {loc['location']}: {loc['device_status']}")

# Test 2: Call /api/locations endpoint
print("\n Calling /api/locations...")
try:
    response = requests.get(f"{BASE_URL}/api/locations", timeout=10)
    if response.status_code == 200:
        api_data = response.json()
        print("\n /api/locations response:")
        for loc in api_data['locations']:
            print(f"  - {loc['location']}: {loc['device_status']}")
    else:
        print(f" Error: {response.status_code}")
        print(response.text)
except Exception as e:
    print(f" Exception calling endpoint: {e}")
