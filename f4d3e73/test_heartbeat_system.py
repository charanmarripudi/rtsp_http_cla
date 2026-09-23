#!/usr/bin/env python3
import sys
import os
import time

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import device_manager
from server import read_locations

print("=== Testing Heartbeat System ===")

# Test 1: Mark heartbeat for RPI-001
device_id = "RPI-001"
device_ip = "192.168.96.26"

print(f"\nTest 1: Marking heartbeat for {device_id}")
result = device_manager.mark_device_heartbeat(device_id, device_ip)
print(f"Success: {result}")

# Test 2: Get device status
print(f"\nTest 2: Getting status for {device_id}")
status = device_manager.get_device_status(device_id)
print(f"Status: {status}")

# Test 3: Get all statuses
print(f"\nTest 3: Getting all device statuses")
all_statuses = device_manager.get_all_device_statuses()
print(f"All statuses: {all_statuses}")

# Test 4: Check if device is online
print(f"\nTest 4: Checking if {device_id} is online with IP {device_ip}")
is_online = device_manager.check_registered_pi_online(device_id, device_ip)
print(f"Is online: {is_online}")

print("\n=== Testing Complete! ===")
