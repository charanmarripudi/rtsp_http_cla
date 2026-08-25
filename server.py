from fastapi import FastAPI, Body, Query, Request
from typing import Optional, Tuple, Any
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import httpx

# Load .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # If dotenv not installed, just use system env vars


class DevicePingRequest(BaseModel):
    location: str
    device_id: str
    device_ip: str

class RpiHeartbeat(BaseModel):
    location_id: str
    device_id: Optional[str] = None
    serial_number: Optional[str] = None
    device_ip: str
    status: str
    timestamp: Optional[str] = None
    cpu: Optional[float] = None
    memory: Optional[float] = None

class DeviceHeartbeat(BaseModel):
    device_id: str
    device_ip: str

# Store for RPI heartbeats
DEVICE_STATUS = {}

from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response, FileResponse
import os, glob, subprocess, mimetypes, signal, json, socket, time, threading, sys

def get_alerts_base_url():
    try:
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        public_url_file = os.path.join(BASE_DIR, "hls", "public_url.txt")
        if os.path.exists(public_url_file):
            with open(public_url_file) as f:
                val = f.read().strip()
                if val and not val.startswith("("):
                    return val
    except: pass
    return os.getenv("ALERTS_BASE_URL", "")
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from datetime import datetime, timezone
from alert_store import DB_DSN, ensure_alerts_schema, insert_alert_db
import device_manager
DEVICE_HEARTBEAT_TTL = device_manager.DEVICE_HEARTBEAT_TTL  # configurable via env var (default 90s)


# --- New Async Check Functions ---
SSH_PORT = 22
HEALTH_CHECK_PORT = 8080

# --- Whitelist of known Raspberry Pi IP addresses ---
# Add all your Raspberry Pi IPs here!
RASPBERRY_PI_IPS = {
    "192.168.96.78",  # Your current Raspberry Pi
    # Add more here if you get more Pis!
}


async def is_port_open(host: str, port: int, timeout: float = 3.0) -> Tuple[bool, str]:
    """Async check if a port is open"""
    try:
        conn = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        reader, writer = conn
        writer.close()
        await writer.wait_closed()
        return True, "Connected"
    except socket.gaierror as e:
        return False, f"DNS error: {e}"
    except asyncio.TimeoutError:
        return False, "Connection timeout"
    except OSError as e:
        return False, str(e)


async def check_pi_heartbeat(ip: str, port: int = 8080, timeout: float = 3.0) -> Tuple[bool, str]:
    """Async check Raspberry Pi heartbeat via HTTP"""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"http://{ip}:{port}/health")
            if response.status_code == 200:
                return True, "Heartbeat OK"
            return False, f"Heartbeat returned {response.status_code}"
    except Exception as e:
        return False, str(e)


async def check_device_online_async(ip: str, use_heartbeat: bool = True, timeout: float = 1.0) -> Tuple[bool, str]:
    """Async wrapper for device checks: try heartbeat first, then SSH if needed"""
    if not ip or not ip.strip():
        return False, "No IP"
    
    # 1. Validate IP format first
    try:
        socket.inet_aton(ip)
        octets = list(map(int, ip.split('.')))
        if len(octets) != 4 or any(o < 0 or o > 255 for o in octets):
            return False, "Invalid IP"
    except (socket.error, ValueError):
        return False, "Invalid IP"
    
    # 2. Try heartbeat first
    if use_heartbeat:
        online, msg = await check_pi_heartbeat(ip, HEALTH_CHECK_PORT, timeout=timeout)
        if online:
            return online, msg
    
    # 3. Try SSH as fallback
    return await is_port_open(ip, SSH_PORT, timeout=timeout)


def determine_device_online_status_sync(loc: dict, check_network: bool = False) -> Tuple[bool, str]:
    """Determine device online status synchronously by checking heartbeats first, then network probe if requested"""
    if not isinstance(loc, dict):
        return False, "Invalid location data"
    
    device_id = str(loc.get("device_id", "")).strip()
    serial = str(loc.get("serial_number", "")).strip()
    location_id = str(loc.get("id", "")).strip()
    ip = str(loc.get("device_ip", "")).strip()
    
    device_identifier = device_id or serial
    
    # 1. Check in-memory DEVICE_STATUS (from /api/rpi-heartbeat or /edge-device/heartbeat)
    if device_identifier and location_id:
        key = f"{location_id}:{device_identifier}"
        if key in DEVICE_STATUS:
            hb_data = DEVICE_STATUS[key]
            now_utc = datetime.now(timezone.utc)
            time_diff = (now_utc - hb_data["last_communicated_time"]).total_seconds()
            if time_diff <= DEVICE_HEARTBEAT_TTL:
                return True, "Heartbeat received (from /api/rpi-heartbeat)"
                
    # 2. Check device_manager (from /device/heartbeat)
    if device_id:
        device_info = device_manager.get_device_status(device_id)
        if device_info and device_info.get("device_status") == "online":
            return True, "Heartbeat received (from /device/heartbeat)"
            
    # 3. Network check fallback if requested
    if check_network and ip:
        online = check_device_online(
            ip, 
            timeout=1.0, 
            expected_device_id=device_id,
            serial_number=serial,
            location_id=location_id,
            is_rpi=True
        )
        if online:
            return True, "Device online via network probe"
            
    return False, "Offline (No recent heartbeat)"


async def determine_device_online_status_async(loc: dict, check_network: bool = False, use_heartbeat: bool = True) -> Tuple[bool, str]:
    """Determine device online status asynchronously by checking heartbeats first, then network probe if requested"""
    if not isinstance(loc, dict):
        return False, "Invalid location data"
        
    device_id = str(loc.get("device_id", "")).strip()
    serial = str(loc.get("serial_number", "")).strip()
    location_id = str(loc.get("id", "")).strip()
    ip = str(loc.get("device_ip", "")).strip()
    
    device_identifier = device_id or serial
    
    # 1. Check in-memory DEVICE_STATUS (from /api/rpi-heartbeat or /edge-device/heartbeat)
    if device_identifier and location_id:
        key = f"{location_id}:{device_identifier}"
        if key in DEVICE_STATUS:
            hb_data = DEVICE_STATUS[key]
            now_utc = datetime.now(timezone.utc)
            time_diff = (now_utc - hb_data["last_communicated_time"]).total_seconds()
            if time_diff <= DEVICE_HEARTBEAT_TTL:
                return True, "Heartbeat received (from /api/rpi-heartbeat)"
                
    # 2. Check device_manager (from /device/heartbeat)
    if device_id:
        device_info = device_manager.get_device_status(device_id)
        if device_info and device_info.get("device_status") == "online":
            return True, "Heartbeat received (from /device/heartbeat)"
            
    # 3. Network check fallback if requested
    if check_network and ip:
        online, msg = await check_device_online_async(ip, use_heartbeat=use_heartbeat)
        if online:
            return True, f"Device online via network probe: {msg}"
            
    return False, "Offline (No recent heartbeat)"


def _attach_last_communicated_time(updated_loc: dict, loc: dict) -> None:
    """Attach last_communicated_time to an updated location dict using DEVICE_STATUS or device_manager."""
    loc_device_id = str(loc.get("device_id", "")).strip()
    loc_serial = str(loc.get("serial_number", "")).strip()
    loc_location_id = str(loc.get("id", "")).strip()
    device_identifier = loc_device_id or loc_serial

    # Priority 1: DEVICE_STATUS dict (from /api/rpi-heartbeat or /edge-device/heartbeat)
    if device_identifier and loc_location_id:
        key = f"{loc_location_id}:{device_identifier}"
        if key in DEVICE_STATUS:
            try:
                updated_loc["last_communicated_time"] = DEVICE_STATUS[key]["last_communicated_time"].isoformat()
                return
            except Exception:
                pass

    # Priority 2: device_manager (from /device/heartbeat)
    if loc_device_id:
        device_info = device_manager.get_device_status(loc_device_id)
        if device_info and device_info.get("last_communicated_time"):
            updated_loc["last_communicated_time"] = device_info["last_communicated_time"]





# ── Optional DB import ──
try:
    import psycopg2
    import psycopg2.extras
    from psycopg2 import pool as pg_pool
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

mimetypes.add_type("application/vnd.apple.mpegurl", ".m3u8")
mimetypes.add_type("video/mp2t", ".ts")

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
STREAMS_CONF = os.path.join(BASE_DIR, "streams.conf")
STREAMS_JSON = os.path.join(BASE_DIR, "streams.json")
LOCATIONS_JSON = os.path.join(BASE_DIR, "locations.json")
MODEL_DIR    = os.path.join(BASE_DIR, "models")
HLS_DIR      = os.path.join(BASE_DIR, "hls")
if os.path.islink(HLS_DIR):
    try: os.unlink(HLS_DIR)
    except: pass
os.makedirs(HLS_DIR, exist_ok=True)
os.makedirs(os.path.join(HLS_DIR, "alerts"), exist_ok=True)

ALERTS_DIR   = os.path.join(HLS_DIR, "alerts")
ALERTS_JSON  = os.path.join(ALERTS_DIR, "alerts.json")
CAMERA_MODELS_JSON = os.path.join(BASE_DIR, "camera_models.json")
_locations_cache = None
_streams_metadata_cache = None
_streams_location_index_cache = None
config_cache_lock = threading.Lock()

# ─────────────────────────────────────────────────────────────
# DB CONFIG
# ─────────────────────────────────────────────────────────────
_db_pool = None

def get_db_pool():
    global _db_pool
    if _db_pool is None and PSYCOPG2_AVAILABLE:
        try:
            _db_pool = pg_pool.ThreadedConnectionPool(1, 10, dsn=DB_DSN)
        except Exception as e: print(f"[DB] pool error: {e}")
    return _db_pool

def get_db_conn():
    p = get_db_pool()
    return p.getconn() if p else None

def release_db_conn(conn):
    if _db_pool and conn: _db_pool.putconn(conn)

def init_db():
    if not PSYCOPG2_AVAILABLE: return
    conn = get_db_conn()
    if not conn: return
    try:
        cur = conn.cursor()
        ensure_alerts_schema(cur)
        conn.commit(); cur.close()
    except Exception as e: print(f"[DB] init error: {e}"); conn.rollback()
    finally: release_db_conn(conn)

# ─────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────
os.makedirs(ALERTS_DIR, exist_ok=True)
if not os.path.exists(ALERTS_JSON): json.dump([], open(ALERTS_JSON, "w"))

app = FastAPI()
app.mount("/hls/alerts", StaticFiles(directory=ALERTS_DIR), name="alerts")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
def read_streams_conf():
    if not os.path.exists(STREAMS_CONF): return []
    return [l.strip() for l in open(STREAMS_CONF) if l.strip() and not l.strip().startswith("#")]

def read_streams_metadata():
    global _streams_metadata_cache, _streams_location_index_cache
    with config_cache_lock:
        if _streams_metadata_cache is not None:
            return [dict(item) for item in _streams_metadata_cache]
    if not os.path.exists(STREAMS_JSON): return []
    try:
        with open(STREAMS_JSON) as fp:
            data = json.load(fp)
        data = data if isinstance(data, list) else []
        # Enrich each stream entry with location data from locations.json (single source of truth)
        locations = read_locations()
        location_map = {}
        for loc in locations:
            if loc.get("location"):
                location_map[loc["location"]] = loc
            if loc.get("id"):
                location_map[loc["id"]] = loc
        # Now enrich streams
        enriched = []
        for item in data:
            if not isinstance(item, dict):
                continue
            enriched_item = dict(item)
            # Find matching location
            matching_loc = None
            if item.get("location_id") and item.get("location_id") in location_map:
                matching_loc = location_map[item.get("location_id")]
            elif item.get("location") and item.get("location") in location_map:
                matching_loc = location_map[item.get("location")]
            
            if matching_loc:
                # Override with location's data as single source of truth
                enriched_item["location"] = matching_loc["location"]
                enriched_item["location_id"] = matching_loc["id"]
                enriched_item["device_id"] = matching_loc.get("device_id", "")
                enriched_item["device_ip"] = matching_loc.get("device_ip", "")
                enriched_item["device_status"] = matching_loc.get("device_status", "offline")
            
            enriched.append(enriched_item)
        # Update cache with enriched data
        with config_cache_lock:
            _streams_metadata_cache = [dict(item) for item in enriched]
            _streams_location_index_cache = build_stream_location_index(_streams_metadata_cache)
        return enriched
    except: return []

def read_locations():
    global _locations_cache
    with config_cache_lock:
        if _locations_cache is not None:
            return [dict(item) for item in _locations_cache]
    if not os.path.exists(LOCATIONS_JSON):
        metadata = read_streams_metadata()
        seeded = []
        seen = set()
        for idx, item in enumerate(metadata):
            if not isinstance(item, dict): continue
            name = str(item.get("location", "")).strip() or f"Location {idx + 1}"
            if name.lower() in seen: continue
            seen.add(name.lower())
            seeded.append({
                "id": f"loc-{len(seeded) + 1}",
                "location": name,
                "device_id": item.get("device_id", ""),
                "device_ip": item.get("device_ip", ""),
                "device_status": item.get("device_status", "offline")
            })
        with config_cache_lock:
            _locations_cache = [dict(item) for item in seeded]
        return seeded
    try:
        with open(LOCATIONS_JSON) as fp:
            data = json.load(fp)
        data = data if isinstance(data, list) else []
        with config_cache_lock:
            _locations_cache = [dict(item) for item in data if isinstance(item, dict)]
        return data
    except: return []

def normalize_location_entry(item, idx):
    if not isinstance(item, dict): item = {}
    location = str(item.get("location", "")).strip()
    
    # Robust unique ID: Use timestamp + index to prevent overwriting existing locations
    generated_id = f"loc-{int(time.time() * 1000) + idx}"
    
    data = {
        "id": str(item.get("id") or generated_id).strip(),
        "location": location
    }
    data.update(normalize_device_fields(item))
    return data

def normalize_device_fields(item):
    # Flexible key mapping to support UI variations (e.g. "device id", "device ip")
    d_id = item.get("device_id") or item.get("device id") or ""
    d_ip = item.get("device_ip") or item.get("device ip") or ""
    d_type = item.get("device_type") or item.get("device type") or ""
    
    return {
        "device_id": str(d_id).strip(),
        "serial_number": str(item.get("serial_number", "")).strip(),
        "device_ip": str(d_ip).strip(),
        "device_type": str(d_type).strip(),
        "device_status": str(item.get("device_status", "offline")).strip().lower() or "offline"
    }

def normalize_stream_entry(item, idx):
    if isinstance(item, dict):
        rtsp = str(item.get("rtsp", "")).strip()
        location = str(item.get("location", "")).strip()
        location_id = str(item.get("location_id", "")).strip()
        device_fields = normalize_device_fields(item)
        model_configs = item.get("model_configs", {})
        try: conf = float(item.get("conf", 0.40))
        except: conf = 0.40
        try: iou = float(item.get("iou", 0.45))
        except: iou = 0.45
    else:
        rtsp = str(item).strip()
        location = f"Location {idx + 1}"
        location_id = f"loc-{idx + 1}"
        device_fields = normalize_device_fields({})
        model_configs = {}
        conf = 0.40
        iou = 0.45
    data = {
        "rtsp": rtsp,
        "location": location,
        "location_id": location_id,
        "conf": conf,
        "iou": iou,
        "model_configs": model_configs
    }
    data.update(device_fields)
    return data

def write_json_atomic(path, data):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as fp:
        json.dump(data, fp, indent=2)
    try:
        os.replace(tmp_path, path)
    except OSError:
        with open(path, "w") as fp:
            json.dump(data, fp, indent=2)
        try:
            os.remove(tmp_path)
        except OSError:
            pass

def is_valid_rtsp_url(value):
    try:
        parsed = urlparse(value)
        return parsed.scheme in ("rtsp", "rtsps") and bool(parsed.hostname)
    except: return False

def sanitize_location(value):
    clean = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in " _-.,()").strip()
    return clean[:120]

def build_stream_location_index(streams_metadata):
    active_locations = set()
    for stream in streams_metadata:
        if not isinstance(stream, dict): continue
        if stream.get("location_id"): active_locations.add(("id", stream.get("location_id")))
        if stream.get("location"): active_locations.add(("name", stream.get("location")))
    return active_locations

def get_stream_location_index():
    global _streams_location_index_cache
    with config_cache_lock:
        if _streams_location_index_cache is not None:
            return set(_streams_location_index_cache)
    metadata = read_streams_metadata()
    with config_cache_lock:
        if _streams_location_index_cache is None:
            _streams_location_index_cache = build_stream_location_index(metadata)
        return set(_streams_location_index_cache)

def location_stream_active(loc, streams_index):
    return ("id", loc.get("id")) in streams_index or ("name", loc.get("location")) in streams_index

def _async_kill(proc):
    def target():
        if not proc: return
        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except: pass
    threading.Thread(target=target, daemon=True).start()

def _kill_raw_ffmpeg_for_camera(cid: str):
    """Kill the raw FFmpeg for a camera without cleaning its HLS directory.
    Called before starting detection so the detector's RTSP connection is the
    only one consuming camera bandwidth — prevents slow-motion caused by two
    concurrent RTSP connections competing for the same stream."""
    cid = str(cid)
    if cid not in cid_to_rtsp:
        return
    rtsp = cid_to_rtsp[cid]
    # Only kill if this camera is the sole user of this RTSP URL.
    # If multiple camera IDs share the same RTSP, keep the proc alive for others.
    count = sum(1 for k, v in cid_to_rtsp.items() if v == rtsp)
    if count <= 1 and rtsp in rtsp_cache:
        cached = rtsp_cache[rtsp]
        if cached["proc"].poll() is None:
            _async_kill(cached["proc"])
        del rtsp_cache[rtsp]
    if cid in cid_to_rtsp:
        del cid_to_rtsp[cid]

def _clean_stale_detected_segments(camera: str):
    """Remove stale .ts segments from the detected HLS directory.
    Called after stop_detection so HLS.js won't serve frozen detected frames
    when the player switches back to the raw stream (prevents black screen)."""
    det_dir = os.path.join(HLS_DIR, f"stream{camera}_detected")
    if os.path.exists(det_dir) and not os.path.islink(det_dir):
        for ts_file in glob.glob(os.path.join(det_dir, "*.ts")):
            try: os.remove(ts_file)
            except: pass

def _clean_camera_dirs(camera: str):
    # Clean only raw stream directory, leave detected alone if detection is running!
    # First handle raw
    raw_dir = os.path.join(HLS_DIR, f"stream{camera}_raw")
    if os.path.lexists(raw_dir):
        if os.path.islink(raw_dir):
            os.unlink(raw_dir)
        else:
            import shutil
            shutil.rmtree(raw_dir)
    os.makedirs(raw_dir, exist_ok=True)
    
    # Only clean detected if NOT running detection!
    if camera not in running:
        det_dir = os.path.join(HLS_DIR, f"stream{camera}_detected")
        if os.path.lexists(det_dir):
            if os.path.islink(det_dir):
                os.unlink(det_dir)
            else:
                import shutil
                shutil.rmtree(det_dir)
        os.makedirs(det_dir, exist_ok=True)

def _extract_ip_port(u):
    try:
        p = urlparse(u)
        return p.hostname or "", p.port or 554
    except: return "", 554

def check_pi_heartbeat_sync(ip: str, port: int, timeout: float = 3.0) -> Tuple[bool, str]:
    try:
        import requests
        response = requests.get(f"http://{ip}:{port}/health", timeout=timeout)
        if response.status_code == 200:
            return True, "Heartbeat OK"
        return False, f"Heartbeat returned {response.status_code}"
    except Exception as e:
        return False, str(e)


def check_device_online(ip: str, timeout: float = 3.0, expected_device_id: Optional[str] = None, serial_number: Optional[str] = None, location_id: Optional[str] = None, is_rpi: bool = False) -> bool:
    """
    Check device status: first check DEVICE_STATUS for recent heartbeat if it's an RPI, then try port 22
    """
    # First check DEVICE_STATUS if we have location_id, identifier, AND it's an RPI
    device_identifier = expected_device_id or serial_number
    if device_identifier and location_id and is_rpi:
        key = f"{location_id}:{device_identifier}"
        if key in DEVICE_STATUS:
            data = DEVICE_STATUS[key]
            now = datetime.now(timezone.utc)
            time_diff = (now - data["last_communicated_time"]).total_seconds()
            if time_diff <= 90:
                return True
    
    if not ip or not ip.strip():
        return False
    ip = ip.strip()
    
    # 1. Strict IP address format validation
    try:
        socket.inet_aton(ip)
        octets = list(map(int, ip.split('.')))
        if len(octets) != 4 or any(o < 0 or o > 255 for o in octets):
            return False
    except (socket.error, ValueError):
        return False
    
    # 2. Try heartbeat (port 8080 /health) first
    heartbeat_online, _ = check_pi_heartbeat_sync(ip, HEALTH_CHECK_PORT, timeout)
    if heartbeat_online:
        return True
    
    # 3. Try SSH (port 22) as fallback
    try:
        with socket.create_connection((ip, 22), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

# ─────────────────────────────────────────────────────────────
# CAMERA STATE
# ─────────────────────────────────────────────────────────────
running = {}
# Cache: rtsp_url → process info:
rtsp_cache = {}  # key: rtsp url (normalized) → {"proc": subprocess, "sd": stream_dir}
# Map from camera id to rtsp url (to share the same segments
cid_to_rtsp = {}

def start_raw_stream(i, u):
    # Normalize RTSP URL to use as cache key
    normalized_rtsp = u.strip()
    cid = str(i)
    cid_to_rtsp[cid] = normalized_rtsp
    
    # Check if we already have a running FFmpeg for this RTSP
    if normalized_rtsp in rtsp_cache:
        cached = rtsp_cache[normalized_rtsp]
        if cached["proc"].poll() is None:
            # Still running, just symlink our stream dir to cached dir
            our_sd = os.path.join(HLS_DIR, f"stream{cid}_raw")
            cached_sd = cached["sd"]
            # Remove old dir if exists
            import shutil
            if os.path.lexists(our_sd):
                if os.path.islink(our_sd):
                    os.unlink(our_sd)
                elif os.path.isdir(our_sd):
                    shutil.rmtree(our_sd)
                else:
                    os.remove(our_sd)
            # Symlink
            os.symlink(os.path.basename(cached_sd), our_sd)
            return
        else:
            # Proc died, remove from cache
            del rtsp_cache[normalized_rtsp]
    
    # New RTSP, start new FFmpeg
    sd = os.path.join(HLS_DIR, f"stream{cid}_raw")
    # Clean old segments but do not delete the parent directory to prevent 404s
    import shutil
    if os.path.lexists(sd):
        if os.path.islink(sd):
            os.unlink(sd)
            os.makedirs(sd, exist_ok=True)
        else:
            for f in glob.glob(os.path.join(sd, "*")):
                try:
                    if os.path.isdir(f): shutil.rmtree(f)
                    else: os.remove(f)
                except: pass
    else:
        os.makedirs(sd, exist_ok=True)
    # Step 1: Clean old files in raw stream dir
    for f in glob.glob(os.path.join(sd, "*")):
        try: os.remove(f)
        except: pass
    # Step 2: Log file
    log_file = os.path.join(sd, "ffmpeg.log")
    try: os.remove(log_file)
    except: pass
    session_id = int(time.time())
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-rtsp_transport", "tcp",
        "-probesize", "2M", "-analyzeduration", "2M",
        "-i", u,
        "-an",
        "-r", "20",
        "-vf", "scale=1280:720,setdar=16/9",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-profile:v", "main", "-level:v", "4.0",
        "-b:v", "600k", "-maxrate", "800k", "-bufsize", "1.5M",
        "-threads", "1", "-pix_fmt", "yuv420p",
        "-g", "40", "-keyint_min", "40", "-sc_threshold", "0",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "8",
        "-hls_flags", "delete_segments+independent_segments+discont_start+omit_endlist+temp_file",
        "-hls_segment_filename", os.path.join(sd, "segment_%d.ts"),
        os.path.join(sd, "playlist.m3u8")
    ]
    log_fh = open(log_file, "w")
    print(f"[LOG] Camera {cid} raw stream started with resolution: 1280x720 (720p HD), FPS: 20.0, Speed: 1.4x real-time (GOP 40), Bitrate: 600k (max 800k)")
    proc = subprocess.Popen(cmd, stdout=log_fh, stderr=log_fh)
    rtsp_cache[normalized_rtsp] = {"proc": proc, "sd": sd}
    # Also, symlink any other cids already mapped to this rtsp
    for other_cid, other_rtsp in list(cid_to_rtsp.items()):
        if other_rtsp == normalized_rtsp and other_cid != cid:
            other_sd = os.path.join(HLS_DIR, f"stream{other_cid}_raw")
            import shutil
            if os.path.lexists(other_sd):
                if os.path.islink(other_sd):
                    os.unlink(other_sd)
                elif os.path.isdir(other_sd):
                    shutil.rmtree(other_sd)
                else:
                    os.remove(other_sd)
            os.symlink(os.path.basename(sd), other_sd)

def monitor_raw_streams_loop():
    time.sleep(10)
    while True:
        try:
            conf_urls = read_streams_conf()
            for i, u in enumerate(conf_urls):
                cid = str(i)
                normalized_rtsp = u.strip()
                
                # Check if this camera or any other camera sharing this RTSP URL has detection active
                rtsp_detection_active = False
                for other_cid in list(running.keys()):
                    try:
                        other_idx = int(other_cid)
                        if other_idx < len(conf_urls):
                            other_rtsp = conf_urls[other_idx].strip()
                            if other_rtsp == normalized_rtsp:
                                if running[other_cid].get("proc") and running[other_cid]["proc"].poll() is None:
                                    rtsp_detection_active = True
                                    break
                    except: pass
                
                if rtsp_detection_active:
                    continue
                
                is_running = False
                if normalized_rtsp in rtsp_cache:
                    cached = rtsp_cache[normalized_rtsp]
                    if cached["proc"].poll() is None:
                        is_running = True
                
                if not is_running:
                    print(f"[MONITOR] Raw stream {cid} ({normalized_rtsp}) is not running! Restarting...")
                    start_raw_stream(i, u)
        except Exception as e:
            print(f"[MONITOR] Error: {e}")
        time.sleep(5)

def stop_raw_stream(i):
    cid = str(i)
    if cid in running: _async_kill(running[cid].get("proc")); del running[cid]; _clean_camera_dirs(cid)
    # Check if it's in our cid_to_rtsp
    if cid in cid_to_rtsp:
        rtsp = cid_to_rtsp[cid]
        # Check if this is the last cid using this rtsp
        count = sum(1 for k, v in cid_to_rtsp.items() if v == rtsp)
        if count <= 1:
            if rtsp in rtsp_cache:
                cached = rtsp_cache[rtsp]
                proc = cached["proc"]
                if proc.poll() is None:
                    _async_kill(proc)
                # Clean up the main dir
                sd = cached["sd"]
                import shutil
                try:
                    shutil.rmtree(sd)
                except:
                    pass
                del rtsp_cache[rtsp]
        del cid_to_rtsp[cid]

@app.on_event("startup")
async def startup_event():
    init_db()
    for i, u in enumerate(read_streams_conf()): start_raw_stream(i, u)
    t = threading.Thread(target=monitor_raw_streams_loop, daemon=True)
    t.start()

# ─────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────
@app.get("/hls/camera/{cam_id}/{filename}")
async def serve_camera_virtual_file(cam_id: str, filename: str):
    sub = f"stream{cam_id}_detected" if cam_id in running and os.path.exists(os.path.join(HLS_DIR, f"stream{cam_id}_detected/playlist.m3u8")) else f"stream{cam_id}_raw"
    return await serve_hls(f"{sub}/{filename}")

@app.get("/hls/camera/{cam_id}/playlist.m3u8")
async def smart_hls_playlist(cam_id: str): return await serve_camera_virtual_file(cam_id, "playlist.m3u8")

@app.get("/hls/{path:path}")
async def serve_hls(path: str):
    # Remove raw-to-detected playlist intercept to prevent segment 404 mismatches and buffering
    # if path.endswith("playlist.m3u8") and "_raw" in path:
    #     cid = path.split("_raw")[0].replace("stream", "")
    #     if cid in running and os.path.exists(os.path.join(HLS_DIR, f"stream{cid}_detected/playlist.m3u8")):
    #         return await serve_hls(f"stream{cid}_detected/playlist.m3u8")
    fp = os.path.join(HLS_DIR, path)
    if not os.path.exists(fp): return Response(status_code=404)
    mt = "application/vnd.apple.mpegurl" if path.endswith(".m3u8") else "video/mp2t" if path.endswith(".ts") else "application/octet-stream"
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0"
    }
    return FileResponse(fp, media_type=mt, headers=headers)

@app.post("/api/update-thresholds")
async def update_thresholds(req: Request):
    try:
        d = await req.json()
        cid = str(d.get("camera", ""))
        conf = float(d.get("conf", 0.40)) if "conf" in d else None
        iou = float(d.get("iou", 0.45)) if "iou" in d else None
        model_configs = d.get("model_configs")
        target_model = d.get("model")
        
        entries = read_streams_metadata()
        for idx, entry in enumerate(entries):
            if str(entry.get("id", idx)) == cid or str(idx) == cid:
                if conf is not None: entry["conf"] = conf
                if iou is not None: entry["iou"] = iou
                if model_configs and isinstance(model_configs, dict):
                    entry["model_configs"] = model_configs
                elif target_model and conf is not None and iou is not None:
                    mc = entry.get("model_configs") or {}
                    clean_m = target_model.replace(".pt", "")
                    mc[target_model] = {"conf": conf, "iou": iou}
                    mc[clean_m] = {"conf": conf, "iou": iou}
                    mc[clean_m + ".pt"] = {"conf": conf, "iou": iou}
                    entry["model_configs"] = mc
        write_json_atomic(STREAMS_JSON, entries)
        with config_cache_lock:
            global _streams_metadata_cache
            _streams_metadata_cache = [dict(e) for e in entries]
        if cid in running:
            if conf is not None: running[cid]["conf"] = conf
            if iou is not None: running[cid]["iou"] = iou
            if model_configs: running[cid]["model_configs"] = model_configs
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/models")
def get_models(): return {"models": [f for f in os.listdir(MODEL_DIR) if f.endswith(".pt")]} if os.path.exists(MODEL_DIR) else {"models": []}

@app.get("/api/streams")
def get_streams(
    location_id: Optional[str] = None, 
    location: Optional[str] = None,
    rtsp: Optional[str] = None
):
    # If rtsp is provided in a GET request, we treat it as an 'add' for convenience
    if rtsp and isinstance(rtsp, str):
        return save_streams(None, rtsp, location, location_id)
        
    metadata = read_streams_metadata()
    
    results = []
    for i, meta in enumerate(metadata):
        if not isinstance(meta, dict): continue
        
        # Filtering logic (Case-insensitive for 'location')
        m_loc_id = str(meta.get("location_id") or "").strip()
        m_loc_name = str(meta.get("location") or "").strip().lower()
        
        f_loc_id = str(location_id or "").strip()
        f_loc_name = str(location or "").strip().lower()

        if f_loc_id and m_loc_id != f_loc_id:
            continue
        if f_loc_name and m_loc_name != f_loc_name:
            continue
            
        results.append({
            "id": i,
            "label": meta.get("label") or f"Camera {i+1}",
            "location": meta.get("location", f"Location {i+1}"),
            "location_id": meta.get("location_id", ""),
            "device_id": meta.get("device_id", ""),
            "device_ip": meta.get("device_ip", ""),
            "device_status": meta.get("device_status", "offline"),
            "rtsp": meta.get("rtsp", ""),
            "conf": float(meta.get("conf", 0.40)),
            "iou": float(meta.get("iou", 0.45)),
            "model_configs": meta.get("model_configs", {}),
            "hls_live": f"/hls/camera/{i}/playlist.m3u8",
            "hls_raw": f"/hls/stream{i}_raw/playlist.m3u8",
            "hls_detected": f"/hls/stream{i}_detected/playlist.m3u8"
        })
    return results

@app.get("/api/streams/fetch")
def fetch_filtered_streams_get(
    location_id: Optional[str] = None, 
    location: Optional[str] = None
):
    """Fetch filtered streams via URL query parameters."""
    return get_streams(location_id=location_id, location=location)

@app.post("/api/streams/fetch")
def fetch_filtered_streams_post(d: Optional[dict] = Body(default={})):
    """Fetch filtered streams via JSON body."""
    try:
        # Handle cases where d might be None if 'null' is sent as body
        payload = d or {}
        return get_streams(
            location_id=payload.get("location_id"),
            location=payload.get("location")
        )
    except Exception as e:
        print(f"[API] fetch_filtered_streams_post error: {e}")
        return {"error": str(e), "status": "failed"}

@app.post("/api/streams")
def save_streams(
    data: Optional[list] = Body(default=None),
    rtsp: Optional[str] = None,
    location: Optional[str] = None,
    location_id: Optional[str] = None
):
    global _streams_metadata_cache, _streams_location_index_cache
    
    # Load valid locations from locations.json (single source of truth)
    valid_locations = read_locations()
    valid_loc_map = {}
    for loc in valid_locations:
        if loc.get("location"):
            valid_loc_map[loc["location"]] = loc
        if loc.get("id"):
            valid_loc_map[loc["id"]] = loc
    
    # Helper to get valid location data
    def get_valid_loc(loc_name=None, loc_id=None):
        if loc_id and loc_id in valid_loc_map:
            return valid_loc_map[loc_id]
        if loc_name and loc_name in valid_loc_map:
            return valid_loc_map[loc_name]
        return None
    
    # CASE 1: Append/Update/Remove Mode (Query Params)
    if rtsp is not None:
        current_metadata = read_streams_metadata()
        
        # Find valid location
        valid_loc = get_valid_loc(location, location_id)
        if not valid_loc and rtsp.strip() and location:
            # Auto-register location if not found
            loc_id = location_id or f"loc-{int(time.time())}"
            valid_loc = {
                "id": loc_id,
                "location": location,
                "device_id": "RPI-001",
                "serial_number": "",
                "device_ip": "192.168.96.36",
                "device_type": "rpi",
                "device_status": "offline"
            }
            current_locations = read_locations()
            current_locations.append(valid_loc)
            write_json_atomic(LOCATIONS_JSON, current_locations)
            valid_loc_map[location] = valid_loc
            valid_loc_map[loc_id] = valid_loc
        elif not valid_loc and rtsp.strip():
            return {"error": "Location name is required to auto-register this new location."}
        
        # Find if this specific RTSP already exists
        exists_idx = -1
        for i, entry in enumerate(current_metadata):
            if entry.get("rtsp") == rtsp.strip():
                exists_idx = i
                break
        
        # If RTSP is provided as an empty string via query param (e.g., ?rtsp=), remove it
        if not rtsp.strip():
            if exists_idx >= 0:
                stop_raw_stream(exists_idx)
                current_metadata.pop(exists_idx)
        else:
            new_entry = {
                "rtsp": rtsp.strip(),
                "location": valid_loc["location"],
                "location_id": valid_loc["id"],
                "device_id": valid_loc.get("device_id", ""),
                "device_ip": valid_loc.get("device_ip", ""),
                "device_status": valid_loc.get("device_status", "offline")
            }
            if exists_idx >= 0:
                # Merge: Only update fields that are provided
                old_entry = current_metadata[exists_idx]
                for key, val in new_entry.items():
                    if val or key not in old_entry: old_entry[key] = val
            else:
                current_metadata.append(new_entry)
        
        entries = current_metadata

    # CASE 2: Smart Bulk Save Mode (JSON Body)
    elif data is not None:
        current_metadata = read_streams_metadata()
        
        # 1. Identify locations present in the incoming data
        incoming_locations = set()
        incoming_location_ids = set()
        
        if location: incoming_locations.add(location.strip().lower())
        if location_id: incoming_location_ids.add(str(location_id).strip())
        
        new_entries_to_add = []
        for item in data:
            entry = normalize_stream_entry(item, len(new_entries_to_add))
            if entry.get("rtsp"):
                # Validate location is from valid locations
                valid_loc = get_valid_loc(entry.get("location"), entry.get("location_id"))
                if not valid_loc and entry.get("location"):
                    # Auto-register location if not found
                    loc_id = entry.get("location_id") or f"loc-{int(time.time() * 1000) + len(new_entries_to_add)}"
                    valid_loc = {
                        "id": loc_id,
                        "location": entry["location"],
                        "device_id": "RPI-001",
                        "serial_number": "",
                        "device_ip": "192.168.96.36",
                        "device_type": "rpi",
                        "device_status": "offline"
                    }
                    current_locations = read_locations()
                    current_locations.append(valid_loc)
                    write_json_atomic(LOCATIONS_JSON, current_locations)
                    valid_loc_map[entry["location"]] = valid_loc
                    valid_loc_map[loc_id] = valid_loc
                elif not valid_loc:
                    continue  # Skip if no location name is available to register
                # Update entry with valid location data
                entry["location"] = valid_loc["location"]
                entry["location_id"] = valid_loc["id"]
                entry["device_id"] = valid_loc.get("device_id", "")
                entry["device_ip"] = valid_loc.get("device_ip", "")
                entry["device_status"] = valid_loc.get("device_status", "offline")
                
                new_entries_to_add.append(entry)
                if entry.get("location"): incoming_locations.add(entry["location"].strip().lower())
                if entry.get("location_id"): incoming_location_ids.add(str(entry["location_id"]).strip())
        
        # 2. Filter out current entries that belong to the same locations
        # This allows us to "replace" cameras for specific locations without deleting others
        if location_id is None and location is None:
            entries = new_entries_to_add
        else:
            final_entries = []
            for old_entry in current_metadata:
                old_loc = str(old_entry.get("location") or "").strip().lower()
                old_loc_id = str(old_entry.get("location_id") or "").strip()
                
                if old_loc in incoming_locations or old_loc_id in incoming_location_ids:
                    # This location is being updated by the incoming data, so we skip the old record
                    continue
                final_entries.append(old_entry)
                
            # 3. Add the new entries
            final_entries.extend(new_entries_to_add)
            entries = final_entries
    else:
        # Fallback to current state
        entries = read_streams_metadata()

    # 4. Strict RTSP URL Deduplication to prevent duplicate RTSP entries on restart
    seen_rtsps = set()
    deduped_entries = []
    for entry in entries:
        r_url = str(entry.get("rtsp") or "").strip()
        if r_url and r_url not in seen_rtsps:
            seen_rtsps.add(r_url)
            deduped_entries.append(entry)
    entries = deduped_entries

    # Common persistence logic
    old_metadata = read_streams_metadata()
    old_cid_to_rtsp = dict(cid_to_rtsp)  # Snapshot of current state
    urls = [entry["rtsp"] for entry in entries if entry.get("rtsp")]
    open(STREAMS_CONF, "w").write("\n".join(urls))
    write_json_atomic(STREAMS_JSON, entries)
    
    with config_cache_lock:
        _streams_metadata_cache = None  # Clear cache so it gets enriched again on next read
        _streams_location_index_cache = None
    
    # ONLY modify streams that actually changed, NOT ALL!
    for i, u in enumerate(urls):
        cid = str(i)
        old_u = old_cid_to_rtsp.get(cid)
        if old_u != u:  # If RTSP changed OR new stream
            # Stop old if it was running
            if old_u is not None:
                stop_raw_stream(int(cid))
                # Clean both detected/raw only if this cid had a different RTSP before
                _clean_camera_dirs(str(cid))
            start_raw_stream(i, u)
    
    # Stop streams that are NO LONGER in the new list
    max_new_idx = len(urls) - 1
    for cid in list(old_cid_to_rtsp.keys()):
        idx = int(cid)
        if idx > max_new_idx:
            stop_raw_stream(idx)
            _clean_camera_dirs(cid)
    
    return get_streams(location_id=location_id, location=location)

@app.delete("/api/streams")
def delete_stream(
    rtsp: str = Query(...),
    location_id: str = Query(...),
    location: str = Query(...)
):
    """
    Strictly delete a camera stream. 
    Requires RTSP URL, location_id, AND location name to match exactly.
    """
    global _streams_metadata_cache, _streams_location_index_cache
    
    current_metadata = read_streams_metadata()
    exists_idx = -1
    
    # Input cleanup
    req_rtsp = rtsp.strip()
    req_loc_id = location_id.strip()
    req_loc_name = location.strip().lower()
    
    for i, entry in enumerate(current_metadata):
        # Strict matching logic: All three must match the record in our database
        stored_rtsp = entry.get("rtsp", "")
        stored_loc_id = str(entry.get("location_id", "")).strip()
        stored_loc_name = str(entry.get("location", "")).strip().lower()
        
        if stored_rtsp == req_rtsp and stored_loc_id == req_loc_id and stored_loc_name == req_loc_name:
            exists_idx = i
            break
            
    if exists_idx >= 0:
        # Stop the FFmpeg process
        stop_raw_stream(exists_idx)
        # Remove from metadata
        current_metadata.pop(exists_idx)
        
        # Save changes
        urls = [entry["rtsp"] for entry in current_metadata if entry.get("rtsp")]
        open(STREAMS_CONF, "w").write("\n".join(urls))
        write_json_atomic(STREAMS_JSON, current_metadata)
        
        with config_cache_lock:
            _streams_metadata_cache = [dict(entry) for entry in current_metadata]
            _streams_location_index_cache = build_stream_location_index(_streams_metadata_cache)
            
        return {"status": "deleted", "message": "Camera removed successfully."}
    
    return {
        "status": "failed", 
        "message": "Deletion denied. Provided RTSP, Location ID, or Location Name does not match our records.",
        "hint": "Ensure all 3 parameters are correct."
    }

@app.get("/api/status")
def get_status():
    for cid in list(running.keys()):
        if not (running[cid].get("proc") and running[cid]["proc"].poll() is None): del running[cid]
    return {"active": list(running.keys()), "models": {k: v["models"] for k, v in running.items()}}

@app.post("/api/start")
def start_detection(d: dict):
    cid, rtsp, conf, iou = str(d["camera"]), d["rtsp"], float(d.get("conf", 0.25)), float(d.get("iou", 0.45))
    if not is_valid_rtsp_url(rtsp): return {"error": "invalid rtsp"}
    mods = list(d["models"]) if "models" in d else [d["model"]] if "model" in d else []
    if not mods: return {"error": "no model"}
    
    # Get location from payload, or use saved location from streams.json, or default label
    loc = d.get("location")
    if not loc:
        # Try to pull saved location from streams metadata
        # First, search by RTSP URL (more reliable than index)
        metadata = read_streams_metadata()
        for entry in metadata:
            if isinstance(entry, dict) and entry.get("rtsp") == rtsp:
                loc = entry.get("location")
                break
        # If not found by RTSP, try by index (fallback)
        if not loc:
            try:
                cam_idx = int(cid)
                if cam_idx < len(metadata) and isinstance(metadata[cam_idx], dict):
                    loc = metadata[cam_idx].get("location")
            except (ValueError, IndexError):
                pass
    # If still no location, use default label
    if not loc:
        loc = f"Camera {int(cid)+1}"
    loc = sanitize_location(loc) or loc
    
    # Save model assignment to camera_models.json
    if os.path.exists(CAMERA_MODELS_JSON):
        cm = json.load(open(CAMERA_MODELS_JSON))
    else:
        cm = {}
    cm[cid] = mods
    json.dump(cm, open(CAMERA_MODELS_JSON, "w"), indent=2)
    
    if cid in running: 
        _async_kill(running[cid].get("proc")); 
        del running[cid]

    # Clean only detected dir
    det_dir = os.path.join(HLS_DIR, f"stream{cid}_detected")
    if os.path.exists(det_dir):
        if os.path.islink(det_dir):
            os.unlink(det_dir)
        else:
            import shutil
            shutil.rmtree(det_dir)
    os.makedirs(det_dir, exist_ok=True)

    # Save model_configs if provided
    model_configs = d.get("model_configs") or {}
    if model_configs:
        try:
            entries = read_streams_metadata()
            for idx, entry in enumerate(entries):
                if str(entry.get("id", idx)) == cid or str(idx) == cid:
                    entry["model_configs"] = model_configs
            write_json_atomic(STREAMS_JSON, entries)
        except Exception as e:
            print(f"[ERROR] Failed to save model_configs: {e}")

    # Start detector worker with model_configs JSON argument
    cmd = [sys.executable, os.path.join(BASE_DIR, "detector/start_detection.py"), cid, rtsp, ",".join(mods), str(conf), str(iou), loc, json.dumps(model_configs)]
    log = open(os.path.join(HLS_DIR, f"stream{cid}_detected/worker.log"), "a")
    running[cid] = {"proc": subprocess.Popen(cmd, stdout=log, stderr=log), "models": mods, "conf": conf, "iou": iou, "location": loc, "model_configs": model_configs}
    return {"status": "started", "camera": cid, "models": mods, "location": loc}

@app.post("/api/stop-raw")
def stop_raw_for_camera(d: dict):
    cid = str(d.get("camera", ""))
    _kill_raw_ffmpeg_for_camera(cid)
    return {"status": "ok", "camera": cid}

@app.post("/api/stop")
def stop_detection(d: dict):
    cid = str(d["camera"])
    if cid in running:
        proc = running[cid].get("proc")
        del running[cid]
        # Synchronously wait for the detector process to exit before restarting the raw
        # stream. Fire-and-forget (_async_kill) causes a race: the detector may still be
        # writing detected HLS segments while the player has already switched to raw,
        # resulting in a black screen or frozen playback for 3-4 seconds.
        if proc:
            try:
                proc.kill()
                proc.wait(timeout=3.0)
            except: pass
        # Remove stale detected .ts segments so HLS.js doesn't serve old frozen frames
        # when the video element switches back to the raw stream playlist.
        _clean_stale_detected_segments(cid)
    urls = read_streams_conf()
    if int(cid) < len(urls):
        start_raw_stream(int(cid), urls[int(cid)])
        # Block until the first raw segment is generated and listed in playlist.m3u8 (max 5 seconds)
        sd = os.path.join(HLS_DIR, f"stream{cid}_raw")
        playlist_file = os.path.join(sd, "playlist.m3u8")
        t_start = time.time()
        while time.time() - t_start < 5.0:
            if os.path.exists(playlist_file):
                try:
                    with open(playlist_file) as f:
                        if ".ts" in f.read():
                            break
                except: pass
            time.sleep(0.1)
    return {"status": "stopped"}

@app.post("/api/detection/start")
def ds_alias(d: dict): return start_detection(d)
@app.post("/api/detection/stop")
def dst_alias(d: dict): return stop_detection(d)
@app.get("/api/detection/status")
def dsta_alias(): return get_status()

@app.get("/api/camera-models")
def get_cm(): return json.load(open(CAMERA_MODELS_JSON)) if os.path.exists(CAMERA_MODELS_JSON) else {}
@app.get("/api/camera-models/{camera_id}")
def get_cm_id(camera_id: str): return {"models": get_cm().get(camera_id, [])}
@app.post("/api/camera-models")
def save_cm(d: dict = Body(...)):
    # Get old model assignments first
    old_cm = json.load(open(CAMERA_MODELS_JSON)) if os.path.exists(CAMERA_MODELS_JSON) else {}
    
    # Save new assignments
    json.dump(d, open(CAMERA_MODELS_JSON, "w"), indent=2)
    
    # Stop detection for any cameras that no longer have models
    for cid in list(old_cm.keys()):
        # Check if this camera still has models in new assignment
        new_has_models = cid in d and len(d.get(cid, [])) > 0
        if not new_has_models and cid in running:
            # Stop detection for this camera
            if cid in running: 
                _async_kill(running[cid].get("proc"))
                del running[cid]
            # DO NOT clean detected directory - leave existing files there!
            # Start raw stream again
            urls = read_streams_conf()
            if int(cid) < len(urls): 
                start_raw_stream(int(cid), urls[int(cid)])
    
    return {"status": "saved"}

@app.get("/api/locations")
async def get_locations(
    request: Request,
    location: Optional[str] = None,
    device_id: Optional[str] = None,
    serial_number: Optional[str] = None,
    device_ip: Optional[str] = None,
    device_status: Optional[str] = None,
    device_type: Optional[str] = None,
    id: Optional[str] = None,
    use_heartbeat: bool = True,
    check_status: bool = True
):
    """
    Returns all locations with real-time device status.
    - If 'location' is provided, it treats it as an 'add/update' request for convenience (GET method).
    - check_status=True  → async network ping per device (slow but accurate)
    - check_status=False → heartbeat-only status check (fast, default)
    """
    # 1. If location is provided, treat as an ADD request (matching UI team usage)
    if location:
        # Check for keys with spaces in the query string too
        params = dict(request.query_params)
        d_id = device_id or params.get("device id")
        d_ip = device_ip or params.get("device ip")
        d_type = device_type or params.get("device type")
        
        # Call save_locations logic
        save_locations(
            location=location,
            device_id=d_id,
            serial_number=serial_number,
            device_ip=d_ip,
            device_status=device_status,
            device_type=d_type,
            id=id
        )

    # 2. Proceed with normal GET logic
    locations = read_locations()
    updated_locations = []

    if check_status and not location: # Skip slow check if we just added/updated
        # Slow path: async status check (heartbeat + optional network ping)
        tasks = [determine_device_online_status_async(loc, check_network=True) for loc in locations]
        results = await asyncio.gather(*tasks)
        for loc, (is_online, message) in zip(locations, results):
            updated_loc = dict(loc)
            updated_loc["device_status"] = "online" if is_online else "offline"
            updated_loc["status_message"] = message
            _attach_last_communicated_time(updated_loc, loc)
            updated_locations.append(updated_loc)
    else:
        # Fast path: heartbeat-only (no network I/O)
        for loc in locations:
            is_online, message = determine_device_online_status_sync(loc, check_network=False)
            updated_loc = dict(loc)
            updated_loc["device_status"] = "online" if is_online else "offline"
            updated_loc["status_message"] = message
            _attach_last_communicated_time(updated_loc, loc)
            updated_locations.append(updated_loc)

    return {"locations": updated_locations}

@app.post("/api/locations")
def save_locations(
    data: Optional[Any] = Body(default=None),
    location: Optional[str] = None,
    device_id: Optional[str] = None,
    serial_number: Optional[str] = None,
    device_ip: Optional[str] = None,
    device_status: Optional[str] = None,
    device_type: Optional[str] = None,
    id: Optional[str] = None
):
    global _locations_cache, _streams_metadata_cache, _streams_location_index_cache
    
    # 1. Load current state
    current_locations = read_locations()
    
    # 2. Determine if we are appending via Query Params or overwriting via Body
    # We prioritize Query Params if 'location' is present
    if location and isinstance(location, str):
        # APPEND/UPDATE MODE (Query Params)
        status = str(device_status or "offline").strip().lower()
        device_type_str = str(device_type or "").strip().lower()
        is_rpi = device_type_str in ["rpi", "raspberry pi", "raspberrypi"]
        
        if device_ip and is_rpi:
            status = "online" if check_device_online(
                device_ip, 
                timeout=1.0, 
                expected_device_id=device_id,
                serial_number=serial_number,
                location_id=id, 
                is_rpi=is_rpi
            ) else "offline"
        else:
            status = "offline"

        new_entry = {
            "id": str(id or f"loc-{int(time.time())}").strip(),
            "location": str(location).strip(),
            "device_id": str(device_id or "").strip(),
            "serial_number": str(serial_number or "").strip(),
            "device_ip": str(device_ip or "").strip(),
            "device_type": str(device_type or "").strip(),
            "device_status": status
        }
        
        # Check for existing entry to update
        exists = False
        for i, loc in enumerate(current_locations):
            if loc.get("location") == new_entry["location"] or loc.get("id") == new_entry["id"]:
                current_locations[i] = new_entry
                exists = True
                break
        
        if not exists:
            current_locations.append(new_entry)
            
        final_locations = current_locations
    
    # CASE 2: Smart Bulk Save Mode (JSON Body)
    elif data is not None:
        # Wrap single object into a list if needed
        if isinstance(data, dict):
            data = [data]
        elif not isinstance(data, list):
            return {"error": "Invalid data format. Expected list or object."}

        # 1. Identify locations present in the incoming data
        incoming_ids = set()
        incoming_names = set()
        
        if id: incoming_ids.add(str(id).strip())
        if location: incoming_names.add(location.strip().lower())
        
        new_locs_to_add = []
        for item in data:
            loc = normalize_location_entry(item, len(new_locs_to_add))
            if loc.get("location"):
                new_locs_to_add.append(loc)
                if loc.get("id"): incoming_ids.add(str(loc["id"]).strip())
                if loc.get("location"): incoming_names.add(loc["location"].strip().lower())
        
        # Map current locations for quick lookup
        current_map = {str(loc.get("id")).strip(): loc for loc in current_locations if loc.get("id")}
        
        # 2. Identify deleted locations (present in current_locations but not in the incoming data)
        deleted_locations = []
        for old_loc in current_locations:
            o_id = str(old_loc.get("id") or "").strip()
            o_name = str(old_loc.get("location") or "").strip().lower()
            
            if o_id in incoming_ids or o_name in incoming_names:
                continue
            deleted_locations.append(old_loc)
            
        # Overwrite the locations list with new_locs_to_add (effectively removing omitted ones)
        final_locations = new_locs_to_add

        # 3. Clean up camera streams and stop processes for deleted locations
        if deleted_locations:
            deleted_ids = {str(loc.get("id")).strip() for loc in deleted_locations if loc.get("id")}
            deleted_names = {str(loc.get("location")).strip().lower() for loc in deleted_locations if loc.get("location")}
            
            streams_metadata = read_streams_metadata()
            streams_changed = False
            for idx in range(len(streams_metadata) - 1, -1, -1):
                stream = streams_metadata[idx]
                s_loc_id = str(stream.get("location_id") or "").strip()
                s_loc_name = str(stream.get("location") or "").strip().lower()
                
                if s_loc_id in deleted_ids or s_loc_name in deleted_names:
                    stop_raw_stream(idx)
                    streams_metadata.pop(idx)
                    streams_changed = True
                    
            if streams_changed:
                urls = [entry["rtsp"] for entry in streams_metadata if entry.get("rtsp")]
                with open(STREAMS_CONF, "w") as sf:
                    sf.write("\n".join(urls))
                write_json_atomic(STREAMS_JSON, streams_metadata)
                with config_cache_lock:
                    _streams_metadata_cache = [dict(entry) for entry in streams_metadata]
                    _streams_location_index_cache = build_stream_location_index(_streams_metadata_cache)

        # 4. Optimized Parallel Status Check for Bulk Save
        # Only probe if NEW or if IP/Type changed to fix slowness!
        probe_list = []
        for loc in new_locs_to_add:
            lid = str(loc.get("id")).strip()
            if lid not in current_map:
                probe_list.append(loc)
            else:
                old = current_map[lid]
                # If IP or Type changed, or it was previously offline, re-probe
                if (loc.get("device_ip") != old.get("device_ip") or 
                    loc.get("device_type") != old.get("device_type") or
                    old.get("device_status") != "online"):
                    probe_list.append(loc)
                else:
                    # Keep existing online status
                    loc["device_status"] = old.get("device_status", "offline")

        if probe_list:
            with ThreadPoolExecutor(max_workers=10) as executor:
                def probe_status(loc):
                    if loc.get("device_ip"):
                        loc_device_type = str(loc.get("device_type", "")).strip().lower()
                        is_rpi = loc_device_type in ["rpi", "raspberry pi", "raspberrypi"]
                        
                        if is_rpi:
                            loc["device_status"] = "online" if check_device_online(
                                loc["device_ip"], 
                                timeout=1.0, 
                                expected_device_id=loc.get("device_id"), 
                                serial_number=loc.get("serial_number"),
                                location_id=loc.get("id"),
                                is_rpi=is_rpi
                            ) else "offline"
                        else:
                            loc["device_status"] = "offline"
                
                list(executor.map(probe_status, probe_list))
    else:
        return {"status": "success", "locations": current_locations}

    # 3. Persist and update cache
    write_json_atomic(LOCATIONS_JSON, final_locations)
    with config_cache_lock:
        _locations_cache = [dict(item) for item in final_locations]
    
    return {"status": "saved", "count": len(final_locations), "locations": final_locations}

@app.delete("/api/locations")
def delete_location(location: str = Query(...)):
    """Delete a location by its name."""
    global _locations_cache, _streams_metadata_cache, _streams_location_index_cache
    current_locations = read_locations()
    target_name = location.strip().lower()
    
    deleted_locations = [loc for loc in current_locations if str(loc.get("location", "")).strip().lower() == target_name]
    
    if deleted_locations:
        final_locations = [loc for loc in current_locations if str(loc.get("location", "")).strip().lower() != target_name]
        write_json_atomic(LOCATIONS_JSON, final_locations)
        with config_cache_lock:
            _locations_cache = [dict(item) for item in final_locations]
            
        # Clean up associated streams
        deleted_ids = {str(loc.get("id")).strip() for loc in deleted_locations if loc.get("id")}
        deleted_names = {str(loc.get("location")).strip().lower() for loc in deleted_locations if loc.get("location")}
        
        streams_metadata = read_streams_metadata()
        streams_changed = False
        for idx in range(len(streams_metadata) - 1, -1, -1):
            stream = streams_metadata[idx]
            s_loc_id = str(stream.get("location_id") or "").strip()
            s_loc_name = str(stream.get("location") or "").strip().lower()
            
            if s_loc_id in deleted_ids or s_loc_name in deleted_names:
                stop_raw_stream(idx)
                streams_metadata.pop(idx)
                streams_changed = True
                
        if streams_changed:
            urls = [entry["rtsp"] for entry in streams_metadata if entry.get("rtsp")]
            with open(STREAMS_CONF, "w") as sf:
                sf.write("\n".join(urls))
            write_json_atomic(STREAMS_JSON, streams_metadata)
            with config_cache_lock:
                _streams_metadata_cache = [dict(entry) for entry in streams_metadata]
                _streams_location_index_cache = build_stream_location_index(_streams_metadata_cache)
                
        return {"status": "deleted", "location": location}
    
    return {"status": "not_found", "message": f"Location '{location}' not found."}

# ─────────────────────────────────────────────────────────────
# DEVICES & ALERTS
# ─────────────────────────────────────────────────────────────
@app.get("/api/devices")
async def get_devices(use_heartbeat: bool = True, check_status: bool = False):
    res = []
    locations = read_locations()
    streams_index = get_stream_location_index()
    
    if not check_status:
        # Quick mode: use saved statuses + DEVICE_STATUS check!
        for i, loc in enumerate(locations):
            loc_device_id = str(loc.get("device_id", "")).strip()
            loc_location_id = str(loc.get("id", "")).strip()
            device_status = loc.get("device_status", "offline")
            status_message = "Using saved status"
            
            if loc_device_id and loc_location_id:
                key = f"{loc_location_id}:{loc_device_id}"
                if key in DEVICE_STATUS:
                    data = DEVICE_STATUS[key]
                    now = datetime.now(timezone.utc)
                    time_diff = (now - data["last_communicated_time"]).total_seconds()
                    if time_diff <=90:
                        device_status = "online"
                        status_message = "Heartbeat received"
                    else:
                        device_status = "offline"
                        status_message = "Last heartbeat too old"
                else:
                    status_message = "No heartbeat received"
                    
            res.append({
                "device_id": loc.get("device_id") or f"DEV-{i}",
                "device_name": loc.get("location") or f"Location {i+1}",
                "location": loc.get("location") or f"Location {i+1}",
                "device_ip": loc.get("device_ip", ""),
                "device_status": device_status,
                "status_message": status_message,
                "stream_active": location_stream_active(loc, streams_index)
            })
        return {"devices": res}
    
    # Check device statuses (async)
    async def check_all_devices():
        results = []
        for loc in locations:
            ip = loc.get("device_ip", "")
            device_id = str(loc.get("device_id", "")).strip()
            location_id = str(loc.get("id", "")).strip()
            
            # First check DEVICE_STATUS for recent heartbeat
            if device_id and location_id:
                key = f"{location_id}:{device_id}"
                if key in DEVICE_STATUS:
                    data = DEVICE_STATUS[key]
                    now = datetime.now(timezone.utc)
                    time_diff = (now - data["last_communicated_time"]).total_seconds()
                    if time_diff <= 90:
                        results.append((True, "Heartbeat received"))
                        continue
            
            # Fall back to original check if no recent heartbeat
            if ip:
                res = await check_device_online_async(ip, use_heartbeat=use_heartbeat)
                results.append(res)
            else:
                results.append((False, "No IP"))
        return results

    results = await check_all_devices()
    
    for i, (loc, (is_online, status_message)) in enumerate(zip(locations, results)):
        res.append({
            "device_id": loc.get("device_id") or f"DEV-{i}",
            "device_name": loc.get("location") or f"Location {i+1}",
            "location": loc.get("location") or f"Location {i+1}",
            "device_ip": loc.get("device_ip", ""),
            "device_status": "online" if is_online else "offline",
            "status_message": status_message,
            "stream_active": location_stream_active(loc, streams_index)
        })
    return {"devices": res}

@app.get("/api/devices/{device_id}")
def get_device(device_id: str):
    locations = read_locations()
    streams_index = get_stream_location_index()
    for i, loc in enumerate(locations):
        stable_device_id = loc.get("device_id") or f"DEV-{i}"
        if stable_device_id == device_id:
            ip = loc.get("device_ip", "")
            status = loc.get("device_status") or "offline"
            
            # First check DEVICE_STATUS
            loc_device_id = str(loc.get("device_id", "")).strip()
            location_id = str(loc.get("id", "")).strip()
            if loc_device_id and location_id:
                key = f"{location_id}:{loc_device_id}"
                if key in DEVICE_STATUS:
                    data = DEVICE_STATUS[key]
                    now = datetime.now(timezone.utc)
                    time_diff = (now - data["last_communicated_time"]).total_seconds()
                    if time_diff <= 90:
                        status = "online"
                    else:
                        status = "offline"
                elif ip:
                    status = "online" if check_device_online(ip, timeout=1.0, expected_device_id=loc_device_id, location_id=location_id) else "offline"
            elif ip:
                status = "online" if check_device_online(ip, timeout=1.0, expected_device_id=loc_device_id, location_id=location_id) else "offline"
            
            return {
                "device_id": stable_device_id,
                "device_name": loc.get("location") or f"Location {i+1}",
                "location": loc.get("location") or f"Location {i+1}",
                "device_ip": ip,
                "device_status": status,
                "stream_active": location_stream_active(loc, streams_index)
            }
    return {"error": "not found"}

@app.post("/api/devices/ping")
async def ping_device(d: DevicePingRequest, use_heartbeat: bool = True):
    """
    STRICT check: pass location, device_id, device_ip.
    Only returns 'online' if the record exists AND all 3 parameters match AND the IP is reachable.
    """
    global _locations_cache
    req_device_id = str(d.device_id).strip()
    req_device_ip  = str(d.device_ip).strip()
    req_location   = str(d.location).strip()

    if not req_device_ip:
        return {"error": "device_ip is required"}

    # 1. Strictly find if this device exists with these exact credentials
    locations = read_locations()
    target_loc = None
    
    for loc in locations:
        stored_id = str(loc.get("device_id", "")).strip()
        stored_ip = str(loc.get("device_ip", "")).strip()
        stored_name = str(loc.get("location", "")).strip().lower()
        
        # All three must match the record in our database
        if (stored_id == req_device_id and 
            stored_ip == req_device_ip and 
            stored_name == req_location.lower()):
            target_loc = loc
            break

    if not target_loc:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=403, 
            detail="Access Denied: The provided Device ID, IP, and Location name do not match any registered Raspberry Pi in our database."
        )

    # 2. Check DEVICE_STATUS first
    is_online = False
    status_message = "No heartbeat"
    # Find the location ID for this device
    target_location_id = target_loc.get("id", "")
    if target_location_id and req_device_id:
        key = f"{target_location_id}:{req_device_id}"
        if key in DEVICE_STATUS:
            data = DEVICE_STATUS[key]
            now = datetime.now(timezone.utc)
            time_diff = (now - data["last_communicated_time"]).total_seconds()
            if time_diff <= 90:
                is_online = True
                status_message = "Heartbeat received"
    
    # 3. If no recent heartbeat, fall back to probing
    if not is_online:
        is_online, status_message = await check_device_online_async(req_device_ip, use_heartbeat=use_heartbeat)
    
    new_status = "online" if is_online else "offline"

    # 3. Update the record status
    target_loc["device_status"] = new_status
    
    write_json_atomic(LOCATIONS_JSON, locations)
    with config_cache_lock:
        _locations_cache = [dict(item) for item in locations]

    return {
        "device_id": req_device_id,
        "device_ip": req_device_ip,
        "location": req_location,
        "device_status": new_status,
        "status_message": status_message,
        "updated": True
    }

# --- RPI Heartbeat Endpoints ---
@app.post("/api/rpi-heartbeat")
async def rpi_heartbeat(data: RpiHeartbeat):
    global _locations_cache
    
    # CLEAR LOCATIONS CACHE TO READ FRESH DATA DIRECTLY FROM DISK!
    with config_cache_lock:
        _locations_cache = None
    
    # 1. Determine the device identifier (use device_id if present, else serial_number)
    device_identifier = data.device_id or data.serial_number
    if not device_identifier:
        return {
            "status": False,
            "message": "Either device_id or serial_number is required"
        }
    
    # 2. Validate against registered devices in locations.json
    locations = read_locations()
    registered_device = None
    for loc in locations:
        loc_id = str(loc.get("id", "")).strip()
        loc_device_id = str(loc.get("device_id", "")).strip()
        loc_serial = str(loc.get("serial_number", "")).strip()
        loc_ip = str(loc.get("device_ip", "")).strip()
        loc_device_type = str(loc.get("device_type", "")).strip().lower()
        
        # Match location_id AND (device_id OR serial_number) AND device_type is RPI
        if (loc_id == str(data.location_id).strip()) and (
            (loc_device_id and loc_device_id == str(device_identifier).strip()) or 
            (loc_serial and loc_serial == str(device_identifier).strip())
        ):
            registered_device = loc
            break
    
    # 3. Check registration
    if not registered_device:
        return {
            "status": False,
            "message": "Device not registered with this location_id"
        }
    
    # 4. Check that device_type is Raspberry Pi (case-insensitive)
    reg_device_type = str(registered_device.get("device_type", "")).strip().lower()
    if reg_device_type not in ["rpi", "raspberry pi", "raspberrypi"]:
        return {
            "status": False,
            "message": "Device is not registered as a Raspberry Pi - set 'device_type' to 'Raspberry Pi'"
        }
    
    # 5. Check device_ip matches registered device
    registered_ip = str(registered_device.get("device_ip", "")).strip()
    if registered_ip and registered_ip != str(data.device_ip).strip():
        return {
            "status": False,
            "message": "Device IP does not match registered IP"
        }
    
    # 5. Process heartbeat
    key = f"{data.location_id}:{device_identifier}"
    
    # Use provided timestamp or current time
    if data.timestamp:
        try:
            last_communicated = datetime.fromisoformat(data.timestamp.replace('Z', '+00:00'))
        except:
            last_communicated = datetime.now(timezone.utc)
    else:
        last_communicated = datetime.now(timezone.utc)
    
    DEVICE_STATUS[key] = {
        "location_id": data.location_id,
        "device_id": data.device_id or device_identifier,
        "serial_number": data.serial_number or device_identifier,
        "device_ip": data.device_ip,
        "status": data.status,
        "cpu": data.cpu,
        "memory": data.memory,
        "last_communicated_time": last_communicated
    }
    
    # Also update locations.json to mark as online
    for i, loc in enumerate(locations):
        loc_id = str(loc.get("id", "")).strip()
        loc_device_id = str(loc.get("device_id", "")).strip()
        loc_serial = str(loc.get("serial_number", "")).strip()
        
        if (loc_id == str(data.location_id).strip()) and (
            (loc_device_id and loc_device_id == str(device_identifier).strip()) or 
            (loc_serial and loc_serial == str(device_identifier).strip())
        ):
            locations[i]["device_status"] = "online"
            locations[i]["device_ip"] = data.device_ip
            break
    
    write_json_atomic(LOCATIONS_JSON, locations)
    with config_cache_lock:
        _locations_cache = [dict(item) for item in locations]
    
    return {
        "status": True,
        "message": "Heartbeat received"
    }

@app.get("/api/rpi-status")
async def get_rpi_status(
    location_id: Optional[str] = None,
    device_id: Optional[str] = None,
    serial_number: Optional[str] = None
):
    device_identifier = device_id or serial_number
    if location_id and device_identifier:
        key = f"{location_id}:{device_identifier}"
        data = DEVICE_STATUS.get(key)
        
        if not data:
            return {
                "location_id": location_id,
                "device_id": device_id,
                "serial_number": serial_number,
                "status": "offline",
                "is_online": False,
                "message": "No heartbeat received"
            }
        
        # Check if last communicated time is within 90 seconds
        now = datetime.now(timezone.utc)
        time_diff = (now - data["last_communicated_time"]).total_seconds()
        
        if time_diff > DEVICE_HEARTBEAT_TTL:
            return {
                **data,
                "status": "offline",
                "is_online": False,
                "message": f"Last heartbeat too old"
            }
        
        return {
            **data,
            "status": "online",
            "is_online": True
        }
    
    # If no specific location/device provided, return all statuses
    all_statuses = []
    now = datetime.now(timezone.utc)
    for key, data in DEVICE_STATUS.items():
        time_diff = (now - data["last_communicated_time"]).total_seconds()
        is_online = time_diff <= DEVICE_HEARTBEAT_TTL
        all_statuses.append({
            **data,
            "status": "online" if is_online else "offline",
            "is_online": is_online
        })
    return {"statuses": all_statuses}

# --- Edge-Device Heartbeat Aliases ---
@app.post("/edge-device/heartbeat")
async def edge_device_heartbeat_alias(data: RpiHeartbeat):
    return await rpi_heartbeat(data)

@app.get("/edge-device/status")
async def edge_device_status_alias(
    location_id: Optional[str] = None,
    device_id: Optional[str] = None,
    serial_number: Optional[str] = None
):
    return await get_rpi_status(location_id=location_id, device_id=device_id, serial_number=serial_number)

# --- Simple Device Heartbeat Endpoints (as per user's example) ---
@app.post("/device/heartbeat")
def device_heartbeat(data: DeviceHeartbeat):
    """
    Heartbeat endpoint for Raspberry Pi devices
    """
    # Find location_id from locations.json AND update locations
    locations = read_locations()
    location_id = None
    updated = False
    
    for i, loc in enumerate(locations):
        if str(loc.get("device_id", "")).strip() == str(data.device_id).strip():
            location_id = str(loc.get("id", "")).strip()
            # Update this location in the list!
            locations[i]["device_status"] = "online"
            if data.device_ip:
                locations[i]["device_ip"] = str(data.device_ip).strip()
            updated = True
    
    if updated:
        write_json_atomic(LOCATIONS_JSON, locations)
        with config_cache_lock:
            _locations_cache = None  # Clear cache to read fresh data!
    
    # Mark heartbeat with location_id
    success = device_manager.mark_device_heartbeat(data.device_id, data.device_ip, location_id)
    return {"status": success, "message": "heartbeat received" if success else "invalid device info"}

@app.get("/device/status")
def device_status(device_id: str, device_ip: str):
    """
    Check device status and return all required fields
    """
    # First get status from device_manager (primary source)
    status = device_manager.get_device_status(device_id)
    
    # Check if ip matches
    if not status or status["device_ip"] != device_ip:
        return {
            "device_id": device_id,
            "device_ip": device_ip,
            "device_status": "offline",
            "last_communicated_time": ""
        }
    
    return status

@app.get("/api/cameras/with-models")
def get_cameras_with_models():
    """
    Returns every configured camera together with its assigned detection models.

    Response:
    [
      {
        "id": 0,
        "label": "Camera 1",
        "location": "Hyderabad",
        "location_id": "loc-1",
        "rtsp": "rtsp://...",
        "models": ["nik_ppe_best.pt"],
        "detection_active": true
      },
      ...
    ]
    """
    urls      = read_streams_conf()
    metadata  = read_streams_metadata()
    cm        = json.load(open(CAMERA_MODELS_JSON)) if os.path.exists(CAMERA_MODELS_JSON) else {}

    result = []
    for i, u in enumerate(urls):
        meta = metadata[i] if i < len(metadata) and isinstance(metadata[i], dict) else {}
        result.append({
            "id": i,
            "label": meta.get("label") or f"Camera {i+1}",
            "location": meta.get("location", f"Location {i+1}"),
            "location_id": meta.get("location_id", ""),
            "device_id": meta.get("device_id", ""),
            "device_ip": meta.get("device_ip", ""),
            "device_status": meta.get("device_status", "offline"),
            "rtsp": u,
            "models": cm.get(str(i), []),
            "detection_active": str(i) in running and (running[str(i)].get("proc") and running[str(i)]["proc"].poll() is None)
        })
    return result

@app.post("/api/alerts")
def create_alert(d: dict = Body(...)):
    # Local JSON Store
    try:
        data = json.load(open(ALERTS_JSON)) if os.path.exists(ALERTS_JSON) else []
        data.append(d); json.dump(data, open(ALERTS_JSON, "w"), indent=4)
    except: pass
    return {"status": "saved"}

@app.post("/api/alerts/db")
def store_alert_in_db(d: dict = Body(...)):
    """
    Requested API endpoint for alerts data storing in db.
    Payload: {"camera_id": "...", "location": "...", "type_of_alert": "...", "image_path": "..."}
    """
    if not PSYCOPG2_AVAILABLE: return {"error": "psycopg2 not installed"}
    conn = get_db_conn()
    if not conn: return {"error": "no db conn"}
    try:
        cur = conn.cursor()
        insert_alert_db(cur, d.get("camera_id") or d.get("camera"), d.get("location"), d.get("type_of_alert"), d.get("image_path"))
        conn.commit(); cur.close()
        return {"status": "success"}
    except Exception as e:
        conn.rollback(); print(f"[DB] alert insert error: {e}"); return {"error": "failed to store alert"}
    finally: release_db_conn(conn)

@app.get("/api/alerts")
def get_alerts_json():
    try:
        if not os.path.exists(ALERTS_JSON): return []
        with open(ALERTS_JSON, "r") as f:
            data = json.load(f)
        # Ensure data is a list
        if not isinstance(data, list): data = []
        
        results = []
        for a in data:
            if not isinstance(a, dict): continue
            img_path = a.get("image") or ""
            filename = img_path.split("/")[-1] if img_path else ""
            if filename:
                base_url = get_alerts_base_url()
                if base_url:
                    a["image"] = f"{base_url.rstrip('/')}/hls/alerts/{filename}"
                else:
                    a["image"] = f"/hls/alerts/{filename}"
                results.append(a)
        # Show alerts in the order from alerts.json (newest first)
        return results
    except Exception as e:
        print(f"[Alerts] Error reading JSON: {e}")
        return []

@app.get("/api/alerts/db")
def get_alerts_db():
    if not PSYCOPG2_AVAILABLE: return []
    conn = get_db_conn()
    if not conn: return []
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT alert_id, camera_id, location, type_of_alert, image_path, created_at FROM alerts ORDER BY created_at DESC LIMIT 50")
        rows = [dict(r) for r in cur.fetchall()]
        base_url = get_alerts_base_url()
        for r in rows:
            r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else ""
            img_path = r.get("image_path") or ""
            # Always extract just the filename (strips any old/stale domain)
            filename = img_path.split("/")[-1] if img_path else ""
            if filename:
                if base_url:
                    r["image_path"] = f"{base_url.rstrip('/')}/hls/alerts/{filename}"
                else:
                    r["image_path"] = f"/hls/alerts/{filename}"
        cur.close()
        return rows
    except: return []
    finally: release_db_conn(conn)

@app.get("/api/alerts/images")
def get_alert_images():
    if not os.path.exists(ALERTS_DIR): return {"images": [], "total": 0}
    imgs = []
    for f in sorted(os.listdir(ALERTS_DIR), reverse=True):
        if f.lower().endswith((".jpg", ".jpeg", ".png")):
            base_url = get_alerts_base_url()
            if base_url:
                img_url = f"{base_url.rstrip('/')}/hls/alerts/{f}"
            else:
                img_url = f"/hls/alerts/{f}"
            imgs.append({"filename": f, "url": img_url, "size_kb": round(os.path.getsize(os.path.join(ALERTS_DIR, f))/1024, 1)})
    data = get_alerts_json(); amap = {a.get("image").split("/")[-1] if a.get("image") else "": a for a in data}
    for img in imgs:
        m = amap.get(img["filename"], {})
        img.update({"camera": m.get("camera", ""), "event": m.get("event", ""), "time": m.get("time", "")})
    return {"images": imgs, "total": len(imgs)}

@app.get("/api/alerts/images/{filename}")
def get_alert_image(filename: str):
    fp = os.path.abspath(os.path.join(ALERTS_DIR, filename))
    if os.path.commonpath([os.path.abspath(ALERTS_DIR), fp]) != os.path.abspath(ALERTS_DIR):
        return Response(status_code=400)
    return FileResponse(fp, headers={"Access-Control-Allow-Origin": "*"}) if os.path.exists(fp) else Response(status_code=404)

# ─────────────────────────────────────────────────────────────
# ANALYTICS ENDPOINTS
# ─────────────────────────────────────────────────────────────
def _norm_filter_value(value): 
    return str(value or "").strip().lower() 

def _as_list(value): 
    if value is None: 
        return [] 
    if isinstance(value, list): 
        return [str(item).strip() for item in value if str(item).strip()] 
    if isinstance(value, tuple): 
        return [str(item).strip() for item in value if str(item).strip()] 
    return [item.strip() for item in str(value).split(",") if item.strip()] 

def _model_matches(selected_model, camera_models): 
    selected = _norm_filter_value(selected_model) 
    selected_stem = selected[:-3] if selected.endswith(".pt") else selected 
    for model in camera_models: 
        current = _norm_filter_value(model) 
        current_stem = current[:-3] if current.endswith(".pt") else current 
        if selected in (current, current_stem) or selected_stem in (current, current_stem): 
            return True 
    return False

def get_analytics_mapping_core(location: Optional[str] = None, models: Optional[list] = None): 
    """Core logic for analytics mapping.""" 
    cameras = get_cameras_with_models() 
    f_location = _norm_filter_value(location) 
    f_models = _as_list(models) 

    by_location = {} 
    by_usecase = {} 
    matching_cameras = 0 
    
    for cam in cameras: 
        cam_location = cam["location"] 
        cam_models = cam["models"] 
        cam_location_key = _norm_filter_value(cam_location) 
        
        if f_location and cam_location_key != f_location: 
            continue 
        if f_models: 
            if not any(_model_matches(model, cam_models) for model in f_models): 
                continue 
        matching_cameras += 1 
        
        if cam_location not in by_location: 
            by_location[cam_location] = {"location": cam_location, "cameras": [], "usecases": []} 
        
        by_location[cam_location]["cameras"].append({ 
            "id": cam["id"], 
            "label": cam["label"], 
            "location": cam_location, 
            "location_id": cam.get("location_id", ""), 
            "rtsp": cam.get("rtsp", ""), 
            "models": cam_models, 
            "detection_active": cam["detection_active"] 
        }) 
        by_location[cam_location]["usecases"] = sorted(set(by_location[cam_location]["usecases"] + cam_models)) 
        
        for model in cam_models: 
            if model not in by_usecase: 
                by_usecase[model] = {"locations": {}} 
            if cam_location not in by_usecase[model]["locations"]: 
                by_usecase[model]["locations"][cam_location] = {"location": cam_location, "cameras": []} 
            by_usecase[model]["locations"][cam_location]["cameras"].append({ 
                "id": cam["id"], 
                "label": cam["label"], 
                "location": cam_location, 
                "location_id": cam.get("location_id", ""), 
                "rtsp": cam.get("rtsp", ""), 
                "models": cam_models, 
                "detection_active": cam["detection_active"] 
            }) 
            
    final_by_usecase = {} 
    for model, data in by_usecase.items(): 
        final_by_usecase[model] = { 
            "locations": [ 
                {"name": loc, "cameras": loc_data["cameras"]} 
                for loc, loc_data in data["locations"].items() 
            ] 
        } 
            
    return { 
        "by_location": by_location, 
        "by_usecase": final_by_usecase, 
        "summary": { 
            "total_cameras": len(cameras), 
            "matching_cameras": matching_cameras, 
            "matching_locations": len(by_location) 
        } 
    } 

@app.post("/api/analytics/by-location")
def get_analytics_by_location(d: dict = Body(default={})):
    """Returns only location-grouped data."""
    models = d.get("models") or d.get("usecase")
    res = get_analytics_mapping_core(location=d.get("location"), models=models)
    return res.get("by_location", {})

@app.post("/api/analytics/by-usecase")
def get_analytics_by_usecase(d: dict = Body(default={})):
    """Returns only usecase-grouped data."""
    # Support both "usecase" (singular) and "models" (plural) keys
    models = d.get("models") or d.get("usecase")
    res = get_analytics_mapping_core(location=d.get("location"), models=models)
    return res.get("by_usecase", {}) 

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="ui")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8080, reload=False, log_level="warning")





# from fastapi import FastAPI, Body, Query, Request
# from typing import Optional, Tuple, Any
# from pydantic import BaseModel
# from fastapi.middleware.cors import CORSMiddleware
# import asyncio
# import httpx

# # Load .env file
# try:
#     from dotenv import load_dotenv
#     load_dotenv()
# except ImportError:
#     pass  # If dotenv not installed, just use system env vars


# class DevicePingRequest(BaseModel):
#     location: str
#     device_id: str
#     device_ip: str

# class RpiHeartbeat(BaseModel):
#     location_id: str
#     device_id: Optional[str] = None
#     serial_number: Optional[str] = None
#     device_ip: str
#     status: str
#     timestamp: Optional[str] = None
#     cpu: Optional[float] = None
#     memory: Optional[float] = None

# class DeviceHeartbeat(BaseModel):
#     device_id: str
#     device_ip: str

# # Store for RPI heartbeats
# DEVICE_STATUS = {}

# from fastapi.staticfiles import StaticFiles
# from fastapi.responses import Response, FileResponse
# import os, glob, subprocess, mimetypes, signal, json, socket, time, threading

# ALERTS_BASE_URL = os.getenv("ALERTS_BASE_URL", "")
# from concurrent.futures import ThreadPoolExecutor
# from urllib.parse import urlparse
# from datetime import datetime, timezone
# from alert_store import DB_DSN, ensure_alerts_schema, insert_alert_db
# import device_manager
# DEVICE_HEARTBEAT_TTL = device_manager.DEVICE_HEARTBEAT_TTL  # configurable via env var (default 90s)


# # --- New Async Check Functions ---
# SSH_PORT = 22
# HEALTH_CHECK_PORT = 8080

# # --- Whitelist of known Raspberry Pi IP addresses ---
# # Add all your Raspberry Pi IPs here!
# RASPBERRY_PI_IPS = {
#     "192.168.96.78",  # Your current Raspberry Pi
#     # Add more here if you get more Pis!
# }


# async def is_port_open(host: str, port: int, timeout: float = 3.0) -> Tuple[bool, str]:
#     """Async check if a port is open"""
#     try:
#         conn = await asyncio.wait_for(
#             asyncio.open_connection(host, port),
#             timeout=timeout,
#         )
#         reader, writer = conn
#         writer.close()
#         await writer.wait_closed()
#         return True, "Connected"
#     except socket.gaierror as e:
#         return False, f"DNS error: {e}"
#     except asyncio.TimeoutError:
#         return False, "Connection timeout"
#     except OSError as e:
#         return False, str(e)


# async def check_pi_heartbeat(ip: str, port: int = 8080, timeout: float = 3.0) -> Tuple[bool, str]:
#     """Async check Raspberry Pi heartbeat via HTTP"""
#     try:
#         async with httpx.AsyncClient(timeout=timeout) as client:
#             response = await client.get(f"http://{ip}:{port}/health")
#             if response.status_code == 200:
#                 return True, "Heartbeat OK"
#             return False, f"Heartbeat returned {response.status_code}"
#     except Exception as e:
#         return False, str(e)


# async def check_device_online_async(ip: str, use_heartbeat: bool = True, timeout: float = 1.0) -> Tuple[bool, str]:
#     """Async wrapper for device checks: try heartbeat first, then SSH if needed"""
#     if not ip or not ip.strip():
#         return False, "No IP"
    
#     # 1. Validate IP format first
#     try:
#         socket.inet_aton(ip)
#         octets = list(map(int, ip.split('.')))
#         if len(octets) != 4 or any(o < 0 or o > 255 for o in octets):
#             return False, "Invalid IP"
#     except (socket.error, ValueError):
#         return False, "Invalid IP"
    
#     # 2. Try heartbeat first
#     if use_heartbeat:
#         online, msg = await check_pi_heartbeat(ip, HEALTH_CHECK_PORT, timeout=timeout)
#         if online:
#             return online, msg
    
#     # 3. Try SSH as fallback
#     return await is_port_open(ip, SSH_PORT, timeout=timeout)


# def determine_device_online_status_sync(loc: dict, check_network: bool = False) -> Tuple[bool, str]:
#     """Determine device online status synchronously by checking heartbeats first, then network probe if requested"""
#     if not isinstance(loc, dict):
#         return False, "Invalid location data"
    
#     device_id = str(loc.get("device_id", "")).strip()
#     serial = str(loc.get("serial_number", "")).strip()
#     location_id = str(loc.get("id", "")).strip()
#     ip = str(loc.get("device_ip", "")).strip()
    
#     device_identifier = device_id or serial
    
#     # 1. Check in-memory DEVICE_STATUS (from /api/rpi-heartbeat or /edge-device/heartbeat)
#     if device_identifier and location_id:
#         key = f"{location_id}:{device_identifier}"
#         if key in DEVICE_STATUS:
#             hb_data = DEVICE_STATUS[key]
#             now_utc = datetime.now(timezone.utc)
#             time_diff = (now_utc - hb_data["last_communicated_time"]).total_seconds()
#             if time_diff <= DEVICE_HEARTBEAT_TTL:
#                 return True, "Heartbeat received (from /api/rpi-heartbeat)"
                
#     # 2. Check device_manager (from /device/heartbeat)
#     if device_id:
#         device_info = device_manager.get_device_status(device_id)
#         if device_info and device_info.get("device_status") == "online":
#             return True, "Heartbeat received (from /device/heartbeat)"
            
#     # 3. Network check fallback if requested
#     if check_network and ip:
#         online = check_device_online(
#             ip, 
#             timeout=1.0, 
#             expected_device_id=device_id,
#             serial_number=serial,
#             location_id=location_id,
#             is_rpi=True
#         )
#         if online:
#             return True, "Device online via network probe"
            
#     return False, "Offline (No recent heartbeat)"


# async def determine_device_online_status_async(loc: dict, check_network: bool = False, use_heartbeat: bool = True) -> Tuple[bool, str]:
#     """Determine device online status asynchronously by checking heartbeats first, then network probe if requested"""
#     if not isinstance(loc, dict):
#         return False, "Invalid location data"
        
#     device_id = str(loc.get("device_id", "")).strip()
#     serial = str(loc.get("serial_number", "")).strip()
#     location_id = str(loc.get("id", "")).strip()
#     ip = str(loc.get("device_ip", "")).strip()
    
#     device_identifier = device_id or serial
    
#     # 1. Check in-memory DEVICE_STATUS (from /api/rpi-heartbeat or /edge-device/heartbeat)
#     if device_identifier and location_id:
#         key = f"{location_id}:{device_identifier}"
#         if key in DEVICE_STATUS:
#             hb_data = DEVICE_STATUS[key]
#             now_utc = datetime.now(timezone.utc)
#             time_diff = (now_utc - hb_data["last_communicated_time"]).total_seconds()
#             if time_diff <= DEVICE_HEARTBEAT_TTL:
#                 return True, "Heartbeat received (from /api/rpi-heartbeat)"
                
#     # 2. Check device_manager (from /device/heartbeat)
#     if device_id:
#         device_info = device_manager.get_device_status(device_id)
#         if device_info and device_info.get("device_status") == "online":
#             return True, "Heartbeat received (from /device/heartbeat)"
            
#     # 3. Network check fallback if requested
#     if check_network and ip:
#         online, msg = await check_device_online_async(ip, use_heartbeat=use_heartbeat)
#         if online:
#             return True, f"Device online via network probe: {msg}"
            
#     return False, "Offline (No recent heartbeat)"


# def _attach_last_communicated_time(updated_loc: dict, loc: dict) -> None:
#     """Attach last_communicated_time to an updated location dict using DEVICE_STATUS or device_manager."""
#     loc_device_id = str(loc.get("device_id", "")).strip()
#     loc_serial = str(loc.get("serial_number", "")).strip()
#     loc_location_id = str(loc.get("id", "")).strip()
#     device_identifier = loc_device_id or loc_serial

#     # Priority 1: DEVICE_STATUS dict (from /api/rpi-heartbeat or /edge-device/heartbeat)
#     if device_identifier and loc_location_id:
#         key = f"{loc_location_id}:{device_identifier}"
#         if key in DEVICE_STATUS:
#             try:
#                 updated_loc["last_communicated_time"] = DEVICE_STATUS[key]["last_communicated_time"].isoformat()
#                 return
#             except Exception:
#                 pass

#     # Priority 2: device_manager (from /device/heartbeat)
#     if loc_device_id:
#         device_info = device_manager.get_device_status(loc_device_id)
#         if device_info and device_info.get("last_communicated_time"):
#             updated_loc["last_communicated_time"] = device_info["last_communicated_time"]





# # ── Optional DB import ──
# try:
#     import psycopg2
#     import psycopg2.extras
#     from psycopg2 import pool as pg_pool
#     PSYCOPG2_AVAILABLE = True
# except ImportError:
#     PSYCOPG2_AVAILABLE = False

# mimetypes.add_type("application/vnd.apple.mpegurl", ".m3u8")
# mimetypes.add_type("video/mp2t", ".ts")

# BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
# STREAMS_CONF = os.path.join(BASE_DIR, "streams.conf")
# STREAMS_JSON = os.path.join(BASE_DIR, "streams.json")
# LOCATIONS_JSON = os.path.join(BASE_DIR, "locations.json")
# MODEL_DIR    = os.path.join(BASE_DIR, "models")
# HLS_DIR      = os.path.join(BASE_DIR, "hls")
# ALERTS_DIR   = os.path.join(HLS_DIR, "alerts")
# ALERTS_JSON  = os.path.join(ALERTS_DIR, "alerts.json")
# CAMERA_MODELS_JSON = os.path.join(BASE_DIR, "camera_models.json")
# _locations_cache = None
# _streams_metadata_cache = None
# _streams_location_index_cache = None
# config_cache_lock = threading.Lock()

# # ─────────────────────────────────────────────────────────────
# # DB CONFIG
# # ─────────────────────────────────────────────────────────────
# _db_pool = None

# def get_db_pool():
#     global _db_pool
#     if _db_pool is None and PSYCOPG2_AVAILABLE:
#         try:
#             _db_pool = pg_pool.ThreadedConnectionPool(1, 10, dsn=DB_DSN)
#         except Exception as e: print(f"[DB] pool error: {e}")
#     return _db_pool

# def get_db_conn():
#     p = get_db_pool()
#     return p.getconn() if p else None

# def release_db_conn(conn):
#     if _db_pool and conn: _db_pool.putconn(conn)

# def init_db():
#     if not PSYCOPG2_AVAILABLE: return
#     conn = get_db_conn()
#     if not conn: return
#     try:
#         cur = conn.cursor()
#         ensure_alerts_schema(cur)
#         conn.commit(); cur.close()
#     except Exception as e: print(f"[DB] init error: {e}"); conn.rollback()
#     finally: release_db_conn(conn)

# # ─────────────────────────────────────────────────────────────
# # SETUP
# # ─────────────────────────────────────────────────────────────
# os.makedirs(ALERTS_DIR, exist_ok=True)
# if not os.path.exists(ALERTS_JSON): json.dump([], open(ALERTS_JSON, "w"))

# app = FastAPI()
# app.mount("/hls/alerts", StaticFiles(directory=ALERTS_DIR), name="alerts")
# app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# # ─────────────────────────────────────────────────────────────
# # HELPERS
# # ─────────────────────────────────────────────────────────────
# def read_streams_conf():
#     if not os.path.exists(STREAMS_CONF): return []
#     return [l.strip() for l in open(STREAMS_CONF) if l.strip() and not l.strip().startswith("#")]

# def read_streams_metadata():
#     global _streams_metadata_cache, _streams_location_index_cache
#     with config_cache_lock:
#         if _streams_metadata_cache is not None:
#             return [dict(item) for item in _streams_metadata_cache]
#     if not os.path.exists(STREAMS_JSON): return []
#     try:
#         with open(STREAMS_JSON) as fp:
#             data = json.load(fp)
#         data = data if isinstance(data, list) else []
#         # Enrich each stream entry with location data from locations.json (single source of truth)
#         locations = read_locations()
#         location_map = {}
#         for loc in locations:
#             if loc.get("location"):
#                 location_map[loc["location"]] = loc
#             if loc.get("id"):
#                 location_map[loc["id"]] = loc
#         # Now enrich streams
#         enriched = []
#         for item in data:
#             if not isinstance(item, dict):
#                 continue
#             enriched_item = dict(item)
#             # Find matching location
#             matching_loc = None
#             if item.get("location_id") and item.get("location_id") in location_map:
#                 matching_loc = location_map[item.get("location_id")]
#             elif item.get("location") and item.get("location") in location_map:
#                 matching_loc = location_map[item.get("location")]
            
#             if matching_loc:
#                 # Override with location's data as single source of truth
#                 enriched_item["location"] = matching_loc["location"]
#                 enriched_item["location_id"] = matching_loc["id"]
#                 enriched_item["device_id"] = matching_loc.get("device_id", "")
#                 enriched_item["device_ip"] = matching_loc.get("device_ip", "")
#                 enriched_item["device_status"] = matching_loc.get("device_status", "offline")
            
#             enriched.append(enriched_item)
#         # Update cache with enriched data
#         with config_cache_lock:
#             _streams_metadata_cache = [dict(item) for item in enriched]
#             _streams_location_index_cache = build_stream_location_index(_streams_metadata_cache)
#         return enriched
#     except: return []

# def read_locations():
#     global _locations_cache
#     with config_cache_lock:
#         if _locations_cache is not None:
#             return [dict(item) for item in _locations_cache]
#     if not os.path.exists(LOCATIONS_JSON):
#         metadata = read_streams_metadata()
#         seeded = []
#         seen = set()
#         for idx, item in enumerate(metadata):
#             if not isinstance(item, dict): continue
#             name = str(item.get("location", "")).strip() or f"Location {idx + 1}"
#             if name.lower() in seen: continue
#             seen.add(name.lower())
#             seeded.append({
#                 "id": f"loc-{len(seeded) + 1}",
#                 "location": name,
#                 "device_id": item.get("device_id", ""),
#                 "device_ip": item.get("device_ip", ""),
#                 "device_status": item.get("device_status", "offline")
#             })
#         with config_cache_lock:
#             _locations_cache = [dict(item) for item in seeded]
#         return seeded
#     try:
#         with open(LOCATIONS_JSON) as fp:
#             data = json.load(fp)
#         data = data if isinstance(data, list) else []
#         with config_cache_lock:
#             _locations_cache = [dict(item) for item in data if isinstance(item, dict)]
#         return data
#     except: return []

# def normalize_location_entry(item, idx):
#     if not isinstance(item, dict): item = {}
#     location = str(item.get("location", "")).strip()
    
#     # Robust unique ID: Use timestamp + index to prevent overwriting existing locations
#     generated_id = f"loc-{int(time.time() * 1000) + idx}"
    
#     data = {
#         "id": str(item.get("id") or generated_id).strip(),
#         "location": location
#     }
#     data.update(normalize_device_fields(item))
#     return data

# def normalize_device_fields(item):
#     # Flexible key mapping to support UI variations (e.g. "device id", "device ip")
#     d_id = item.get("device_id") or item.get("device id") or ""
#     d_ip = item.get("device_ip") or item.get("device ip") or ""
#     d_type = item.get("device_type") or item.get("device type") or ""
    
#     return {
#         "device_id": str(d_id).strip(),
#         "serial_number": str(item.get("serial_number", "")).strip(),
#         "device_ip": str(d_ip).strip(),
#         "device_type": str(d_type).strip(),
#         "device_status": str(item.get("device_status", "offline")).strip().lower() or "offline"
#     }

# def normalize_stream_entry(item, idx):
#     if isinstance(item, dict):
#         rtsp = str(item.get("rtsp", "")).strip()
#         location = str(item.get("location", "")).strip()
#         location_id = str(item.get("location_id", "")).strip()
#         device_fields = normalize_device_fields(item)
#     else:
#         rtsp = str(item).strip()
#         location = f"Location {idx + 1}"
#         location_id = f"loc-{idx + 1}"
#         device_fields = normalize_device_fields({})
#     data = {
#         "rtsp": rtsp,
#         "location": location,
#         "location_id": location_id
#     }
#     data.update(device_fields)
#     return data

# def write_json_atomic(path, data):
#     tmp_path = f"{path}.tmp"
#     with open(tmp_path, "w") as fp:
#         json.dump(data, fp, indent=2)
#     try:
#         os.replace(tmp_path, path)
#     except OSError:
#         with open(path, "w") as fp:
#             json.dump(data, fp, indent=2)
#         try:
#             os.remove(tmp_path)
#         except OSError:
#             pass

# def is_valid_rtsp_url(value):
#     try:
#         parsed = urlparse(value)
#         return parsed.scheme in ("rtsp", "rtsps") and bool(parsed.hostname)
#     except: return False

# def sanitize_location(value):
#     clean = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in " _-.,()").strip()
#     return clean[:120]

# def build_stream_location_index(streams_metadata):
#     active_locations = set()
#     for stream in streams_metadata:
#         if not isinstance(stream, dict): continue
#         if stream.get("location_id"): active_locations.add(("id", stream.get("location_id")))
#         if stream.get("location"): active_locations.add(("name", stream.get("location")))
#     return active_locations

# def get_stream_location_index():
#     global _streams_location_index_cache
#     with config_cache_lock:
#         if _streams_location_index_cache is not None:
#             return set(_streams_location_index_cache)
#     metadata = read_streams_metadata()
#     with config_cache_lock:
#         if _streams_location_index_cache is None:
#             _streams_location_index_cache = build_stream_location_index(metadata)
#         return set(_streams_location_index_cache)

# def location_stream_active(loc, streams_index):
#     return ("id", loc.get("id")) in streams_index or ("name", loc.get("location")) in streams_index

# def _async_kill(proc):
#     def target():
#         if not proc: return
#         try:
#             proc.kill()
#             proc.wait(timeout=2.0)
#         except: pass
#     threading.Thread(target=target, daemon=True).start()

# def _kill_raw_ffmpeg_for_camera(cid: str):
#     """Kill the raw FFmpeg for a camera without cleaning its HLS directory.
#     Called before starting detection so the detector's RTSP connection is the
#     only one consuming camera bandwidth — prevents slow-motion caused by two
#     concurrent RTSP connections competing for the same stream."""
#     cid = str(cid)
#     if cid not in cid_to_rtsp:
#         return
#     rtsp = cid_to_rtsp[cid]
#     # Only kill if this camera is the sole user of this RTSP URL.
#     # If multiple camera IDs share the same RTSP, keep the proc alive for others.
#     count = sum(1 for k, v in cid_to_rtsp.items() if v == rtsp)
#     if count <= 1 and rtsp in rtsp_cache:
#         cached = rtsp_cache[rtsp]
#         if cached["proc"].poll() is None:
#             _async_kill(cached["proc"])
#         del rtsp_cache[rtsp]
#     if cid in cid_to_rtsp:
#         del cid_to_rtsp[cid]

# def _clean_stale_detected_segments(camera: str):
#     """Remove stale .ts segments from the detected HLS directory.
#     Called after stop_detection so HLS.js won't serve frozen detected frames
#     when the player switches back to the raw stream (prevents black screen)."""
#     det_dir = os.path.join(HLS_DIR, f"stream{camera}_detected")
#     if os.path.exists(det_dir) and not os.path.islink(det_dir):
#         for ts_file in glob.glob(os.path.join(det_dir, "*.ts")):
#             try: os.remove(ts_file)
#             except: pass

# def _clean_camera_dirs(camera: str):
#     # Clean only raw stream directory, leave detected alone if detection is running!
#     # First handle raw
#     raw_dir = os.path.join(HLS_DIR, f"stream{camera}_raw")
#     if os.path.exists(raw_dir):
#         if os.path.islink(raw_dir):
#             os.unlink(raw_dir)
#         else:
#             import shutil
#             shutil.rmtree(raw_dir)
#     os.makedirs(raw_dir, exist_ok=True)
    
#     # Only clean detected if NOT running detection!
#     if camera not in running:
#         det_dir = os.path.join(HLS_DIR, f"stream{camera}_detected")
#         if os.path.exists(det_dir):
#             if os.path.islink(det_dir):
#                 os.unlink(det_dir)
#             else:
#                 import shutil
#                 shutil.rmtree(det_dir)
#         os.makedirs(det_dir, exist_ok=True)

# def _extract_ip_port(u):
#     try:
#         p = urlparse(u)
#         return p.hostname or "", p.port or 554
#     except: return "", 554

# def check_pi_heartbeat_sync(ip: str, port: int, timeout: float = 3.0) -> Tuple[bool, str]:
#     try:
#         import requests
#         response = requests.get(f"http://{ip}:{port}/health", timeout=timeout)
#         if response.status_code == 200:
#             return True, "Heartbeat OK"
#         return False, f"Heartbeat returned {response.status_code}"
#     except Exception as e:
#         return False, str(e)


# def check_device_online(ip: str, timeout: float = 3.0, expected_device_id: Optional[str] = None, serial_number: Optional[str] = None, location_id: Optional[str] = None, is_rpi: bool = False) -> bool:
#     """
#     Check device status: first check DEVICE_STATUS for recent heartbeat if it's an RPI, then try port 22
#     """
#     # First check DEVICE_STATUS if we have location_id, identifier, AND it's an RPI
#     device_identifier = expected_device_id or serial_number
#     if device_identifier and location_id and is_rpi:
#         key = f"{location_id}:{device_identifier}"
#         if key in DEVICE_STATUS:
#             data = DEVICE_STATUS[key]
#             now = datetime.now(timezone.utc)
#             time_diff = (now - data["last_communicated_time"]).total_seconds()
#             if time_diff <= 90:
#                 return True
    
#     if not ip or not ip.strip():
#         return False
#     ip = ip.strip()
    
#     # 1. Strict IP address format validation
#     try:
#         socket.inet_aton(ip)
#         octets = list(map(int, ip.split('.')))
#         if len(octets) != 4 or any(o < 0 or o > 255 for o in octets):
#             return False
#     except (socket.error, ValueError):
#         return False
    
#     # 2. Try heartbeat (port 8080 /health) first
#     heartbeat_online, _ = check_pi_heartbeat_sync(ip, HEALTH_CHECK_PORT, timeout)
#     if heartbeat_online:
#         return True
    
#     # 3. Try SSH (port 22) as fallback
#     try:
#         with socket.create_connection((ip, 22), timeout=timeout):
#             return True
#     except (socket.timeout, ConnectionRefusedError, OSError):
#         return False

# # ─────────────────────────────────────────────────────────────
# # CAMERA STATE
# # ─────────────────────────────────────────────────────────────
# running = {}
# # Cache: rtsp_url → process info:
# rtsp_cache = {}  # key: rtsp url (normalized) → {"proc": subprocess, "sd": stream_dir}
# # Map from camera id to rtsp url (to share the same segments
# cid_to_rtsp = {}

# def start_raw_stream(i, u):
#     # Normalize RTSP URL to use as cache key
#     normalized_rtsp = u.strip()
#     cid = str(i)
#     cid_to_rtsp[cid] = normalized_rtsp
    
#     # Check if we already have a running FFmpeg for this RTSP
#     if normalized_rtsp in rtsp_cache:
#         cached = rtsp_cache[normalized_rtsp]
#         if cached["proc"].poll() is None:
#             # Still running, just symlink our stream dir to cached dir
#             our_sd = os.path.join(HLS_DIR, f"stream{cid}_raw")
#             cached_sd = cached["sd"]
#             # Remove old dir if exists
#             import shutil
#             if os.path.lexists(our_sd):
#                 if os.path.islink(our_sd):
#                     os.unlink(our_sd)
#                 elif os.path.isdir(our_sd):
#                     shutil.rmtree(our_sd)
#                 else:
#                     os.remove(our_sd)
#             # Symlink
#             os.symlink(os.path.basename(cached_sd), our_sd)
#             return
#         else:
#             # Proc died, remove from cache
#             del rtsp_cache[normalized_rtsp]
    
#     # New RTSP, start new FFmpeg
#     sd = os.path.join(HLS_DIR, f"stream{cid}_raw")
#     # Clean directory completely
#     import shutil
#     if os.path.lexists(sd):
#         if os.path.islink(sd):
#             os.unlink(sd)
#         elif os.path.isdir(sd):
#             shutil.rmtree(sd)
#         else:
#             os.remove(sd)
#     os.makedirs(sd, exist_ok=True)
#     # Step 1: Create placeholder playlist
#     placeholder_playlist = os.path.join(sd, "playlist.m3u8")
#     with open(placeholder_playlist, "w") as f:
#         f.write("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:3\n#EXT-X-MEDIA-SEQUENCE:0\n")
#     # Step2: Clean old files (just in case)
#     for f in glob.glob(os.path.join(sd, "*")):
#         if f != placeholder_playlist:
#             try: os.remove(f)
#             except: pass
#     # Step3: Log file
#     log_file = os.path.join(sd, "ffmpeg.log")
#     try: os.remove(log_file)
#     except: pass
#     # FFmpeg cmd
#     cmd = [
#         "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
#         "-rtsp_transport", "tcp",
#         "-probesize", "10M", "-analyzeduration", "10M",
#         "-rtsp_flags", "prefer_tcp",
#         "-timeout", "5000000",
#         "-i", u,
#         "-r", "15",
#         "-an",
#         "-vf", "scale=854:-2",
#         "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
#         "-b:v", "500k", "-maxrate", "600k", "-bufsize", "1000k",
#         "-threads", "2", "-pix_fmt", "yuv420p",
#         "-g", "30", "-keyint_min", "30",
#         "-f", "hls",
#         "-hls_time", "2",
#         "-hls_list_size", "8",
#         "-hls_flags", "delete_segments+independent_segments+discont_start+temp_file",
#         "-hls_segment_filename", os.path.join(sd, "segment_%d.ts"),
#         os.path.join(sd, "playlist.m3u8")
#     ]
#     log_fh = open(log_file, "w")
#     proc = subprocess.Popen(cmd, stdout=log_fh, stderr=log_fh)
#     rtsp_cache[normalized_rtsp] = {"proc": proc, "sd": sd}
#     # Also, symlink any other cids already mapped to this rtsp
#     for other_cid, other_rtsp in list(cid_to_rtsp.items()):
#         if other_rtsp == normalized_rtsp and other_cid != cid:
#             other_sd = os.path.join(HLS_DIR, f"stream{other_cid}_raw")
#             import shutil
#             if os.path.lexists(other_sd):
#                 if os.path.islink(other_sd):
#                     os.unlink(other_sd)
#                 elif os.path.isdir(other_sd):
#                     shutil.rmtree(other_sd)
#                 else:
#                     os.remove(other_sd)
#             os.symlink(os.path.basename(sd), other_sd)

# def stop_raw_stream(i):
#     cid = str(i)
#     if cid in running: _async_kill(running[cid].get("proc")); del running[cid]; _clean_camera_dirs(cid)
#     # Check if it's in our cid_to_rtsp
#     if cid in cid_to_rtsp:
#         rtsp = cid_to_rtsp[cid]
#         # Check if this is the last cid using this rtsp
#         count = sum(1 for k, v in cid_to_rtsp.items() if v == rtsp)
#         if count <= 1:
#             if rtsp in rtsp_cache:
#                 cached = rtsp_cache[rtsp]
#                 proc = cached["proc"]
#                 if proc.poll() is None:
#                     _async_kill(proc)
#                 # Clean up the main dir
#                 sd = cached["sd"]
#                 import shutil
#                 try:
#                     shutil.rmtree(sd)
#                 except:
#                     pass
#                 del rtsp_cache[rtsp]
#         del cid_to_rtsp[cid]

# @app.on_event("startup")
# async def startup_event():
#     init_db()
#     for i, u in enumerate(read_streams_conf()): start_raw_stream(i, u)

# # ─────────────────────────────────────────────────────────────
# # ROUTES
# # ─────────────────────────────────────────────────────────────
# @app.get("/hls/camera/{cam_id}/{filename}")
# async def serve_camera_virtual_file(cam_id: str, filename: str):
#     sub = f"stream{cam_id}_detected" if cam_id in running and os.path.exists(os.path.join(HLS_DIR, f"stream{cam_id}_detected/playlist.m3u8")) else f"stream{cam_id}_raw"
#     return await serve_hls(f"{sub}/{filename}")

# @app.get("/hls/camera/{cam_id}/playlist.m3u8")
# async def smart_hls_playlist(cam_id: str): return await serve_camera_virtual_file(cam_id, "playlist.m3u8")

# @app.get("/hls/{path:path}")
# async def serve_hls(path: str):
#     if path.endswith("playlist.m3u8") and "_raw" in path:
#         cid = path.split("_raw")[0].replace("stream", "")
#         if cid in running and os.path.exists(os.path.join(HLS_DIR, f"stream{cid}_detected/playlist.m3u8")):
#             return await serve_hls(f"stream{cid}_detected/playlist.m3u8")
#     fp = os.path.join(HLS_DIR, path)
#     if not os.path.exists(fp): return Response(status_code=404)
#     mt = "application/vnd.apple.mpegurl" if path.endswith(".m3u8") else "video/mp2t" if path.endswith(".ts") else "application/octet-stream"
#     try:
#         with open(fp, "rb") as f:
#             content = f.read()
#         return Response(content=content, media_type=mt, headers={"Cache-Control": "no-store"})
#     except Exception as e:
#         return Response(status_code=500)

# @app.get("/api/models")
# def get_models(): return {"models": [f for f in os.listdir(MODEL_DIR) if f.endswith(".pt")]} if os.path.exists(MODEL_DIR) else {"models": []}

# @app.get("/api/streams")
# def get_streams(
#     location_id: Optional[str] = None, 
#     location: Optional[str] = None,
#     rtsp: Optional[str] = None
# ):
#     # If rtsp is provided in a GET request, we treat it as an 'add' for convenience
#     if rtsp and isinstance(rtsp, str):
#         return save_streams(None, rtsp, location, location_id)
        
#     metadata = read_streams_metadata()
    
#     results = []
#     for i, meta in enumerate(metadata):
#         if not isinstance(meta, dict): continue
        
#         # Filtering logic (Case-insensitive for 'location')
#         m_loc_id = str(meta.get("location_id") or "").strip()
#         m_loc_name = str(meta.get("location") or "").strip().lower()
        
#         f_loc_id = str(location_id or "").strip()
#         f_loc_name = str(location or "").strip().lower()

#         if f_loc_id and m_loc_id != f_loc_id:
#             continue
#         if f_loc_name and m_loc_name != f_loc_name:
#             continue
            
#         results.append({
#             "id": i,
#             "label": meta.get("label") or f"Camera {i+1}",
#             "location": meta.get("location", f"Location {i+1}"),
#             "location_id": meta.get("location_id", ""),
#             "device_id": meta.get("device_id", ""),
#             "device_ip": meta.get("device_ip", ""),
#             "device_status": meta.get("device_status", "offline"),
#             "rtsp": meta.get("rtsp", ""),
#             "hls_live": f"/hls/camera/{i}/playlist.m3u8",
#             "hls_raw": f"/hls/stream{i}_raw/playlist.m3u8",
#             "hls_detected": f"/hls/stream{i}_detected/playlist.m3u8"
#         })
#     return results

# @app.get("/api/streams/fetch")
# def fetch_filtered_streams_get(
#     location_id: Optional[str] = None, 
#     location: Optional[str] = None
# ):
#     """Fetch filtered streams via URL query parameters."""
#     return get_streams(location_id=location_id, location=location)

# @app.post("/api/streams/fetch")
# def fetch_filtered_streams_post(d: Optional[dict] = Body(default={})):
#     """Fetch filtered streams via JSON body."""
#     try:
#         # Handle cases where d might be None if 'null' is sent as body
#         payload = d or {}
#         return get_streams(
#             location_id=payload.get("location_id"),
#             location=payload.get("location")
#         )
#     except Exception as e:
#         print(f"[API] fetch_filtered_streams_post error: {e}")
#         return {"error": str(e), "status": "failed"}

# @app.post("/api/streams")
# def save_streams(
#     data: Optional[list] = Body(default=None),
#     rtsp: Optional[str] = None,
#     location: Optional[str] = None,
#     location_id: Optional[str] = None
# ):
#     global _streams_metadata_cache, _streams_location_index_cache
    
#     # Load valid locations from locations.json (single source of truth)
#     valid_locations = read_locations()
#     valid_loc_map = {}
#     for loc in valid_locations:
#         if loc.get("location"):
#             valid_loc_map[loc["location"]] = loc
#         if loc.get("id"):
#             valid_loc_map[loc["id"]] = loc
    
#     # Helper to get valid location data
#     def get_valid_loc(loc_name=None, loc_id=None):
#         if loc_id and loc_id in valid_loc_map:
#             return valid_loc_map[loc_id]
#         if loc_name and loc_name in valid_loc_map:
#             return valid_loc_map[loc_name]
#         return None
    
#     # CASE 1: Append/Update/Remove Mode (Query Params)
#     if rtsp is not None:
#         current_metadata = read_streams_metadata()
        
#         # Find valid location
#         valid_loc = get_valid_loc(location, location_id)
#         if not valid_loc and rtsp.strip():
#             # If we're adding a stream, require valid location
#             return {"error": "Location must be from your manually managed locations. Please use a valid location name or ID."}
        
#         # Find if this specific RTSP already exists
#         exists_idx = -1
#         for i, entry in enumerate(current_metadata):
#             if entry.get("rtsp") == rtsp.strip():
#                 exists_idx = i
#                 break
        
#         # If RTSP is provided as an empty string via query param (e.g., ?rtsp=), remove it
#         if not rtsp.strip():
#             if exists_idx >= 0:
#                 stop_raw_stream(exists_idx)
#                 current_metadata.pop(exists_idx)
#         else:
#             new_entry = {
#                 "rtsp": rtsp.strip(),
#                 "location": valid_loc["location"],
#                 "location_id": valid_loc["id"],
#                 "device_id": valid_loc.get("device_id", ""),
#                 "device_ip": valid_loc.get("device_ip", ""),
#                 "device_status": valid_loc.get("device_status", "offline")
#             }
#             if exists_idx >= 0:
#                 # Merge: Only update fields that are provided
#                 old_entry = current_metadata[exists_idx]
#                 for key, val in new_entry.items():
#                     if val or key not in old_entry: old_entry[key] = val
#             else:
#                 current_metadata.append(new_entry)
        
#         entries = current_metadata

#     # CASE 2: Smart Bulk Save Mode (JSON Body)
#     elif data is not None:
#         current_metadata = read_streams_metadata()
        
#         # 1. Identify locations present in the incoming data
#         incoming_locations = set()
#         incoming_location_ids = set()
        
#         if location: incoming_locations.add(location.strip().lower())
#         if location_id: incoming_location_ids.add(str(location_id).strip())
        
#         new_entries_to_add = []
#         for item in data:
#             entry = normalize_stream_entry(item, len(new_entries_to_add))
#             if entry.get("rtsp"):
#                 # Validate location is from valid locations
#                 valid_loc = get_valid_loc(entry.get("location"), entry.get("location_id"))
#                 if not valid_loc:
#                     continue  # Skip invalid locations
#                 # Update entry with valid location data
#                 entry["location"] = valid_loc["location"]
#                 entry["location_id"] = valid_loc["id"]
#                 entry["device_id"] = valid_loc.get("device_id", "")
#                 entry["device_ip"] = valid_loc.get("device_ip", "")
#                 entry["device_status"] = valid_loc.get("device_status", "offline")
                
#                 new_entries_to_add.append(entry)
#                 if entry.get("location"): incoming_locations.add(entry["location"].strip().lower())
#                 if entry.get("location_id"): incoming_location_ids.add(str(entry["location_id"]).strip())
        
#         # 2. Filter out current entries that belong to the same locations
#         # This allows us to "replace" cameras for specific locations without deleting others
#         final_entries = []
#         for old_entry in current_metadata:
#             old_loc = str(old_entry.get("location") or "").strip().lower()
#             old_loc_id = str(old_entry.get("location_id") or "").strip()
            
#             if old_loc in incoming_locations or old_loc_id in incoming_location_ids:
#                 # This location is being updated by the incoming data, so we skip the old record
#                 continue
#             final_entries.append(old_entry)
            
#         # 3. Add the new entries
#         final_entries.extend(new_entries_to_add)
#         entries = final_entries
#     else:
#         # Fallback to current state
#         entries = read_streams_metadata()

#     # Common persistence logic
#     old_metadata = read_streams_metadata()
#     old_cid_to_rtsp = dict(cid_to_rtsp)  # Snapshot of current state
#     urls = [entry["rtsp"] for entry in entries if entry.get("rtsp")]
#     open(STREAMS_CONF, "w").write("\n".join(urls))
#     write_json_atomic(STREAMS_JSON, entries)
    
#     with config_cache_lock:
#         _streams_metadata_cache = None  # Clear cache so it gets enriched again on next read
#         _streams_location_index_cache = None
    
#     # ONLY modify streams that actually changed, NOT ALL!
#     for i, u in enumerate(urls):
#         cid = str(i)
#         old_u = old_cid_to_rtsp.get(cid)
#         if old_u != u:  # If RTSP changed OR new stream
#             # Stop old if it was running
#             if old_u is not None:
#                 stop_raw_stream(int(cid))
#                 # Clean both detected/raw on
# ly if this cid had a different RTSP before
#                 _clean_camera_dirs(str(cid))
#             start_raw_stream(i, u)
    
#     # Stop streams that are NO LONGER in the new list
#     max_new_idx = len(urls) - 1
#     for cid in list(old_cid_to_rtsp.keys()):
#         idx = int(cid)
#         if idx > max_new_idx:
#             stop_raw_stream(idx)
#             _clean_camera_dirs(cid)
    
#     return get_streams(location_id=location_id, location=location)

# @app.delete("/api/streams")
# def delete_stream(
#     rtsp: str = Query(...),
#     location_id: str = Query(...),
#     location: str = Query(...)
# ):
#     """
#     Strictly delete a camera stream. 
#     Requires RTSP URL, location_id, AND location name to match exactly.
#     """
#     global _streams_metadata_cache, _streams_location_index_cache
    
#     current_metadata = read_streams_metadata()
#     exists_idx = -1
    
#     # Input cleanup
#     req_rtsp = rtsp.strip()
#     req_loc_id = location_id.strip()
#     req_loc_name = location.strip().lower()
    
#     for i, entry in enumerate(current_metadata):
#         # Strict matching logic: All three must match the record in our database
#         stored_rtsp = entry.get("rtsp", "")
#         stored_loc_id = str(entry.get("location_id", "")).strip()
#         stored_loc_name = str(entry.get("location", "")).strip().lower()
        
#         if stored_rtsp == req_rtsp and stored_loc_id == req_loc_id and stored_loc_name == req_loc_name:
#             exists_idx = i
#             break
            
#     if exists_idx >= 0:
#         # Stop the FFmpeg process
#         stop_raw_stream(exists_idx)
#         # Remove from metadata
#         current_metadata.pop(exists_idx)
        
#         # Save changes
#         urls = [entry["rtsp"] for entry in current_metadata if entry.get("rtsp")]
#         open(STREAMS_CONF, "w").write("\n".join(urls))
#         write_json_atomic(STREAMS_JSON, current_metadata)
        
#         with config_cache_lock:
#             _streams_metadata_cache = [dict(entry) for entry in current_metadata]
#             _streams_location_index_cache = build_stream_location_index(_streams_metadata_cache)
            
#         return {"status": "deleted", "message": "Camera removed successfully."}
    
#     return {
#         "status": "failed", 
#         "message": "Deletion denied. Provided RTSP, Location ID, or Location Name does not match our records.",
#         "hint": "Ensure all 3 parameters are correct."
#     }

# @app.get("/api/status")
# def get_status():
#     for cid in list(running.keys()):
#         if not (running[cid].get("proc") and running[cid]["proc"].poll() is None): del running[cid]
#     return {"active": list(running.keys()), "models": {k: v["models"] for k, v in running.items()}}

# @app.post("/api/start")
# def start_detection(d: dict):
#     cid, rtsp, conf, iou = str(d["camera"]), d["rtsp"], float(d.get("conf", 0.25)), float(d.get("iou", 0.45))
#     if not is_valid_rtsp_url(rtsp): return {"error": "invalid rtsp"}
#     mods = list(d["models"]) if "models" in d else [d["model"]] if "model" in d else []
#     if not mods: return {"error": "no model"}
    
#     # Get location from payload, or use saved location from streams.json, or default label
#     loc = d.get("location")
#     if not loc:
#         # Try to pull saved location from streams metadata
#         # First, search by RTSP URL (more reliable than index)
#         metadata = read_streams_metadata()
#         for entry in metadata:
#             if isinstance(entry, dict) and entry.get("rtsp") == rtsp:
#                 loc = entry.get("location")
#                 break
#         # If not found by RTSP, try by index (fallback)
#         if not loc:
#             try:
#                 cam_idx = int(cid)
#                 if cam_idx < len(metadata) and isinstance(metadata[cam_idx], dict):
#                     loc = metadata[cam_idx].get("location")
#             except (ValueError, IndexError):
#                 pass
#     # If still no location, use default label
#     if not loc:
#         loc = f"Camera {int(cid)+1}"
#     loc = sanitize_location(loc) or loc
    
#     # Save model assignment to camera_models.json
#     if os.path.exists(CAMERA_MODELS_JSON):
#         cm = json.load(open(CAMERA_MODELS_JSON))
#     else:
#         cm = {}
#     cm[cid] = mods
#     json.dump(cm, open(CAMERA_MODELS_JSON, "w"), indent=2)
    
#     if cid in running: 
#         _async_kill(running[cid].get("proc")); 
#         del running[cid]
#     # Only clean detected dir, leave raw dir alone!
#     def clean_only_detected(camera: str):
#         det_dir = os.path.join(HLS_DIR, f"stream{camera}_detected")
#         if os.path.exists(det_dir):
#             if os.path.islink(det_dir):
#                 os.unlink(det_dir)
#             else:
#                 import shutil
#                 shutil.rmtree(det_dir)
#         os.makedirs(det_dir, exist_ok=True)
#     clean_only_detected(cid)

#     # Kill the raw FFmpeg for this camera before starting the detector.
#     # Without this, both the raw FFmpeg and the detector worker open their own
#     # RTSP connection simultaneously. Two concurrent RTSP consumers compete for
#     # the camera's available bandwidth, causing the detected stream to run in
#     # slow-motion. The detector opens its own RTSP connection internally, so
#     # the raw FFmpeg is not needed while detection is active.
#     _kill_raw_ffmpeg_for_camera(cid)

#     # Pass location to worker
#     cmd = ["python3", os.path.join(BASE_DIR, "detector/start_detection.py"), cid, rtsp, ",".join(mods), str(conf), str(iou), loc]
#     log = open(os.path.join(HLS_DIR, f"stream{cid}_detected/worker.log"), "a")
#     running[cid] = {"proc": subprocess.Popen(cmd, stdout=log, stderr=log), "models": mods, "conf": conf, "iou": iou, "location": loc}
#     return {"status": "started", "camera": cid, "models": mods, "location": loc}

# @app.post("/api/stop")
# def stop_detection(d: dict):
#     cid = str(d["camera"])
#     if cid in running:
#         proc = running[cid].get("proc")
#         del running[cid]
#         # Synchronously wait for the detector process to exit before restarting the raw
#         # stream. Fire-and-forget (_async_kill) causes a race: the detector may still be
#         # writing detected HLS segments while the player has already switched to raw,
#         # resulting in a black screen or frozen playback for 3-4 seconds.
#         if proc:
#             try:
#                 proc.kill()
#                 proc.wait(timeout=3.0)
#             except: pass
#         # Remove stale detected .ts segments so HLS.js doesn't serve old frozen frames
#         # when the video element switches back to the raw stream playlist.
#         _clean_stale_detected_segments(cid)
#     urls = read_streams_conf()
#     if int(cid) < len(urls):
#         start_raw_stream(int(cid), urls[int(cid)])
#     return {"status": "stopped"}

# @app.post("/api/detection/start")
# def ds_alias(d: dict): return start_detection(d)
# @app.post("/api/detection/stop")
# def dst_alias(d: dict): return stop_detection(d)
# @app.get("/api/detection/status")
# def dsta_alias(): return get_status()

# @app.get("/api/camera-models")
# def get_cm(): return json.load(open(CAMERA_MODELS_JSON)) if os.path.exists(CAMERA_MODELS_JSON) else {}
# @app.get("/api/camera-models/{camera_id}")
# def get_cm_id(camera_id: str): return {"models": get_cm().get(camera_id, [])}
# @app.post("/api/camera-models")
# def save_cm(d: dict = Body(...)):
#     # Get old model assignments first
#     old_cm = json.load(open(CAMERA_MODELS_JSON)) if os.path.exists(CAMERA_MODELS_JSON) else {}
    
#     # Save new assignments
#     json.dump(d, open(CAMERA_MODELS_JSON, "w"), indent=2)
    
#     # Stop detection for any cameras that no longer have models
#     for cid in list(old_cm.keys()):
#         # Check if this camera still has models in new assignment
#         new_has_models = cid in d and len(d.get(cid, [])) > 0
#         if not new_has_models and cid in running:
#             # Stop detection for this camera
#             if cid in running: 
#                 _async_kill(running[cid].get("proc"))
#                 del running[cid]
#             # DO NOT clean detected directory - leave existing files there!
#             # Start raw stream again
#             urls = read_streams_conf()
#             if int(cid) < len(urls): 
#                 start_raw_stream(int(cid), urls[int(cid)])
    
#     return {"status": "saved"}

# @app.get("/api/locations")
# async def get_locations(
#     request: Request,
#     location: Optional[str] = None,
#     device_id: Optional[str] = None,
#     serial_number: Optional[str] = None,
#     device_ip: Optional[str] = None,
#     device_status: Optional[str] = None,
#     device_type: Optional[str] = None,
#     id: Optional[str] = None,
#     use_heartbeat: bool = True,
#     check_status: bool = True
# ):
#     """
#     Returns all locations with real-time device status.
#     - If 'location' is provided, it treats it as an 'add/update' request for convenience (GET method).
#     - check_status=True  → async network ping per device (slow but accurate)
#     - check_status=False → heartbeat-only status check (fast, default)
#     """
#     # 1. If location is provided, treat as an ADD request (matching UI team usage)
#     if location:
#         # Check for keys with spaces in the query string too
#         params = dict(request.query_params)
#         d_id = device_id or params.get("device id")
#         d_ip = device_ip or params.get("device ip")
#         d_type = device_type or params.get("device type")
        
#         # Call save_locations logic
#         save_locations(
#             location=location,
#             device_id=d_id,
#             serial_number=serial_number,
#             device_ip=d_ip,
#             device_status=device_status,
#             device_type=d_type,
#             id=id
#         )

#     # 2. Proceed with normal GET logic
#     locations = read_locations()
#     updated_locations = []

#     if check_status and not location: # Skip slow check if we just added/updated
#         # Slow path: async status check (heartbeat + optional network ping)
#         tasks = [determine_device_online_status_async(loc, check_network=True) for loc in locations]
#         results = await asyncio.gather(*tasks)
#         for loc, (is_online, message) in zip(locations, results):
#             updated_loc = dict(loc)
#             updated_loc["device_status"] = "online" if is_online else "offline"
#             updated_loc["status_message"] = message
#             _attach_last_communicated_time(updated_loc, loc)
#             updated_locations.append(updated_loc)
#     else:
#         # Fast path: heartbeat-only (no network I/O)
#         for loc in locations:
#             is_online, message = determine_device_online_status_sync(loc, check_network=False)
#             updated_loc = dict(loc)
#             updated_loc["device_status"] = "online" if is_online else "offline"
#             updated_loc["status_message"] = message
#             _attach_last_communicated_time(updated_loc, loc)
#             updated_locations.append(updated_loc)

#     return {"locations": updated_locations}

# @app.post("/api/locations")
# def save_locations(
#     data: Optional[Any] = Body(default=None),
#     location: Optional[str] = None,
#     device_id: Optional[str] = None,
#     serial_number: Optional[str] = None,
#     device_ip: Optional[str] = None,
#     device_status: Optional[str] = None,
#     device_type: Optional[str] = None,
#     id: Optional[str] = None
# ):
#     global _locations_cache, _streams_metadata_cache, _streams_location_index_cache
    
#     # 1. Load current state
#     current_locations = read_locations()
    
#     # 2. Determine if we are appending via Query Params or overwriting via Body
#     # We prioritize Query Params if 'location' is present
#     if location and isinstance(location, str):
#         # APPEND/UPDATE MODE (Query Params)
#         status = str(device_status or "offline").strip().lower()
#         device_type_str = str(device_type or "").strip().lower()
#         is_rpi = device_type_str in ["rpi", "raspberry pi", "raspberrypi"]
        
#         if device_ip and is_rpi:
#             status = "online" if check_device_online(
#                 device_ip, 
#                 timeout=1.0, 
#                 expected_device_id=device_id,
#                 serial_number=serial_number,
#                 location_id=id, 
#                 is_rpi=is_rpi
#             ) else "offline"
#         else:
#             status = "offline"

#         new_entry = {
#             "id": str(id or f"loc-{int(time.time())}").strip(),
#             "location": str(location).strip(),
#             "device_id": str(device_id or "").strip(),
#             "serial_number": str(serial_number or "").strip(),
#             "device_ip": str(device_ip or "").strip(),
#             "device_type": str(device_type or "").strip(),
#             "device_status": status
#         }
        
#         # Check for existing entry to update
#         exists = False
#         for i, loc in enumerate(current_locations):
#             if loc.get("location") == new_entry["location"] or loc.get("id") == new_entry["id"]:
#                 current_locations[i] = new_entry
#                 exists = True
#                 break
        
#         if not exists:
#             current_locations.append(new_entry)
            
#         final_locations = current_locations
    
#     # CASE 2: Smart Bulk Save Mode (JSON Body)
#     elif data is not None:
#         # Wrap single object into a list if needed
#         if isinstance(data, dict):
#             data = [data]
#         elif not isinstance(data, list):
#             return {"error": "Invalid data format. Expected list or object."}

#         # 1. Identify locations present in the incoming data
#         incoming_ids = set()
#         incoming_names = set()
        
#         if id: incoming_ids.add(str(id).strip())
#         if location: incoming_names.add(location.strip().lower())
        
#         new_locs_to_add = []
#         for item in data:
#             loc = normalize_location_entry(item, len(new_locs_to_add))
#             if loc.get("location"):
#                 new_locs_to_add.append(loc)
#                 if loc.get("id"): incoming_ids.add(str(loc["id"]).strip())
#                 if loc.get("location"): incoming_names.add(loc["location"].strip().lower())
        
#         # Map current locations for quick lookup
#         current_map = {str(loc.get("id")).strip(): loc for loc in current_locations if loc.get("id")}
        
#         # 2. Identify deleted locations (present in current_locations but not in the incoming data)
#         deleted_locations = []
#         for old_loc in current_locations:
#             o_id = str(old_loc.get("id") or "").strip()
#             o_name = str(old_loc.get("location") or "").strip().lower()
            
#             if o_id in incoming_ids or o_name in incoming_names:
#                 continue
#             deleted_locations.append(old_loc)
            
#         # Overwrite the locations list with new_locs_to_add (effectively removing omitted ones)
#         final_locations = new_locs_to_add

#         # 3. Clean up camera streams and stop processes for deleted locations
#         if deleted_locations:
#             deleted_ids = {str(loc.get("id")).strip() for loc in deleted_locations if loc.get("id")}
#             deleted_names = {str(loc.get("location")).strip().lower() for loc in deleted_locations if loc.get("location")}
            
#             streams_metadata = read_streams_metadata()
#             streams_changed = False
#             for idx in range(len(streams_metadata) - 1, -1, -1):
#                 stream = streams_metadata[idx]
#                 s_loc_id = str(stream.get("location_id") or "").strip()
#                 s_loc_name = str(stream.get("location") or "").strip().lower()
                
#                 if s_loc_id in deleted_ids or s_loc_name in deleted_names:
#                     stop_raw_stream(idx)
#                     streams_metadata.pop(idx)
#                     streams_changed = True
                    
#             if streams_changed:
#                 urls = [entry["rtsp"] for entry in streams_metadata if entry.get("rtsp")]
#                 with open(STREAMS_CONF, "w") as sf:
#                     sf.write("\n".join(urls))
#                 write_json_atomic(STREAMS_JSON, streams_metadata)
#                 with config_cache_lock:
#                     _streams_metadata_cache = [dict(entry) for entry in streams_metadata]
#                     _streams_location_index_cache = build_stream_location_index(_streams_metadata_cache)

#         # 4. Optimized Parallel Status Check for Bulk Save
#         # Only probe if NEW or if IP/Type changed to fix slowness!
#         probe_list = []
#         for loc in new_locs_to_add:
#             lid = str(loc.get("id")).strip()
#             if lid not in current_map:
#                 probe_list.append(loc)
#             else:
#                 old = current_map[lid]
#                 # If IP or Type changed, or it was previously offline, re-probe
#                 if (loc.get("device_ip") != old.get("device_ip") or 
#                     loc.get("device_type") != old.get("device_type") or
#                     old.get("device_status") != "online"):
#                     probe_list.append(loc)
#                 else:
#                     # Keep existing online status
#                     loc["device_status"] = old.get("device_status", "offline")

#         if probe_list:
#             with ThreadPoolExecutor(max_workers=10) as executor:
#                 def probe_status(loc):
#                     if loc.get("device_ip"):
#                         loc_device_type = str(loc.get("device_type", "")).strip().lower()
#                         is_rpi = loc_device_type in ["rpi", "raspberry pi", "raspberrypi"]
                        
#                         if is_rpi:
#                             loc["device_status"] = "online" if check_device_online(
#                                 loc["device_ip"], 
#                                 timeout=1.0, 
#                                 expected_device_id=loc.get("device_id"), 
#                                 serial_number=loc.get("serial_number"),
#                                 location_id=loc.get("id"),
#                                 is_rpi=is_rpi
#                             ) else "offline"
#                         else:
#                             loc["device_status"] = "offline"
                
#                 list(executor.map(probe_status, probe_list))
#     else:
#         return {"status": "success", "locations": current_locations}

#     # 3. Persist and update cache
#     write_json_atomic(LOCATIONS_JSON, final_locations)
#     with config_cache_lock:
#         _locations_cache = [dict(item) for item in final_locations]
    
#     return {"status": "saved", "count": len(final_locations), "locations": final_locations}

# @app.delete("/api/locations")
# def delete_location(location: str = Query(...)):
#     """Delete a location by its name."""
#     global _locations_cache, _streams_metadata_cache, _streams_location_index_cache
#     current_locations = read_locations()
#     target_name = location.strip().lower()
    
#     deleted_locations = [loc for loc in current_locations if str(loc.get("location", "")).strip().lower() == target_name]
    
#     if deleted_locations:
#         final_locations = [loc for loc in current_locations if str(loc.get("location", "")).strip().lower() != target_name]
#         write_json_atomic(LOCATIONS_JSON, final_locations)
#         with config_cache_lock:
#             _locations_cache = [dict(item) for item in final_locations]
            
#         # Clean up associated streams
#         deleted_ids = {str(loc.get("id")).strip() for loc in deleted_locations if loc.get("id")}
#         deleted_names = {str(loc.get("location")).strip().lower() for loc in deleted_locations if loc.get("location")}
        
#         streams_metadata = read_streams_metadata()
#         streams_changed = False
#         for idx in range(len(streams_metadata) - 1, -1, -1):
#             stream = streams_metadata[idx]
#             s_loc_id = str(stream.get("location_id") or "").strip()
#             s_loc_name = str(stream.get("location") or "").strip().lower()
            
#             if s_loc_id in deleted_ids or s_loc_name in deleted_names:
#                 stop_raw_stream(idx)
#                 streams_metadata.pop(idx)
#                 streams_changed = True
                
#         if streams_changed:
#             urls = [entry["rtsp"] for entry in streams_metadata if entry.get("rtsp")]
#             with open(STREAMS_CONF, "w") as sf:
#                 sf.write("\n".join(urls))
#             write_json_atomic(STREAMS_JSON, streams_metadata)
#             with config_cache_lock:
#                 _streams_metadata_cache = [dict(entry) for entry in streams_metadata]
#                 _streams_location_index_cache = build_stream_location_index(_streams_metadata_cache)
                
#         return {"status": "deleted", "location": location}
    
#     return {"status": "not_found", "message": f"Location '{location}' not found."}

# # ─────────────────────────────────────────────────────────────
# # DEVICES & ALERTS
# # ─────────────────────────────────────────────────────────────
# @app.get("/api/devices")
# async def get_devices(use_heartbeat: bool = True, check_status: bool = False):
#     res = []
#     locations = read_locations()
#     streams_index = get_stream_location_index()
    
#     if not check_status:
#         # Quick mode: use saved statuses + DEVICE_STATUS check!
#         for i, loc in enumerate(locations):
#             loc_device_id = str(loc.get("device_id", "")).strip()
#             loc_location_id = str(loc.get("id", "")).strip()
#             device_status = loc.get("device_status", "offline")
#             status_message = "Using saved status"
            
#             if loc_device_id and loc_location_id:
#                 key = f"{loc_location_id}:{loc_device_id}"
#                 if key in DEVICE_STATUS:
#                     data = DEVICE_STATUS[key]
#                     now = datetime.now(timezone.utc)
#                     time_diff = (now - data["last_communicated_time"]).total_seconds()
#                     if time_diff <=90:
#                         device_status = "online"
#                         status_message = "Heartbeat received"
#                     else:
#                         device_status = "offline"
#                         status_message = "Last heartbeat too old"
#                 else:
#                     status_message = "No heartbeat received"
                    
#             res.append({
#                 "device_id": loc.get("device_id") or f"DEV-{i}",
#                 "device_name": loc.get("location") or f"Location {i+1}",
#                 "location": loc.get("location") or f"Location {i+1}",
#                 "device_ip": loc.get("device_ip", ""),
#                 "device_status": device_status,
#                 "status_message": status_message,
#                 "stream_active": location_stream_active(loc, streams_index)
#             })
#         return {"devices": res}
    
#     # Check device statuses (async)
#     async def check_all_devices():
#         results = []
#         for loc in locations:
#             ip = loc.get("device_ip", "")
#             device_id = str(loc.get("device_id", "")).strip()
#             location_id = str(loc.get("id", "")).strip()
            
#             # First check DEVICE_STATUS for recent heartbeat
#             if device_id and location_id:
#                 key = f"{location_id}:{device_id}"
#                 if key in DEVICE_STATUS:
#                     data = DEVICE_STATUS[key]
#                     now = datetime.now(timezone.utc)
#                     time_diff = (now - data["last_communicated_time"]).total_seconds()
#                     if time_diff <= 90:
#                         results.append((True, "Heartbeat received"))
#                         continue
            
#             # Fall back to original check if no recent heartbeat
#             if ip:
#                 res = await check_device_online_async(ip, use_heartbeat=use_heartbeat)
#                 results.append(res)
#             else:
#                 results.append((False, "No IP"))
#         return results

#     results = await check_all_devices()
    
#     for i, (loc, (is_online, status_message)) in enumerate(zip(locations, results)):
#         res.append({
#             "device_id": loc.get("device_id") or f"DEV-{i}",
#             "device_name": loc.get("location") or f"Location {i+1}",
#             "location": loc.get("location") or f"Location {i+1}",
#             "device_ip": loc.get("device_ip", ""),
#             "device_status": "online" if is_online else "offline",
#             "status_message": status_message,
#             "stream_active": location_stream_active(loc, streams_index)
#         })
#     return {"devices": res}

# @app.get("/api/devices/{device_id}")
# def get_device(device_id: str):
#     locations = read_locations()
#     streams_index = get_stream_location_index()
#     for i, loc in enumerate(locations):
#         stable_device_id = loc.get("device_id") or f"DEV-{i}"
#         if stable_device_id == device_id:
#             ip = loc.get("device_ip", "")
#             status = loc.get("device_status") or "offline"
            
#             # First check DEVICE_STATUS
#             loc_device_id = str(loc.get("device_id", "")).strip()
#             location_id = str(loc.get("id", "")).strip()
#             if loc_device_id and location_id:
#                 key = f"{location_id}:{loc_device_id}"
#                 if key in DEVICE_STATUS:
#                     data = DEVICE_STATUS[key]
#                     now = datetime.now(timezone.utc)
#                     time_diff = (now - data["last_communicated_time"]).total_seconds()
#                     if time_diff <= 90:
#                         status = "online"
#                     else:
#                         status = "offline"
#                 elif ip:
#                     status = "online" if check_device_online(ip, timeout=1.0, expected_device_id=loc_device_id, location_id=location_id) else "offline"
#             elif ip:
#                 status = "online" if check_device_online(ip, timeout=1.0, expected_device_id=loc_device_id, location_id=location_id) else "offline"
            
#             return {
#                 "device_id": stable_device_id,
#                 "device_name": loc.get("location") or f"Location {i+1}",
#                 "location": loc.get("location") or f"Location {i+1}",
#                 "device_ip": ip,
#                 "device_status": status,
#                 "stream_active": location_stream_active(loc, streams_index)
#             }
#     return {"error": "not found"}

# @app.post("/api/devices/ping")
# async def ping_device(d: DevicePingRequest, use_heartbeat: bool = True):
#     """
#     STRICT check: pass location, device_id, device_ip.
#     Only returns 'online' if the record exists AND all 3 parameters match AND the IP is reachable.
#     """
#     global _locations_cache
#     req_device_id = str(d.device_id).strip()
#     req_device_ip  = str(d.device_ip).strip()
#     req_location   = str(d.location).strip()

#     if not req_device_ip:
#         return {"error": "device_ip is required"}

#     # 1. Strictly find if this device exists with these exact credentials
#     locations = read_locations()
#     target_loc = None
    
#     for loc in locations:
#         stored_id = str(loc.get("device_id", "")).strip()
#         stored_ip = str(loc.get("device_ip", "")).strip()
#         stored_name = str(loc.get("location", "")).strip().lower()
        
#         # All three must match the record in our database
#         if (stored_id == req_device_id and 
#             stored_ip == req_device_ip and 
#             stored_name == req_location.lower()):
#             target_loc = loc
#             break

#     if not target_loc:
#         from fastapi import HTTPException
#         raise HTTPException(
#             status_code=403, 
#             detail="Access Denied: The provided Device ID, IP, and Location name do not match any registered Raspberry Pi in our database."
#         )

#     # 2. Check DEVICE_STATUS first
#     is_online = False
#     status_message = "No heartbeat"
#     # Find the location ID for this device
#     target_location_id = target_loc.get("id", "")
#     if target_location_id and req_device_id:
#         key = f"{target_location_id}:{req_device_id}"
#         if key in DEVICE_STATUS:
#             data = DEVICE_STATUS[key]
#             now = datetime.now(timezone.utc)
#             time_diff = (now - data["last_communicated_time"]).total_seconds()
#             if time_diff <= 90:
#                 is_online = True
#                 status_message = "Heartbeat received"
    
#     # 3. If no recent heartbeat, fall back to probing
#     if not is_online:
#         is_online, status_message = await check_device_online_async(req_device_ip, use_heartbeat=use_heartbeat)
    
#     new_status = "online" if is_online else "offline"

#     # 3. Update the record status
#     target_loc["device_status"] = new_status
    
#     write_json_atomic(LOCATIONS_JSON, locations)
#     with config_cache_lock:
#         _locations_cache = [dict(item) for item in locations]

#     return {
#         "device_id": req_device_id,
#         "device_ip": req_device_ip,
#         "location": req_location,
#         "device_status": new_status,
#         "status_message": status_message,
#         "updated": True
#     }

# # --- RPI Heartbeat Endpoints ---
# @app.post("/api/rpi-heartbeat")
# async def rpi_heartbeat(data: RpiHeartbeat):
#     global _locations_cache
    
#     # CLEAR LOCATIONS CACHE TO READ FRESH DATA DIRECTLY FROM DISK!
#     with config_cache_lock:
#         _locations_cache = None
    
#     # 1. Determine the device identifier (use device_id if present, else serial_number)
#     device_identifier = data.device_id or data.serial_number
#     if not device_identifier:
#         return {
#             "status": False,
#             "message": "Either device_id or serial_number is required"
#         }
    
#     # 2. Validate against registered devices in locations.json
#     locations = read_locations()
#     registered_device = None
#     for loc in locations:
#         loc_id = str(loc.get("id", "")).strip()
#         loc_device_id = str(loc.get("device_id", "")).strip()
#         loc_serial = str(loc.get("serial_number", "")).strip()
#         loc_ip = str(loc.get("device_ip", "")).strip()
#         loc_device_type = str(loc.get("device_type", "")).strip().lower()
        
#         # Match location_id AND (device_id OR serial_number) AND device_type is RPI
#         if (loc_id == str(data.location_id).strip()) and (
#             (loc_device_id and loc_device_id == str(device_identifier).strip()) or 
#             (loc_serial and loc_serial == str(device_identifier).strip())
#         ):
#             registered_device = loc
#             break
    
#     # 3. Check registration
#     if not registered_device:
#         return {
#             "status": False,
#             "message": "Device not registered with this location_id"
#         }
    
#     # 4. Check that device_type is Raspberry Pi (case-insensitive)
#     reg_device_type = str(registered_device.get("device_type", "")).strip().lower()
#     if reg_device_type not in ["rpi", "raspberry pi", "raspberrypi"]:
#         return {
#             "status": False,
#             "message": "Device is not registered as a Raspberry Pi - set 'device_type' to 'Raspberry Pi'"
#         }
    
#     # 5. Check device_ip matches registered device
#     registered_ip = str(registered_device.get("device_ip", "")).strip()
#     if registered_ip and registered_ip != str(data.device_ip).strip():
#         return {
#             "status": False,
#             "message": "Device IP does not match registered IP"
#         }
    
#     # 5. Process heartbeat
#     key = f"{data.location_id}:{device_identifier}"
    
#     # Use provided timestamp or current time
#     if data.timestamp:
#         try:
#             last_communicated = datetime.fromisoformat(data.timestamp.replace('Z', '+00:00'))
#         except:
#             last_communicated = datetime.now(timezone.utc)
#     else:
#         last_communicated = datetime.now(timezone.utc)
    
#     DEVICE_STATUS[key] = {
#         "location_id": data.location_id,
#         "device_id": data.device_id or device_identifier,
#         "serial_number": data.serial_number or device_identifier,
#         "device_ip": data.device_ip,
#         "status": data.status,
#         "cpu": data.cpu,
#         "memory": data.memory,
#         "last_communicated_time": last_communicated
#     }
    
#     # Also update locations.json to mark as online
#     for i, loc in enumerate(locations):
#         loc_id = str(loc.get("id", "")).strip()
#         loc_device_id = str(loc.get("device_id", "")).strip()
#         loc_serial = str(loc.get("serial_number", "")).strip()
        
#         if (loc_id == str(data.location_id).strip()) and (
#             (loc_device_id and loc_device_id == str(device_identifier).strip()) or 
#             (loc_serial and loc_serial == str(device_identifier).strip())
#         ):
#             locations[i]["device_status"] = "online"
#             locations[i]["device_ip"] = data.device_ip
#             break
    
#     write_json_atomic(LOCATIONS_JSON, locations)
#     with config_cache_lock:
#         _locations_cache = [dict(item) for item in locations]
    
#     return {
#         "status": True,
#         "message": "Heartbeat received"
#     }

# @app.get("/api/rpi-status")
# async def get_rpi_status(
#     location_id: Optional[str] = None,
#     device_id: Optional[str] = None,
#     serial_number: Optional[str] = None
# ):
#     device_identifier = device_id or serial_number
#     if location_id and device_identifier:
#         key = f"{location_id}:{device_identifier}"
#         data = DEVICE_STATUS.get(key)
        
#         if not data:
#             return {
#                 "location_id": location_id,
#                 "device_id": device_id,
#                 "serial_number": serial_number,
#                 "status": "offline",
#                 "is_online": False,
#                 "message": "No heartbeat received"
#             }
        
#         # Check if last communicated time is within 90 seconds
#         now = datetime.now(timezone.utc)
#         time_diff = (now - data["last_communicated_time"]).total_seconds()
        
#         if time_diff > DEVICE_HEARTBEAT_TTL:
#             return {
#                 **data,
#                 "status": "offline",
#                 "is_online": False,
#                 "message": f"Last heartbeat too old"
#             }
        
#         return {
#             **data,
#             "status": "online",
#             "is_online": True
#         }
    
#     # If no specific location/device provided, return all statuses
#     all_statuses = []
#     now = datetime.now(timezone.utc)
#     for key, data in DEVICE_STATUS.items():
#         time_diff = (now - data["last_communicated_time"]).total_seconds()
#         is_online = time_diff <= DEVICE_HEARTBEAT_TTL
#         all_statuses.append({
#             **data,
#             "status": "online" if is_online else "offline",
#             "is_online": is_online
#         })
#     return {"statuses": all_statuses}

# # --- Edge-Device Heartbeat Aliases ---
# @app.post("/edge-device/heartbeat")
# async def edge_device_heartbeat_alias(data: RpiHeartbeat):
#     return await rpi_heartbeat(data)

# @app.get("/edge-device/status")
# async def edge_device_status_alias(
#     location_id: Optional[str] = None,
#     device_id: Optional[str] = None,
#     serial_number: Optional[str] = None
# ):
#     return await get_rpi_status(location_id=location_id, device_id=device_id, serial_number=serial_number)

# # --- Simple Device Heartbeat Endpoints (as per user's example) ---
# @app.post("/device/heartbeat")
# def device_heartbeat(data: DeviceHeartbeat):
#     """
#     Heartbeat endpoint for Raspberry Pi devices
#     """
#     # Find location_id from locations.json AND update locations
#     locations = read_locations()
#     location_id = None
#     updated = False
    
#     for i, loc in enumerate(locations):
#         if str(loc.get("device_id", "")).strip() == str(data.device_id).strip():
#             location_id = str(loc.get("id", "")).strip()
#             # Update this location in the list!
#             locations[i]["device_status"] = "online"
#             if data.device_ip:
#                 locations[i]["device_ip"] = str(data.device_ip).strip()
#             updated = True
    
#     if updated:
#         write_json_atomic(LOCATIONS_JSON, locations)
#         with config_cache_lock:
#             _locations_cache = None  # Clear cache to read fresh data!
    
#     # Mark heartbeat with location_id
#     success = device_manager.mark_device_heartbeat(data.device_id, data.device_ip, location_id)
#     return {"status": success, "message": "heartbeat received" if success else "invalid device info"}

# @app.get("/device/status")
# def device_status(device_id: str, device_ip: str):
#     """
#     Check device status and return all required fields
#     """
#     # First get status from device_manager (primary source)
#     status = device_manager.get_device_status(device_id)
    
#     # Check if ip matches
#     if not status or status["device_ip"] != device_ip:
#         return {
#             "device_id": device_id,
#             "device_ip": device_ip,
#             "device_status": "offline",
#             "last_communicated_time": ""
#         }
    
#     return status

# @app.get("/api/cameras/with-models")
# def get_cameras_with_models():
#     """
#     Returns every configured camera together with its assigned detection models.

#     Response:
#     [
#       {
#         "id": 0,
#         "label": "Camera 1",
#         "location": "Hyderabad",
#         "location_id": "loc-1",
#         "rtsp": "rtsp://...",
#         "models": ["nik_ppe_best.pt"],
#         "detection_active": true
#       },
#       ...
#     ]
#     """
#     urls      = read_streams_conf()
#     metadata  = read_streams_metadata()
#     cm        = json.load(open(CAMERA_MODELS_JSON)) if os.path.exists(CAMERA_MODELS_JSON) else {}

#     result = []
#     for i, u in enumerate(urls):
#         meta = metadata[i] if i < len(metadata) and isinstance(metadata[i], dict) else {}
#         result.append({
#             "id": i,
#             "label": meta.get("label") or f"Camera {i+1}",
#             "location": meta.get("location", f"Location {i+1}"),
#             "location_id": meta.get("location_id", ""),
#             "device_id": meta.get("device_id", ""),
#             "device_ip": meta.get("device_ip", ""),
#             "device_status": meta.get("device_status", "offline"),
#             "rtsp": u,
#             "models": cm.get(str(i), []),
#             "detection_active": str(i) in running and (running[str(i)].get("proc") and running[str(i)]["proc"].poll() is None)
#         })
#     return result

# @app.post("/api/alerts")
# def create_alert(d: dict = Body(...)):
#     # Local JSON Store
#     try:
#         data = json.load(open(ALERTS_JSON)) if os.path.exists(ALERTS_JSON) else []
#         data.append(d); json.dump(data, open(ALERTS_JSON, "w"), indent=4)
#     except: pass
#     return {"status": "saved"}

# @app.post("/api/alerts/db")
# def store_alert_in_db(d: dict = Body(...)):
#     """
#     Requested API endpoint for alerts data storing in db.
#     Payload: {"camera_id": "...", "location": "...", "type_of_alert": "...", "image_path": "..."}
#     """
#     if not PSYCOPG2_AVAILABLE: return {"error": "psycopg2 not installed"}
#     conn = get_db_conn()
#     if not conn: return {"error": "no db conn"}
#     try:
#         cur = conn.cursor()
#         insert_alert_db(cur, d.get("camera_id") or d.get("camera"), d.get("location"), d.get("type_of_alert"), d.get("image_path"))
#         conn.commit(); cur.close()
#         return {"status": "success"}
#     except Exception as e:
#         conn.rollback(); print(f"[DB] alert insert error: {e}"); return {"error": "failed to store alert"}
#     finally: release_db_conn(conn)

# @app.get("/api/alerts")
# def get_alerts_json():
#     try:
#         if not os.path.exists(ALERTS_JSON): return []
#         with open(ALERTS_JSON, "r") as f:
#             data = json.load(f)
#         # Ensure data is a list
#         if not isinstance(data, list): data = []
        
#         results = []
#         for a in data:
#             if not isinstance(a, dict): continue
#             img_name = a.get("image")
#             # If image is just a filename, convert to full path
#             if img_name and "/" not in img_name and os.path.exists(os.path.join(ALERTS_DIR, img_name)):
#                 if ALERTS_BASE_URL:
#                     a["image"] = f"{ALERTS_BASE_URL.rstrip('/')}/hls/alerts/{img_name}"
#                 else:
#                     a["image"] = f"/hls/alerts/{img_name}"
#                 results.append(a)
#             elif img_name:
#                 results.append(a)
#         # Sort to show newest alerts first
#         return results[::-1]
#     except Exception as e:
#         print(f"[Alerts] Error reading JSON: {e}")
#         return []

# @app.get("/api/alerts/db")
# def get_alerts_db():
#     if not PSYCOPG2_AVAILABLE: return []
#     conn = get_db_conn()
#     if not conn: return []
#     try:
#         cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
#         cur.execute("SELECT alert_id, camera_id, location, type_of_alert, image_path, created_at FROM alerts ORDER BY created_at DESC LIMIT 50")
#         rows = [dict(r) for r in cur.fetchall()]
#         for r in rows:
#             r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else ""
#             # If image_path is just a filename, convert to full path
#             img_path = r.get("image_path")
#             if img_path and "/" not in img_path and os.path.exists(os.path.join(ALERTS_DIR, img_path)):
#                 if ALERTS_BASE_URL:
#                     r["image_path"] = f"{ALERTS_BASE_URL.rstrip('/')}/hls/alerts/{img_path}"
#                 else:
#                     r["image_path"] = f"/hls/alerts/{img_path}"
#         cur.close()
#         return rows
#     except: return []
#     finally: release_db_conn(conn)

# @app.get("/api/alerts/images")
# def get_alert_images():
#     if not os.path.exists(ALERTS_DIR): return {"images": [], "total": 0}
#     imgs = []
#     for f in sorted(os.listdir(ALERTS_DIR), reverse=True):
#         if f.lower().endswith((".jpg", ".jpeg", ".png")):
#             if ALERTS_BASE_URL:
#                 img_url = f"{ALERTS_BASE_URL.rstrip('/')}/hls/alerts/{f}"
#             else:
#                 img_url = f"/hls/alerts/{f}"
#             imgs.append({"filename": f, "url": img_url, "size_kb": round(os.path.getsize(os.path.join(ALERTS_DIR, f))/1024, 1)})
#     data = get_alerts_json(); amap = {a.get("image").split("/")[-1] if a.get("image") else "": a for a in data}
#     for img in imgs:
#         m = amap.get(img["filename"], {})
#         img.update({"camera": m.get("camera", ""), "event": m.get("event", ""), "time": m.get("time", "")})
#     return {"images": imgs, "total": len(imgs)}

# @app.get("/api/alerts/images/{filename}")
# def get_alert_image(filename: str):
#     fp = os.path.abspath(os.path.join(ALERTS_DIR, filename))
#     if os.path.commonpath([os.path.abspath(ALERTS_DIR), fp]) != os.path.abspath(ALERTS_DIR):
#         return Response(status_code=400)
#     return FileResponse(fp) if os.path.exists(fp) else Response(status_code=404)

# # ─────────────────────────────────────────────────────────────
# # ANALYTICS ENDPOINTS
# # ─────────────────────────────────────────────────────────────
# def _norm_filter_value(value): 
#     return str(value or "").strip().lower() 

# def _as_list(value): 
#     if value is None: 
#         return [] 
#     if isinstance(value, list): 
#         return [str(item).strip() for item in value if str(item).strip()] 
#     if isinstance(value, tuple): 
#         return [str(item).strip() for item in value if str(item).strip()] 
#     return [item.strip() for item in str(value).split(",") if item.strip()] 

# def _model_matches(selected_model, camera_models): 
#     selected = _norm_filter_value(selected_model) 
#     selected_stem = selected[:-3] if selected.endswith(".pt") else selected 
#     for model in camera_models: 
#         current = _norm_filter_value(model) 
#         current_stem = current[:-3] if current.endswith(".pt") else current 
#         if selected in (current, current_stem) or selected_stem in (current, current_stem): 
#             return True 
#     return False

# def get_analytics_mapping_core(location: Optional[str] = None, models: Optional[list] = None): 
#     """Core logic for analytics mapping.""" 
#     cameras = get_cameras_with_models() 
#     f_location = _norm_filter_value(location) 
#     f_models = _as_list(models) 

#     by_location = {} 
#     by_usecase = {} 
#     matching_cameras = 0 
    
#     for cam in cameras: 
#         cam_location = cam["location"] 
#         cam_models = cam["models"] 
#         cam_location_key = _norm_filter_value(cam_location) 
        
#         if f_location and cam_location_key != f_location: 
#             continue 
#         if f_models: 
#             if not any(_model_matches(model, cam_models) for model in f_models): 
#                 continue 
#         matching_cameras += 1 
        
#         if cam_location not in by_location: 
#             by_location[cam_location] = {"location": cam_location, "cameras": [], "usecases": []} 
        
#         by_location[cam_location]["cameras"].append({ 
#             "id": cam["id"], 
#             "label": cam["label"], 
#             "location": cam_location, 
#             "location_id": cam.get("location_id", ""), 
#             "rtsp": cam.get("rtsp", ""), 
#             "models": cam_models, 
#             "detection_active": cam["detection_active"] 
#         }) 
#         by_location[cam_location]["usecases"] = sorted(set(by_location[cam_location]["usecases"] + cam_models)) 
        
#         for model in cam_models: 
#             if model not in by_usecase: 
#                 by_usecase[model] = {"locations": {}} 
#             if cam_location not in by_usecase[model]["locations"]: 
#                 by_usecase[model]["locations"][cam_location] = {"location": cam_location, "cameras": []} 
#             by_usecase[model]["locations"][cam_location]["cameras"].append({ 
#                 "id": cam["id"], 
#                 "label": cam["label"], 
#                 "location": cam_location, 
#                 "location_id": cam.get("location_id", ""), 
#                 "rtsp": cam.get("rtsp", ""), 
#                 "models": cam_models, 
#                 "detection_active": cam["detection_active"] 
#             }) 
            
#     final_by_usecase = {} 
#     for model, data in by_usecase.items(): 
#         final_by_usecase[model] = { 
#             "locations": [ 
#                 {"name": loc, "cameras": loc_data["cameras"]} 
#                 for loc, loc_data in data["locations"].items() 
#             ] 
#         } 
            
#     return { 
#         "by_location": by_location, 
#         "by_usecase": final_by_usecase, 
#         "summary": { 
#             "total_cameras": len(cameras), 
#             "matching_cameras": matching_cameras, 
#             "matching_locations": len(by_location) 
#         } 
#     } 

# @app.post("/api/analytics/by-location")
# def get_analytics_by_location(d: dict = Body(default={})):
#     """Returns only location-grouped data."""
#     models = d.get("models") or d.get("usecase")
#     res = get_analytics_mapping_core(location=d.get("location"), models=models)
#     return res.get("by_location", {})

# @app.post("/api/analytics/by-usecase")
# def get_analytics_by_usecase(d: dict = Body(default={})):
#     """Returns only usecase-grouped data."""
#     # Support both "usecase" (singular) and "models" (plural) keys
#     models = d.get("models") or d.get("usecase")
#     res = get_analytics_mapping_core(location=d.get("location"), models=models)
#     return res.get("by_usecase", {}) 

# app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
# app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="ui")

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run("server:app", host="0.0.0.0", port=8080, reload=False, log_level="warning")