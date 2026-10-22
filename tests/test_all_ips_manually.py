import socket
import subprocess
import json

with open("/Users/algofusion/Documents/dnc_backend_v2/orchestrator/Delta/Go/rtsp_http_cla/locations.json", "r") as f:
    locations = json.load(f)

print("="*100)
print(f"{'LOCATION':<20} {'IP':<20} {'SSH OPEN?':<15} {'STATUS':<10}")
print("="*100)

for loc in locations:
    location_name = loc.get("location", "")
    ip = loc.get("device_ip", "")
    ssh_open = False
    if ip:
        try:
            with socket.create_connection((ip, 22), timeout=1.0):
                ssh_open = True
        except Exception as e:
            pass
    
    status = "online" if ssh_open else "offline"
    print(f"{location_name:<20} {ip:<20} {ssh_open!s:<15} {status:<10}")

print("="*100)
print("\nIf an IP shows 'online' but you think it shouldn't, that's because:")
print("1. That IP is actually assigned to a device that IS powered on and has SSH port 22 open!")
print("2. It might be a different device (like your laptop, another computer, etc.)!")
