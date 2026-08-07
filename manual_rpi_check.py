import socket
import subprocess
import json

# Raspberry Pi OUIs
PI_OUIS = {"b8:27:eb", "dc:a6:32", "e4:5f:01"}
RPI_IP = "192.168.96.78"  # Replace with your RPi IP if different

print("="*50)
print("MANUAL RASPBERRY PI STATUS CHECK")
print("="*50)

print("\n1. Checking if IP is valid...")
try:
    socket.inet_aton(RPI_IP)
    octets = list(map(int, RPI_IP.split('.')))
    if len(octets) != 4 or any(o < 0 or o > 255 for o in octets):
        print("   Invalid IP format")
    else:
        print(f"   Valid IP: {RPI_IP}")
except Exception as e:
    print(f"   Invalid IP: {e}")

print("\n2. Checking SSH port (22)...")
try:
    with socket.create_connection((RPI_IP, 22), timeout=3.0):
        print(f"   SSH port 22 is OPEN on {RPI_IP}")
except Exception as e:
    print(f"   SSH port 22 is NOT open: {e}")

print("\n3. Checking ARP table for MAC address...")
try:
    print("   Pinging RPi to populate ARP table...")
    subprocess.run(["ping", "-c", "2", "-W", "1", RPI_IP], capture_output=True, text=True, timeout=5)
    
    arp_result = subprocess.run(["arp", "-n", RPI_IP], capture_output=True, text=True, timeout=5)
    print(f"\n   ARP output:\n{arp_result.stdout}")
    
    mac_found = None
    lines = arp_result.stdout.strip().split('\n')
    for line in lines:
        if RPI_IP in line:
            parts = line.split()
            for part in parts:
                if ':' in part or '-' in part:
                    mac_found = part.replace('-', ':').lower()
                    break
    
    if mac_found:
        mac_prefix = mac_found[:8]
        print(f"\n   Found MAC: {mac_found}")
        print(f"   MAC prefix: {mac_prefix}")
        if mac_prefix in PI_OUIS:
            print(f"   MAC matches RPi OUI!")
        else:
            print(f"   MAC does NOT match RPi OUI! (Expected one of: {', '.join(PI_OUIS)})")
    else:
        print(f"   No MAC found for IP {RPI_IP}")
        
except Exception as e:
    print(f"   Error checking ARP: {e}")

print("\n" + "="*50)
print("Check complete!")
print("="*50)