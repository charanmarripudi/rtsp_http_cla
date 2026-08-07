import requests
import json

BASE_URL = "http://localhost:8080"

endpoints = [
    "/api/locations",
    "/api/status",
    "/api/streams",
    "/api/alerts"
]

for endpoint in endpoints:
    print(f"\ Testing {endpoint}...")
    try:
        response = requests.get(f"{BASE_URL}{endpoint}", timeout=10)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            # print(json.dumps(data, indent=2)[:500] + "...") # Limit output
            if isinstance(data, dict):
                keys = data.keys()
                print(f"Keys: {list(keys)}")
                if 'locations' in data:
                    print(f"Found {len(data['locations'])} locations")
                if 'streams' in data:
                    print(f"Found {len(data['streams'])} streams")
                if 'running' in data:
                    print(f"Running detections: {data['running']}")
            elif isinstance(data, list):
                print(f"Found {len(data)} items")
        else:
            print(f"Error: {response.text}")
    except Exception as e:
        print(f"Exception: {e}")
