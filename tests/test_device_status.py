import socket
import sys
import json
import subprocess
sys.path.insert(0, '/Users/algofusion/Documents/dnc_backend_v2/orchestrator/Delta/Go/rtsp_http_cla')

from server import check_device_online

# First, let's ping 192.168.96.78 to populate the ARP table
print("Pinging 192.168.96.78 to populate ARP table...")
subprocess.run(["ping", "-c", "2", "-W", "1", "192.168.96.78"], capture_output=True, text=True)

# Now let's check the ARP table for it
print("\nChecking ARP table for 192.168.96.78:")
arp_result = subprocess.run(["arp", "-n", "192.168.96.78"], capture_output=True, text=True)
print(arp_result.stdout)

# Now let's test all entries
print("\nTesting all entries in locations.json:")
with open('locations.json', 'r') as f:
    locations = json.load(f)

for loc in locations:
    ip = loc.get('device_ip', '')
    loc_name = loc.get('location', 'Unknown')
    status = check_device_online(ip)
    print(f" {loc_name:<20} | IP: {ip:<20} | Online: {status}")