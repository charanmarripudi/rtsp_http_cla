import socket
import subprocess
import platform
from datetime import datetime

# Sample RPi devices
devices = [
    {
        "device_id": "algo",
        "device_ip": "192.168.96.78"
    }
]


def ping_device(ip):
    """
    Returns True if device is online else False
    """

    # Windows uses -n, Linux/Mac uses -c
    param = "-n" if platform.system().lower() == "windows" else "-c"

    command = ["ping", param, "1", ip]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        return result.returncode == 0

    except Exception as e:
        print(f"Ping error for {ip}: {e}")
        return False


# Check all devices
for device in devices:

    device_id = device["device_id"]
    device_ip = device["device_ip"]

    is_online = ping_device(device_ip)

    status = "ONLINE" if is_online else "OFFLINE"

    print({
        "device_id": device_id,
        "device_ip": device_ip,
        "status": status,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })