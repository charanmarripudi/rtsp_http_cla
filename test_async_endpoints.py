import httpx
import json

BASE_URL = "http://localhost:8080/api"


async def test_api_locations():
    async with httpx.AsyncClient() as client:
        print("Testing GET /api/locations (SSH check)...")
        response = await client.get(f"{BASE_URL}/locations")
        print(f"Status: {response.status_code}")
        data = response.json()
        print(f"Found {len(data['locations'])} locations:")
        for loc in data['locations']:
            print(f"  {loc['location']:20} | {loc['device_ip']:15} | {loc['device_status']:8} | {loc.get('status_message', 'N/A')}")


async def test_ping_device():
    print("\nTesting POST /api/devices/ping (akola depot)...")
    async with httpx.AsyncClient() as client:
        payload = {
            "location": "akola depot",
            "device_id": "algo",
            "device_ip": "192.168.96.78"
        }
        response = await client.post(f"{BASE_URL}/devices/ping", json=payload)
        print(f"Status: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_api_locations())
    asyncio.run(test_ping_device())
