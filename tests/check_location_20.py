
import socket
import subprocess

def check_device_online(ip: str, timeout: float = 2.0) -> bool:
    if not ip or not ip.strip():
        return False
    ip = ip.strip()
    try:
        socket.inet_aton(ip)
    except socket.error:
        return False
    # Check SSH port first
    for port in (22,):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                print(f" Port 22 (SSH) open on {ip}!")
                return True
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            print(f" Port 22 check failed for {ip}: {e}")
    # If SSH fails, try ping
    try:
        cmd = ["ping", "-c", "1", "-W", str(int(timeout)), ip]
        print(f" Trying to ping {ip} with command: {' '.join(cmd)}")
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout+1)
        if result.returncode == 0:
            print(f" Ping successful for {ip}!")
            return True
        else:
            print(f" Ping failed for {ip}!")
    except Exception as e:
        print(f" Ping check failed for {ip}: {e}")
    return False

test_ip = "192.168.96.26"
print(f"Checking status of {test_ip} (Location 20)...")
status = check_device_online(test_ip, timeout=5)
print(f"\nFINAL STATUS: {'ONLINE' if status else 'OFFLINE'}")
