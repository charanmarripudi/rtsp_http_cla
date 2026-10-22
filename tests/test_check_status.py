#!/usr/bin/env python3
import sys
import os
import asyncio
import json

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import our modules
import device_manager
from server import read_locations, config_cache_lock

print("=== Testing check_status=True Path ===")

# First, mark a heartbeat
device_id = "RPI-001"
device_ip = "192.168.96.26"
print(f"Marking heartbeat for {device_id} @ {device_ip}")
device_manager.mark_device_heartbeat(device_id, device_ip)

# Reset cache
with config_cache_lock:
    import server
    server._locations_cache = None

# Read locations
locations = read_locations()

# Simulate the async check_all_statuses function!
async def test_check_all():
    import server
    from datetime import datetime, timezone
    
    results = []
    for loc in locations:
        ip = loc.get("device_ip", "")
        loc_device_id = str(loc.get("device_id", "")).strip()
        
        if loc_device_id == device_id:
            print(f"\nChecking {loc.get('location')} ({loc_device_id})")
            
            # Check device_manager
            if loc_device_id and ip:
                is_online_dm = device_manager.check_registered_pi_online(loc_device_id, ip)
                print(f"  device_manager check: {is_online_dm}")
                
                if is_online_dm:
                    print("  ✓ Should return online!")
                    results.append((True, "test"))
                    continue
        
        # For other locations, just add placeholder
        results.append((False, "test"))
    print("\n=== End of Test ===")

# Run the async test
asyncio.run(test_check_all())
