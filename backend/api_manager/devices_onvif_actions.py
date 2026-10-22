import os
import sys
import traceback
from typing import Optional, List, Dict, Any
import fastapi
from pydantic import BaseModel, Field

# Ensure workspace root is in sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    import urdhva_base
    HAS_URDHVA_BASE = True
except ImportError:
    urdhva_base = None
    HAS_URDHVA_BASE = False

try:
    from utilities.onvif_controller import OnvifController
except ImportError:
    from onvif_controller import OnvifController

router = fastapi.APIRouter(prefix='/devices', tags=['Devices'])


# =====================================================================
# Request Parameter Schemas
# =====================================================================

class DevicesControlPanTiltParams(BaseModel):
    device_id: Optional[str] = None
    location_id: Optional[str] = None
    device_ip: Optional[str] = None
    onvif_port: Optional[int] = 80
    onvif_username: Optional[str] = "admin"
    onvif_password: Optional[str] = ""
    position: str  # 'Left', 'Right', 'Top', 'Bottom', 'Up', 'Down', 'LeftUp', 'RightUp', 'LeftDown', 'RightDown'
    pan: Optional[float] = 0.0
    tilt: Optional[float] = 0.0


class DevicesControlZoomParams(BaseModel):
    device_id: Optional[str] = None
    location_id: Optional[str] = None
    device_ip: Optional[str] = None
    onvif_port: Optional[int] = 80
    onvif_username: Optional[str] = "admin"
    onvif_password: Optional[str] = ""
    position: str  # 'ZoomIn', 'ZoomOut'
    zoom: Optional[float] = 0.5


class DevicesTestConnectivityParams(BaseModel):
    device_ip: str
    onvif_port: Optional[int] = 80
    onvif_username: Optional[str] = "admin"
    onvif_password: Optional[str] = ""


class DevicesValidateCredentialsParams(BaseModel):
    device_ip: str
    onvif_port: Optional[int] = 80
    onvif_username: Optional[str] = "admin"
    onvif_password: Optional[str] = ""


# =====================================================================
# Action control_pan_tilt
# =====================================================================
@router.post('/control-pan-tilt', tags=['Devices'])
async def devices_control_pan_tilt(data: DevicesControlPanTiltParams):
    device_data = data.model_dump()
    position_map = {
        'Left': {'pan': -0.5, 'tilt': 0.0},
        'Right': {'pan': 0.5, 'tilt': 0.0},
        'Top': {'pan': 0.0, 'tilt': 0.5},
        'Up': {'pan': 0.0, 'tilt': 0.5},
        'Bottom': {'pan': 0.0, 'tilt': -0.5},
        'Down': {'pan': 0.0, 'tilt': -0.5},
        'LeftUp': {'pan': -0.5, 'tilt': 0.5},
        'UpLeft': {'pan': -0.5, 'tilt': 0.5},
        'RightUp': {'pan': 0.5, 'tilt': 0.5},
        'UpRight': {'pan': 0.5, 'tilt': 0.5},
        'LeftDown': {'pan': -0.5, 'tilt': -0.5},
        'DownLeft': {'pan': -0.5, 'tilt': -0.5},
        'RightDown': {'pan': 0.5, 'tilt': -0.5},
        'DownRight': {'pan': 0.5, 'tilt': -0.5}
    }
    pos_info = position_map.get(data.position, {'pan': data.pan or 0.0, 'tilt': data.tilt or 0.0})
    device_data.update(pos_info)

    onvif_creds = None

    if data.device_ip:
        onvif_creds = {
            'ip': data.device_ip,
            'port': data.onvif_port or 80,
            'username': data.onvif_username or 'admin',
            'password': data.onvif_password or ''
        }
    elif data.device_id and data.location_id and HAS_URDHVA_BASE and 'Devices' in globals():
        q = f"device_id='{data.device_id}' and location_id='{data.location_id}'"
        try:
            resp = await globals()['Devices'].get_all(urdhva_base.queryparams.QueryParams(q=q, limit=1), resp_type='plain')
            if resp and resp.get('data', []):
                rec = resp["data"][0]
                onvif_creds = {
                    'ip': rec.get("device_ip"),
                    'port': rec.get("onvif_credentials", {}).get("onvif_port", 80),
                    'username': rec.get("onvif_credentials", {}).get("onvif_username", 'admin'),
                    'password': rec.get("onvif_credentials", {}).get("onvif_password", '')
                }
        except Exception as e:
            print(f"DB lookup error: {e}")

    if not onvif_creds or not onvif_creds.get('ip'):
        return False, "Not found in database and no direct IP provided"

    try:
        from onvif_client import OnvifPtzClient
        client = OnvifPtzClient(ip=onvif_creds['ip'], port=onvif_creds['port'], username=onvif_creds['username'], password=onvif_creds['password'])
        direction = str(data.position).lower()
        res = client.step(direction, 0.5, 0.35)
        if res and isinstance(res, dict) and res.get("status") in ["success", "ok"]:
            return True, "pan_tilt operation successful"
    except Exception:
        pass

    try:
        controller = OnvifController(**onvif_creds)
        controller.connect()
        controller.pan_tilt(device_data.get('pan', 0.0), device_data.get('tilt', 0.0))
        return True, "pan_tilt operation successful"
    except Exception as e:
        print(f"Pan-tilt error: {e}")
        print(traceback.format_exc())
        return False, "pan-tilt is not enabled"


# =====================================================================
# Action control_zoom
# =====================================================================
@router.post('/control-zoom', tags=['Devices'])
async def devices_control_zoom(data: DevicesControlZoomParams):
    device_data = data.model_dump()
    pos_str = str(data.position).lower()
    zoom_val = 0.5 if pos_str in ['zoomin', 'in', 'zoom_in'] else -0.5
    device_data['zoom'] = zoom_val

    onvif_creds = None

    if data.device_ip:
        port_val = data.onvif_port or 80
        if port_val == 8888:
            port_val = 80
        onvif_creds = {
            'ip': data.device_ip,
            'port': port_val,
            'username': data.onvif_username or 'admin',
            'password': data.onvif_password or ''
        }
    elif data.device_id and data.location_id and HAS_URDHVA_BASE and 'Devices' in globals():
        q = f"device_id='{data.device_id}' and location_id='{data.location_id}'"
        try:
            resp = await globals()['Devices'].get_all(urdhva_base.QueryParams(q=q, limit=1), resp_type='plain')
            if resp and resp.get('data', []):
                rec = resp["data"][0]
                p = rec.get("onvif_credentials", {}).get("onvif_port", 80)
                if p == 8888: p = 80
                onvif_creds = {
                    'ip': rec.get("device_ip"),
                    'port': p,
                    'username': rec.get("onvif_credentials", {}).get("onvif_username", 'admin'),
                    'password': rec.get("onvif_credentials", {}).get("onvif_password", '')
                }
        except Exception as e:
            print(f"DB lookup error: {e}")

    if not onvif_creds or not onvif_creds.get('ip'):
        return {"status": "error", "message": "Not found in database and no direct IP provided"}

    try:
        from onvif_client import OnvifPtzClient
        client = OnvifPtzClient(ip=onvif_creds['ip'], port=onvif_creds['port'], username=onvif_creds['username'], password=onvif_creds['password'])
        z_dir = "zoom_in" if zoom_val > 0 else "zoom_out"
        res = client.zoom_step(z_dir, 0.5, 0.8)
        if res and isinstance(res, dict) and res.get("status") in ["success", "ok"]:
            return {"status": "success", "zoom": zoom_val, "message": "Zoom operation successful"}
    except Exception:
        pass

    try:
        controller = OnvifController(**onvif_creds)
        controller.connect()
        res = controller.zoom(zoom_val)
        ok, msg = res if isinstance(res, tuple) else (True, "Zoom operation successful")
        if ok:
            return {"status": "success", "zoom": zoom_val, "message": str(msg)}
        else:
            try:
                from onvif_client import OnvifPtzClient
                client = OnvifPtzClient(ip=onvif_creds['ip'], port=onvif_creds['port'], username=onvif_creds['username'], password=onvif_creds['password'])
                return client.zoom_step(z_dir, 0.5, 0.8)
            except Exception:
                return {"status": "error", "zoom": zoom_val, "message": str(msg)}
    except Exception as e:
        print(f"Zoom error: {e}")
        try:
            from onvif_client import OnvifPtzClient
            client = OnvifPtzClient(ip=onvif_creds['ip'], port=onvif_creds['port'], username=onvif_creds['username'], password=onvif_creds['password'])
            return client.zoom_step(z_dir, 0.5, 0.8)
        except Exception:
            return {"status": "error", "message": f"Zoom exception: {str(e)}"}


# =====================================================================
# Action test_connectivity
# =====================================================================
@router.post('/test-connectivity', tags=['Devices'])
async def devices_test_connectivity(data: DevicesTestConnectivityParams):
    """
    Tests connectivity to a camera device using various protocols.
    """
    try:
        controller = OnvifController(
            ip=data.device_ip,
            port=data.onvif_port,
            username=data.onvif_username,
            password=data.onvif_password
        )
        is_valid = controller.validate_credentials()
        return True, [{"protocol": "ONVIF", "status": "Connected" if is_valid else "Failed"}]
    except Exception as e:
        return False, [{"protocol": "ONVIF", "status": "Error", "message": str(e)}]


# =====================================================================
# Action validate_credentials
# =====================================================================
@router.post('/validate-credentials', tags=['Devices'])
async def devices_validate_credentials(data: DevicesValidateCredentialsParams):
    """
    Validates device credentials for authentication.
    """
    try:
        controller = OnvifController(
            ip=data.device_ip,
            port=data.onvif_port,
            username=data.onvif_username,
            password=data.onvif_password
        )
        is_valid = controller.validate_credentials()
        return True, {"valid": is_valid}
    except Exception as e:
        return False, {"valid": False, "error": str(e)}
