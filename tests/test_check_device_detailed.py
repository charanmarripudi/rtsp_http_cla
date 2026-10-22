import json
import os
import socket
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCATIONS_JSON = os.path.join(BASE_DIR, "locations.json")

def check_device_online_detailed(ip: str, timeout: float = 2.0):
    print(f"\n=== CHECKING IP: {ip} ===")
    if not ip or not ip.strip():
        print(" No IP provided.")
        return False
    ip = ip.strip()
    
    # Validate IP format
    try:
        socket.inet_aton(ip)
        print(" Valid IPv4 address format.")
    except socket.error:
        print(" Invalid IPv4 address format.")
        return False
    
    # Check SSH port (22)
    for port in (22,):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                print(f" SSH port {port} OPEN! Device is ONLINE.")
                return True
        except socket.timeout:
            print(f"  SSH port {port} TIMEOUT.")
        except ConnectionRefusedError:
            print(f" SSH port {port} CONNECTION REFUSED.")
        except OSError as e:
            print(f"  SSH port {port} ERROR: {e}")
    
    # Try ping
    try:
        cmd = ["ping", "-c", "1", "-W", str(int(timeout)), ip]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout + 1)
        if result.returncode == 0:
            print(" PING SUCCESS! Device is ONLINE.")
            return True
        else:
            print(" PING FAILED.")
    except Exception as e:
        print(f"  PING ERROR: {e}")
    
    print(" Device is OFFLINE.")
    return False

def read_locations():
    if not os.path.exists(LOCATIONS_JSON):
        return []
    try:
        with open(LOCATIONS_JSON) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except:
        return []

print("=== DETAILED DEVICE STATUS CHECK ===")
locations = read_locations()
print(f"Total locations: {len(locations)}")

for i, loc in enumerate(locations):
    name = loc.get("location", f"Location {i+1}")
    ip = loc.get("device_ip", "")
    saved_status = loc.get("device_status", "offline")
    print(f"\n--- {name} ---")
    print(f"  Saved status: {saved_status}")
    real_status = check_device_online_detailed(ip, timeout=1.0)
    print(f"  Real status: {'online' if real_status else 'offline'}")
