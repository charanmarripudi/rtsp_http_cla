import socket
import subprocess
import json

# Raspberry Pi OUIs
PI_OUIS = {"b8:27:eb", "dc:a6:32", "e4:5f:01"}
RPI_IP = "192.168.96.78"  # Replace with your RPi IP if different

print("="*50)
print("BETTER ARP PARSING TEST")
print("="*50)

print(f"\nChecking IP: {RPI_IP}")
print("Running full arp -a to see all entries...")
try:
    full_arp = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5)
    print(f"\nFull ARP table:\n{full_arp.stdout}")
    
    print("\nLooking for IP in ARP table:")
    mac_found = None
    lines = full_arp.stdout.strip().split('\n')
    for line in lines:
        if RPI_IP in line:
            print(f"\nFound line: {repr(line)}")
            parts = line.split()
            print(f"Line parts: {parts}")
            for part in parts:
                if part.count(':') == 5 or part.count('-') == 5:
                    mac_found = part.replace('-', ':').lower()
                    break
    
    if mac_found:
        mac_prefix = mac_found[:8]
        print(f"\n   Found MAC: {mac_found}")
        print(f"   MAC prefix: {mac_prefix}")
        if mac_prefix in PI_OUIS:
            print(f"    MAC matches RPi OUI!")
        else:
            print(f"    MAC does NOT match RPi OUI! (Expected one of: {', '.join(PI_OUIS)})")
    else:
        print(f"    No MAC found for IP {RPI_IP}")
        
except Exception as e:
    print(f"    Error checking ARP: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*50)
print("Check complete!")
print("="*50)