#!/usr/bin/env python3
import sys
import os
import json
from datetime import datetime, timezone
import time

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import our modules
import device_manager
from server import read_locations, LOCATIONS_JSON, config_cache_lock

print("=== Debugging Device Heartbeat System ===")

# First, let's reset _locations_cache
with config_cache_lock:
    import server
    server._locations_cache = None

# Test 1: Mark a heartbeat
device_id = "RPI-001"
device_ip = "192.168.96.26"
print(f"\nTest 1: Marking heartbeat for {device_id} @ {device_ip}")
result = device_manager.mark_device_heartbeat(device_id, device_ip)
print(f"Mark heartbeat result: {result}")
print(f"_heartbeats contents: {device_manager._heartbeats}")

# Test 2: Check if registered
print(f"\nTest 2: Checking if {device_id} is online")
is_online = device_manager.check_registered_pi_online(device_id, device_ip)
print(f"Is online? {is_online}")

# Test 3: Check locations from file
print(f"\nTest 3: Reading locations.json")
locations = read_locations()
print(f"Number of locations: {len(locations)}")
for loc in locations:
    if loc.get("device_id") == device_id:
        print(f"Found {device_id} in locations:")
        print(f"  device_status: {loc.get('device_status')}")
        print(f"  device_ip: {loc.get('device_ip')}")

# Check server's get_locations logic
print(f"\nTest 4: Simulating /api/locations logic")
for loc in locations:
    if loc.get("device_id") == device_id:
        loc_device_id = str(loc.get("device_id", "")).strip()
        loc_device_ip = str(loc.get("device_ip", "")).strip()
        print(f"Checking loc_device_id: {loc_device_id}, loc_device_ip: {loc_device_ip}")
        
        check_result = device_manager.check_registered_pi_online(loc_device_id, loc_device_ip)
        print(f"check_registered_pi_online result: {check_result}")

print("\n=== End of Debug ===")
