import os
import time
import urllib.request
import base64
from typing import Optional, List

try:
    import urdhva_base
    HAS_URDHVA_BASE = True
except ImportError:
    urdhva_base = None
    HAS_URDHVA_BASE = False

try:
    from onvif import ONVIFCamera
    HAS_ONVIF = True
except ImportError:
    ONVIFCamera = None
    HAS_ONVIF = False


class OnvifController:
    """
    ONVIF & CGI Controller for IP Cameras.
    Handles PTZ (Pan/Tilt/Zoom), RTSP URL retrieval, profiles, and device information.
    Automatically supports both ONVIF WSDL/SOAP and HTTP CGI PTZ interfaces.
    """
    def __init__(self, ip: str, port: int, username: str, password: str, wsdl_dir: Optional[str] = None):
        self.ip = ip
        self.port = int(port) if port else 80
        self.username = username

        if HAS_URDHVA_BASE and hasattr(urdhva_base, 'types') and hasattr(urdhva_base.types, 'Secret'):
            try:
                self.password = urdhva_base.types.Secret(password).get_secret()
            except Exception:
                self.password = str(password) if password is not None else ""
        elif hasattr(password, 'get_secret'):
            self.password = password.get_secret()
        else:
            self.password = str(password) if password is not None else ""

        self.camera: Optional[ONVIFCamera] = None
        self.media_service = None
        self.ptz_service = None
        self.profile = None

        if wsdl_dir and os.path.exists(wsdl_dir):
            self.wsdl_dir = os.path.abspath(wsdl_dir)
        elif HAS_URDHVA_BASE and hasattr(urdhva_base, '__file__') and urdhva_base.__file__:
            calculated_wsdl = os.path.abspath(os.path.join(os.path.dirname(urdhva_base.__file__),
                                                           '..', '..', 'services',
                                                           'base_configuration', 'wsdl'))
            if os.path.exists(calculated_wsdl):
                self.wsdl_dir = calculated_wsdl
            else:
                self.wsdl_dir = None
        else:
            self.wsdl_dir = None

    def connect(self):
        if not HAS_ONVIF:
            self.camera = None
            self.media_service = None
            self.profile = None
            self.ptz_service = None
            return

        try:
            if self.wsdl_dir and os.path.exists(self.wsdl_dir):
                self.camera = ONVIFCamera(self.ip, self.port, self.username, self.password,
                                          wsdl_dir=self.wsdl_dir)
            else:
                self.camera = ONVIFCamera(self.ip, self.port, self.username, self.password)

            self.media_service = self.camera.create_media_service()
            profiles = self.media_service.GetProfiles()
            if profiles:
                self.profile = profiles[0]
            try:
                self.ptz_service = self.camera.create_ptz_service()
            except Exception:
                self.ptz_service = None
        except Exception:
            # Fallback for cameras where ONVIF SOAP WSDL returns fault
            self.camera = None
            self.media_service = None
            self.profile = None
            self.ptz_service = None

    def _send_cgi_ptz(self, act: str, speed: int = 5, duration: float = 1.0):
        """
        Fallback HTTP CGI interface for Ambicam / HiSilicon / IPC devices.
        """
        url = f"http://{self.ip}:{self.port}/cgi-bin/hi3510/ptzctrl.cgi?-step=0&-act={act}&-speed={speed}&-presetNUM=0"
        auth_bytes = f"{self.username}:{self.password}".encode('utf-8')
        auth_header = f"Basic {base64.b64encode(auth_bytes).decode('ascii')}"

        req = urllib.request.Request(url, method='PUT')
        req.add_header('Authorization', auth_header)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = resp.read().decode('utf-8', errors='ignore')
                if duration > 0 and act != 'stop':
                    time.sleep(duration)
                    self._send_cgi_ptz('stop', duration=0)
                return True, result
        except Exception as e:
            return False, str(e)

    def validate_credentials(self) -> bool:
        # 1. Try standard ONVIF protocol
        try:
            self.connect()
            if self.profile and self.media_service:
                self.media_service.GetStreamUri({'StreamSetup': {'Stream': 'RTP-Unicast',
                                                                 'Transport': {'Protocol': 'RTSP'}},
                                                 'ProfileToken': self.profile.token})
                return True
        except Exception:
            pass

        # 2. Fallback check for HTTP CGI camera interface
        success, _ = self._send_cgi_ptz('stop', duration=0)
        return success

    def get_rtsp_url(self) -> str:
        if self.profile and self.media_service:
            stream_uri = self.media_service.GetStreamUri({
                'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'RTSP'}},
                'ProfileToken': self.profile.token
            })
            return stream_uri.Uri
        return f"rtsp://{self.username}:{self.password}@{self.ip}:554/ch0_0.264"

    def get_basic_config(self) -> dict:
        if not self.profile:
            return {}
        return {
            "profile_token": getattr(self.profile, 'token', None),
            "resolution": getattr(getattr(self.profile, 'VideoEncoderConfiguration', None), 'Resolution', None),
            "encoding": getattr(getattr(self.profile, 'VideoEncoderConfiguration', None), 'Encoding', None),
            "fps": getattr(getattr(getattr(self.profile, 'VideoEncoderConfiguration', None), 'RateControl', None), 'FrameRateLimit', None)
        }

    def pan_tilt(self, pan: float, tilt: float):
        # Try standard ONVIF PTZ first
        if self.ptz_service and self.profile:
            try:
                config = self.ptz_service.GetConfigurationOptions(
                    {'ConfigurationToken': self.profile.PTZConfiguration.token}
                )
                request = self.ptz_service.create_type('ContinuousMove')
                request.ProfileToken = self.profile.token
                request.Velocity = {'PanTilt': {'x': pan, 'y': tilt}}
                self.ptz_service.ContinuousMove(request)
                time.sleep(1)
                self.stop()
                return
            except Exception:
                pass

        # Fallback to HTTP CGI movement
        direction = 'left' if pan < 0 else ('right' if pan > 0 else ('up' if tilt > 0 else ('down' if tilt < 0 else 'stop')))
        self._send_cgi_ptz(direction, duration=1.0)

    def zoom(self, zoom_val: float):
        # Try standard ONVIF PTZ first
        if self.ptz_service and self.profile:
            try:
                request = self.ptz_service.create_type('ContinuousMove')
                request.ProfileToken = self.profile.token
                request.Velocity = {'Zoom': {'x': zoom_val}}
                self.ptz_service.ContinuousMove(request)
                time.sleep(1)
                self.stop()
                return
            except Exception:
                pass

        # Fallback to HTTP CGI zoom
        act = 'zoomin' if zoom_val > 0 else ('zoomout' if zoom_val < 0 else 'stop')
        self._send_cgi_ptz(act, duration=1.0)

    def move_direction(self, direction: str, duration: float = 1.0):
        direction_lower = direction.lower()
        if direction_lower in ['left', 'right', 'up', 'down', 'top', 'bottom']:
            pan_val = -0.5 if direction_lower == 'left' else (0.5 if direction_lower == 'right' else 0.0)
            tilt_val = 0.5 if direction_lower in ['up', 'top'] else (-0.5 if direction_lower in ['down', 'bottom'] else 0.0)
            self.pan_tilt(pan=pan_val, tilt=tilt_val)
        elif direction_lower in ['zoomin', 'zoom_in']:
            self.zoom(0.5)
        elif direction_lower in ['zoomout', 'zoom_out']:
            self.zoom(-0.5)
        elif direction_lower == 'stop':
            self.stop()
        else:
            raise ValueError(f"Unknown direction: {direction}")

    def stop(self):
        if self.ptz_service and self.profile:
            try:
                request = self.ptz_service.create_type('Stop')
                request.ProfileToken = self.profile.token
                request.PanTilt = True
                request.Zoom = True
                self.ptz_service.Stop(request)
            except Exception:
                pass
        self._send_cgi_ptz('stop', duration=0)

    def get_presets(self) -> List[str]:
        if not self.ptz_service or not self.profile:
            return []
        try:
            presets = self.ptz_service.GetPresets({'ProfileToken': self.profile.token})
            return [preset.Name for preset in presets]
        except Exception:
            return []

    def goto_preset(self, preset_token: str):
        if self.ptz_service and self.profile:
            request = self.ptz_service.create_type('GotoPreset')
            request.ProfileToken = self.profile.token
            request.PresetToken = preset_token
            self.ptz_service.GotoPreset(request)

    def get_device_info(self) -> dict:
        """
        Fetching system base information
        """
        if self.camera:
            try:
                device_info = self.camera.devicemgmt.GetDeviceInformation()
                info = {
                    "manufacturer": getattr(device_info, 'Manufacturer', None),
                    "model": getattr(device_info, 'Model', None),
                    "firmware_version": getattr(device_info, 'FirmwareVersion', None),
                    "serial_number": getattr(device_info, 'SerialNumber', None),
                    "hardware_id": getattr(device_info, 'HardwareId', None),
                    **self.get_allowed_controls(),
                    **self.get_device_time(),
                }
                onvif_details = {}
                onvif_details['profile_data'], info['profiles'] = self.get_profiles()
                info['onvif_details'] = onvif_details
                return info
            except Exception:
                pass

        return {
            "manufacturer": "AMBICAM / HiSilicon",
            "model": "IP Camera",
            "pan_tilt_enabled": True,
            "zoom_enabled": True,
            "rtsp_url": self.get_rtsp_url()
        }

    def get_device_time(self) -> dict:
        """
        Fetching system time
        """
        if not self.camera:
            return {"device_time": "N/A", "timezone": "N/A", "day_light_savings_enabled": False}
        try:
            device_time = self.camera.devicemgmt.GetSystemDateAndTime()
            system_time = device_time.UTCDateTime
            date = system_time.Date
            time_val = system_time.Time
            formatted_time = (f"{date.Year}-{date.Month:02d}-{date.Day:02d} "
                              f"{time_val.Hour:02d}:{time_val.Minute:02d}:{time_val.Second:02d} UTC")
            return {"device_time": formatted_time, "timezone": getattr(device_time.TimeZone, 'TZ', None),
                    "day_light_savings_enabled": getattr(device_time, 'DaylightSavings', False)}
        except Exception:
            return {"device_time": "N/A", "timezone": "N/A", "day_light_savings_enabled": False}

    def get_allowed_controls(self) -> dict:
        """
        Fetching allowed hardware controls
        """
        allowed_controls = {"pan_tilt_enabled": True, "zoom_enabled": True}
        if self.ptz_service and self.profile:
            try:
                ptz_config_options = self.ptz_service.GetConfigurationOptions(
                    {'ConfigurationToken': self.profile.PTZConfiguration.token})

                if ptz_config_options.Spaces.AbsolutePanTiltPositionSpace is not None and \
                        len(ptz_config_options.Spaces.AbsolutePanTiltPositionSpace) > 0:
                    allowed_controls["pan_tilt_enabled"] = True

                if ptz_config_options.Spaces.AbsoluteZoomPositionSpace is not None and \
                        len(ptz_config_options.Spaces.AbsoluteZoomPositionSpace) > 0:
                    allowed_controls["zoom_enabled"] = True
            except Exception:
                pass
        return allowed_controls

    def get_profiles(self):
        if not self.media_service:
            return [], []
        profile_data = []
        protocols = ['UDP', 'RTSP', 'HTTP', 'TCP']
        try:
            profiles = self.media_service.GetProfiles()
            for profile in profiles:
                profile_info = {
                    "profile_name": profile.Name,
                    "profile_token": profile.token,
                    "stream_uris": []
                }
                for protocol in protocols:
                    try:
                        stream_uri = self.media_service.GetStreamUri({
                            'StreamSetup': {
                                'Stream': 'RTP-Unicast',
                                'Transport': {'Protocol': 'RTSP'}
                            },
                            'ProfileToken': profile.token
                        })
                        profile_info["stream_uris"].append({
                            "Protocol": protocol,
                            "URI": stream_uri.Uri
                        })
                    except Exception as e:
                        profile_info["stream_uris"].append({
                            "Protocol": protocol,
                            "URI": None,
                            "Error": str(e)
                        })
                profile_data.append(profile_info)
        except Exception as e:
            print(f"Failed to retrieve profiles: {str(e)}")
        return profile_data, [rec['profile_name'] for rec in profile_data]
