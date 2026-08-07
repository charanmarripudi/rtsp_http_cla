import asyncio
from server import determine_device_online_status_async

async def main():
    loc = {
        "id": "loc-1779975956008",
        "location": "Location 16",
        "device_id": "algo",
        "serial_number": "",
        "device_ip": "192.168.96.78",
        "device_type": ""
    }
    
    print("Testing determine_device_online_status_async for 192.168.96.78...")
    res = await determine_device_online_status_async(loc, check_network=True)
    print("Result:", res)

if __name__ == "__main__":
    asyncio.run(main())
