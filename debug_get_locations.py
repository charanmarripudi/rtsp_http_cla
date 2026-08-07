import json
import os
import time

# Copy-paste the necessary functions from server.py to debug
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCATIONS_JSON = os.path.join(BASE_DIR, "locations.json")

# Mock read_locations
def read_locations():
    if not os.path.exists(LOCATIONS_JSON):
        return []
    try:
        with open(LOCATIONS_JSON) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except:
        return []

# Mock write_json_atomic
def write_json_atomic(path, data):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    try:
        os.replace(tmp_path, path)
    except OSError:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        try:
            os.remove(tmp_path)
        except OSError:
            pass

# Simulate the request
location = "Balsore Depot"
device_id = "5123"
device_ip = "192.168.96.78"
device_status = None
id = None

print("=== DEBUG: get_locations simulation ===")
print(f"Input params: location={repr(location)}, device_id={repr(device_id)}, device_ip={repr(device_ip)}")

print("\n=== Step 1: Read current locations ===")
current_locations = read_locations()
print(f"Got {len(current_locations)} locations")

new_entry = {
    "id": str(id or f"loc-{int(time.time())}").strip(),
    "location": str(location).strip(),
    "device_id": str(device_id or "").strip(),
    "device_ip": str(device_ip or "").strip(),
    "device_status": "offline"
}
print(f"\n=== Step 2: Created new entry ===")
print(json.dumps(new_entry, indent=2))

# Check if exists
exists = False
print("\n=== Step 3: Checking for existing entry ===")
for i, loc in enumerate(current_locations):
    print(f"  Checking loc {i}: id={repr(loc.get('id'))}, name={repr(loc.get('location'))}")
    if loc.get("location") == new_entry["location"] or loc.get("id") == new_entry["id"]:
        print(f"  → MATCH! Updating index {i}")
        current_locations[i] = new_entry
        exists = True
        break

if not exists:
    print("  → No match, appending new entry")
    current_locations.append(new_entry)

final_locations = current_locations
print(f"\n=== Step 4: Final locations count: {len(final_locations)} ===")

print("\n=== Step 5: Saving to file ===")
print(f"Writing to {LOCATIONS_JSON}")
write_json_atomic(LOCATIONS_JSON, final_locations)

print("\n=== Step 6: Verifying file after save ===")
with open(LOCATIONS_JSON) as f:
    saved = json.load(f)
print(f"Saved {len(saved)} locations")
for loc in saved:
    if loc.get("location") == "Balsore Depot":
        print(" SUCCESS: Balsore Depot found in saved file!")
        print(json.dumps(loc, indent=2))
        break
else:
    print(" ERROR: Balsore Depot NOT found in saved file!")
