
import json
import os
import subprocess
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCATIONS_JSON = os.path.join(BASE_DIR, "locations.json")

def check_device_online(ip, timeout=2.0):
    import socket
    if not ip or not ip.strip():
        return False
    ip = ip.strip()
    try:
        socket.inet_aton(ip)
    except socket.error:
        return False
    for port in (22,):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            continue
    try:
        cmd = ["ping", "-c", "1", "-W", str(int(timeout)), ip]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout+1)
        return result.returncode == 0
    except:
        return False

print("=== TESTING DEVICE ONLINE/OFFLINE ===")
with open(LOCATIONS_JSON, "r") as f:
    locations = json.load(f)
    for loc in locations:
        ip = loc.get("device_ip", "")
        status = "online" if check_device_online(ip, timeout=1) else "offline"
        print(f"  Location: {loc['location']:20} | Device IP: {ip:16} | Status: {status:8} | Saved status: {loc.get('device_status', 'N/A'):8}")
