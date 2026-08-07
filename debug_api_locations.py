#!/usr/bin/env python3

import requests
import json
import sys

BASE_URL = "http://localhost:8080"

def print_response(name, params):
    try:
        print(f"\n{'='*70}")
        print(f"  Testing: GET /api/locations with params = {repr(params)}")
        print(f"{'='*70}")
        response = requests.get(
            f"{BASE_URL}/api/locations",
            params=params,
            timeout=10
        )
        print(f"  Status code: {response.status_code}")
        if response.status_code != 200:
            print(f"  Error: {response.text}")
            return

        data = response.json()
        print(f"\n  Locations found: {len(data.get('locations', []))}")
        print("\n  --- Locations with device_id='algo' ---")
        for loc in data.get('locations', []):
            if str(loc.get('device_id', '')).strip() == 'algo':
                print(f"  Location: {loc.get('location')}")
                print(f"  Device ID: {loc.get('device_id')}")
                print(f"  Device IP: {loc.get('device_ip')}")
                print(f"  Device Status: {loc.get('device_status')}")
                print(f"  Last Communicated: {loc.get('last_communicated_time')}")
                print(f"  Status Message: {loc.get('status_message')}")
                print("  ---")
    except Exception as e:
        print(f"\n  ERROR: {e}")
        print(f"  Traceback:", sys.exc_info()[2])


def main():
    print("=== Debugging /api/locations endpoint ===")
    
    # Test all parameter combinations
    test_cases = [
        ("No params", {}),
        ("check_status=False", {"check_status": "false"}),
        ("check_status=True", {"check_status": "true"}),
        ("use_heartbeat=True", {"use_heartbeat": "true"}),
        ("use_heartbeat=True&check_status=False", {"use_heartbeat": "true", "check_status": "false"}),
        ("use_heartbeat=True&check_status=True", {"use_heartbeat": "true", "check_status": "true"})
    ]

    for name, params in test_cases:
        print_response(name, params)


if __name__ == "__main__":
    main()

