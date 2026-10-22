import sys
import os
import json
sys.path.insert(0, '/Users/algofusion/Documents/dnc_backend_v2/orchestrator/Delta/Go/rtsp_http_cla')

from server import check_device_online, write_json_atomic, LOCATIONS_JSON

# Step 1: Read the file
with open(LOCATIONS_JSON, 'r') as f:
    locations = json.load(f)

print(f"Loaded {len(locations)} locations")
print("-" * 80)

# Step 2: Check each one
for i, loc in enumerate(locations):
    ip = loc.get("device_ip", "")
    name = loc.get("location", "Unknown")
    old_status = loc.get("device_status", "offline")
    
    if ip:
        is_online = check_device_online(ip)
        new_status = "online" if is_online else "offline"
        locations[i]["device_status"] = new_status
        
        print(f"{name:<30} | IP: {ip:<20} | Old: {old_status:<10} | New: {new_status:<10}")
    else:
        print(f"{name:<30} | No IP")

print("-" * 80)

# Step 3: Write back to file
write_json_atomic(LOCATIONS_JSON, locations)
print("Updated locations.json successfully!")