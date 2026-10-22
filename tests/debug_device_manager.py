#!/usr/bin/env python3

import sys
sys.path.insert(0, '/Users/algofusion/Documents/dnc_backend_v2/orchestrator/Delta/Go/rtsp_http_cla')
import device_manager

print("=== Debugging device_manager ===")

print("\n1. Calling mark_device_heartbeat...")
result = device_manager.mark_device_heartbeat("algo", "192.168.96.78", "loc-test")
print(f"   Result: {result}")

print("\n2. Checking _heartbeats dict:")
import json
print(f"   {json.dumps(device_manager._heartbeats, indent=2)}")

print("\n3. Checking if device_heartbeats.json exists...")
import os
heartbeat_file = os.path.join(os.path.dirname(__file__), "device_heartbeats.json")
print(f"   Path: {heartbeat_file}")
if os.path.exists(heartbeat_file):
    print(f"   File size: {os.path.getsize(heartbeat_file)} bytes")
    with open(heartbeat_file, "r") as f:
        print(f"   File content:\n{json.dumps(json.load(f), indent=2)}")
else:
    print("    File doesn't exist!")

print("\n=== Calling _save_heartbeats() directly! ===")
device_manager._save_heartbeats()
if os.path.exists(heartbeat_file):
    print(f"    Now file exists!")
    with open(heartbeat_file, "r") as f:
        print(f"   File content:\n{json.dumps(json.load(f), indent=2)}")
else:
    print(f"    File still doesn't exist!")

