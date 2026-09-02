import urllib.request
import urllib.error
import base64
import hashlib
import os
import time
import re
import threading
from datetime import datetime, timezone

class OnvifPtzClient:
    def __init__(self, ip="192.168.96.30", port=8888, username="admin", password=""):
        self.ip = ip
        self.port = int(port)
        self.username = username
        self.password = password
        self.base_url = f"http://{self.ip}:{self.port}/onvif"
        self.ptz_url = f"{self.base_url}/ptz_service"
        self.media_url = f"{self.base_url}/media"
        self.device_url = f"{self.base_url}/device_service"
        self.profile_token = "PROFILE_000"
        self._lock = threading.Lock()

    def _create_ws_security_header(self):
        """Generates standard WS-Security Password Digest header."""
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        nonce_bytes = os.urandom(16)
        nonce_b64 = base64.b64encode(nonce_bytes).decode("ascii")
        
        sha = hashlib.sha1()
        sha.update(nonce_bytes + created.encode("utf-8") + self.password.encode("utf-8"))
        digest_b64 = base64.b64encode(sha.digest()).decode("ascii")
        
        return f"""
        <s:Header xmlns:s="http://www.w3.org/2003/05/soap-envelope">
          <wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
            <wsse:UsernameToken>
              <wsse:Username>{self.username}</wsse:Username>
              <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest_b64}</wsse:Password>
              <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</wsse:Nonce>
              <wsu:Created>{created}</wsu:Created>
            </wsse:UsernameToken>
          </wsse:Security>
        </s:Header>"""

    def _soap_request(self, endpoint_url, body_xml, timeout=3.0):
        """Sends a SOAP XML request to the ONVIF service endpoint."""
        try:
            header_xml = self._create_ws_security_header()
            envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"
            xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
            xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
            xmlns:tt="http://www.onvif.org/ver10/schema">
  {header_xml}
  <s:Body>
    {body_xml}
  </s:Body>
</s:Envelope>"""
            req = urllib.request.Request(
                endpoint_url,
                data=envelope.encode("utf-8"),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            return 504, f"<error>{str(e)}</error>"

    def move(self, direction: str, speed: float = 0.5):
        speed = max(0.05, min(1.0, float(speed)))
        x, y = 0.0, 0.0
        dir_lower = direction.lower().strip().replace("-", "").replace("_", "").replace(" ", "")

        if dir_lower == "up": x, y = 0.0, speed
        elif dir_lower == "down": x, y = 0.0, -speed
        elif dir_lower == "left": x, y = -speed, 0.0
        elif dir_lower == "right": x, y = speed, 0.0
        elif dir_lower in ["upleft", "leftup"]: x, y = -speed, speed
        elif dir_lower in ["upright", "rightup"]: x, y = speed, speed
        elif dir_lower in ["downleft", "leftdown"]: x, y = -speed, -speed
        elif dir_lower in ["downright", "rightdown"]: x, y = speed, -speed
        else:
            return {"status": "error", "message": f"Unknown direction '{direction}'"}

        body = f"""
        <tptz:ContinuousMove>
          <tptz:ProfileToken>{self.profile_token}</tptz:ProfileToken>
          <tptz:Velocity>
            <tt:PanTilt x="{x:.2f}" y="{y:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>
          </tptz:Velocity>
        </tptz:ContinuousMove>"""
        with self._lock:
            status, resp = self._soap_request(self.ptz_url, body)
            if status == 200:
                return {"status": "success", "action": "move", "direction": direction, "speed": speed, "http_status": status}
            return {"status": "error", "action": "move", "message": "ONVIF request failed", "http_status": status}

    def zoom(self, direction: str, speed: float = 0.5):
        speed = max(0.05, min(1.0, float(speed)))
        z = 0.0
        dir_lower = direction.lower().strip()
        if dir_lower in ["in", "zoomin"]: z = speed
        elif dir_lower in ["out", "zoomout"]: z = -speed
        else:
            return {"status": "error", "message": f"Unknown zoom direction '{direction}'"}

        # Try ContinuousMove first for hold-to-zoom
        body_cont = f"""
        <tptz:ContinuousMove>
          <tptz:ProfileToken>{self.profile_token}</tptz:ProfileToken>
          <tptz:Velocity>
            <tt:PanTilt x="0.00" y="0.00" xmlns:tt="http://www.onvif.org/ver10/schema"/>
            <tt:Zoom x="{z:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>
          </tptz:Velocity>
        </tptz:ContinuousMove>"""
        with self._lock:
            status, resp = self._soap_request(self.ptz_url, body_cont)
            if status == 200:
                return {"status": "success", "action": "zoom", "direction": direction, "speed": speed, "http_status": status}
            
            # Fallback: ContinuousMove without PanTilt tag
            body_cont_simple = f"""
            <tptz:ContinuousMove>
              <tptz:ProfileToken>{self.profile_token}</tptz:ProfileToken>
              <tptz:Velocity>
                <tt:Zoom x="{z:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>
              </tptz:Velocity>
            </tptz:ContinuousMove>"""
            status2, resp2 = self._soap_request(self.ptz_url, body_cont_simple)
            if status2 == 200:
                return {"status": "success", "action": "zoom", "direction": direction, "speed": speed, "http_status": status2}
            
            # Fallback: RelativeMove
            step_z = z * 0.2
            body_rel = f"""
            <tptz:RelativeMove>
              <tptz:ProfileToken>{self.profile_token}</tptz:ProfileToken>
              <tptz:Translation>
                <tt:Zoom x="{step_z:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>
              </tptz:Translation>
            </tptz:RelativeMove>"""
            status3, resp3 = self._soap_request(self.ptz_url, body_rel)
            if status3 == 200:
                return {"status": "success", "action": "zoom", "direction": direction, "speed": speed, "http_status": status3}

            return {"status": "error", "action": "zoom", "message": "ONVIF zoom request failed", "http_status": status}

    def stop(self):
        body = f"""
        <tptz:Stop>
          <tptz:ProfileToken>{self.profile_token}</tptz:ProfileToken>
          <tptz:PanTilt>true</tptz:PanTilt>
          <tptz:Zoom>true</tptz:Zoom>
        </tptz:Stop>"""
        with self._lock:
            status, resp = self._soap_request(self.ptz_url, body)
            if status == 200:
                return {"status": "success", "action": "stop", "http_status": status}
            return {"status": "error", "action": "stop", "message": "ONVIF request failed", "http_status": status}

    def step(self, direction: str, speed: float = 0.5, duration: float = 0.35):
        duration = max(0.1, min(3.0, float(duration)))
        self.move(direction, speed)
        time.sleep(duration)
        return self.stop()

    def zoom_step(self, direction: str, speed: float = 0.5, duration: float = 0.8):
        speed = max(0.05, min(1.0, float(speed)))
        z = 0.0
        dir_lower = direction.lower().strip()
        if dir_lower in ["in", "zoomin"]: z = speed
        elif dir_lower in ["out", "zoomout"]: z = -speed
        else:
            return {"status": "error", "message": f"Unknown zoom direction '{direction}'"}

        # Try RelativeMove first for single-click step zoom
        step_z = z * 0.3
        body_rel = f"""
        <tptz:RelativeMove>
          <tptz:ProfileToken>{self.profile_token}</tptz:ProfileToken>
          <tptz:Translation>
            <tt:Zoom x="{step_z:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>
          </tptz:Translation>
        </tptz:RelativeMove>"""
        with self._lock:
            status, resp = self._soap_request(self.ptz_url, body_rel)
            if status == 200:
                return {"status": "success", "action": "zoom_step", "direction": direction, "mode": "relative", "http_status": 200}

        # Fallback to ContinuousMove pulse if RelativeMove is unsupported
        duration = max(0.3, min(3.0, float(duration)))
        self.zoom(direction, speed)
        time.sleep(duration)
        return self.stop()

    def get_status(self):
        body = f"""
        <tptz:GetStatus>
          <tptz:ProfileToken>{self.profile_token}</tptz:ProfileToken>
        </tptz:GetStatus>"""
        try:
            status, resp = self._soap_request(self.ptz_url, body)
            pan_match = re.search(r'PanTilt[^>]*x="([^"]+)"\s*y="([^"]+)"', resp)
            zoom_match = re.search(r'Zoom[^>]*x="([^"]+)"', resp)
            pan = float(pan_match.group(1)) if pan_match else 0.0
            tilt = float(pan_match.group(2)) if pan_match else 0.0
            zoom = float(zoom_match.group(1)) if zoom_match else 0.0
            is_moving = "<MoveStatus>" in resp and ("MOVING" in resp)
            return {"status": "online", "pan": pan, "tilt": tilt, "zoom": zoom, "is_moving": is_moving}
        except Exception as e:
            return {"status": "offline", "error": str(e)}

    def get_presets(self):
        body = f"""
        <tptz:GetPresets>
          <tptz:ProfileToken>{self.profile_token}</tptz:ProfileToken>
        </tptz:GetPresets>"""
        try:
            status, resp = self._soap_request(self.ptz_url, body)
            presets = []
            matches = re.findall(r'<[^:]*:Preset[^>]*token="([^"]+)"[^>]*>.*?<[^:]*:Name>(.*?)</[^:]*:Name>', resp, re.DOTALL)
            for token, name in matches:
                presets.append({"token": token, "name": name.strip()})
            return {"status": "success", "presets": presets, "count": len(presets)}
        except Exception as e:
            return {"status": "error", "error": str(e), "presets": []}

    def goto_preset(self, preset_token: str, speed: float = 1.0):
        body = f"""
        <tptz:GotoPreset>
          <tptz:ProfileToken>{self.profile_token}</tptz:ProfileToken>
          <tptz:PresetToken>{preset_token}</tptz:PresetToken>
          <tptz:Speed>
            <tt:PanTilt x="{speed:.2f}" y="{speed:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>
            <tt:Zoom x="{speed:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>
          </tptz:Speed>
        </tptz:GotoPreset>"""
        with self._lock:
            status, resp = self._soap_request(self.ptz_url, body)
            return {"status": "success", "action": "goto_preset", "preset_token": preset_token, "http_status": status}

    def set_preset(self, preset_name: str):
        body = f"""
        <tptz:SetPreset>
          <tptz:ProfileToken>{self.profile_token}</tptz:ProfileToken>
          <tptz:PresetName>{preset_name}</tptz:PresetName>
        </tptz:SetPreset>"""
        with self._lock:
            status, resp = self._soap_request(self.ptz_url, body)
            token_match = re.search(r'<[^:]*:PresetToken>(.*?)</[^:]*:PresetToken>', resp)
            token = token_match.group(1) if token_match else None
            return {"status": "success", "action": "set_preset", "preset_name": preset_name, "token": token}

    def remove_preset(self, preset_token: str):
        body = f"""
        <tptz:RemovePreset>
          <tptz:ProfileToken>{self.profile_token}</tptz:ProfileToken>
          <tptz:PresetToken>{preset_token}</tptz:PresetToken>
        </tptz:RemovePreset>"""
        with self._lock:
            status, resp = self._soap_request(self.ptz_url, body)
            return {"status": "success", "action": "remove_preset", "preset_token": preset_token}

    def get_stream_uris(self):
        """Dynamically retrieves Main Stream and Sub Stream RTSP URLs via ONVIF GetStreamUri."""
        uris = {}
        for token, label in [("PROFILE_000", "main"), ("PROFILE_001", "sub")]:
            body = f"""
            <trt:GetStreamUri>
              <trt:StreamSetup>
                <tt:Stream>RTP-Unicast</tt:Stream>
                <tt:Transport>
                  <tt:Protocol>RTSP</tt:Protocol>
                </tt:Transport>
              </trt:StreamSetup>
              <trt:ProfileToken>{token}</trt:ProfileToken>
            </trt:GetStreamUri>"""
            try:
                status, resp = self._soap_request(self.media_url, body)
                uri_match = re.search(r'<[^:]*:Uri>(.*?)</[^:]*:Uri>', resp)
                if uri_match:
                    raw_uri = uri_match.group(1).strip()
                    if self.username and "@" not in raw_uri:
                        prefix = f"{self.username}:{self.password}@" if self.password else f"{self.username}@"
                        formatted_uri = raw_uri.replace("rtsp://", f"rtsp://{prefix}")
                    else:
                        formatted_uri = raw_uri
                    uris[label] = {
                        "profile_token": token,
                        "label": "Main Stream (1080p)" if label == "main" else "Sub Stream (Fast/SD)",
                        "rtsp_url": formatted_uri,
                        "raw_uri": raw_uri
                    }
            except Exception as e:
                uris[label] = {"error": str(e)}
        return {"status": "success", "streams": uris}

    def get_device_info(self):
        body = """<tds:GetDeviceInformation/>"""
        try:
            status, resp = self._soap_request(self.device_url, body)
            mfg = re.search(r'<[^:]*:Manufacturer>(.*?)</[^:]*:Manufacturer>', resp)
            model = re.search(r'<[^:]*:Model>(.*?)</[^:]*:Model>', resp)
            firmware = re.search(r'<[^:]*:FirmwareVersion>(.*?)</[^:]*:FirmwareVersion>', resp)
            serial = re.search(r'<[^:]*:SerialNumber>(.*?)</[^:]*:SerialNumber>', resp)
            return {
                "status": "online",
                "manufacturer": mfg.group(1) if mfg else "Unknown",
                "model": model.group(1) if model else "Unknown",
                "firmware": firmware.group(1) if firmware else "Unknown",
                "serial_number": serial.group(1) if serial else "Unknown"
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

