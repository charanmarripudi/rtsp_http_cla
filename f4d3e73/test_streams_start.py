
import os
import json
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STREAMS_CONF = os.path.join(BASE_DIR, "streams.conf")
STREAMS_JSON = os.path.join(BASE_DIR, "streams.json")

def read_streams_conf():
    if not os.path.exists(STREAMS_CONF):
        return []
    try:
        with open(STREAMS_CONF, "r") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
        print("=== STREAMS CONF ===")
        for i, line in enumerate(lines):
            print(f"  {i}: {line}")
        return lines
    except Exception as e:
        print(f"Error reading streams.conf: {e}")
        return []

print(read_streams_conf())
