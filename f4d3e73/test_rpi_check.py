import socket
import subprocess
import json
import re
import asyncio
import httpx


def get_mac_address(ip: str) -> str | None:
    """Try to get MAC address from ARP table"""
    try:
        if not ip or not ip.strip():
            return None
        
        # Ping the IP first to populate ARP table
        try:
            subprocess.run(
                ["ping", "-c", "2", "-W", "1", ip],
                capture_output=True,
                text=True,
                timeout=2
            )
        except Exception:
            pass
        
        # Get ARP table
        try:
            arp_output = subprocess.check_output(
                ["arp", "-a", ip],
                text=True,
                timeout=2
            )
            # Parse MAC address from output
            # Patterns like "aa:bb:cc:dd:ee:ff" or "aabb.ccdd.eeff"
            mac_pattern = re.compile(r'([0-9a-fA-F]{1,2}:){5}[0-9a-fA-F]{1,2}|([0-9a-fA-F]{4}\.){2}[0-9a-fA-F]{4}')
            match = mac_pattern.search(arp_output)
            if match:
                mac = match.group(0)
                # Normalize to lowercase xx:xx:xx:xx:xx:xx format
                mac = mac.replace(".", "")
                mac = ":".join([mac[i:i+2] for i in range(0, 12, 2)])
                return mac.lower()
        except Exception as e:
            return None
        
        return None
    except Exception as e:
        return None


def is_raspberry_pi_mac(mac: str) -> bool:
    """Check if MAC address is a Raspberry Pi"""
    # Raspberry Pi MAC prefixes:
    # b8:27:eb (Raspberry Pi Foundation)
    # dc:a6:32 (Raspberry Pi Trading)
    # e4:5f:01 (Raspberry Pi Ltd)
    if not mac:
        return False
    mac_prefixes = [
        "b8:27:eb",
        "dc:a6:32",
        "e4:5f:01"
    ]
    mac = mac.lower()
    for prefix in mac_prefixes:
        if mac.startswith(prefix):
            return True
    return False


# Test all IPs
with open("locations.json", "r") as f:
    locations = json.load(f)

print("="*120)
print(f"{'LOCATION':<20} {'IP':<20} {'SSH?':<8} {'MAC':<18} {'RPI?':<8} {'FINAL STATUS':<12}")
print("="*120)

for loc in locations:
    location_name = loc.get("location", "")
    ip = loc.get("device_ip", "")
    ssh_open = False
    mac = None
    is_rpi = False
    
    if ip:
        try:
            with socket.create_connection((ip, 22), timeout=1.0):
                ssh_open = True
        except Exception:
            pass
        
        mac = get_mac_address(ip)
        is_rpi = is_raspberry_pi_mac(mac)
    
    final_online = ssh_open and is_rpi
    print(f"{location_name:<20} {ip:<20} {ssh_open!s:<8} {mac or 'N/A':<18} {is_rpi!s:<8} {'ONLINE' if final_online else 'OFFLINE':<12}")
