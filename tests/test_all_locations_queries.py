#!/usr/bin/env python3
"""
Test /api/locations with all possible query parameters!
"""
import requests
import json

BASE_URL = "http://localhost:8080"


def print_separator(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


test_cases = [
    ("No params", {}),
    ("check_status=False", {"check_status": "false"}),
    ("check_status=True", {"check_status": "true"}),
    ("use_heartbeat=True", {"use_heartbeat": "true"}),
    ("use_heartbeat=True&check_status=false", {"use_heartbeat": "true", "check_status": "false"}),
    ("use_heartbeat=True&check_status=true", {"use_heartbeat": "true", "check_status": "true"}),
]

print_separator("Testing /api/locations with All Query Combinations")
for test_name, params in test_cases:
    print_separator(f"Testing: {test_name}")
    try:
        response = requests.get(
            f"{BASE_URL}/api/locations",
            params=params,
            timeout=10,
        )
        print(f"Response code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            # Print only algo locations
            print(f"Found {len(data.get('locations', []))} locations total")
            print("\n  Devices with device_id='algo':")
            print("  ------------------------------")
            for loc in data.get("locations", []):
                if str(loc.get("device_id", "")).strip() == "algo":
                    print(f"  - {loc['location']} ({loc['id']})")
                    print(f"    Status: {loc.get('device_status')}")
                    print(f"    Status msg: {loc.get('status_message')}")
                    print()
        else:
            print(f"Error: {response.text}")
    except Exception as e:
        print(f" Error: {e}")
