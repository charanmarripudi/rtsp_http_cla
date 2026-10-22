import hmac
import os
import threading
import time
import json
from datetime import datetime, timezone


DEVICE_HEARTBEAT_TTL = int(os.getenv("DEVICE_HEARTBEAT_TTL", "90"))
DEVICE_HEARTBEAT_TOKEN = os.getenv("DEVICE_HEARTBEAT_TOKEN", "")

# Path to persistent storage file
_HEARTBEAT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "device_heartbeats.json"
)

_heartbeat_lock = threading.Lock()
_heartbeats = {}  # Key: device_id, Value: dict with device_id, device_ip, location_id, last_communicated_time, device_status


def _load_heartbeats():
    """Load heartbeats from file on module load"""
    global _heartbeats
    if os.path.exists(_HEARTBEAT_FILE):
        try:
            with open(_HEARTBEAT_FILE, "r") as f:
                _heartbeats = json.load(f)
        except Exception:
            _heartbeats = {}

def _save_heartbeats():
    """Save heartbeats to file"""
    try:
        with open(_HEARTBEAT_FILE, "w") as f:
            json.dump(_heartbeats, f, indent=2)
    except Exception:
        pass

# Load heartbeats on module load
_load_heartbeats()


def validate_device_token(token):
    if not DEVICE_HEARTBEAT_TOKEN:
        return False
    return hmac.compare_digest(str(token or "").strip(), DEVICE_HEARTBEAT_TOKEN)


def mark_device_heartbeat(device_id, device_ip, location_id=None):
    device_id = str(device_id or "").strip()
    device_ip = str(device_ip or "").strip()
    if not device_id or not device_ip:
        return False
    
    now = datetime.now(timezone.utc)
    with _heartbeat_lock:
        _heartbeats[device_id] = {
            "device_id": device_id,
            "device_ip": device_ip,
            "location_id": location_id or _heartbeats.get(device_id, {}).get("location_id", ""),
            "last_communicated_time": now.isoformat().replace("+00:00", "Z"),
            "device_status": "online"
        }
        # Save to file
        _save_heartbeats()
    return True


def get_device_status(device_id):
    device_id = str(device_id or "").strip()
    if not device_id:
        return None
    
    now = time.time()
    with _heartbeat_lock:
        heartbeat = _heartbeats.get(device_id)
        if not heartbeat:
            return None
        
        # Convert last_communicated_time to timestamp for comparison
        try:
            last_dt = datetime.fromisoformat(heartbeat["last_communicated_time"].replace("Z", "+00:00"))
            last_seen = last_dt.timestamp()
        except:
            last_seen = 0
        
        is_online = (now - last_seen) <= DEVICE_HEARTBEAT_TTL
        
        # Return a copy with updated status
        return {
            "device_id": heartbeat["device_id"],
            "device_ip": heartbeat["device_ip"],
            "location_id": heartbeat.get("location_id", ""),
            "last_communicated_time": heartbeat["last_communicated_time"],
            "device_status": "online" if is_online else "offline"
        }


def check_registered_pi_online(device_id, device_ip):
    device_id = str(device_id or "").strip()
    device_ip = str(device_ip or "").strip()
    if not device_id or not device_ip:
        return False
    
    status = get_device_status(device_id)
    if not status:
        return False
    
    return status["device_ip"] == device_ip and status["device_status"] == "online"


def get_all_device_statuses():
    result = {}
    now = time.time()
    
    with _heartbeat_lock:
        for device_id, heartbeat in _heartbeats.items():
            # Calculate online status without calling get_device_status (to avoid deadlock)
            try:
                last_dt = datetime.fromisoformat(heartbeat["last_communicated_time"].replace("Z", "+00:00"))
                last_seen = last_dt.timestamp()
            except:
                last_seen = 0
            
            is_online = (now - last_seen) <= DEVICE_HEARTBEAT_TTL
            
            result[device_id] = {
                "device_id": heartbeat["device_id"],
                "device_ip": heartbeat["device_ip"],
                "location_id": heartbeat.get("location_id", ""),
                "last_communicated_time": heartbeat["last_communicated_time"],
                "device_status": "online" if is_online else "offline"
            }
    
    return result
