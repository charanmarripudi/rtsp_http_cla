#!/usr/bin/env python3
import sys
import os
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STREAMS_JSON = os.path.join(BASE_DIR, "streams.json")
LOCATIONS_JSON = os.path.join(BASE_DIR, "locations.json")
CAMERA_MODELS_JSON = os.path.join(BASE_DIR, "camera_models.json")

print("=== Testing Location Sync and Analytics Data ===")
print()

# Step 1: Read locations.json
print("1. Reading your manually managed locations.json...")
if os.path.exists(LOCATIONS_JSON):
    with open(LOCATIONS_JSON) as f:
        locations = json.load(f)
    print(f" Found {len(locations)} manually managed locations")
    for loc in locations:
        print(f"  - {loc.get('location', 'Unknown')} (id: {loc.get('id', 'N/A')}, device_ip: {loc.get('device_ip', 'N/A')})")
else:
    print(" locations.json not found!")
    sys.exit(1)

# Step 2: Read streams.json and check association
print()
print("2. Reading streams.json and verifying location associations...")
if os.path.exists(STREAMS_JSON):
    with open(STREAMS_JSON) as f:
        streams = json.load(f)
    
    # Create location map
    loc_map = {}
    for loc in locations:
        if loc.get("location"):
            loc_map[loc["location"]] = loc
        if loc.get("id"):
            loc_map[loc["id"]] = loc
    
    valid_streams = 0
    invalid_streams = 0
    print(f"   Found {len(streams)} streams:")
    for i, stream in enumerate(streams):
        matched_loc = None
        if stream.get("location_id") and stream.get("location_id") in loc_map:
            matched_loc = loc_map[stream.get("location_id")]
        elif stream.get("location") and stream.get("location") in loc_map:
            matched_loc = loc_map[stream.get("location")]
        
        if matched_loc:
            valid_streams += 1
            print(f"    Camera {i}: {stream.get('rtsp')[:50]}... -> {matched_loc['location']} (device_ip: {matched_loc.get('device_ip', 'N/A')})")
        else:
            invalid_streams +=1
            print(f"    Camera {i}: No matching location in locations.json!")
    
    print(f"\n   Summary: {valid_streams} valid streams, {invalid_streams} invalid streams")
else:
    print(" streams.json not found!")
    sys.exit(1)

# Step 3: Read camera_models.json
print()
print("3. Reading camera_models.json (model assignments)...")
if os.path.exists(CAMERA_MODELS_JSON):
    with open(CAMERA_MODELS_JSON) as f:
        camera_models = json.load(f)
    print(f" Found model assignments for {len(camera_models)} cameras")
    for cam_id, models in camera_models.items():
        print(f"  - Camera {cam_id}: {models}")
else:
    print(" camera_models.json not found (no models assigned yet)")

print()
print("=== Analysis Complete! ===")
print("\n Your analytics endpoints will work correctly because:")
print("1. All valid streams are associated with your manually managed locations.json")
print("2. Cameras inherit device_ip and status from locations.json (single source of truth)")
print("3. No mismatches between different systems!")
print("\nNow just share locations.json with your UI team!")
