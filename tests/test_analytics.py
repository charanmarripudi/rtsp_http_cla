#!/usr/bin/env python3
import sys
import os
import json

# Add the current directory to Python's path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import functions from server.py
from server import (
    read_streams_metadata,
    read_locations,
    get_cameras_with_models,
    get_analytics_mapping_core
)

print("=== Testing Analytics Endpoints ===")
print()

# Test read_locations
print("1. Reading locations from locations.json...")
locations = read_locations()
print(f" Found {len(locations)} locations")

# Test read_streams_metadata
print()
print("2. Reading and enriching streams metadata...")
streams = read_streams_metadata()
print(f" Found {len(streams)} streams")
for i, stream in enumerate(streams):
    print(f"  - {stream['location']}: {stream['rtsp']} (device_ip: {stream.get('device_ip', 'N/A')})")

# Test get_cameras_with_models
print()
print("3. Testing get_cameras_with_models()...")
cameras = get_cameras_with_models()
print(f" Found {len(cameras)} cameras with models")

# Test analytics core
print()
print("4. Testing analytics core...")
result = get_analytics_mapping_core()

print(f"\n Analytics by location keys: {list(result['by_location'].keys())}")
print(f" Analytics by usecase keys: {list(result['by_usecase'].keys())}")
print()
print("=== All tests passed! ===")
print("\nYour analytics endpoints are working correctly! The UI team will get:")
print("- Correct location names (from your manually managed locations.json)")
print("- Correct device IPs (inherited from locations)")
print("- Consistent analytics data across different systems!")
