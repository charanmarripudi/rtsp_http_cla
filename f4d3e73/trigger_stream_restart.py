
import requests
import json

BASE_URL = "http://localhost:8080"

print("=== GETTING CURRENT STREAMS ===")
try:
    response = requests.get(f"{BASE_URL}/api/streams", timeout=10)
    response.raise_for_status()
    streams = response.json()
    print(json.dumps(streams, indent=2))
    
    print("\n=== SAVING STREAMS TO TRIGGER RESTART ===")
    save_response = requests.post(f"{BASE_URL}/api/streams", json=streams, timeout=30)
    save_response.raise_for_status()
    print(json.dumps(save_response.json(), indent=2))
    
except Exception as e:
    print(f"ERROR: {e}")
