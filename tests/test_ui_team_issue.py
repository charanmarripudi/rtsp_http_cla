
import json
import requests

BASE_URL = "http://localhost:8080"  # use localhost for testing

print("=== TESTING /api/streams/fetch with location='Delhi Terminal' ===")
fetch_response = requests.post(f"{BASE_URL}/api/streams/fetch", json={"location": "Delhi Terminal"}, timeout=10)
print(f"STATUS CODE: {fetch_response.status_code}")
print("RESPONSE:")
print(json.dumps(fetch_response.json(), indent=2))

print("\n=== TESTING /api/start with UI team's payload ===")
start_payload = {
    "camera": 5,
    "models": ["ppe_new.pt"],
    "rtsp": "rtsp://admin:Algo_1212@192.168.96.231:554/stream1",
    "conf": 0.25,
    "iou": 0.45
}
start_response = requests.post(f"{BASE_URL}/api/start", json=start_payload, timeout=30)
print(f"STATUS CODE: {start_response.status_code}")
print("RESPONSE:")
print(json.dumps(start_response.json(), indent=2))

print("\n=== GETTING /api/streams ===")
streams_response = requests.get(f"{BASE_URL}/api/streams", timeout=10)
print("RESPONSE:")
print(json.dumps(streams_response.json(), indent=2))

print("\n=== TESTING /api/analytics/by-usecase with usecase='ppe_new.pt' ===")
analytics_response = requests.post(f"{BASE_URL}/api/analytics/by-usecase", json={"usecase": "ppe_new.pt"}, timeout=10)
print(f"STATUS CODE: {analytics_response.status_code}")
print("RESPONSE:")
print(json.dumps(analytics_response.json(), indent=2))
