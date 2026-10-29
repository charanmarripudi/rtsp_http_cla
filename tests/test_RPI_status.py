import subprocess
import platform

def check_rpi_status(device_id, device_ip):
    """
    Check if Raspberry Pi is online using ping.
    """

    param = "-n" if platform.system().lower() == "windows" else "-c"

    try:
        result = subprocess.run(
            ["ping", param, "1", device_ip],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            status = "ONLINE"
        else:
            status = "OFFLINE"

    except Exception as e:
        status = f"ERROR: {e}"

    return {
        "device_id": device_id,
        "device_ip": device_ip,
        "status": status
    }


# Test
device_id = "algo"
device_ip = "192.168.96.28"  # Replace with your RPi IP

result = check_rpi_status(device_id, device_ip)
print(result)




# import socket


# def check_device_status(device_id, device_ip, port=22):
#     try:
#         sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#         sock.settimeout(3)

#         result = sock.connect_ex((device_ip, port))
#         sock.close()

#         status = "ONLINE" if result == 0 else "OFFLINE"

#         return {
#             "device_id": device_id,
#             "device_ip": device_ip,
#             "status": status
#         }

#     except Exception as e:
#         return {
#             "device_id": device_id,
#             "device_ip": device_ip,
#             "status": "ERROR",
#             "message": str(e)
#         }


# print(check_device_status("algo", "192.168.96.26"))