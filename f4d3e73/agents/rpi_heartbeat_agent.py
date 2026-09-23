
import time
import socket
import requests
from datetime import datetime, timezone

# ---------------------- CONFIG ----------------------
BACKEND_URL = "http://localhost:8080/api/rpi-heartbeat"  # Or your backend's public URL

# Set YOUR Raspberry Pi's location_id and device_id/serial_number
LOCATION_ID = "loc-1780038850556"  # Replace with your actual loc ID from locations.json
DEVICE_ID = "RPI-001"  # Replace with your device_id from locations.json
# OR use SERIAL_NUMBER if you prefer:
# SERIAL_NUMBER = "RPI-SERIAL-123"


def get_local_ip():
    """Get the local IP of this Raspberry Pi"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        print(f"Could not get local IP: {e}")
        return "127.0.0.1"


def get_system_metrics():
    """Get simple system metrics (cpu, memory) - optional"""
    cpu = None
    memory = None
    try:
        # Try to read from /proc/loadavg for CPU (simple)
        with open("/proc/loadavg", "r") as f:
            load_avg = f.read().split()[0]
            cpu = float(load_avg)
        # Try to get memory usage
        with open("/proc/meminfo", "r") as f:
            mem_total = 0
            mem_available = 0
            for line in f:
                parts = line.strip().split()
                if parts[0] == "MemTotal:":
                    mem_total = int(parts[1])
                elif parts[0] == "MemAvailable:":
                    mem_available = int(parts[1])
            if mem_total > 0:
                memory = ((mem_total - mem_available) / mem_total) * 100
    except Exception as e:
        pass
    return cpu, memory


def send_heartbeat():
    """Send a heartbeat to the backend"""
    device_ip = get_local_ip()
    cpu, memory = get_system_metrics()
    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "location_id": LOCATION_ID,
        "device_id": DEVICE_ID,
        # Uncomment if using serial number instead:
        # "serial_number": SERIAL_NUMBER,
        "device_ip": device_ip,
        "status": "online",
        "timestamp": now,
        "cpu": cpu,
        "memory": memory
    }

    try:
        print(f"Sending heartbeat to {BACKEND_URL}...")
        print(f"Payload: {payload}")

        response = requests.post(
            BACKEND_URL,
            json=payload,
            timeout=5,
            headers={"Content-Type": "application/json"}
        )

        response.raise_for_status()  # Raise HTTP errors
        print(f"Heartbeat sent successfully! Response: {response.status_code} - {response.json()}")
        return True

    except requests.exceptions.RequestException as e:
        print(f"Failed to send heartbeat: {str(e)}")
        return False


if __name__ == "__main__":
    print("Raspberry Pi Heartbeat Agent Starting...")
    print(f"Will send heartbeat every 30 seconds to {BACKEND_URL}")
    print("Press Ctrl+C to stop\n")

    while True:
        try:
            send_heartbeat()
        except KeyboardInterrupt:
            print("\nStopping Heartbeat Agent...")
            break
        except Exception as e:
            print(f"Unexpected error in main loop: {e}")

        time.sleep(30)  # Send every 30 seconds
