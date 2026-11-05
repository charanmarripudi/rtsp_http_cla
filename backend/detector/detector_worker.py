import os
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"
os.environ["VECLIB_MAXIMUM_THREADS"] = "2"
os.environ["NUMEXPR_NUM_THREADS"] = "2"
os.environ["TORCH_NUM_THREADS"] = "2"
os.environ["OPENCV_FOR_THREADS_NUM"] = "1"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|max_delay;500000|timeout;5000000"

import cv2, subprocess, time, threading, queue, json, math, glob, hashlib, traceback
from collections import Counter
import numpy as np
try:
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
except Exception:
    pass
from datetime import datetime
from ultralytics import YOLO
from ultralytics.utils.plotting import Annotator, colors
try:
    import psycopg2
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

# Load .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pathlib import Path
import sys

_curr = Path(__file__).resolve()
BASE_DIR = None
for p in [_curr.parent, _curr.parent.parent, _curr.parent.parent.parent]:
    if (p / "models").is_dir() and (p / "backend").is_dir():
        BASE_DIR = p
        break
if BASE_DIR is None:
    BASE_DIR = _curr.parents[2] if len(_curr.parents) > 2 else _curr.parent.parent

BACKEND_DIR = BASE_DIR / "backend"
DETECTOR_DIR = BACKEND_DIR / "detector"

import shutil

# Auto-resolve FFmpeg binary from virtualenv or imageio_ffmpeg if not in system PATH
if not shutil.which("ffmpeg"):
    venv_bin = os.path.join(sys.prefix, "bin", "ffmpeg")
    if os.path.exists(venv_bin):
        os.environ["PATH"] = os.path.dirname(venv_bin) + os.pathsep + os.environ.get("PATH", "")
    else:
        try:
            import imageio_ffmpeg
            ff_exe = imageio_ffmpeg.get_ffmpeg_exe()
            if ff_exe and os.path.exists(ff_exe):
                ff_dir = os.path.dirname(ff_exe)
                target_symlink = os.path.join(ff_dir, "ffmpeg")
                if not os.path.exists(target_symlink):
                    try: os.symlink(ff_exe, target_symlink)
                    except: pass
                os.environ["PATH"] = ff_dir + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            pass

for p in [str(DETECTOR_DIR), str(BACKEND_DIR), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Optimize PyTorch CPU threading to 2 dedicated cores on Raspberry Pi 4 CPU
try:
    import torch
    torch.set_num_threads(2)
    if hasattr(torch, "set_num_interop_threads"):
        torch.set_num_interop_threads(1)
except Exception:
    pass

import re

from alert_store import DB_DSN, ensure_alerts_schema, insert_alert_db, insert_alert_via_psql

try:
    from vehicle_speed_tracker.speed_tracker import VehicleSpeedTracker
except ImportError:
    try:
        from speed_tracker import VehicleSpeedTracker
    except ImportError:
        VehicleSpeedTracker = None

try:
    from crowd_tracker.Crowd import CrowdMonitoring
except ImportError:
    try:
        from Crowd import CrowdMonitoring
    except ImportError:
        CrowdMonitoring = None

try:
    from backend.detector.byte_tracker import EdgeByteTracker
except ImportError:
    try:
        from byte_tracker import EdgeByteTracker
    except ImportError:
        EdgeByteTracker = None

YOLO_CACHE = {}

# ------------------------------------------------------------------------------
# TUNABLE KNOBS
# ------------------------------------------------------------------------------
# Track age (2.0s) prevents flickering and keeps valid detections stably rendered on live video
TRACK_MAX_AGE_S = 2.0

# 0.0 EMA gives 100% instant weight to fresh coordinates so boxes lock tightly to moving objects with zero lag
BOX_EMA_ALPHA = 0.0

# Minimum IoU overlap to consider two boxes the same detection (same-class NMS)
NMS_SAME_IOU_THRESH = 0.30
NMS_SAME_IO_MIN_THRESH = 0.50

# Minimum IoU overlap to suppress the weaker side of an opposite-class pair
# (e.g.  Safety-Vest vs NO-Safety-Vest on the same person)
NMS_OPP_IOU_THRESH = 0.20
NMS_OPP_IO_MIN_THRESH = 0.30

# Hard floor for YOLO confidence passed to model.predict().
# Keeping this at 0.06 lets us catch low-confidence detections for per-class
# post-filtering, but we apply a stricter per-class gate afterwards.
PREDICT_CONF_FLOOR = 0.10   # raised from 0.06 -> fewer ghost boxes fed into NMS

# Scheduler inter-camera sleep (seconds). Allows CPU cooldown and prevents thermal throttling.
SCHEDULER_SLEEP_S = 0.06

# Label rendering
LABEL_FONT_SCALE   = 0.55
LABEL_THICKNESS    = 1
LABEL_BOX_PADDING  = 5

# Global persistent alert cooldown tracker (cam_id, alert_class) -> timestamp
GLOBAL_ALERT_COOLDOWNS = {}
GLOBAL_ALERT_COOLDOWNS_LOCK = threading.Lock()
ALERT_COOLDOWN_SECONDS = 30.0

def check_and_record_alert_cooldown(cam_id, alert_class, cooldown_s=30.0):
    """
    Thread-safe global alert cooldown manager across all cameras and workers.
    Ensures a strict minimum gap (default 30 seconds) between alerts of the same class on the same camera,
    persisting across worker restarts, slider updates, and stream reconnections.
    """
    now_t = time.time()
    c_clean = str(alert_class).strip().lower().replace("_", "-").replace("saftey", "safety")
    key = (str(cam_id), c_clean)
    with GLOBAL_ALERT_COOLDOWNS_LOCK:
        last_t = GLOBAL_ALERT_COOLDOWNS.get(key, 0.0)
        if (now_t - last_t) < cooldown_s:
            return False
        GLOBAL_ALERT_COOLDOWNS[key] = now_t
        return True

def clean_str(s):
    res = re.sub(r'[^a-z0-9]', '', str(s).lower())
    return res.replace("saftey", "safety")

def extract_negation_and_core(s):
    s_str = str(s).strip()
    if " - " in s_str:
        s_str = s_str.split(" - ")[-1]
    elif ":" in s_str:
        s_str = s_str.split(":")[-1]
    elif "/" in s_str:
        s_str = s_str.split("/")[-1]
        
    cleaned = clean_str(s_str)
    
    # Generic violation keywords
    if cleaned in ("none", "noppe", "noviolation", "violation", "withoutppe"):
        return True, "violation", cleaned

    neg_prefixes = ["no", "without", "non", "un"]
    is_neg = False
    core = cleaned
    for p in neg_prefixes:
        if cleaned.startswith(p):
            is_neg = True
            core = cleaned[len(p):]
            break
    
    synonym_map = {
        "hardhat": "headgear",
        "helmet": "headgear",
        "safetyhelmet": "headgear",
        "headprotection": "headgear",
        "head": "headgear",
        "vest": "bodyvest",
        "safetyvest": "bodyvest",
        "reflectivevest": "bodyvest",
        "jacket": "bodyvest",
        "mask": "facemask",
        "facemask": "facemask",
        "facecover": "facemask",
        "goggles": "eyegoggles",
        "glasses": "eyegoggles",
        "safetyglasses": "eyegoggles",
        "eyewear": "eyegoggles",
        "glove": "handgloves",
        "gloves": "handgloves",
        "handglove": "handgloves",
        "shoe": "footshoes",
        "shoes": "footshoes",
        "boot": "footshoes",
        "boots": "footshoes",
        "safetyshoe": "footshoes",
        "safetyshoes": "footshoes",
        "gumboots": "footshoes",
        "gum_boots": "footshoes",
        "saftey-belt": "safetybelt",
        "saftey_belt": "safetybelt",
        "safetybelt": "safetybelt",
        "safteyvest": "bodyvest",
        "harness": "safetybelt",
        "person": "person",
        "worker": "person",
        "human": "person",
        "man": "person",
        "woman": "person",
        "fire": "fire",
        "flame": "fire",
        "smoke": "smoke",
    }
    mapped_core = synonym_map.get(core, core)
    return is_neg, mapped_core, cleaned

def is_negative_violation_class(cls_name: str, model_name: str = "") -> bool:
    """
    Returns True ONLY if the class represents a negative safety alert / violation.
    Normal compliant objects (Hardhat, Mask, Vest, Person, Dome_Closed, Valve_Box_Close, Good_tyre)
    return False and NEVER trigger violation alerts.
    """
    if not cls_name:
        return False

    raw = str(cls_name).strip()
    c = raw.lower().replace("_", "-").replace(" ", "-")

    # 1. Baseline objects & people are NEVER violation alerts
    baseline = (
        "person", "worker", "human", "man", "woman", "people",
        "machinery", "vehicle", "car", "truck", "bus", "van", "bike",
        "pickup-truck", "tank-truck", "safety-cone"
    )
    if c in baseline:
        return False

    # 2. Compliant PPE / equipment states are POSITIVE compliance -> NEVER violation alerts
    positive_compliant = {
        "hardhat", "helmet", "safety-helmet", "headgear", "headprotection",
        "vest", "safety-vest", "saftey-vest", "reflective-vest", "bodyvest", "jacket",
        "mask", "facemask", "facecover",
        "gloves", "glove", "handgloves",
        "boots", "shoes", "gum-boots", "footshoes",
        "goggles", "glasses", "safetyglasses", "eyewear", "eyegoggles",
        "harness", "saftey-belt", "safety-belt", "cap-lamp",
        "dome-closed", "domeclosed",
        "valve-box-close", "valveboxclose", "valve-box-closed",
        "good-tyre", "goodtyre",
        "fire-extinguisher", "sand-bucket", "wheel-choke",
        "spark-arrestor", "sparkarrestor", "spark-arrested", "sparkarrested",
        "fire-hose", "firehose", "firehose-box", "firehosebox",
        "reflective-tape", "traffic-tape", "tape",
        "reflective-tape-floor", "reflective-tape-person", "reflective-tape-vehicles",
        "traffic-tape-bollards", "traffic-tape-cone"
    }
    if c in positive_compliant:
        return False

    # 3. Explicit negative prefixes (NO-Hardhat, No_Mask, No-Safety-Vest, no-helmet, etc.)
    neg_prefixes = ("no-", "no_", "non-", "without-", "without_")
    if any(c.startswith(p) for p in neg_prefixes) or "no-ppe" in c or "noppe" in c:
        return True

    # 4. Critical safety hazards and violations
    hazards_and_violations = {
        "fall-detected", "violation", "none",
        "fire", "smoke",
        "spillage",
        "dome-opened", "dome-open", "domeopened", "domeopen",
        "valve-box-open", "valveboxopen", "valve-box-opened",
        "defective"
    }
    if any(hv in c for hv in hazards_and_violations):
        return True

    # 5. Over speed / crowd alerts / intrusion alerts / unauthorized activities
    if ("over-speed" in c or "overspeed" in c or "crowd-alert" in c or "crowd alert" in c.replace("-", " ") or
        "intrusion" in c or "non-working" in c or "non_working" in c or "unauthorized" in c or
        "sitting" in c or "lying" in c or "loitering" in c or "smoking" in c or "fighting" in c or
        "cooking" in c or "crowd" in c):
        return True

    return False

def match_class(box_cls, enabled_cls):
    """
    Robust negation and synonym-aware class matcher.
    Returns True if box_cls matches enabled_cls dynamically across models.
    """
    if not box_cls or not enabled_cls:
        return False
        
    b_neg, b_core, b_clean = extract_negation_and_core(box_cls)
    e_neg, e_core, e_clean = extract_negation_and_core(enabled_cls)
    
    if b_clean == e_clean:
        return True
        
    if b_neg != e_neg:
        return False
        
    # Generic violation matching (e.g. 'none' from construction.pt matches 'NO-Hardhat' / 'NO-Safety Vest')
    if b_core == "violation" or e_core == "violation":
        return True
        
    if b_core == e_core:
        return True
        
    if (b_core in e_core or e_core in b_core) and len(b_core) >= 3 and len(e_core) >= 3:
        return True
        
    return False

def calculate_joint_angle(p1, p2, p3):
    """
    Computes interior angle at joint p2 between points p1 and p3 in degrees [0, 180].
    Used for biomechanical joint flexion analysis (knee, hip, elbow).
    """
    u = (p1[0] - p2[0], p1[1] - p2[1])
    v = (p3[0] - p2[0], p3[1] - p2[1])
    dot = u[0] * v[0] + u[1] * v[1]
    nu = math.hypot(u[0], u[1])
    nv = math.hypot(v[0], v[1])
    if nu < 1e-4 or nv < 1e-4:
        return 180.0
    cos_theta = max(-1.0, min(1.0, dot / (nu * nv)))
    return math.degrees(math.acos(cos_theta))

def classify_pose_activity(trk, kp_data, box, dwell_time, enabled_activities, all_persons=None, frame_height=360, has_fire_smoke=False, now_t=None, is_video_test=False):
    """
    Industrial Terminal Activity Classifier using Biomechanical Joint Angles, Spatial Context, & Temporal Persistence.
    Designed for real-time edge CCTV deployments at petroleum terminals (OISD standards) and AI Video Lab testing.
    
    Activities evaluated:
    - cooking: Crouched / low tending posture over stove/ground, floor food prep/eating, or open flame/smoke proximity
    - smoking_gesture: High-confidence hand-to-mouth gesture with acute elbow flexion and upward forearm
    - fighting_posture: Multi-person proximity + combat guard stance / punches (requires >= 2 combatants)
    - lying_down: Stationary horizontal torso orientation resting on ground for >= 2.5s
    - sitting: Optional seated posture with bent knee angles (disabled by default in parking area)
    - crowd: Group of 3+ distinct, non-overlapping persons gathering in parking bay
    - loitering: Optional dwell time inside restricted parking bay ROI >= 30.0s
    """
    if not enabled_activities:
        return None, 0.0, None

    if now_t is None:
        now_t = time.time()

    def _is_enabled(act_name):
        return any(act_name.lower().replace('-', '_') in str(e).lower().replace('-', '_') for e in enabled_activities)

    def _get_start_t(key):
        val = trk.get(key)
        return val if val is not None else now_t

    x1, y1, x2, y2 = box if (box and len(box) == 4) else [0, 0, 1, 1]
    bw = max(1.0, x2 - x1)
    bh = max(1.0, y2 - y1)
    aspect_ratio = bw / bh
    is_bottom_cropped = (y2 >= frame_height * 0.90)

    has_kp = (kp_data is not None and len(kp_data) >= 17)

    # 1. Biomechanical Upright Orientation Check
    is_upright = False
    if has_kp:
        nose = kp_data[0]
        eyes_ears = [kp_data[i] for i in range(1, 5) if kp_data[i][2] > 0.20]
        head_y = nose[1] if nose[2] > 0.20 else (sum(p[1] for p in eyes_ears)/len(eyes_ears) if eyes_ears else None)
        l_sh, r_sh = kp_data[5], kp_data[6]
        l_hip, r_hip = kp_data[11], kp_data[12]

        sh_valid = (l_sh[2] > 0.20 and r_sh[2] > 0.20)
        sh_y = min(l_sh[1], r_sh[1]) if sh_valid else (l_sh[1] if l_sh[2] > 0.20 else r_sh[1])

        sh_mid_x = (l_sh[0] + r_sh[0]) / 2.0 if sh_valid else (l_sh[0] if l_sh[2] > 0.20 else r_sh[0])
        head_x = nose[0] if nose[2] > 0.20 else sh_mid_x

        # If head keypoint is vertically above shoulders (upright head/torso axis)
        if head_y is not None and sh_y > 0 and (sh_y - head_y) > abs(sh_mid_x - head_x) * 0.60 and (sh_y - head_y) > bh * 0.10:
            is_upright = True
        # If shoulders are aligned and hips are below shoulders
        elif (l_sh[2] > 0.20 or r_sh[2] > 0.20) and (l_hip[2] > 0.20 or r_hip[2] > 0.20):
            sh_mid_y = (l_sh[1] + r_sh[1]) / 2.0
            hip_mid_y = (l_hip[1] + r_hip[1]) / 2.0
            hip_mid_x = (l_hip[0] + r_hip[0]) / 2.0
            if (hip_mid_y - sh_mid_y) > abs(hip_mid_x - sh_mid_x) * 0.8:
                is_upright = True
        # If both shoulders detected horizontally with no hips (upper body portrait crop)
        elif sh_valid and abs(l_sh[0] - r_sh[0]) > bw * 0.25 and abs(l_sh[1] - r_sh[1]) < abs(l_sh[0] - r_sh[0]) * 0.50:
            is_upright = True

    # 2. Standing Check (A standing person has tall aspect ratio and/or vertically extended legs)
    is_definitely_standing = (aspect_ratio < 0.60)
    if has_kp:
        l_hip, r_hip = kp_data[11], kp_data[12]
        l_knee, r_knee = kp_data[13], kp_data[14]
        l_ank, r_ank = kp_data[15], kp_data[16]

        hip_y = min(l_hip[1], r_hip[1]) if (l_hip[2] > 0.20 and r_hip[2] > 0.20) else (l_hip[1] if l_hip[2] > 0.20 else r_hip[1] if r_hip[2] > 0.20 else None)
        knee_y = min(l_knee[1], r_knee[1]) if (l_knee[2] > 0.20 and r_knee[2] > 0.20) else (l_knee[1] if l_knee[2] > 0.20 else r_knee[1] if r_knee[2] > 0.20 else None)
        ank_y = max(l_ank[1], r_ank[1]) if (l_ank[2] > 0.15 and r_ank[2] > 0.15) else (l_ank[1] if l_ank[2] > 0.15 else r_ank[1] if r_ank[2] > 0.15 else None)

        # Standing in loose clothing / lungi / wide stance: hips are in upper half, ankles near bottom, knees in middle
        if hip_y is not None and knee_y is not None and ank_y is not None:
            legs_extended = ((ank_y - hip_y) >= bh * 0.46) and ((knee_y - hip_y) >= bh * 0.20) and ((ank_y - knee_y) >= bh * 0.20)
            if (hip_y - y1) < bh * 0.52 and legs_extended and (ank_y >= y1 + bh * 0.75):
                is_definitely_standing = True

        l_ang = calculate_joint_angle(l_hip, l_knee, l_ank) if (l_hip[2] > 0.20 and l_knee[2] > 0.20 and l_ank[2] > 0.15) else None
        r_ang = calculate_joint_angle(r_hip, r_knee, r_ank) if (r_hip[2] > 0.20 and r_knee[2] > 0.20 and r_ank[2] > 0.15) else None
        if l_ang is not None and r_ang is not None and l_ang > 135.0 and r_ang > 135.0 and (ank_y - hip_y) >= bh * 0.44:
            is_definitely_standing = True
        elif (l_ang is not None and l_ang > 135.0) or (r_ang is not None and r_ang > 135.0):
            if aspect_ratio < 0.70 and (ank_y - hip_y) >= bh * 0.44:
                is_definitely_standing = True

        # Medium / upper-body standing crop: upright torso, head above shoulders, shoulders above hips
        if not is_definitely_standing and is_upright:
            l_sh, r_sh = kp_data[5], kp_data[6]
            sh_valid = (l_sh[2] > 0.20 or r_sh[2] > 0.20)
            sh_y = min(l_sh[1], r_sh[1]) if (l_sh[2] > 0.20 and r_sh[2] > 0.20) else (l_sh[1] if l_sh[2] > 0.20 else r_sh[1])
            if sh_valid and hip_y is not None and (hip_y - sh_y) >= bh * 0.24:
                if knee_y is not None and (knee_y - hip_y) >= bh * 0.18:
                    is_definitely_standing = True
                elif ank_y is None and aspect_ratio < 0.85:
                    is_definitely_standing = True

    # 3. Unauthorized Cooking / Food Preparation in Parking Bay / Restricted Area
    # Evaluates:
    # A. Presence of open flame / smoke companion detection near person
    # B. Crouched / low tending posture over stove/ground (floor eating / cooking)
    # C. Hands working downward in front of lap/waist (cutting vegetables / tending stove)
    if _is_enabled('cooking'):
        is_cooking = False
        if has_fire_smoke:
            is_cooking = True
        elif has_kp and not is_definitely_standing:
            l_sh, r_sh = kp_data[5], kp_data[6]
            l_hip, r_hip = kp_data[11], kp_data[12]
            l_knee, r_knee = kp_data[13], kp_data[14]
            l_wr, r_wr = kp_data[9], kp_data[10]

            sh_valid = (l_sh[2] > 0.20 or r_sh[2] > 0.20)
            hip_valid = (l_hip[2] > 0.20 or r_hip[2] > 0.20)
            knee_valid = (l_knee[2] > 0.20 or r_knee[2] > 0.20)
            wr_valid = (l_wr[2] > 0.20 and r_wr[2] > 0.20)  # BOTH hands must be visible for food prep without fire

            sh_y = min(l_sh[1], r_sh[1]) if (l_sh[2] > 0.20 and r_sh[2] > 0.20) else (l_sh[1] if l_sh[2] > 0.20 else r_sh[1])
            hip_y = min(l_hip[1], r_hip[1]) if (l_hip[2] > 0.20 and r_hip[2] > 0.20) else (l_hip[1] if l_hip[2] > 0.20 else r_hip[1])
            knee_y = min(l_knee[1], r_knee[1]) if (l_knee[2] > 0.20 and r_knee[2] > 0.20) else (l_knee[1] if l_knee[2] > 0.20 else r_knee[1])
            wr_y = (l_wr[1] + r_wr[1]) / 2.0 if wr_valid else None

            # Crouching / seated posture (hips low to ground or knees flexed or wide crouch)
            hips_low_to_ground = ((y2 - hip_y) < bh * 0.45) if hip_valid else False
            knee_flexed = False
            if hip_valid and knee_valid:
                l_k_ang = calculate_joint_angle(l_hip, l_knee, kp_data[15]) if (l_hip[2] > 0.20 and l_knee[2] > 0.20 and kp_data[15][2] > 0.15) else None
                r_k_ang = calculate_joint_angle(r_hip, r_knee, kp_data[16]) if (r_hip[2] > 0.20 and r_knee[2] > 0.20 and kp_data[16][2] > 0.15) else None
                knee_flexed = (l_k_ang is not None and l_k_ang < 125.0) or (r_k_ang is not None and r_k_ang < 125.0) or abs(knee_y - hip_y) < bh * 0.25

            is_crouched_or_seated = hips_low_to_ground or knee_flexed or (aspect_ratio >= 0.85)

            # REJECT: Upright walking or standing person
            is_tall_standing = (is_definitely_standing or not is_crouched_or_seated) and is_upright and sh_valid and hip_valid and (hip_y - sh_y) >= bh * 0.26

            # REJECT: Arms separated on sides of body (normal walking/standing posture)
            arms_separated = wr_valid and abs(l_wr[0] - r_wr[0]) > bw * 0.35

            if not is_tall_standing and not arms_separated and wr_valid and is_crouched_or_seated:
                # Food prep requires both hands close together in front of lap/abdomen/ground
                hands_clustered = abs(l_wr[0] - r_wr[0]) <= bw * 0.35
                hands_at_lap_or_ground = (wr_y >= hip_y - bh * 0.20) if hip_valid else True

                if hands_clustered and hands_at_lap_or_ground:
                    is_cooking = True

        if is_cooking:
            c_start = _get_start_t('cook_start_t')
            trk['cook_start_t'] = c_start
            wait_time = 0.0 if is_video_test else 1.2
            if (now_t - c_start) >= wait_time:
                sub_desc = 'Active Cooking with Open Flame / Smoke Detected' if has_fire_smoke else 'Unauthorized Cooking / Food Prep in Restricted Area'
                return 'cooking', 0.92, sub_desc
        else:
            trk['cook_start_t'] = None

    # 4. Smoking Gesture (Hand-to-Mouth with Acute Elbow Flexion and Upward Forearm)
    if _is_enabled('smoking_gesture') and has_kp and bh >= 70:
        nose = kp_data[0]
        l_wr, r_wr = kp_data[9], kp_data[10]
        l_el, r_el = kp_data[7], kp_data[8]
        l_sh, r_sh = kp_data[5], kp_data[6]
        l_knee, r_knee = kp_data[13], kp_data[14]

        face_conf = nose[2]
        face_x, face_y = nose[0], nose[1]

        if face_conf >= 0.35 and (l_sh[2] > 0.30 or r_sh[2] > 0.30):
            sh_y = min(l_sh[1], r_sh[1]) if (l_sh[2] > 0.30 and r_sh[2] > 0.30) else (l_sh[1] if l_sh[2] > 0.30 else r_sh[1])
            mouth_y = face_y + bh * 0.04
            is_smoking = False

            # Check if both hands are at collar/neck (jacket pulling / collar adjustment - Screenshot 5)
            both_hands_at_collar = (l_wr[2] > 0.30 and r_wr[2] > 0.30 and
                                    abs(l_wr[1] - r_wr[1]) < bh * 0.15 and
                                    l_wr[1] >= face_y and r_wr[1] >= face_y and
                                    abs(l_wr[0] - r_wr[0]) > bw * 0.18)

            # Check if elbow is resting on knee / lap (contemplative sitting / "The Thinker" - Screenshot 1)
            elbow_on_knee = False
            for el_pt in [l_el, r_el]:
                for kn_pt in [l_knee, r_knee]:
                    if el_pt[2] > 0.25 and kn_pt[2] > 0.25:
                        if math.hypot(el_pt[0] - kn_pt[0], el_pt[1] - kn_pt[1]) < bh * 0.18:
                            elbow_on_knee = True
                            break

            if not both_hands_at_collar and not elbow_on_knee:
                for wr, el, sh in [(l_wr, l_el, l_sh), (r_wr, r_el, r_sh)]:
                    if wr[2] >= 0.45 and el[2] >= 0.35 and sh[2] >= 0.30:
                        dist_to_mouth = math.hypot(wr[0] - face_x, wr[1] - mouth_y)
                        is_at_mouth = (face_y - bh * 0.03 <= wr[1] <= sh_y) and (abs(wr[0] - face_x) < bw * 0.20)
                        forearm_upward = (el[1] - wr[1] > bh * 0.08)
                        elbow_ang = calculate_joint_angle(sh, el, wr)
                        acute_flexion = (elbow_ang <= 115.0)

                        if dist_to_mouth < min(bh * 0.12, bw * 0.22) and is_at_mouth and forearm_upward and acute_flexion:
                            is_smoking = True
                            break

            if is_smoking:
                s_start = _get_start_t('smoke_start_t')
                trk['smoke_start_t'] = s_start
                wait_time = 0.0 if is_video_test else 1.8
                if (now_t - s_start) >= wait_time:
                    return 'smoking_gesture', 0.88, 'Persistent Hand-to-Mouth Smoking Gesture'
            else:
                trk['smoke_start_t'] = None

    # 5. Fighting Detection (Proximity & Altercation Mapping from User's Logic)
    if _is_enabled('fighting_posture'):
        is_fighting = False
        h1 = max(1.0, bh)
        center1 = np.array([(x1 + x2) / 2.0, (y1 + y2) / 2.0])
        
        nose1 = kp_data[0] if (has_kp and len(kp_data) >= 17 and kp_data[0][2] > 0.15) else np.array([center1[0], y1 + h1 * 0.2, 0.0])
        left_wrist1 = kp_data[9] if (has_kp and len(kp_data) >= 17 and kp_data[9][2] > 0.15) else None
        right_wrist1 = kp_data[10] if (has_kp and len(kp_data) >= 17 and kp_data[10][2] > 0.15) else None
        wrists1 = [w[:2] for w in [left_wrist1, right_wrist1] if w is not None]

        # Pairwise interaction check with all other persons (User's Exact Logic)
        if all_persons:
            for other in all_persons:
                if other is box:
                    continue
                
                if isinstance(other, dict):
                    o_box = other.get('box', other.get('bbox'))
                    o_kp = other.get('keypoints')
                    o_beh = other.get('behavior', other.get('unauth_activity'))
                else:
                    o_box = other
                    o_kp = None
                    o_beh = None
                
                if not o_box or len(o_box) != 4 or o_box == box:
                    continue
                # Skip interaction processing if one target is already verified lying flat on floor
                if o_beh == 'lying_down':
                    continue
                
                ox1, oy1, ox2, oy2 = o_box
                h2 = max(1.0, oy2 - oy1)
                center2 = np.array([(ox1 + ox2) / 2.0, (oy1 + oy2) / 2.0])
                
                # Measure structural gap metrics between separate individuals
                dist_centers = math.hypot(center1[0] - center2[0], center1[1] - center2[1])
                avg_height = (h1 + h2) / 2.0
                
                dynamic_center_thresh = avg_height * 1.3
                dynamic_contact_thresh = avg_height * 0.45
                
                # Check metrics mapping closest hand positions relative to opponent's face area
                o_nose = o_kp[0][:2] if (o_kp is not None and len(o_kp) >= 17 and o_kp[0][2] > 0.15) else [center2[0], oy1 + h2 * 0.2]
                o_wrists = [w[:2] for w in [o_kp[9], o_kp[10]] if (w is not None and len(w) >= 3 and w[2] > 0.15)] if (o_kp is not None and len(o_kp) >= 17) else []
                
                min_wrist_gap = float('inf')
                if wrists1:
                    for w1 in wrists1:
                        min_wrist_gap = min(min_wrist_gap, math.hypot(w1[0] - o_nose[0], w1[1] - o_nose[1]))
                if o_wrists:
                    for w2 in o_wrists:
                        min_wrist_gap = min(min_wrist_gap, math.hypot(w2[0] - nose1[0], w2[1] - nose1[1]))
                
                # Alternative grappling check: Bounding Box intersection fallback
                x_overlap = max(0.0, min(x2, ox2) - max(x1, ox1))
                y_overlap = max(0.0, min(y2, oy2) - max(y1, oy1))
                has_heavy_overlap = (x_overlap > 0) and (y_overlap > 0)
                
                wrists_valid = bool(wrists1 or o_wrists)
                is_close = dist_centers < dynamic_center_thresh
                is_touching = (min_wrist_gap < dynamic_contact_thresh) and wrists_valid
                
                if (is_close and is_touching) or has_heavy_overlap:
                    is_fighting = True
                    break

        # Solitary / Sparring Combat Stance Fallback
        if not is_fighting and has_kp and len(kp_data) >= 17:
            l_sh, r_sh = kp_data[5], kp_data[6]
            l_wr, r_wr = kp_data[9], kp_data[10]
            l_el, r_el = kp_data[7], kp_data[8]
            sh_y = min(l_sh[1], r_sh[1]) if (l_sh[2] > 0.25 and r_sh[2] > 0.25) else (l_sh[1] if l_sh[2] > 0.25 else r_sh[1])
            
            near_combatant = False
            if all_persons and len(all_persons) > 1:
                for ob in all_persons:
                    o_b = ob.get('box', ob.get('bbox')) if isinstance(ob, dict) else ob
                    if not o_b or o_b == box: continue
                    d_c = math.hypot((x1 + x2)/2.0 - (o_b[0] + o_b[2])/2.0, (y1 + y2)/2.0 - (o_b[1] + o_b[3])/2.0)
                    if d_c < max(bw, bh) * 2.2:
                        near_combatant = True
                        break
            
            if near_combatant:
                l_fist_raised = (l_wr[2] > 0.25 and sh_y > 0 and l_wr[1] <= sh_y + bh * 0.08)
                r_fist_raised = (r_wr[2] > 0.25 and sh_y > 0 and r_wr[1] <= sh_y + bh * 0.08)
                l_guard_bent = (l_el[2] > 0.25 and calculate_joint_angle(l_sh, l_el, l_wr) < 125.0) if l_fist_raised else False
                r_guard_bent = (r_el[2] > 0.25 and calculate_joint_angle(r_sh, r_el, r_wr) < 125.0) if r_fist_raised else False
                both_hands_guard = (l_fist_raised and r_fist_raised and (l_guard_bent or r_guard_bent))
                wrists_together = (l_fist_raised and r_fist_raised and abs(l_wr[0] - r_wr[0]) < bw * 0.25 and abs(l_wr[1] - r_wr[1]) < bh * 0.10)
                
                one_punch_extended = False
                if l_fist_raised and l_el[2] > 0.25 and calculate_joint_angle(l_sh, l_el, l_wr) > 135.0:
                    one_punch_extended = True
                elif r_fist_raised and r_el[2] > 0.25 and calculate_joint_angle(r_sh, r_el, r_wr) > 135.0:
                    one_punch_extended = True
                
                l_hip, r_hip = kp_data[11], kp_data[12]
                l_knee, r_knee = kp_data[13], kp_data[14]
                l_ank, r_ank = kp_data[15], kp_data[16]
                is_kicking = False
                if (l_ank[2] > 0.25 and l_hip[2] > 0.20 and l_ank[1] < l_hip[1] + bh * 0.12):
                    is_kicking = True
                elif (r_ank[2] > 0.25 and r_hip[2] > 0.20 and r_ank[1] < r_hip[1] + bh * 0.12):
                    is_kicking = True
                elif (l_knee[2] > 0.25 and l_hip[2] > 0.20 and l_knee[1] < l_hip[1] + bh * 0.05):
                    is_kicking = True
                elif (r_knee[2] > 0.25 and r_hip[2] > 0.20 and r_knee[1] < r_hip[1] + bh * 0.05):
                    is_kicking = True
                
                if both_hands_guard or one_punch_extended or is_kicking or (l_fist_raised and r_fist_raised and not wrists_together):
                    is_fighting = True

        if is_fighting:
            f_start = _get_start_t('fight_start_t')
            trk['fight_start_t'] = f_start
            wait_time = 0.0 if is_video_test else 0.5
            if (now_t - f_start) >= wait_time:
                return 'fighting_posture', 0.90, 'Combat Guard Stance / Physical Altercation'
        else:
            trk['fight_start_t'] = None

    # 6. Lying Down Detection (Aspect ratio check + spine profile from User's Logic)
    if _is_enabled('lying_down'):
        # 1. Aspect ratio check: If bounding box is significantly wider than it is tall
        is_horizontal_box = bw > (bh * 1.3)
        
        # 2. Check if nose and ankles share similar vertical positions
        is_horizontal_spine = False
        if has_kp and len(kp_data) >= 17:
            nose = kp_data[0]
            l_ank = kp_data[15]
            r_ank = kp_data[16]
            
            if nose[2] > 0.15 and l_ank[2] > 0.15:
                if abs(nose[1] - l_ank[1]) < (bh * 0.35):
                    is_horizontal_spine = True
            if not is_horizontal_spine and nose[2] > 0.15 and r_ank[2] > 0.15:
                if abs(nose[1] - r_ank[1]) < (bh * 0.35):
                    is_horizontal_spine = True

        is_lying = (is_horizontal_box or is_horizontal_spine) and not is_upright and not is_definitely_standing
        
        # Ground plane fallback for fallen/passed-out person flat on floor
        if not is_lying and aspect_ratio >= 1.8 and bh >= 14 and not is_upright and not is_definitely_standing:
            is_lying = True

        # Centroid stationarity across frames (reject mid-air jumping flips)
        if is_lying:
            prev_cen = trk.get('last_lying_cen')
            cur_cen = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
            trk['last_lying_cen'] = cur_cen
            if prev_cen:
                disp = math.hypot(cur_cen[0] - prev_cen[0], cur_cen[1] - prev_cen[1])
                if disp > max(bw, bh) * 0.25:
                    trk['lie_start_t'] = now_t
                    is_lying = False

        if is_lying:
            l_start = _get_start_t('lie_start_t')
            trk['lie_start_t'] = l_start
            wait_time = 0.0 if is_video_test else 0.8
            if (now_t - l_start) >= wait_time:
                return 'lying_down', 0.90, 'Horizontal Torso Orientation on Ground'
        else:
            trk['lie_start_t'] = None

    # 7. Biomechanical Sitting Detection (Optional - Disabled by default in Parking Area)
    is_sitting = False
    if _is_enabled('sitting') and has_kp and not is_definitely_standing:
        l_hip, r_hip = kp_data[11], kp_data[12]
        l_knee, r_knee = kp_data[13], kp_data[14]
        l_ank, r_ank = kp_data[15], kp_data[16]

        if l_hip[2] > 0.25 and l_knee[2] > 0.25 and l_ank[2] > 0.20:
            l_knee_ang = calculate_joint_angle(l_hip, l_knee, l_ank)
            l_thigh_dy = abs(l_knee[1] - l_hip[1])
            l_thigh_dx = abs(l_knee[0] - l_hip[0])
            if 55.0 <= l_knee_ang <= 130.0 and (l_thigh_dy < l_thigh_dx * 0.70 or l_thigh_dy < bh * 0.18):
                is_sitting = True

        if not is_sitting and r_hip[2] > 0.25 and r_knee[2] > 0.25 and r_ank[2] > 0.20:
            r_knee_ang = calculate_joint_angle(r_hip, r_knee, r_ank)
            r_thigh_dy = abs(r_knee[1] - r_hip[1])
            r_thigh_dx = abs(r_knee[0] - r_hip[0])
            if 55.0 <= r_knee_ang <= 130.0 and (r_thigh_dy < r_thigh_dx * 0.70 or r_thigh_dy < bh * 0.18):
                is_sitting = True

        if not is_sitting and aspect_ratio >= 0.75 and (l_knee[2] > 0.20 and r_knee[2] > 0.20):
            knee_spread = abs(l_knee[0] - r_knee[0])
            if knee_spread > bw * 0.45:
                if l_ank[2] > 0.15 and r_ank[2] > 0.15:
                    knee_y = (l_knee[1] + r_knee[1]) / 2.0
                    ank_y = (l_ank[1] + r_ank[1]) / 2.0
                    if abs(knee_y - ank_y) < bh * 0.16:
                        is_sitting = True

    if is_sitting:
        sit_start = _get_start_t('sit_start_t')
        trk['sit_start_t'] = sit_start
        wait_time = 0.0 if is_video_test else 1.5
        if (now_t - sit_start) >= wait_time:
            return 'sitting', 0.85, 'Seated Posture with Bent Knee Angles'
    else:
        trk['sit_start_t'] = None

    # 8. Crowd Gathering in Parking Bay (ONLY checked if no individual violation above!)
    # STRICT DEDUPLICATION: Merges overlapping boxes of the same person or phone screen
    if _is_enabled('crowd'):
        dedup_persons = []
        for p_item in (all_persons or []):
            pbox = p_item.get('box', p_item.get('bbox')) if isinstance(p_item, dict) else p_item
            if not pbox or len(pbox) != 4:
                continue
            px1, py1, px2, py2 = pbox
            p_area = max(1.0, px2 - px1) * max(1.0, py2 - py1)
            is_dup = False
            for dbox in dedup_persons:
                dx1, dy1, dx2, dy2 = dbox
                d_area = max(1.0, dx2 - dx1) * max(1.0, dy2 - dy1)
                ix1, iy1 = max(px1, dx1), max(py1, dy1)
                ix2, iy2 = min(px2, dx2), min(py2, dy2)
                if ix1 < ix2 and iy1 < iy2:
                    inter_area = (ix2 - ix1) * (iy2 - iy1)
                    min_area = min(p_area, d_area)
                    if (inter_area / min_area) > 0.25:
                        is_dup = True
                        break
            if not is_dup:
                dedup_persons.append(pbox)

        if len(dedup_persons) >= 3:
            num_persons_nearby = 0
            for ob in dedup_persons:
                ox1, oy1, ox2, oy2 = ob
                dist_cen = math.hypot((x1 + x2)/2.0 - (ox1 + ox2)/2.0, (y1 + y2)/2.0 - (oy1 + oy2)/2.0)
                if dist_cen < max(bw, bh) * 3.5:
                    num_persons_nearby += 1
            if num_persons_nearby >= 3:
                c_start = _get_start_t('crowd_start_t')
                trk['crowd_start_t'] = c_start
                wait_time = 0.5 if is_video_test else 2.5
                if (now_t - c_start) >= wait_time:
                    return 'crowd', 0.90, f'Crowd Gathering ({num_persons_nearby} Persons in Bay)'
            else:
                trk['crowd_start_t'] = None
        else:
            trk['crowd_start_t'] = None

    # 9. Loitering (Track dwell time in parking area >= 30.0s)
    if _is_enabled('loitering'):
        if dwell_time >= 30.0:
            return 'loitering', 0.88, f'Dwell Time {int(dwell_time)}s >= 30s'

    return None, 0.0, None

INFERENCE_LOCK = threading.RLock()

def get_yolo_model(model_path):
    resolved_path = str(model_path)
    m_dir = os.path.join(str(BASE_DIR), "models")
    
    if not os.path.exists(resolved_path):
        basename = os.path.basename(resolved_path)
        candidates = [
            os.path.join(m_dir, resolved_path),
            os.path.join(m_dir, basename),
            os.path.join(str(BASE_DIR), basename),
            os.path.join(str(BASE_DIR), "backend", "models", basename)
        ]
        for c in candidates:
            if os.path.exists(c):
                resolved_path = c
                break

    if not os.path.exists(resolved_path):
        if "vehicle_speed" in resolved_path or "speed" in resolved_path:
            fallback = os.path.join(str(BASE_DIR), "models", "yolov8n.pt")
            if not os.path.exists(fallback):
                fallback = os.path.join(str(BASE_DIR), "yolov8n.pt")
            if os.path.exists(fallback):
                print(f"[CACHE-FALLBACK] vehicle_speed.pt not found on disk, falling back to {fallback}", flush=True)
                resolved_path = fallback
            else:
                return None
        else:
            # File is not on disk - return None to prevent Ultralytics from freezing with GitHub 429 errors
            return None

    if resolved_path not in YOLO_CACHE:
        with INFERENCE_LOCK:
            if resolved_path not in YOLO_CACHE:
                try:
                    print(f"[CACHE] Loading model weights into memory: {resolved_path}", flush=True)
                    model = YOLO(resolved_path)
                    # Warm up model once with a dummy frame to avoid PyTorch first-pass graph compilation latency
                    try:
                        dummy_frame = np.zeros((480, 854, 3), dtype=np.uint8)
                        if 'torch' in globals() and hasattr(torch, 'inference_mode'):
                            with torch.inference_mode():
                                model.predict(source=dummy_frame, imgsz=480, verbose=False)
                        else:
                            model.predict(source=dummy_frame, imgsz=480, verbose=False)
                        print(f"[CACHE] Model warmed up successfully: {resolved_path}", flush=True)
                    except Exception as we:
                        print(f"[CACHE-WARN] Warmup skipped: {we}", flush=True)
                    YOLO_CACHE[resolved_path] = model
                except Exception as e:
                    print(f"[CACHE-ERR] Could not load model weights {resolved_path}: {e}", flush=True)
                    return None
    return YOLO_CACHE.get(resolved_path)

def get_alerts_base_url():
    try:
        env_url = os.getenv("PUBLIC_URL") or os.getenv("TAILSCALE_URL") or os.getenv("BASE_URL")
        if env_url and not env_url.startswith("("):
            return env_url.strip()
        candidate_paths = [
            os.path.join(str(BASE_DIR), "hls", "public_url.txt"),
            os.path.join(str(BASE_DIR), "public_url.txt"),
            "/home/algo/rtsp_http_cla/hls/public_url.txt",
            "hls/public_url.txt"
        ]
        for cp in candidate_paths:
            if os.path.exists(cp):
                with open(cp) as f:
                    val = f.read().strip()
                    if val and not val.startswith("(") and ("http://" in val or "https://" in val):
                        return val
    except: pass
    return ""

def is_opposite_class(cls1, cls2):
    try:
        b_neg, b_core, _ = extract_negation_and_core(cls1)
        e_neg, e_core, _ = extract_negation_and_core(cls2)
        if b_neg != e_neg:
            if b_core == e_core and len(b_core) >= 3:
                return True
            if (b_core in e_core or e_core in b_core) and len(min(b_core, e_core, key=len)) >= 4:
                return True
            pairs = [
                ("tape", "tape"),
                ("hose", "hose"),
                ("spark", "spark"),
                ("extinguisher", "extinguisher"),
                ("chock", "chock"),
                ("choke", "choke"),
                ("sand", "sand"),
                ("bucket", "bucket")
            ]
            for p1, p2 in pairs:
                if (p1 in b_core and p2 in e_core) or (p2 in b_core and p1 in e_core):
                    return True
    except Exception:
        pass
    return False

DYNAMIC_CLASS_COLOR_MAP = {
    # Violations (NO-PPE, Missing Safety Equipment) -> Distinct Warning Colors (Red, Orange, Magenta, Pink)
    "no-spark-arrestor": (20, 20, 237),
    "nosparkarrestor": (20, 20, 237),
    "no-fire-extinguisher": (20, 20, 237),
    "nofireextinguisher": (20, 20, 237),
    "no-sand-bucket": (20, 20, 237),
    "nosandbucket": (20, 20, 237),
    "no-wheel-choke": (20, 20, 237),
    "nowheelchoke": (20, 20, 237),
    "no-fire-hose": (20, 20, 237),
    "nofirehose": (20, 20, 237),
    "no-tape": (20, 20, 237),
    "notape": (20, 20, 237),

    "no-safety-vest": (0, 0, 255),        # Bright Crimson Alert Red (BGR)
    "no-vest": (0, 0, 255),
    "nosafetyvest": (0, 0, 255),
    "novest": (0, 0, 255),
    "no-saftey-vest": (0, 0, 255),

    "no-hardhat": (0, 100, 255),         # Vivid Amber Orange (BGR)
    "no-helmet": (0, 100, 255),
    "nohardhat": (0, 100, 255),
    "nohelmet": (0, 100, 255),

    "no-mask": (255, 0, 255),            # Electric Magenta / Fuchsia (BGR)
    "nomask": (255, 0, 255),

    "no-gloves": (200, 80, 255),         # Neon Pink (BGR)
    "nogloves": (200, 80, 255),

    "no-goggles": (220, 30, 180),        # Deep Violet (BGR)
    "nogoggles": (220, 30, 180),
    "no-glasses": (220, 30, 180),

    "no-shoes": (0, 140, 220),           # Amber Rust (BGR)
    "noshoes": (0, 140, 220),
    "no-boots": (0, 140, 220),

    # Compliant Equipment -> Emerald Green, Cyan Sky-Blue, Lemon Yellow
    "spark-arrestor": (0, 230, 80),
    "sparkarrestor": (0, 230, 80),
    "fire-extinguisher": (0, 230, 80),
    "fireextinguisher": (0, 230, 80),
    "sand-bucket": (0, 230, 80),
    "sandbucket": (0, 230, 80),
    "wheel-choke": (0, 230, 80),
    "wheelchoke": (0, 230, 80),
    "fire-hose": (0, 230, 80),
    "firehose": (0, 230, 80),
    "firehose-box": (0, 230, 80),
    "firehosebox": (0, 230, 80),
    "reflective-tape-floor": (0, 230, 80),
    "reflective-tape-person": (0, 230, 80),
    "reflective-tape-vehicles": (0, 230, 80),
    "traffic-tape-bollards": (0, 230, 80),
    "traffic-tape-cone": (0, 230, 80),

    "safety-vest": (255, 220, 0),        # Electric Sky Blue / Cyan (BGR)
    "vest": (255, 220, 0),
    "saftey-vest": (255, 220, 0),

    "hardhat": (0, 230, 80),             # Emerald Lime Green (BGR)
    "helmet": (0, 230, 80),

    "mask": (0, 230, 255),               # Lemon Yellow (BGR)
    "goggles": (255, 120, 30),           # Cobalt Blue (BGR)
    "gloves": (180, 230, 50),            # Turquoise Teal (BGR)
    "shoes": (50, 205, 50),              # Spring Green (BGR)
    "boots": (50, 205, 50),

    # Objects & People
    "person": (200, 100, 50),            # Slate Blue (BGR)
    "worker": (200, 100, 50),
    "human": (200, 100, 50),
    "fire": (0, 0, 255),                 # Pure Red
    "smoke": (180, 180, 180),            # Silver Grey
}

def get_dynamic_class_color(class_name):
    """
    Returns a unique, fixed, high-contrast BGR color for each class name.
    Guarantees NO-Hardhat, NO-Safety Vest, NO-Mask, etc., have completely distinct colors.
    """
    if not class_name:
        return (0, 255, 255)
    
    norm_name = clean_str(class_name).replace("_", "-").replace(" ", "-")
    if norm_name in DYNAMIC_CLASS_COLOR_MAP:
        return DYNAMIC_CLASS_COLOR_MAP[norm_name]
        
    for k, v in DYNAMIC_CLASS_COLOR_MAP.items():
        if k in norm_name or norm_name in k:
            return v
            
    h = int(hashlib.md5(norm_name.encode('utf-8')).hexdigest(), 16)
    palette = [
        (0, 140, 255),   # Vivid Orange
        (255, 190, 40),  # Electric Cyan
        (30, 45, 255),   # Coral Red
        (0, 230, 115),   # Emerald Green
        (180, 20, 255),  # Magenta
        (0, 215, 255),   # Golden Yellow
        (255, 105, 180), # Neon Pink
        (210, 230, 0),   # Turquoise
        (50, 205, 50),   # Lime Green
        (238, 130, 238), # Violet
        (30, 144, 255),  # Sky Blue
        (255, 215, 0),   # Gold
        (255, 69, 0),    # Red Orange
        (0, 255, 127),   # Spring Green
    ]
    return palette[h % len(palette)]

def get_config_for_model(model_configs, m_name):
    if not isinstance(model_configs, dict) or not model_configs:
        return {}
    m_clean = m_name.replace(".pt", "")
    m_norm = m_clean.lower().replace("_", "-").replace(" ", "-")
    for k, v in model_configs.items():
        if isinstance(v, dict):
            k_clean = str(k).replace(".pt", "")
            k_norm = k_clean.lower().replace("_", "-").replace(" ", "-")
            if k_norm == m_norm or k_clean == m_clean:
                return v
    # Inherit configuration for companion PPE models
    if m_norm in ("ppe-new", "nik-ppe-best", "keremberke-ppe-gear", "hf-ppe-detection"):
        for k, v in model_configs.items():
            if "hansung" in str(k).lower() and isinstance(v, dict):
                return v
    # Inherit configuration for companion vehicle models
    if m_norm == "yolov8n":
        for k, v in model_configs.items():
            if "sand" in str(k).lower() and isinstance(v, dict):
                return v
    if "enabled_classes" in model_configs or "class_configs" in model_configs or "conf" in model_configs:
        return model_configs
    return {}

class InferenceScheduler:
    """
    Centralized, Fair Multi-Camera Inference Scheduler.
    Executes AI inference in strict round-robin sequence across all active cameras.
    Guarantees exactly ONE PyTorch inference runs on the Pi CPU at any time,
    eliminating lock contention, overheating, and thread starvation.
    """
    def __init__(self):
        self._workers = {}
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()

    def register_worker(self, worker):
        with self._lock:
            self._workers[worker.cam_id] = worker
            worker._needs_immediate_inference = True
            print(f"[SCHEDULER] Registered Camera {worker.cam_id} into Central Inference Scheduler (Total Active: {len(self._workers)})", flush=True)
        self.ensure_running()

    def unregister_worker(self, worker):
        with self._lock:
            self._workers.pop(worker.cam_id, None)
            print(f"[SCHEDULER] Unregistered Camera {worker.cam_id} from Central Inference Scheduler (Remaining Active: {len(self._workers)})", flush=True)

    def ensure_running(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop_event.clear()
                self._thread = threading.Thread(target=self._run_loop, daemon=True, name="CentralInferenceScheduler")
                self._thread.start()

    def _run_loop(self):
        print("[SCHEDULER] Centralized Multi-Camera Inference Scheduler active", flush=True)
        while not self._stop_event.is_set():
            with self._lock:
                new_workers = [w for w in self._workers.values() if getattr(w, '_needs_immediate_inference', False)]
                other_workers = [w for w in self._workers.values() if not getattr(w, '_needs_immediate_inference', False)]
                active_workers = new_workers + other_workers

            if not active_workers:
                time.sleep(0.05)
                continue

            for worker in active_workers:
                if self._stop_event.is_set():
                    break
                if worker._stop_event.is_set():
                    continue
                try:
                    worker.run_single_inference_cycle()
                    worker._needs_immediate_inference = False
                except Exception as e:
                    print(f"[SCHEDULER-ERR] Camera {worker.cam_id} inference error: {e}", flush=True)

                time.sleep(SCHEDULER_SLEEP_S)

GLOBAL_INFERENCE_SCHEDULER = InferenceScheduler()


class DetectorWorker:
    @property
    def model_configs(self):
        return getattr(self, "_model_configs", {})

    @model_configs.setter
    def model_configs(self, val):
        self._model_configs = val or {}
        new_roi = self._model_configs.get("roi_polygon")
        if new_roi != getattr(self, "roi_polygon", None):
            self.roi_polygon = new_roi
            if hasattr(self, "_box_lock"):
                with self._box_lock:
                    self._tracked_boxes = []
            if getattr(self, "crowd_engine", None) and new_roi and len(new_roi) == 2:
                try:
                    min_x = min(new_roi[0][0], new_roi[1][0])
                    max_x = max(new_roi[0][0], new_roi[1][0])
                    min_y = min(new_roi[0][1], new_roi[1][1])
                    max_y = max(new_roi[0][1], new_roi[1][1])
                    self.crowd_engine.roi_coords = (int(min_x * self.width), int(min_y * self.height), int(max_x * self.width), int(max_y * self.height))
                except Exception:
                    pass
        if getattr(self, "crowd_engine", None):
            cfg = get_config_for_model(self._model_configs, "yolov8n.pt") if self._model_configs else {}
            class_cfg = cfg.get("class_configs", {}).get("person", {}) if isinstance(cfg, dict) else {}
            val = class_cfg.get("crowd_threshold")
            if val is None:
                val = cfg.get("crowd_threshold")
            if val is not None:
                try:
                    self.crowd_engine.crowd_threshold = max(1, int(val))
                except Exception:
                    pass
        print(f"[WORKER-ROI-UPDATE] Camera {getattr(self, 'cam_id', '?')} model_configs updated, roi_polygon={self.roi_polygon}", flush=True)

    @staticmethod
    def _resolve_companion_models(model_paths):
        paths = list(model_paths) if isinstance(model_paths, list) else ([model_paths] if model_paths else [])
        seen = []
        for p in paths:
            if p and p not in seen:
                seen.append(p)

        companion_dirs = [
            os.path.join(str(BASE_DIR), "backend", "companion_models"),
            os.path.join(str(BASE_DIR), "companion_models"),
            "/home/algo/Documents/rtsp_http_cla/backend/companion_models",
            "/home/algo/Documents/rtsp_http_cla/companion_models",
            os.path.join(str(BASE_DIR), "models")
        ]

        # Hansung PPE requires ppe_new.pt and nik_ppe_best.pt to dynamically cover all 7 PPE violation classes:
        # NO-Hardhat, NO-Mask, NO-Safety Vest, No-Gloves, No-Shoes, No-Goggles, No-Safety-Belt
        has_hansung = any("hansung_ppe_violations" in str(p).lower() for p in seen)
        if has_hansung:
            for comp in ["ppe_new.pt", "nik_ppe_best.pt"]:
                for cd in companion_dirs:
                    cp = os.path.join(cd, comp)
                    if os.path.exists(cp) and cp not in seen:
                        seen.append(cp)
                        break

        # Companion model expansion for sand_ext_chocks (detect vehicles/trucks to check wheel chock compliance)
        has_sand_ext = any("sand_ext_chocks" in str(p).lower() for p in seen)
        if has_sand_ext:
            for cd in companion_dirs:
                cp = os.path.join(cd, "yolov8n.pt")
                if os.path.exists(cp) and cp not in seen:
                    seen.append(cp)
                    break

        # Companion model expansion for unauthorized_activity: Final_Fire_smoke.pt for fire/smoke cooking detection
        has_unauth = any("unauthorized" in str(p).lower() for p in seen)
        if has_unauth:
            for cd in companion_dirs:
                cp = os.path.join(cd, "Final_Fire_smoke.pt")
                if os.path.exists(cp) and cp not in seen:
                    seen.append(cp)
                    break

        # Companion model expansion for spark_arrestor: yolov8n.pt to detect vehicles/trucks for compliance
        has_spark = any("spark_arrest" in str(p).lower() for p in seen)
        if has_spark:
            for cd in companion_dirs:
                cp = os.path.join(cd, "yolov8n.pt")
                if os.path.exists(cp) and cp not in seen:
                    seen.append(cp)
                    break

        return seen

    def __init__(self, rtsp_url, output_dir, model_paths, conf=0.20, iou=0.45, location="Camera", model_configs=None):
        self._model_configs = model_configs or {}
        self.roi_polygon = self._model_configs.get("roi_polygon") if isinstance(self._model_configs, dict) else None
        self.rtsp_url, self.output_dir, self.conf, self.iou, self.location = rtsp_url, output_dir, conf, iou, location
        self.model_paths = DetectorWorker._resolve_companion_models(model_paths)
        self.cam_id = os.path.basename(output_dir).replace("stream", "").replace("_detected", "")
        speed_flag = False
        if isinstance(self._model_configs, dict):
            speed_flag = bool(self._model_configs.get("enable_speed") or self._model_configs.get("speed_tracking") or self._model_configs.get("is_speed"))
        self.is_speed_worker = (str(self.cam_id) == "speed" or speed_flag)
        self.is_crowd_worker = (str(self.cam_id) == "crowd")
        _check_models = self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]
        self.fps = 10.0
        self.width, self.height = 640, 360
        self._latest_raw_frame = None
        self._latest_speed_frame = None
        self._tracked_boxes = []
        self._prev_inference_boxes = []
        self._frame_lock, self._box_lock = threading.Lock(), threading.Lock()
        self._stop_event = threading.Event()
        self._last_frame_time, self._cap_ok = time.time(), True
        self.alert_timers, self.alert_triggered = {}, set()
        self.speed_limit_kmh = float(os.getenv("TERMINAL_SPEED_LIMIT", "10.0"))
        self.road_distance_meters = float(os.getenv("TERMINAL_GATE_GANTRY_METERS", "20.0"))
        self.line_a_ratio = float(os.getenv("LINE_A_RATIO", "0.40"))
        self.line_b_ratio = float(os.getenv("LINE_B_RATIO", "0.75"))
        self.vehicle_counts = Counter()
        self.overspeed_count = 0
        self.speed_records = []
        self.models = None
        self._db_conn = None
        self._start_time = time.time()
        self._models_active_time = None
        self._first_box_logged = False
        self._ema_tracks = {}
        self._ema_next_id = 0
        self.byte_tracker = EdgeByteTracker(
            track_thresh=float(os.getenv("BYTETRACK_THRESH", "0.35")),
            low_thresh=float(os.getenv("BYTETRACK_LOW_THRESH", "0.12")),
            match_thresh=float(os.getenv("BYTETRACK_MATCH_THRESH", "0.65")),
            track_buffer=int(os.getenv("BYTETRACK_BUFFER", "45")),
            frame_rate=30
        ) if EdgeByteTracker is not None else None

        # Standalone VehicleSpeedTracker Engine (Low-CPU imgsz=320, skip=2)
        if self.is_speed_worker and VehicleSpeedTracker is not None:
            veh_model = self.model_paths[0] if (isinstance(self.model_paths, list) and self.model_paths) else str(self.model_paths)
            speed_cfg_file = os.path.join(BASE_DIR, "speed_config.json")
            persisted_cfg = {}
            if os.path.exists(speed_cfg_file):
                try:
                    with open(speed_cfg_file, "r") as scf:
                        persisted_cfg = json.load(scf)
                except: pass
            
            line_a_cfg = persisted_cfg.get("line_a", {"x1_pct": 5, "y1_pct": int(self.line_a_ratio * 100), "x2_pct": 95, "y2_pct": int(self.line_a_ratio * 100)})
            line_b_cfg = persisted_cfg.get("line_b", {"x1_pct": 5, "y1_pct": int(self.line_b_ratio * 100), "x2_pct": 95, "y2_pct": int(self.line_b_ratio * 100)})

            speed_cfg = {
                "model_path": veh_model,
                "speed_limit_kmh": float(persisted_cfg.get("speed_limit_kmh", self.speed_limit_kmh)),
                "road_distance_meters": float(persisted_cfg.get("road_distance_meters", self.road_distance_meters)),
                "confidence_threshold": max(0.20, float(self.conf)),
                "imgsz": 320,
                "frame_skip": 1,
                "line_a": line_a_cfg,
                "line_b": line_b_cfg,
                "allowed_classes": ["truck", "car", "pickup truck", "bike", "tank truck", "vehicle", "van", "bus"],
                "alerts_dir": os.path.join(str(BASE_DIR), "alerts")
            }
            self.speed_tracker_engine = VehicleSpeedTracker(speed_cfg)
            try:
                dummy = np.zeros((320, 320, 3), dtype=np.uint8)
                self.speed_tracker_engine.model.predict(source=dummy, imgsz=320, verbose=False)
                print(f"[SPEED-ENGINE] Pre-warmed YOLO model weights in memory", flush=True)
            except Exception as e:
                print(f"[SPEED-ENGINE] Model pre-warm warning: {e}", flush=True)
            print(f"[SPEED-ENGINE] Initialized standalone VehicleSpeedTracker for Camera {self.cam_id} (imgsz=320, limit={self.speed_limit_kmh}km/h)", flush=True)
        else:
            self.speed_tracker_engine = None

        # Standalone CrowdMonitoring Engine (YOLOv8n Person Tracking & ROI Group Clustering)
        if self.is_crowd_worker and CrowdMonitoring is not None:
            crowd_model = "yolov8n.pt"
            roi_c = None
            if self.roi_polygon and len(self.roi_polygon) == 2:
                try:
                    min_x = min(self.roi_polygon[0][0], self.roi_polygon[1][0])
                    max_x = max(self.roi_polygon[0][0], self.roi_polygon[1][0])
                    min_y = min(self.roi_polygon[0][1], self.roi_polygon[1][1])
                    max_y = max(self.roi_polygon[0][1], self.roi_polygon[1][1])
                    roi_c = (int(min_x * self.width), int(min_y * self.height), int(max_x * self.width), int(max_y * self.height))
                except Exception:
                    roi_c = None

            cfg = get_config_for_model(self.model_configs, "yolov8n.pt") if self.model_configs else {}
            class_cfg = cfg.get("class_configs", {}).get("person", {}) if isinstance(cfg, dict) else {}
            thresh = int(class_cfg.get("crowd_threshold") or cfg.get("crowd_threshold") or os.getenv("CROWD_THRESHOLD", "4"))
            dist_px = int(cfg.get("group_distance_px", 180)) if isinstance(cfg, dict) else 180

            self.crowd_engine = CrowdMonitoring(
                model_path=crowd_model,
                crowd_threshold=thresh,
                group_distance_px=dist_px,
                roi_coords=roi_c,
                alert_cooldown_seconds=4.0
            )
            self.crowd_stats = {
                "in_roi_count": 0,
                "total_visible": 0,
                "clusters_count": 0,
                "has_crowd": False,
                "is_alert": False
            }
            print(f"[CROWD-ENGINE] Initialized standalone CrowdMonitoring for Camera {self.cam_id} using yolov8n.pt (threshold={thresh})", flush=True)
        else:
            self.crowd_engine = None
            self.crowd_stats = {}

        self._latest_jpeg_frame = None
        print(f"[TIMER-START] Camera {self.cam_id} Start request initialized at {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}", flush=True)

    def get_jpeg_frame(self):
        with self._frame_lock:
            return getattr(self, '_latest_jpeg_frame', None)

    def update_models(self, model_paths, model_configs=None, conf=None, iou=None, location=None):
        if model_paths is not None:
            self.model_paths = DetectorWorker._resolve_companion_models(model_paths)
            self.models = [get_yolo_model(mp) for mp in self.model_paths]
            self._models_active_time = time.time()
        if model_configs is not None:
            self.model_configs = model_configs
        if conf is not None:
            self.conf = conf
        if iou is not None:
            self.iou = iou
        if location is not None:
            self.location = location
        if hasattr(self, "_box_lock"):
            with self._box_lock:
                self._tracked_boxes = []
        # Reset EMA tracks when models change so stale boxes from old model don't linger
        self._ema_tracks = {}
        self._ema_next_id = 0
        if getattr(self, 'byte_tracker', None) is not None:
            self.byte_tracker.reset()
        self._needs_immediate_inference = True

        self.is_crowd_worker = (str(self.cam_id) == "crowd")
        if self.is_crowd_worker and CrowdMonitoring is not None:
            crowd_model = "yolov8n.pt"
            cfg = get_config_for_model(self.model_configs, "yolov8n.pt") if self.model_configs else {}
            class_cfg = cfg.get("class_configs", {}).get("person", {}) if isinstance(cfg, dict) else {}
            thresh = int(class_cfg.get("crowd_threshold") or cfg.get("crowd_threshold") or os.getenv("CROWD_THRESHOLD", "4"))
            dist_px = int(cfg.get("group_distance_px", 180)) if isinstance(cfg, dict) else 180
            roi_c = None
            if self.roi_polygon and len(self.roi_polygon) == 2:
                try:
                    min_x = min(self.roi_polygon[0][0], self.roi_polygon[1][0])
                    max_x = max(self.roi_polygon[0][0], self.roi_polygon[1][0])
                    min_y = min(self.roi_polygon[0][1], self.roi_polygon[1][1])
                    max_y = max(self.roi_polygon[0][1], self.roi_polygon[1][1])
                    roi_c = (int(min_x * self.width), int(min_y * self.height), int(max_x * self.width), int(max_y * self.height))
                except Exception:
                    roi_c = None
            self.crowd_engine = CrowdMonitoring(
                model_path=crowd_model,
                crowd_threshold=thresh,
                group_distance_px=dist_px,
                roi_coords=roi_c,
                alert_cooldown_seconds=4.0
            )

        if not getattr(self, 'is_speed_worker', False) and not getattr(self, 'is_crowd_worker', False):
            GLOBAL_INFERENCE_SCHEDULER.register_worker(self)
        print(f"[WORKER-DYNAMIC-UPDATE] Camera {getattr(self, 'cam_id', '?')} dynamically updated models to {self.model_paths} in 0ms without restarting RTSP or FFmpeg", flush=True)

    def stop(self):
        self._stop_event.set()
        if not getattr(self, 'is_speed_worker', False) and not getattr(self, 'is_crowd_worker', False):
            GLOBAL_INFERENCE_SCHEDULER.unregister_worker(self)

    def _get_db_conn(self):
        if not PSYCOPG2_AVAILABLE:
            return None
        if self._db_conn is None or getattr(self._db_conn, 'closed', 1) != 0:
            try:
                self._db_conn = psycopg2.connect(DB_DSN, connect_timeout=5)
            except Exception as e:
                print(f"[ALERT-DB-CONN-ERR] PostgreSQL connection to {DB_DSN} failed: {e}", flush=True)
                self._db_conn = None
        return self._db_conn

    def _create_ffmpeg(self):
        os.makedirs(self.output_dir, exist_ok=True)
        for f in glob.glob(os.path.join(self.output_dir, "*")):
            try:
                if os.path.isfile(f) or os.path.islink(f):
                    os.remove(f)
            except Exception:
                pass

        session_id = int(time.time())
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{self.width}x{self.height}", 
            "-r", str(int(self.fps)), "-i", "-", "-an", "-c:v", "libx264", "-preset", "ultrafast", 
            "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-threads", "1",
            "-profile:v", "baseline", "-level:v", "3.1",
            "-b:v", "1500k", "-maxrate", "2200k", "-bufsize", "3000k",
            "-g", str(int(self.fps)), 
            "-keyint_min", str(int(self.fps)), "-sc_threshold", "0",
            "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
            "-hls_flags", "delete_segments+independent_segments+discont_start+omit_endlist+temp_file", 
            "-hls_segment_filename", os.path.join(self.output_dir, f"segment_{session_id}_%d.ts"), 
            os.path.join(self.output_dir, "playlist.m3u8")
        ]
        # NOTE: hls_list_size=6 keeps 6x2s = 12s of segments for remote viewers to buffer
        # without stuttering over high-latency tunnels (ngrok/cloudflare).
        log = open(os.path.join(self.output_dir, "ffmpeg.log"), "a")
        print(f"[LOG] Camera {self.cam_id} detector stream started with resolution: {self.width}x{self.height}, FPS: {self.fps}, Bitrate: 350k (max 450k)", flush=True)
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log, stdout=subprocess.DEVNULL, bufsize=10*1024*1024)

    # --------------------------------------------------------------------------
    # NMS HELPERS
    # --------------------------------------------------------------------------
    @staticmethod
    def _box_iou_and_io_min(b1, b2):
        """Compute IoU and intersection-over-min-area for two [x1,y1,x2,y2] boxes."""
        x1 = max(b1[0], b2[0]); y1 = max(b1[1], b2[1])
        x2 = min(b1[2], b2[2]); y2 = min(b1[3], b2[3])
        if x2 <= x1 or y2 <= y1:
            return 0.0, 0.0
        inter  = (x2 - x1) * (y2 - y1)
        area1  = max(1.0, (b1[2]-b1[0]) * (b1[3]-b1[1]))
        area2  = max(1.0, (b2[2]-b2[0]) * (b2[3]-b2[1]))
        union  = area1 + area2 - inter
        return inter / max(1.0, union), inter / min(area1, area2)

    @staticmethod
    def _apply_nms(boxes):
        """
        Full multi-class NMS pass over a list of (xyxy, color, conf, cls_name) tuples.
        - Boxes sorted by confidence (high->low).
        - Same-class duplicates removed when IoU >= NMS_SAME_IOU_THRESH or io_min >= NMS_SAME_IO_MIN_THRESH.
        - Opposite-class pairs resolved by keeping the higher-confidence box when
          IoU >= NMS_OPP_IOU_THRESH or io_min >= NMS_OPP_IO_MIN_THRESH.
        """
        if not boxes:
            return []
        sorted_boxes = sorted(boxes, key=lambda x: x[2], reverse=True)
        kept = []
        veh_kws = ("car", "truck", "bus", "van", "pickup", "bike", "vehicle")
        for item in sorted_boxes:
            b1, col1, conf1, cls1 = item
            is_veh1 = any(vk in cls1.lower() for vk in veh_kws)
            suppress = False
            for k_item in kept:
                b2, col2, conf2, cls2 = k_item
                is_veh2 = any(vk in cls2.lower() for vk in veh_kws)
                iou, io_min = DetectorWorker._box_iou_and_io_min(b1, b2)
                is_same = match_class(cls1, cls2)
                is_opp  = is_opposite_class(cls1, cls2)
                if is_same and (iou >= NMS_SAME_IOU_THRESH or io_min >= NMS_SAME_IO_MIN_THRESH):
                    suppress = True; break
                if is_veh1 and is_veh2 and (iou >= 0.40 or io_min >= 0.50):
                    suppress = True; break
                if is_opp and (iou >= NMS_OPP_IOU_THRESH or io_min >= NMS_OPP_IO_MIN_THRESH):
                    suppress = True; break
            if not suppress:
                kept.append(item)
        return kept

    def run_single_inference_cycle(self):
        """
        Executed by the Centralized InferenceScheduler in the background.
        1. Captures the latest live frame ONCE.
        2. Evaluates all assigned models on that identical frame snapshot.
        3. Fuses detections with per-model filtering, negation handling, and multi-model NMS.
        4. Updates the smooth bounding box tracker and checks the 3.0s alert timer.
        """
        if self._stop_event.is_set():
            return

        with self._frame_lock:
            f = self._latest_raw_frame

        if f is None:
            return

        # -- Resolve output canvas size --------------------------------------
        orig_h, orig_w = f.shape[:2]
        # We run inference on the ORIGINAL raw frame so YOLO gets best quality.
        # We then scale coordinates to the output stream resolution afterward.
        scale_x = float(self.width)  / max(1.0, float(orig_w))
        scale_y = float(self.height) / max(1.0, float(orig_h))

        f_h, f_w = self.height, self.width

        cur_cls, now = set(), time.time()
        raw_boxes = []   # list of (xyxy_in_output_res, color, conf, cls_name)
        detected_persons_raw = []  # list of (rx1, ry1, rx2, ry2, conf) for dual near/far zoom
        detected_pose_keypoints = []  # list of (box_xyxy, kp_scaled) for pose activity classification

        # Collect all classes the user explicitly enabled for this camera
        camera_selected_classes = []
        if isinstance(self.model_configs, dict):
            for mk, mv in self.model_configs.items():
                if isinstance(mv, dict) and mv.get("enabled_classes"):
                    for ec in mv["enabled_classes"]:
                        if ec not in camera_selected_classes:
                            camera_selected_classes.append(ec)

        if not self.models:
            paths = self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]
            self.models = [get_yolo_model(mp) for mp in paths]
            self._models_active_time = time.time()

        for midx, model in enumerate(self.models):
            if model is None:
                continue
            m_path = self.model_paths[midx] if (isinstance(self.model_paths, list) and midx < len(self.model_paths)) else str(self.model_paths)
            m_name = os.path.basename(m_path)

            m_conf = self.conf
            m_iou  = self.iou
            enabled_classes = None
            class_configs   = {}
            is_veh_model    = ("vehicle" in m_name.lower() or "speed" in m_name.lower())
            is_unauth_mod   = ("unauthorized" in m_name.lower())
            default_imgsz   = 320 if is_veh_model else int(os.getenv("DEFAULT_IMGSZ", "640"))
            m_imgsz         = default_imgsz
            cfg             = get_config_for_model(self.model_configs, m_name)
            if cfg and isinstance(cfg, dict):
                class_configs = dict(cfg.get("class_configs", {}) or {})

            # Companion model inheritance: ppe_new and nik inherit from hansung_ppe_violations if not explicitly configured
            if any(k in m_name.lower() for k in ("ppe_new", "nik")):
                h_cfg = get_config_for_model(self.model_configs, "hansung_ppe_violations.pt")
                if h_cfg and isinstance(h_cfg, dict):
                    if not cfg or not cfg.get("enabled_classes"):
                        h_enabled = h_cfg.get("enabled_classes")
                        if h_enabled is not None:
                            enabled_classes = h_enabled
                    if m_conf == self.conf and "conf" in h_cfg:
                        m_conf = float(h_cfg["conf"])
                    if m_iou == self.iou and "iou" in h_cfg:
                        m_iou = float(h_cfg["iou"])
                    h_cc = h_cfg.get("class_configs", {})
                    if h_cc and not class_configs:
                        class_configs = dict(h_cc)

            if cfg and isinstance(cfg, dict):
                m_conf          = float(cfg.get("conf", m_conf))
                m_iou           = float(cfg.get("iou", m_iou))
                if "enabled_classes" in cfg:
                    enabled_classes = cfg.get("enabled_classes")
                m_imgsz         = int(cfg.get("imgsz", default_imgsz))
                if "class_configs" in cfg:
                    class_configs = cfg.get("class_configs", class_configs) or {}

            if m_imgsz % 32 != 0:
                m_imgsz = int(math.ceil(m_imgsz / 32.0) * 32)

            if is_unauth_mod:
                filter_classes = ["person"]
            elif enabled_classes is not None:
                filter_classes = list(enabled_classes)
            elif camera_selected_classes:
                filter_classes = list(camera_selected_classes)
            else:
                filter_classes = None

            effective_conf = float(m_conf) if (m_conf is not None) else float(self.conf)
            min_class_conf = effective_conf
            if class_configs and isinstance(class_configs, dict):
                for cc in class_configs.values():
                    if isinstance(cc, dict) and "conf" in cc:
                        min_class_conf = min(min_class_conf, float(cc["conf"]))

            predict_kwargs = {
                "source": f,
                "conf":   max(PREDICT_CONF_FLOOR, min(0.20, min_class_conf, effective_conf) if is_unauth_mod else min(min_class_conf, effective_conf)),
                "iou":    m_iou,
                "imgsz":  m_imgsz,
                "verbose": False
            }

            try:
                t_infer_start = time.time()
                if 'torch' in globals() and hasattr(torch, 'inference_mode'):
                    with torch.inference_mode():
                        results = model.predict(**predict_kwargs)
                else:
                    results = model.predict(**predict_kwargs)
                infer_ms = int((time.time() - t_infer_start) * 1000)

                mod_dets   = []
                all_raw_dets = []
                for r in results:
                    if r.boxes:
                        for b in r.boxes:
                            cls_id   = int(b.cls[0].item())
                            cls_name = r.names.get(cls_id, str(cls_id)) if hasattr(r, 'names') else str(cls_id)
                            conf_val = float(b.conf[0].item())
                            all_raw_dets.append(f"{cls_name} {conf_val:.2f}")
                            if is_unauth_mod:
                                if cls_name.lower() == "person":
                                    mod_dets.append(f"person(pose) {conf_val:.2f}")
                            elif filter_classes is None or any(match_class(cls_name, e) for e in filter_classes):
                                mod_dets.append(f"{cls_name} {conf_val:.2f}")

                print(f"[TIMER-INFERENCE] Camera {self.cam_id} model {m_name} (imgsz={m_imgsz}) -> Pure: {infer_ms}ms | Selected: {filter_classes if filter_classes is not None else 'ALL'} | Filtered Match: {mod_dets if mod_dets else 'None'} | Raw YOLO Output: {all_raw_dets if all_raw_dets else 'None'} ({datetime.now().strftime('%H:%M:%S.%f')[:-3]})", flush=True)
            except Exception as pred_err:
                print(f"[PREDICT-ERR] Camera {self.cam_id} model {m_name}: {pred_err}", flush=True)
                continue

            for r in results:
                if r.boxes:
                    for b_idx, b in enumerate(r.boxes):
                        cls_id   = int(b.cls[0].item())
                        cls      = r.names.get(cls_id, str(cls_id)) if hasattr(r, 'names') else str(cls_id)
                        conf_val = float(b.conf[0].item())

                        box_raw = b.xyxy[0].cpu().numpy().tolist()
                        rx1, ry1, rx2, ry2 = box_raw

                        # Always record person detections for dual-distance PPE crop-and-zoom
                        if cls.lower() == "person" and conf_val >= 0.25:
                            detected_persons_raw.append((rx1, ry1, rx2, ry2, conf_val))

                        if is_unauth_mod:
                            if cls.lower() != "person":
                                continue
                        else:
                            if filter_classes is not None:
                                if not any(match_class(cls, e) or is_opposite_class(cls, e) for e in filter_classes):
                                    continue

                            if camera_selected_classes:
                                if not any(match_class(cls, sc) or is_opposite_class(cls, sc) for sc in camera_selected_classes):
                                    continue

                        # Per-class confidence gate (hard reject)
                        req_conf = min(0.20, effective_conf) if is_unauth_mod else effective_conf
                        if class_configs and not is_unauth_mod:
                            for cc_name, cc_val in class_configs.items():
                                if (match_class(cls, cc_name) or is_opposite_class(cls, cc_name)) and isinstance(cc_val, dict) and "conf" in cc_val:
                                    req_conf = float(cc_val["conf"])
                                    break
                        if conf_val < req_conf:
                            continue

                        # Scale coordinates from original raw frame space -> output stream resolution
                        x1 = max(0, min(self.width  - 1, rx1 * scale_x))
                        y1 = max(0, min(self.height - 1, ry1 * scale_y))
                        x2 = max(0, min(self.width  - 1, rx2 * scale_x))
                        y2 = max(0, min(self.height - 1, ry2 * scale_y))
                        box_xyxy = [x1, y1, x2, y2]

                        if is_unauth_mod and hasattr(r, 'keypoints') and r.keypoints is not None:
                            try:
                                if len(r.keypoints.data) > b_idx:
                                    kp_raw = r.keypoints.data[b_idx].cpu().numpy()
                                    kp_scaled = []
                                    for pt in kp_raw:
                                        kp_scaled.append([float(pt[0]) * scale_x, float(pt[1]) * scale_y, float(pt[2])])
                                    detected_pose_keypoints.append((box_xyxy, kp_scaled))
                            except Exception as e_kp:
                                pass
                        
                        bw = max(0, x2 - x1)
                        bh = max(0, y2 - y1)
                        is_veh_cls = any(vk in cls.lower() for vk in ("car", "truck", "bus", "van", "pickup", "tank truck", "vehicle", "bike"))
                        if is_veh_cls:
                            # -- Filter 1: Min area / box size for 640x360 stream canvas --
                            if bw < 14 or bh < 12 or (bw * bh) < 180:
                                continue
                            # -- Filter 2: Roadside reflector poles on image borders --
                            if (bh / max(1, bw)) > 3.2 and (x1 < self.width * 0.10 or x2 > self.width * 0.90):
                                continue
                            # -- Filter 3: Sky filter --
                            if y2 < self.height * 0.16:
                                continue
                        else:
                            is_ppe = any(k in cls.lower() for k in ("hat", "helmet", "mask", "vest", "glove", "boot", "shoe", "goggle", "belt", "harness", "lamp", "tape"))
                            min_dim = 3 if is_ppe else 8
                            if bw < min_dim or bh < min_dim:
                                continue
                            
                        cx = (x1 + x2) / 2.0
                        cy = (y1 + y2) / 2.0

                        if self.roi_polygon and len(self.roi_polygon) == 2:
                            try:
                                roi_x1 = int(min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                                roi_y1 = int(min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                                roi_x2 = int(max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                                roi_y2 = int(max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                                in_roi = (roi_x1 <= cx <= roi_x2 and roi_y1 <= cy <= roi_y2)
                                if not in_roi:
                                    ix1, iy1 = max(x1, roi_x1), max(y1, roi_y1)
                                    ix2, iy2 = min(x2, roi_x2), min(y2, roi_y2)
                                    if ix2 > ix1 and iy2 > iy1:
                                        inter_area = (ix2 - ix1) * (iy2 - iy1)
                                        box_area = max(1.0, (x2 - x1) * (y2 - y1))
                                        if (inter_area / box_area) >= 0.35:
                                            in_roi = True
                                if not in_roi:
                                    continue
                            except Exception:
                                pass

                        color_val = get_dynamic_class_color(cls)
                        raw_boxes.append((box_xyxy, color_val, conf_val, cls))

        # -- Stage 2: Dual Near & Far Distance Detection (Adaptive Person Crop-and-Zoom) --
        # For standard cameras at 10-15m, small PPE items (hardhat, mask, gloves, belt)
        # downscale to 2-5 pixels on full frame inference.
        # We extract high-resolution person crops directly from the native RTSP frame
        # and evaluate all active PPE companion models (Hansung, ppe_new, nik_ppe_best).
        # This provides crystal-clear detection at both 1-4m (close) and 10-15m (far).
        if detected_persons_raw and self.models:
            ppe_companion_models = []
            for midx, p_mod in enumerate(self.models):
                if p_mod is None:
                    continue
                p_pth = self.model_paths[midx] if (isinstance(self.model_paths, list) and midx < len(self.model_paths)) else str(self.model_paths)
                p_nm = os.path.basename(p_pth)
                if any(k in p_nm.lower() for k in ("hansung", "ppe", "nik", "gear")):
                    ppe_companion_models.append((p_mod, p_nm))

            if ppe_companion_models:
                distant_candidates = [
                    p for p in detected_persons_raw
                    if 30 <= (p[3] - p[1]) < (orig_h * 0.45) and (p[2] - p[0]) >= 15
                ]
                unique_distant_persons = []
                for p in distant_candidates:
                    p_box = [p[0], p[1], p[2], p[3]]
                    dup = False
                    for u in unique_distant_persons:
                        u_box = [u[0], u[1], u[2], u[3]]
                        iou, _ = DetectorWorker._box_iou_and_io_min(p_box, u_box)
                        if iou > 0.40:
                            dup = True
                            break
                    if not dup:
                        unique_distant_persons.append(p)

                unique_distant_persons = sorted(unique_distant_persons, key=lambda p: (p[2] - p[0]) * (p[3] - p[1]), reverse=True)[:3]

                for d_rx1, d_ry1, d_rx2, d_ry2, d_conf in unique_distant_persons:
                    pw = d_rx2 - d_rx1
                    ph = d_ry2 - d_ry1
                    pad_x = pw * 0.12
                    pad_y = ph * 0.12
                    cx1 = max(0, int(d_rx1 - pad_x))
                    cy1 = max(0, int(d_ry1 - pad_y))
                    cx2 = min(orig_w, int(d_rx2 + pad_x))
                    cy2 = min(orig_h, int(d_ry2 + pad_y))
                    crop_h = cy2 - cy1
                    crop_w = cx2 - cx1
                    if crop_h < 30 or crop_w < 15:
                        continue

                    person_crop = f[cy1:cy2, cx1:cx2]

                    for p_model, p_name in ppe_companion_models:
                        p_cfg = get_config_for_model(self.model_configs, p_name)
                        # Inherit hansung config if companion model has no explicit config
                        if (not p_cfg or not isinstance(p_cfg, dict)) and any(k in p_name.lower() for k in ("ppe_new", "nik")):
                            p_cfg = get_config_for_model(self.model_configs, "hansung_ppe_violations.pt")
                        p_class_configs = p_cfg.get("class_configs", {}) if isinstance(p_cfg, dict) else {}
                        p_enabled = p_cfg.get("enabled_classes") if isinstance(p_cfg, dict) else None
                        if p_enabled is not None:
                            p_filters = list(p_enabled)
                        elif camera_selected_classes:
                            p_filters = list(camera_selected_classes)
                        else:
                            p_filters = None

                        if p_filters is not None and len(p_filters) == 0:
                            continue

                        p_conf_val = float(p_cfg.get("conf", self.conf)) if isinstance(p_cfg, dict) else float(self.conf)
                        min_crop_conf = p_conf_val
                        if p_class_configs and isinstance(p_class_configs, dict):
                            for cc in p_class_configs.values():
                                if isinstance(cc, dict) and "conf" in cc:
                                    min_crop_conf = min(min_crop_conf, float(cc["conf"]))

                        crop_predict_kwargs = {
                            "source": person_crop,
                            "conf": max(PREDICT_CONF_FLOOR, min_crop_conf),
                            "iou": self.iou,
                            "imgsz": 320,
                            "verbose": False
                        }

                        try:
                            if 'torch' in globals() and hasattr(torch, 'inference_mode'):
                                with torch.inference_mode():
                                    c_results = p_model.predict(**crop_predict_kwargs)
                            else:
                                c_results = p_model.predict(**crop_predict_kwargs)

                            for cr in c_results:
                                if not cr.boxes:
                                    continue
                                for cb in cr.boxes:
                                    c_cls_id = int(cb.cls[0].item())
                                    c_cls = cr.names.get(c_cls_id, str(c_cls_id)) if hasattr(cr, 'names') else str(c_cls_id)
                                    c_conf = float(cb.conf[0].item())

                                    if c_cls.lower() == "person":
                                        continue

                                    if p_filters is not None:
                                        if not any(match_class(c_cls, e) or is_opposite_class(c_cls, e) for e in p_filters):
                                            continue
                                    if camera_selected_classes:
                                        if not any(match_class(c_cls, sc) or is_opposite_class(c_cls, sc) for sc in camera_selected_classes):
                                            continue

                                    req_crop_conf = p_conf_val
                                    if p_class_configs:
                                        for cc_name, cc_val in p_class_configs.items():
                                            if (match_class(c_cls, cc_name) or is_opposite_class(c_cls, cc_name)) and isinstance(cc_val, dict) and "conf" in cc_val:
                                                req_crop_conf = float(cc_val["conf"])
                                                break
                                    if c_conf < req_crop_conf:
                                        continue

                                    c_raw = cb.xyxy[0].cpu().numpy().tolist()
                                    glob_rx1 = cx1 + c_raw[0]
                                    glob_ry1 = cy1 + c_raw[1]
                                    glob_rx2 = cx1 + c_raw[2]
                                    glob_ry2 = cy1 + c_raw[3]

                                    gx1 = max(0, min(self.width - 1, glob_rx1 * scale_x))
                                    gy1 = max(0, min(self.height - 1, glob_ry1 * scale_y))
                                    gx2 = max(0, min(self.width - 1, glob_rx2 * scale_x))
                                    gy2 = max(0, min(self.height - 1, glob_ry2 * scale_y))

                                    gbw = max(0, gx2 - gx1)
                                    gbh = max(0, gy2 - gy1)
                                    if gbw < 3 or gbh < 3:
                                        continue

                                    if self.roi_polygon and len(self.roi_polygon) == 2:
                                        try:
                                            gcx = (gx1 + gx2) / 2.0
                                            gcy = (gy1 + gy2) / 2.0
                                            roi_x1 = int(min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                                            roi_y1 = int(min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                                            roi_x2 = int(max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                                            roi_y2 = int(max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                                            in_roi = (roi_x1 <= gcx <= roi_x2 and roi_y1 <= gcy <= roi_y2)
                                            if not in_roi:
                                                continue
                                        except Exception:
                                            pass

                                    g_col = get_dynamic_class_color(c_cls)
                                    raw_boxes.append(([gx1, gy1, gx2, gy2], g_col, c_conf, c_cls))
                        except Exception as _ce:
                            pass

        # -- Multi-Model NMS ---------------------------------------------------
        kept_items = self._apply_nms(raw_boxes)

        # -- ByteTrack Tracking Engine (Kalman + Occlusion Recovery + Ground Physics) --
        now_t = time.time()
        if not hasattr(self, '_ema_tracks'):
            self._ema_tracks = {}
            self._ema_next_id = 1

        vehicle_keywords = ("truck", "car", "pickup", "bike", "tank", "vehicle", "bus", "motorcycle", "van")

        if getattr(self, 'byte_tracker', None) is not None:
            byte_tracks = self.byte_tracker.update(kept_items, detected_pose_keypoints, img_w=float(self.width))
            active_byte_ids = set()
            for tid, trk_data in byte_tracks.items():
                active_byte_ids.add(tid)
                if tid in self._ema_tracks:
                    # Preserve continuous timing, behavioral tracking timestamps, and speed records
                    for state_key in (
                        'time_a', 'time_b', 'speed_kmh', 'disp_speed', 'is_over_speed',
                        'first_seen', 'last_alert_time', 'cook_start_t', 'smoke_start_t',
                        'fight_start_t', 'sit_start_t', 'loiter_start_t', 'park_start_t'
                    ):
                        if state_key in self._ema_tracks[tid]:
                            trk_data[state_key] = self._ema_tracks[tid][state_key]
                    self._ema_tracks[tid].update(trk_data)
                else:
                    self._ema_tracks[tid] = trk_data

            # Expire tracks no longer active in ByteTrack
            for tid in list(self._ema_tracks.keys()):
                if tid not in active_byte_ids:
                    del self._ema_tracks[tid]
                    if hasattr(self, '_last_side_a') and tid in self._last_side_a:
                        del self._last_side_a[tid]
                    if hasattr(self, '_last_side_b') and tid in self._last_side_b:
                        del self._last_side_b[tid]
        else:
            # Fallback Centroid & Ground Tracking Engine
            matched_ids = set()

            for b_xyxy, color_val, conf_val, cls_name in kept_items:
                bx1, by1, bx2, by2 = b_xyxy
                b_cx = (bx1 + bx2) / 2.0
                b_cy = (by1 + by2) / 2.0
                b_w  = max(1.0, bx2 - bx1)
                b_h  = max(1.0, by2 - by1)
                is_veh = any(vk in cls_name.lower() for vk in vehicle_keywords)

                best_id, best_score = None, 0.0
                for tid, trk in self._ema_tracks.items():
                    if tid in matched_ids:
                        continue
                    trk_cls_lower = str(trk.get('cls', '')).lower()
                    trk_is_veh = any(vk in trk_cls_lower for vk in vehicle_keywords)

                    if is_veh:
                        if not trk_is_veh:
                            continue
                        # Vehicle ground contact centroid matching (bottom-center tyre contact)
                        tx1, ty1, tx2, ty2 = trk['box']
                        t_cx = (tx1 + tx2) / 2.0
                        t_cy = ty2  # Tyre contact
                        curr_cx = b_cx
                        curr_cy = by2
                        dist = math.hypot(curr_cx - t_cx, curr_cy - t_cy)
                        max_veh_dist = min(110.0, max(60.0, float(self.width) * 0.16))
                        if dist <= max_veh_dist:  # Tight distance threshold prevents swapping between different cars
                            score = max(0.01, 1.0 - (dist / max_veh_dist))
                            if score > best_score:
                                best_score = score
                                best_id = tid
                    else:
                        if not match_class(trk['cls'], cls_name):
                            continue
                        iou_v, io_min_v = DetectorWorker._box_iou_and_io_min(trk['box'], b_xyxy)
                        tx1, ty1, tx2, ty2 = trk['box']
                        t_cx = (tx1 + tx2) / 2.0
                        t_cy = (ty1 + ty2) / 2.0
                        dist_x = abs(b_cx - t_cx) / max(b_w, (tx2 - tx1), 1.0)
                        dist_y = abs(b_cy - t_cy) / max(b_h, (ty2 - ty1), 1.0)
                        dist_score = max(0.0, 1.0 - (dist_x**2 + dist_y**2) ** 0.5)
                        score = max(iou_v * 1.2, io_min_v * 0.8, dist_score * 0.5)
                        if score > best_score and score >= 0.10:
                            best_score = score
                            best_id = tid

                if best_id is not None:
                    old_b = self._ema_tracks[best_id]['box']
                    a = 0.35 if is_veh else BOX_EMA_ALPHA
                    smoothed = [
                        old_b[0] * a + b_xyxy[0] * (1 - a),
                        old_b[1] * a + b_xyxy[1] * (1 - a),
                        old_b[2] * a + b_xyxy[2] * (1 - a),
                        old_b[3] * a + b_xyxy[3] * (1 - a),
                    ]
                    
                    hist = self._ema_tracks[best_id].get('history', [])
                    hist.append(1)
                    if len(hist) > 5:
                        hist = hist[-5:]

                    assigned_cls = cls_name
                    if 'truck' in str(self._ema_tracks[best_id]['cls']).lower() and 'car' in cls_name.lower():
                        assigned_cls = self._ema_tracks[best_id]['cls']

                    pos_hist = self._ema_tracks[best_id].get('pos_history', [])
                    pos_hist.append(((smoothed[0] + smoothed[2]) / 2.0, smoothed[3], now_t))
                    if len(pos_hist) > 25:
                        pos_hist = pos_hist[-25:]

                    # Associate latest pose keypoints if person track
                    matched_kp = None
                    if detected_pose_keypoints and 'person' in str(assigned_cls).lower():
                        best_kp_match = None
                        best_kp_score = 0.0
                        s_cx = (smoothed[0] + smoothed[2]) / 2.0
                        s_cy = (smoothed[1] + smoothed[3]) / 2.0
                        diag = max(1.0, math.hypot(smoothed[2] - smoothed[0], smoothed[3] - smoothed[1]))
                        for p_box, p_kp in detected_pose_keypoints:
                            iou, io_min = DetectorWorker._box_iou_and_io_min(smoothed, p_box)
                            p_cx = (p_box[0] + p_box[2]) / 2.0
                            p_cy = (p_box[1] + p_box[3]) / 2.0
                            c_dist = math.hypot(s_cx - p_cx, s_cy - p_cy)
                            dist_score = max(0.0, 1.0 - c_dist / diag)
                            score = max(iou, io_min * 0.85, dist_score * 0.75)
                            if score > best_kp_score and score >= 0.25:
                                best_kp_score = score
                                best_kp_match = p_kp
                        matched_kp = best_kp_match

                    trk_upd = {
                        'box':         smoothed,
                        'color':       color_val,
                        'conf':        conf_val,
                        'cls':         assigned_cls,
                        'last_seen':   now_t,
                        'hit_count':   self._ema_tracks[best_id].get('hit_count', 0) + 1,
                        'history':     hist,
                        'pos_history': pos_hist,
                    }
                    if matched_kp is not None:
                        trk_upd['keypoints'] = matched_kp
                        trk_upd['last_pose_time'] = now_t
                    elif (now_t - self._ema_tracks[best_id].get('last_pose_time', 0.0)) < 0.6:
                        trk_upd['keypoints'] = self._ema_tracks[best_id].get('keypoints')
                    self._ema_tracks[best_id].update(trk_upd)
                    matched_ids.add(best_id)
                else:
                    new_id = self._ema_next_id
                    self._ema_next_id += 1
                    b_init = list(b_xyxy)

                    matched_kp = None
                    if detected_pose_keypoints and 'person' in str(cls_name).lower():
                        best_kp_match = None
                        best_kp_score = 0.0
                        b_cx = (b_init[0] + b_init[2]) / 2.0
                        b_cy = (b_init[1] + b_init[3]) / 2.0
                        diag = max(1.0, math.hypot(b_init[2] - b_init[0], b_init[3] - b_init[1]))
                        for p_box, p_kp in detected_pose_keypoints:
                            iou, io_min = DetectorWorker._box_iou_and_io_min(b_init, p_box)
                            p_cx = (p_box[0] + p_box[2]) / 2.0
                            p_cy = (p_box[1] + p_box[3]) / 2.0
                            c_dist = math.hypot(b_cx - p_cx, b_cy - p_cy)
                            dist_score = max(0.0, 1.0 - c_dist / diag)
                            score = max(iou, io_min * 0.85, dist_score * 0.75)
                            if score > best_kp_score and score >= 0.25:
                                best_kp_score = score
                                best_kp_match = p_kp
                        matched_kp = best_kp_match

                    self._ema_tracks[new_id] = {
                        'box':             b_init,
                        'color':           color_val,
                        'conf':            conf_val,
                        'cls':             cls_name,
                        'last_seen':       now_t,
                        'first_seen':      now_t,
                        'hit_count':       1,
                        'history':         [1],
                        'pos_history':     [((b_init[0] + b_init[2]) / 2.0, b_init[3], now_t)],
                        'time_a':          None,
                        'time_b':          None,
                        'speed_kmh':       None,
                        'disp_speed':      None,
                        'is_over_speed':   False,
                        'last_alert_time': 0.0,
                        'keypoints':       matched_kp,
                        'last_pose_time':  now_t if matched_kp is not None else 0.0,
                    }
                    matched_ids.add(new_id)

            # Record a 0 (miss) in history for active tracks not detected in this cycle
            for tid, trk in self._ema_tracks.items():
                if tid not in matched_ids:
                    hist = trk.get('history', [])
                    hist.append(0)
                    if len(hist) > 5:
                        hist = hist[-5:]
                    trk['history'] = hist

            # Expire stale tracks (1.2s persistence for vehicles so new cars never inherit old timestamps)
            for tid in list(self._ema_tracks.keys()):
                trk_cls = self._ema_tracks[tid].get('cls', '').lower()
                is_veh = any(vk in trk_cls for vk in vehicle_keywords)
                max_age = 1.2 if is_veh else TRACK_MAX_AGE_S
                if now_t - self._ema_tracks[tid].get('last_seen', 0.0) > max_age:
                    del self._ema_tracks[tid]
                    if hasattr(self, '_last_side_a') and tid in self._last_side_a:
                        del self._last_side_a[tid]
                    if hasattr(self, '_last_side_b') and tid in self._last_side_b:
                        del self._last_side_b[tid]
                if hasattr(self, '_last_side_a') and tid in self._last_side_a:
                    del self._last_side_a[tid]
                if hasattr(self, '_last_side_b') and tid in self._last_side_b:
                    del self._last_side_b[tid]

        # Check whether this camera is configured as a dedicated speed camera
        active_mods = self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]
        speed_flag = False
        if isinstance(self.model_configs, dict):
            speed_flag = bool(self.model_configs.get("enable_speed") or self.model_configs.get("speed_tracking") or self.model_configs.get("is_speed"))
        is_speed_camera = (getattr(self, 'is_speed_worker', False) or str(self.cam_id) == "speed" or speed_flag or any("speed" in str(mp).lower() for mp in active_mods))

        # -- Vehicle Speed Estimation & Dual-Line Timing (ONLY for dedicated speed cameras) --
        speed_limit = float(getattr(self, 'speed_limit_kmh', 10.0))
        dist_m = float(getattr(self, 'road_distance_meters', 20.0))
        la_y = int(self.height * getattr(self, 'line_a_ratio', 0.40))
        lb_y = int(self.height * getattr(self, 'line_b_ratio', 0.75))

        if is_speed_camera:
            for tid, trk in self._ema_tracks.items():
                cls_lower = str(trk.get('cls', '')).lower()
                if any(vk in cls_lower for vk in vehicle_keywords):
                    bx = trk['box']
                    cx = (bx[0] + bx[2]) / 2.0
                    cy = bx[3]  # Tyre contact point at bottom edge of bounding box

                    # Reset stale timing if a vehicle took > 10.0s (e.g. stopped or turned around)
                    if trk.get('time_a') is not None and (now_t - trk['time_a'] > 10.0) and trk.get('time_b') is None:
                        trk['time_a'] = None
                    if trk.get('time_b') is not None and (now_t - trk['time_b'] > 10.0) and trk.get('time_a') is None:
                        trk['time_b'] = None

                    # Check Line A (Gate Entry Line)
                    side_a = cy - la_y
                    if not hasattr(self, '_last_side_a'): self._last_side_a = {}
                    if tid in self._last_side_a:
                        if (self._last_side_a[tid] < 0 and side_a >= 0) or (self._last_side_a[tid] > 0 and side_a <= 0):
                            if trk.get('time_a') is None:
                                trk['time_a'] = now_t
                                print(f"[GATE-LINE-A] Vehicle #{tid} ({trk['cls']}) passed Line A at {now_t:.2f}s", flush=True)
                    self._last_side_a[tid] = side_a

                    # Check Line B (Gantry Road Line)
                    side_b = cy - lb_y
                    if not hasattr(self, '_last_side_b'): self._last_side_b = {}
                    if tid in self._last_side_b:
                        if (self._last_side_b[tid] < 0 and side_b >= 0) or (self._last_side_b[tid] > 0 and side_b <= 0):
                            if trk.get('time_b') is None:
                                trk['time_b'] = now_t
                                print(f"[GANTRY-LINE-B] Vehicle #{tid} ({trk['cls']}) passed Line B at {now_t:.2f}s", flush=True)
                                if not hasattr(self, 'vehicle_counts'): self.vehicle_counts = Counter()
                                if not hasattr(self, 'counted_ids'): self.counted_ids = set()
                                if tid not in self.counted_ids:
                                    self.vehicle_counts[trk['cls']] += 1
                                    self.counted_ids.add(tid)
                    self._last_side_b[tid] = side_b

                    # Continuous displacement speed estimation
                    pos_hist = trk.get('pos_history', [])
                    if len(pos_hist) >= 2:
                        curr_cx, curr_cy, curr_t = pos_hist[-1]
                        ref_cx, ref_cy, ref_t = pos_hist[0]
                        for h_cx, h_cy, h_t in reversed(pos_hist[:-1]):
                            if 0.10 <= (curr_t - h_t) <= 0.85:
                                ref_cx, ref_cy, ref_t = h_cx, h_cy, h_t
                                break
                        dt_h = max(0.001, curr_t - ref_t)
                        dy_h = curr_cy - ref_cy
                        if dt_h >= 0.08 and dy_h > 2:
                            dist_px = max(20, lb_y - la_y)
                            meters_per_px = dist_m / float(dist_px)
                            calc_v = (dy_h * meters_per_px / dt_h) * 3.6
                            if 3.0 <= calc_v <= 150.0:
                                old_disp = trk.get('disp_speed')
                                if old_disp is not None:
                                    trk['disp_speed'] = old_disp * 0.35 + calc_v * 0.65
                                else:
                                    trk['disp_speed'] = calc_v

                    # Compute Speed when both lines crossed (Gate -> Gantry or Gantry -> Gate)
                    if trk.get('time_b') is not None and trk.get('speed_kmh') is None:
                        if trk.get('time_a') is not None:
                            delta_t = abs(trk['time_b'] - trk['time_a'])
                            if 0.1 <= delta_t <= 10.0:
                                trk['speed_kmh'] = (dist_m / delta_t) * 3.6
                        elif trk.get('disp_speed') is not None:
                            trk['speed_kmh'] = trk['disp_speed']

                        v = trk.get('speed_kmh')
                        if v is not None:
                            if not hasattr(self, 'speed_records'): self.speed_records = []
                            self.speed_records.append({
                                "vehicle_id": tid,
                                "cls": trk['cls'],
                                "speed_kmh": round(v, 1),
                                "is_over_speed": bool(v > speed_limit),
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            })
                            if len(self.speed_records) > 200:
                                self.speed_records = self.speed_records[-200:]

                            if v > speed_limit:
                                trk['is_over_speed'] = True
                                trk['color'] = (0, 0, 255)  # Pure Red Alert
                                if not hasattr(self, 'overspeed_count'): self.overspeed_count = 0
                                self.overspeed_count += 1
                                print(f"[ALERT] [SPEED-VIOLATION] Camera {self.cam_id}: Vehicle #{tid} ({trk['cls']}) {v:.1f} km/h (Limit: {speed_limit:.0f} km/h)!", flush=True)
                                trk['should_alert_overspeed'] = True

                                # Save alert snapshot
                                try:
                                    alerts_dir = os.path.join(BASE_DIR, "alerts")
                                    os.makedirs(alerts_dir, exist_ok=True)
                                    snap_name = f"speed_violation_cam{self.cam_id}_id{tid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{int(v)}kmh.jpg"
                                    snap_path = os.path.join(alerts_dir, snap_name)
                                    cv2.imwrite(snap_path, pf if 'pf' in locals() else f)
                                    print(f"[ALERT-SAVED] Saved speed violation snapshot: {snap_path}", flush=True)
                                except Exception as e_snap:
                                    print(f"[ALERT-SNAP-ERR] Failed to save speed snapshot: {e_snap}", flush=True)
                            else:
                                trk['color'] = (0, 255, 100)  # Green Normal
                                print(f"[SPEED-NORMAL] Camera {self.cam_id}: Vehicle #{tid} ({trk['cls']}) {v:.1f} km/h", flush=True)

        # -- Pre-classify Unauthorized Activity for Person Tracks in Parking Area / Terminal --
        is_unauth_assigned = any("unauthorized" in str(p).lower() for p in (self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]))
        unauth_classes = None
        if is_unauth_assigned and not is_speed_camera:
            unauth_cfg = get_config_for_model(self.model_configs, "unauthorized_activity.pt") or get_config_for_model(self.model_configs, "unauthorized_activity") or {}
            unauth_classes = unauth_cfg.get("enabled_classes") if isinstance(unauth_cfg, dict) else None
            if unauth_classes is None and camera_selected_classes:
                sub = [c for c in camera_selected_classes if any(k in c.lower() for k in ("cook", "crowd", "lying", "smok", "fight", "sitting", "loiter"))]
                if sub:
                    unauth_classes = sub
            if unauth_classes is None:
                unauth_classes = ["cooking", "crowd", "smoking_gesture", "fighting_posture", "lying_down"]

            has_roi = bool(self.roi_polygon and len(self.roi_polygon) == 2)
            if has_roi:
                r_min_x = min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * self.width
                r_max_x = max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * self.width
                r_min_y = min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * self.height
                r_max_y = max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * self.height

            all_person_records = [
                {
                    'box': t['box'],
                    'keypoints': t.get('keypoints'),
                    'behavior': t.get('unauth_activity')
                }
                for t in self._ema_tracks.values()
                if 'person' in str(t.get('cls', '')).lower()
            ]

            has_fire_smoke = any(
                any(fk in str(t.get('cls', '')).lower() for fk in ("fire", "smoke", "gas"))
                for t in self._ema_tracks.values()
            )

            for tid, trk in self._ema_tracks.items():
                if 'person' in str(trk.get('cls', '')).lower():
                    bx = trk.get('box', [])
                    if len(bx) != 4:
                        continue
                    cx = (bx[0] + bx[2]) / 2.0
                    cy = (bx[1] + bx[3]) / 2.0
                    gx, gy = cx, bx[3]
                    in_zone = True
                    if has_roi:
                        in_zone = ((r_min_x <= cx <= r_max_x and r_min_y <= cy <= r_max_y) or
                                   (r_min_x <= gx <= r_max_x and r_min_y <= gy <= r_max_y))

                    if in_zone and unauth_classes:
                        dwell_time = now_t - trk.get('first_seen', now_t)
                        act, act_conf, act_sub = classify_pose_activity(
                            trk, trk.get('keypoints'), bx, dwell_time, unauth_classes, all_person_records, frame_height=self.height, has_fire_smoke=has_fire_smoke
                        )
                        trk['unauth_activity'] = act
                        trk['unauth_conf'] = act_conf
                        trk['unauth_sub'] = act_sub
                    else:
                        trk['unauth_activity'] = None

        # Build display list from live EMA tracks
        display_boxes = []
        for tid, trk in self._ema_tracks.items():
            trk_cls = trk.get('cls', '')
            unauth_act = trk.get('unauth_activity')

            disp_cls = trk_cls
            is_matched = False
            if camera_selected_classes:
                for sc in camera_selected_classes:
                    if match_class(trk_cls, sc):
                        disp_cls = sc
                        is_matched = True
                        break
                    if is_opposite_class(trk_cls, sc):
                        disp_cls = trk_cls
                        is_matched = True
                        break
                    if unauth_act and match_class(unauth_act, sc):
                        disp_cls = unauth_act
                        is_matched = True
                        break

                # If unauthorized activity model is assigned, display the person track
                # (Green compliant if no violation, Red if an unauthorized activity triggered)
                if not is_matched and is_unauth_assigned and 'person' in trk_cls.lower():
                    disp_cls = unauth_act if unauth_act else "person"
                    is_matched = True

                # If intrusion, perimeter, off-hours, or vehicle intrusion models are assigned, keep person and vehicles visible
                is_perim_assigned = any(m in str(p).lower() for p in (self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]) for m in ("intrusion", "non_working", "off_hours", "vehicle_intrusion", "crowd"))
                if not is_matched and is_perim_assigned:
                    if any(vk in trk_cls.lower() for vk in ("person", "truck", "car", "bus", "motorcycle", "vehicle")):
                        is_matched = True

                if not is_matched:
                    continue
            else:
                has_person_mod = any(m in str(p).lower() for p in (self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]) for m in ("intrusion", "unauth", "non_working", "off_hours", "crowd", "ppe", "hansung", "nik"))
                if 'person' in trk_cls.lower() and not has_person_mod:
                    continue
                if unauth_act:
                    disp_cls = unauth_act

            is_veh = any(vk in trk_cls.lower() for vk in vehicle_keywords)
            max_age = 1.2 if is_veh else TRACK_MAX_AGE_S
            age = now_t - trk.get('last_seen', 0.0)
            if age > max_age:
                continue
            cur_cls.add(disp_cls)
            
            conf_pct = trk.get('conf', 0.8)
            bx = trk.get('box', [0, 0, 0, 0])
            by2 = bx[3]
            sub_lbl = None

            if is_speed_camera:
                speed_val = trk.get('speed_kmh') or trk.get('disp_speed')
                box_header = f"{disp_cls.upper()} #{tid} ({conf_pct:.0%})"
                if trk.get('is_over_speed') or (speed_val is not None and speed_val > speed_limit):
                    sub_lbl = f"OVER SPEED: {speed_val:.1f} km/h (LIMIT: {speed_limit:.0f})"
                    box_col = (0, 0, 255)  # Bright Red
                elif speed_val is not None:
                    sub_lbl = f"SPEED: {speed_val:.1f} km/h"
                    box_col = (0, 255, 100)  # Bright Green
                elif is_veh:
                    if by2 >= lb_y:
                        sub_lbl = "PAST LINE B"
                        box_col = (180, 180, 180)
                    else:
                        sub_lbl = "APPROACHING LINE A"
                        box_col = (0, 220, 255)
                else:
                    box_col = trk.get('color', (0, 255, 100))
            else:
                if unauth_act and (disp_cls == unauth_act or match_class(disp_cls, unauth_act)):
                    box_header = f"VIOLATION: UNAUTHORIZED ACTIVITY ({unauth_act.replace('_', ' ').upper()}) {trk.get('unauth_conf', conf_pct):.2f}"
                    sub_lbl = f"PARKING AREA: {trk.get('unauth_sub', unauth_act.upper())}"
                    box_col = (20, 20, 237)  # Reliance Bold Alert Red (BGR)
                else:
                    is_neg = is_negative_violation_class(disp_cls)
                    if is_neg:
                        box_header = f"VIOLATION: {disp_cls} {conf_pct:.2f}"
                        box_col = (20, 20, 237)  # Reliance Bold Alert Red (BGR)
                    else:
                        box_header = f"{disp_cls} {conf_pct:.2f}"
                        if str(disp_cls).lower() == 'person':
                            box_col = (0, 255, 0)  # Compliant Green by default for normal person
                        else:
                            box_col = trk.get('color', (0, 255, 100))

            display_boxes.append({
                'box':    trk['box'],
                'label':  box_header,
                'sub':    sub_lbl,
                'is_veh': is_veh,
                'color':  box_col,
                'cls':    disp_cls,
                'conf':   trk.get('conf', conf_pct),
                'trk_id': tid,
            })

        # -- Absence Detection for Safety Equipment and Wheel Chocks (sand_ext_chocks) --
        has_sand_ext = any("sand_ext_chocks" in str(p).lower() for p in (self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]))
        absence_alerts_to_trigger = []
        if has_sand_ext and not is_speed_camera:
            cfg = get_config_for_model(self.model_configs, "sand_ext_chocks.pt")
            sand_enabled = cfg.get("enabled_classes") if (cfg and isinstance(cfg, dict)) else None

            def is_sand_cls_enabled(target_name):
                if not sand_enabled:
                    return any(match_class(target_name, sc) for sc in (camera_selected_classes or [])) or not camera_selected_classes
                t_clean = target_name.lower().replace("-", "").replace("_", "")
                return any(t_clean in str(e).lower().replace("-", "").replace("_", "") for e in sand_enabled)

            # 1. Collect positive detections in this cycle
            has_pos_extinguisher = any("extinguisher" in str(b[3]).lower() for b in kept_items)
            has_pos_sand_bucket = any("sand-bucket" in str(b[3]).lower() or "sand_bucket" in str(b[3]).lower() or "bucket" in str(b[3]).lower() for b in kept_items)
            pos_wheel_chocks = [b[0] for b in kept_items if "choke" in str(b[3]).lower() or "chock" in str(b[3]).lower()]

            if not hasattr(self, '_absence_trackers'):
                self._absence_trackers = {
                    "fire_extinguisher_seen": now_t,
                    "sand_bucket_seen": now_t,
                    "wheel_choke_seen": now_t,
                }
            if "wheel_choke_seen" not in self._absence_trackers:
                self._absence_trackers["wheel_choke_seen"] = now_t

            stream_uptime = now_t - getattr(self, '_start_time', now_t)

            # Absence check: Fire Extinguisher (Fast 2.5s response)
            if is_sand_cls_enabled("no-Fire_Extinguisher"):
                if has_pos_extinguisher:
                    self._absence_trackers["fire_extinguisher_seen"] = now_t
                else:
                    time_missing = now_t - self._absence_trackers.get("fire_extinguisher_seen", now_t)
                    if stream_uptime >= 2.5 and time_missing >= 2.5:
                        if self.roi_polygon and len(self.roi_polygon) == 2:
                            rx1 = int(min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                            ry1 = int(min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                            rx2 = int(max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                            ry2 = int(max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                            abox = [rx1, ry1, rx2, ry2]
                        else:
                            abox = [int(f_w * 0.72), int(f_h * 0.65), int(f_w * 0.95), int(f_h * 0.92)]
                        
                        display_boxes.append({
                            'box':   abox,
                            'label': 'VIOLATION: no-Fire_Extinguisher 0.90',
                            'sub':   'MISSING FIRE EXTINGUISHER',
                            'is_veh': False,
                            'color': (20, 20, 237),
                            'cls':   'no-Fire_Extinguisher',
                            'conf':  0.90,
                        })
                        cur_cls.add('no-Fire_Extinguisher')
                        
                        if not hasattr(self, 'alert_cooldowns'): self.alert_cooldowns = {}
                        if check_and_record_alert_cooldown(self.cam_id, 'no-Fire_Extinguisher', 30.0):
                            self.alert_cooldowns['no-Fire_Extinguisher'] = now_t
                            absence_alerts_to_trigger.append('no-Fire_Extinguisher')

            # Absence check: Sand Bucket (Fast 2.5s response)
            if is_sand_cls_enabled("no-Sand-Bucket"):
                if has_pos_sand_bucket:
                    self._absence_trackers["sand_bucket_seen"] = now_t
                else:
                    time_missing = now_t - self._absence_trackers.get("sand_bucket_seen", now_t)
                    if stream_uptime >= 2.5 and time_missing >= 2.5:
                        if self.roi_polygon and len(self.roi_polygon) == 2:
                            rx1 = int(min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                            ry1 = int(min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                            rx2 = int(max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                            ry2 = int(max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                            abox = [rx1, ry1, rx2, ry2]
                        else:
                            abox = [int(f_w * 0.72), int(f_h * 0.38), int(f_w * 0.95), int(f_h * 0.62)]

                        display_boxes.append({
                            'box':   abox,
                            'label': 'VIOLATION: no-Sand-Bucket 0.90',
                            'sub':   'MISSING SAND BUCKET',
                            'is_veh': False,
                            'color': (20, 20, 237),
                            'cls':   'no-Sand-Bucket',
                            'conf':  0.90,
                        })
                        cur_cls.add('no-Sand-Bucket')
                        
                        if not hasattr(self, 'alert_cooldowns'): self.alert_cooldowns = {}
                        if check_and_record_alert_cooldown(self.cam_id, 'no-Sand-Bucket', 30.0):
                            self.alert_cooldowns['no-Sand-Bucket'] = now_t
                            absence_alerts_to_trigger.append('no-Sand-Bucket')

            # Absence check: Wheel Choke (Standalone Equipment Bay / ROI check + stationary vehicle check)
            has_pos_wheel_choke = len(pos_wheel_chocks) > 0
            if is_sand_cls_enabled("no-wheel-choke"):
                if has_pos_wheel_choke:
                    self._absence_trackers["wheel_choke_seen"] = now_t
                else:
                    time_missing = now_t - self._absence_trackers.get("wheel_choke_seen", now_t)
                    if stream_uptime >= 2.5 and time_missing >= 2.5:
                        if self.roi_polygon and len(self.roi_polygon) == 2:
                            rx1 = int(min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                            ry1 = int(min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                            rx2 = int(max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                            ry2 = int(max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                            abox = [rx1, ry1, rx2, ry2]
                        else:
                            abox = [int(f_w * 0.72), int(f_h * 0.10), int(f_w * 0.95), int(f_h * 0.35)]

                        display_boxes.append({
                            'box':   abox,
                            'label': 'VIOLATION: no-wheel-choke 0.90',
                            'sub':   'MISSING WHEEL CHOCK',
                            'is_veh': False,
                            'color': (20, 20, 237),
                            'cls':   'no-wheel-choke',
                            'conf':  0.90,
                        })
                        cur_cls.add('no-wheel-choke')

                        if not hasattr(self, 'alert_cooldowns'): self.alert_cooldowns = {}
                        if check_and_record_alert_cooldown(self.cam_id, 'no-wheel-choke', 30.0):
                            self.alert_cooldowns['no-wheel-choke'] = now_t
                            absence_alerts_to_trigger.append('no-wheel-choke')

                # Additional check: stationary vehicles / trucks unchocked
                for tid, trk in list(self._ema_tracks.items()):
                    trk_cls = str(trk.get('cls', '')).lower()
                    if not any(vk in trk_cls for vk in vehicle_keywords):
                        continue
                    v_first_seen = trk.get('first_seen', now_t)
                    stationary_dur = now_t - v_first_seen
                    
                    vx1, vy1, vx2, vy2 = trk['box']
                    vw = max(1.0, vx2 - vx1)
                    vh = max(1.0, vy2 - vy1)
                    wheel_zone = [vx1 - vw*0.10, vy2 - vh*0.35, vx2 + vw*0.10, vy2 + vh*0.15]
                    
                    has_choke = False
                    for cbox in pos_wheel_chocks:
                        cx1, cy1, cx2, cy2 = cbox
                        ix1, iy1 = max(cx1, wheel_zone[0]), max(cy1, wheel_zone[1])
                        ix2, iy2 = min(cx2, wheel_zone[2]), min(cy2, wheel_zone[3])
                        if ix2 > ix1 and iy2 > iy1:
                            has_choke = True
                            break
                    
                    if not has_choke and stationary_dur >= 3.0:
                        choke_box = [
                            int(vx1 + vw * 0.15),
                            int(vy2 - max(16.0, vh * 0.26)),
                            int(vx2 - vw * 0.15),
                            int(vy2)
                        ]
                        display_boxes.append({
                            'box':   choke_box,
                            'label': 'VIOLATION: no-wheel-choke 0.88',
                            'sub':   f'VEHICLE #{tid} UNCHOCKED',
                            'is_veh': False,
                            'color': (20, 20, 237),
                            'cls':   'no-wheel-choke',
                            'conf':  0.88,
                        })
                        cur_cls.add('no-wheel-choke')

                        if not hasattr(self, 'alert_cooldowns'): self.alert_cooldowns = {}
                        cd_key = f'no-wheel-choke-{tid}'
                        if check_and_record_alert_cooldown(self.cam_id, cd_key, 30.0) and check_and_record_alert_cooldown(self.cam_id, 'no-wheel-choke', 30.0):
                            self.alert_cooldowns[cd_key] = now_t
                            self.alert_cooldowns['no-wheel-choke'] = now_t
                            absence_alerts_to_trigger.append('no-wheel-choke')

        # -- Absence Detection for Fire Hose / Reel Station (150_firehose_best) --
        has_firehose_mod = any("150_firehose_best" in str(p).lower() for p in (self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]))
        if has_firehose_mod and not is_speed_camera:
            cfg = get_config_for_model(self.model_configs, "150_firehose_best.pt")
            hose_enabled = cfg.get("enabled_classes") if (cfg and isinstance(cfg, dict)) else None

            def is_hose_cls_enabled(target_name):
                if not hose_enabled:
                    return any(match_class(target_name, sc) for sc in (camera_selected_classes or [])) or not camera_selected_classes
                t_clean = target_name.lower().replace("-", "").replace("_", "")
                return any(t_clean in str(e).lower().replace("-", "").replace("_", "") for e in hose_enabled)

            has_pos_firehose = any("hose" in str(b[3]).lower() for b in kept_items)

            if not hasattr(self, '_absence_trackers'):
                self._absence_trackers = {}
            if "firehose_seen" not in self._absence_trackers:
                self._absence_trackers["firehose_seen"] = now_t

            stream_uptime = now_t - getattr(self, '_start_time', now_t)

            if is_hose_cls_enabled("no-fire_hose") or is_hose_cls_enabled("no-firehose") or is_hose_cls_enabled("no-firehose_box"):
                if has_pos_firehose:
                    self._absence_trackers["firehose_seen"] = now_t
                else:
                    time_missing = now_t - self._absence_trackers.get("firehose_seen", now_t)
                    if stream_uptime >= 2.5 and time_missing >= 2.5:
                        if self.roi_polygon and len(self.roi_polygon) == 2:
                            rx1 = int(min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                            ry1 = int(min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                            rx2 = int(max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                            ry2 = int(max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                            abox = [rx1, ry1, rx2, ry2]
                        else:
                            abox = [int(f_w * 0.72), int(f_h * 0.10), int(f_w * 0.95), int(f_h * 0.35)]

                        display_boxes.append({
                            'box':   abox,
                            'label': 'VIOLATION: no-fire_hose 0.90',
                            'sub':   'MISSING FIRE HOSE / REEL',
                            'is_veh': False,
                            'color': (20, 20, 237),
                            'cls':   'no-fire_hose',
                            'conf':  0.90,
                        })
                        cur_cls.add('no-fire_hose')

                        if not hasattr(self, 'alert_cooldowns'): self.alert_cooldowns = {}
                        if check_and_record_alert_cooldown(self.cam_id, 'no-fire_hose', 30.0):
                            self.alert_cooldowns['no-fire_hose'] = now_t
                            absence_alerts_to_trigger.append('no-fire_hose')

        # -- Absence Detection for Spark Arrestor Compliance (spark_arrestor) --
        has_spark_mod = any("spark_arrest" in str(p).lower() for p in (self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]))
        if has_spark_mod and not is_speed_camera:
            cfg = get_config_for_model(self.model_configs, "spark_arrestor.pt") or get_config_for_model(self.model_configs, "spark_arrested.pt")
            spark_enabled = cfg.get("enabled_classes") if (cfg and isinstance(cfg, dict)) else None

            def is_spark_cls_enabled(target_name):
                if not spark_enabled:
                    return any(match_class(target_name, sc) for sc in (camera_selected_classes or [])) or not camera_selected_classes
                t_clean = target_name.lower().replace("-", "").replace("_", "")
                return any(t_clean in str(e).lower().replace("-", "").replace("_", "") for e in spark_enabled)

            has_pos_spark = any("spark" in str(b[3]).lower() for b in kept_items)

            if not hasattr(self, '_absence_trackers'):
                self._absence_trackers = {}
            if "spark_arrestor_seen" not in self._absence_trackers:
                self._absence_trackers["spark_arrestor_seen"] = now_t

            stream_uptime = now_t - getattr(self, '_start_time', now_t)

            if is_spark_cls_enabled("no-Spark_Arrestor") or is_spark_cls_enabled("no-spark_arrestor"):
                if has_pos_spark:
                    self._absence_trackers["spark_arrestor_seen"] = now_t
                else:
                    time_missing = now_t - self._absence_trackers.get("spark_arrestor_seen", now_t)
                    if stream_uptime >= 2.5 and time_missing >= 2.5:
                        if self.roi_polygon and len(self.roi_polygon) == 2:
                            rx1 = int(min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                            ry1 = int(min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                            rx2 = int(max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                            ry2 = int(max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                            abox = [rx1, ry1, rx2, ry2]
                        else:
                            abox = [int(f_w * 0.05), int(f_h * 0.65), int(f_w * 0.35), int(f_h * 0.92)]

                        display_boxes.append({
                            'box':   abox,
                            'label': 'VIOLATION: no-Spark_Arrestor 0.90',
                            'sub':   'MISSING SPARK ARRESTOR ON VEHICLE',
                            'is_veh': False,
                            'color': (20, 20, 237),
                            'cls':   'no-Spark_Arrestor',
                            'conf':  0.90,
                        })
                        cur_cls.add('no-Spark_Arrestor')

                        if not hasattr(self, 'alert_cooldowns'): self.alert_cooldowns = {}
                        if check_and_record_alert_cooldown(self.cam_id, 'no-Spark_Arrestor', 30.0):
                            self.alert_cooldowns['no-Spark_Arrestor'] = now_t
                            absence_alerts_to_trigger.append('no-Spark_Arrestor')

        # -- Absence Detection for Safety / Barricade Tape (best_tape) --
        has_tape_mod = any("best_tape" in str(p).lower() for p in (self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]))
        if has_tape_mod and not is_speed_camera:
            cfg = get_config_for_model(self.model_configs, "best_tape.pt")
            tape_enabled = cfg.get("enabled_classes") if (cfg and isinstance(cfg, dict)) else None

            def is_tape_cls_enabled(target_name):
                if not tape_enabled:
                    return any(match_class(target_name, sc) for sc in (camera_selected_classes or [])) or not camera_selected_classes
                t_clean = target_name.lower().replace("-", "").replace("_", "")
                return any(t_clean in str(e).lower().replace("-", "").replace("_", "") for e in tape_enabled)

            has_pos_tape = any("tape" in str(b[3]).lower() for b in kept_items)

            if not hasattr(self, '_absence_trackers'):
                self._absence_trackers = {}
            if "tape_seen" not in self._absence_trackers:
                self._absence_trackers["tape_seen"] = now_t

            stream_uptime = now_t - getattr(self, '_start_time', now_t)

            if is_tape_cls_enabled("no-tape") or is_tape_cls_enabled("no-reflective_tape") or is_tape_cls_enabled("no-traffic_tape"):
                if has_pos_tape:
                    self._absence_trackers["tape_seen"] = now_t
                else:
                    time_missing = now_t - self._absence_trackers.get("tape_seen", now_t)
                    if stream_uptime >= 2.5 and time_missing >= 2.5:
                        if self.roi_polygon and len(self.roi_polygon) == 2:
                            rx1 = int(min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                            ry1 = int(min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                            rx2 = int(max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                            ry2 = int(max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                            abox = [rx1, ry1, rx2, ry2]
                        else:
                            abox = [int(f_w * 0.05), int(f_h * 0.08), int(f_w * 0.35), int(f_h * 0.38)]

                        display_boxes.append({
                            'box':   abox,
                            'label': 'VIOLATION: no-tape 0.90',
                            'sub':   'MISSING SAFETY BARRICADE TAPE',
                            'is_veh': False,
                            'color': (20, 20, 237),
                            'cls':   'no-tape',
                            'conf':  0.90,
                        })
                        cur_cls.add('no-tape')

                        if not hasattr(self, 'alert_cooldowns'): self.alert_cooldowns = {}
                        if check_and_record_alert_cooldown(self.cam_id, 'no-tape', 30.0):
                            self.alert_cooldowns['no-tape'] = now_t
                            absence_alerts_to_trigger.append('no-tape')

        with self._box_lock:
            self._tracked_boxes = display_boxes

        if not getattr(self, '_first_box_logged', False) and display_boxes:
            self._first_box_logged = True
            t_start  = getattr(self, '_start_time', None) or now_t
            t_active = getattr(self, '_models_active_time', None) or t_start
            delay_from_start_ms  = int((now_t - t_start)  * 1000)
            delay_from_active_ms = int((now_t - t_active) * 1000)
            detected_labels     = [b['label'] for b in display_boxes]
            all_classes_str     = ", ".join(list(cur_cls)) if cur_cls else "ALL"
            detected_classes_str = ", ".join(detected_labels)
            print(f"\n==================================================================", flush=True)
            print(f"[STREAM-TIMING] Camera {self.cam_id} FIRST BOUNDING BOX DETECTED & RENDERED!", flush=True)
            print(f" -> Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}", flush=True)
            print(f" -> Stream Active Classes   : [{all_classes_str}]", flush=True)
            print(f" -> Stream Detected Classes : [{detected_classes_str}]", flush=True)
            print(f" -> Delay from Click 'Start': {delay_from_start_ms}ms ({delay_from_start_ms/1000.0:.2f}s)", flush=True)
            print(f" -> Delay from Model Active : {delay_from_active_ms}ms ({delay_from_active_ms/1000.0:.2f}s)", flush=True)
            print(f"==================================================================\n", flush=True)

        # -- Snapshot for Alerts -----------------------------------------------
        # -- Fully Annotated Snapshot for Alerts (Includes Timing Lines, HUD, and Speed) --
        frame_snapshot = cv2.resize(f, (self.width, self.height), interpolation=cv2.INTER_LINEAR)
        snap_h, snap_w = frame_snapshot.shape[:2]

        if is_speed_camera:
            # Draw timing lines onto alert snapshot
            la_y = int(snap_h * getattr(self, 'line_a_ratio', 0.40))
            lb_y = int(snap_h * getattr(self, 'line_b_ratio', 0.75))
            cv2.line(frame_snapshot, (int(snap_w * 0.05), la_y), (int(snap_w * 0.95), la_y), (255, 200, 0), 2)
            cv2.putText(frame_snapshot, "LINE A (GATE ENTRY)", (int(snap_w * 0.06), la_y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 200, 0), 1, cv2.LINE_AA)
            cv2.line(frame_snapshot, (int(snap_w * 0.05), lb_y), (int(snap_w * 0.95), lb_y), (0, 255, 100), 2)
            cv2.putText(frame_snapshot, "LINE B (GANTRY ROAD)", (int(snap_w * 0.06), lb_y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 255, 100), 1, cv2.LINE_AA)

            # Top-Left Telemetry HUD
            hud_w, hud_h = 240, 78
            hud_x1, hud_y1 = 12, 12
            overlay = frame_snapshot.copy()
            cv2.rectangle(overlay, (hud_x1, hud_y1), (hud_x1 + hud_w, hud_y1 + hud_h), (15, 15, 15), -1)
            cv2.addWeighted(overlay, 0.82, frame_snapshot, 0.18, 0, frame_snapshot)
            cv2.rectangle(frame_snapshot, (hud_x1, hud_y1), (hud_x1 + hud_w, hud_y1 + hud_h), (60, 60, 60), 1)

            v_counts = getattr(self, 'vehicle_counts', {})
            trucks = v_counts.get('truck', 0) + v_counts.get('pickup truck', 0)
            cars = v_counts.get('car', 0)
            tot_cnt = sum(v_counts.values()) if v_counts else 0
            sp_lim = getattr(self, 'speed_limit_kmh', 10.0)
            dist_m = getattr(self, 'road_distance_meters', 20.0)

            cv2.putText(frame_snapshot, "TERMINAL SPEED MONITOR", (hud_x1 + 10, hud_y1 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (0, 255, 170), 2, cv2.LINE_AA)
            cv2.putText(frame_snapshot, f"Speed Limit : {sp_lim:.0f} km/h (Gate-Gantry: {dist_m:.0f}m)", (hud_x1 + 10, hud_y1 + 38), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1, cv2.LINE_AA)
            cv2.putText(frame_snapshot, f"Total Count : {tot_cnt}", (hud_x1 + 10, hud_y1 + 54), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(frame_snapshot, f"  car: {cars}  truck: {trucks}", (hud_x1 + 10, hud_y1 + 70), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (255, 190, 40), 1, cv2.LINE_AA)

        # Draw all bounding boxes with vehicle IDs, types, and speed banners onto snapshot
        DetectorWorker._draw_boxes(frame_snapshot, display_boxes)

        # -- Negative Safety Alert Processing (Exclusively alerts on violations) --
        if not is_speed_camera:
            # Absence alerts for safety equipment & wheel chocks
            for a_cls in absence_alerts_to_trigger:
                self.alert_cooldowns[a_cls] = now_t
                print(f"[ALERT-TRIGGER-VIOLATION] Absence alert generated: cam={self.cam_id}, class={a_cls}", flush=True)
                self._save_alert(a_cls, f, display_boxes=display_boxes)

            for tid, trk in list(self._ema_tracks.items()):
                c = trk.get('cls', '')
                if not c:
                    continue

                disp_c = c
                if camera_selected_classes:
                    for sc in camera_selected_classes:
                        if match_class(c, sc):
                            disp_c = sc
                            break

                # STRICT FILTER: ONLY NEGATIVE / VIOLATION CLASSES GENERATE ALERTS
                if not is_negative_violation_class(disp_c):
                    continue

                if camera_selected_classes and not any(match_class(disp_c, sc) for sc in camera_selected_classes):
                    continue

                # 1. Strict 30-Second Cooldown per class per camera (thread-safe global tracking)
                if not hasattr(self, 'alert_cooldowns'):
                    self.alert_cooldowns = {}
                if not check_and_record_alert_cooldown(self.cam_id, disp_c, 30.0):
                    continue
                self.alert_cooldowns[disp_c] = now_t

                total_hits = trk.get('hit_count', 0)
                if total_hits >= 1:
                    trk['last_alert_time'] = now_t
                    print(f"[ALERT-TRIGGER-VIOLATION] Negative alert generated: cam={self.cam_id}, class={disp_c}, track_id={tid}, hits={total_hits}, conf={trk.get('conf', 0.0):.2f}", flush=True)
                    self._save_alert(disp_c, f, display_boxes=display_boxes)

            # Check Crowd Gathering Alert in ROI if person class monitored
            cfg = get_config_for_model(self.model_configs, "yolov8n.pt") if self.model_configs else {}
            class_cfg = cfg.get("class_configs", {}).get("person", {}) if isinstance(cfg, dict) else {}
            val = class_cfg.get("crowd_threshold")
            if val is None:
                val = cfg.get("crowd_threshold")
            if val is not None:
                try: crowd_thresh = int(val)
                except: crowd_thresh = None
            else:
                crowd_thresh = None

            # Crowd limit: Minimum 1 person in ROI
            if crowd_thresh is not None and crowd_thresh < 1:
                crowd_thresh = 1

            if crowd_thresh and crowd_thresh >= 1:
                # Count only ACTIVE, DEDUPLICATED persons detected in the latest frame cycle
                active_person_boxes = []
                for tid, trk in self._ema_tracks.items():
                    c_trk = trk.get('cls', '')
                    if not match_class(c_trk, 'person'):
                        continue
                    # Ghost track filter: must have been seen in current cycle (<0.40s)
                    if (now_t - trk.get('last_seen', 0.0)) > 0.40:
                        continue
                    bx = trk.get('box', [])
                    if len(bx) != 4:
                        continue

                    # ROI filter if ROI boundary configured
                    if self.roi_polygon and len(self.roi_polygon) == 2:
                        min_x = min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * self.width
                        max_x = max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * self.width
                        min_y = min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * self.height
                        max_y = max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * self.height
                        cx = (bx[0] + bx[2]) / 2.0
                        cy = (bx[1] + bx[3]) / 2.0
                        gx, gy = cx, bx[3]
                        if not ((min_x <= cx <= max_x and min_y <= cy <= max_y) or (min_x <= gx <= max_x and min_y <= gy <= max_y)):
                            continue

                    # Overlap deduplication: prevent double-counting of single person
                    is_duplicate = False
                    for existing_box in active_person_boxes:
                        iou_v, io_min_v = DetectorWorker._box_iou_and_io_min(bx, existing_box)
                        if iou_v >= 0.20 or io_min_v >= 0.35:
                            is_duplicate = True
                            break
                    if not is_duplicate:
                        active_person_boxes.append(bx)

                person_boxes_in_roi = len(active_person_boxes)

                # Crowd gathering alert condition:
                # "Crowd ROI Limit: N" defines the maximum permissible occupancy.
                # When limit is N, occupancy <= N is compliant.
                # Violation triggers ONLY when occupancy strictly exceeds limit (count > crowd_thresh)
                # AND when count >= 2 (a single person alone is NEVER a crowd).
                is_crowd_alert = (person_boxes_in_roi > crowd_thresh) and (person_boxes_in_roi >= 2)

                if is_crowd_alert:
                    for b in display_boxes:
                        if b.get('cls', '').lower() == 'person':
                            b['color'] = (20, 20, 237)
                            b['label'] = f"VIOLATION: person {b.get('conf', 0.8):.2f}"
                    cd_key = f"crowd_alert_cam{self.cam_id}"
                    if not hasattr(self, 'alert_cooldowns'):
                        self.alert_cooldowns = {}
                    if check_and_record_alert_cooldown(self.cam_id, cd_key, 30.0):
                        self.alert_cooldowns[cd_key] = now_t
                        crowd_event_name = f"CROWD ALERT: {person_boxes_in_roi} Persons in ROI"
                        print(f"[ALERT-TRIGGER-CROWD] Crowd Gathering alert triggered: cam={self.cam_id}, count={person_boxes_in_roi} (threshold={crowd_thresh})", flush=True)
                        self._save_alert(crowd_event_name, f, display_boxes=display_boxes)
                else:
                    for b in display_boxes:
                        if b.get('cls', '').lower() == 'person':
                            b['color'] = (0, 255, 0)
                            b['label'] = f"person {b.get('conf', 0.8):.2f}"

            # -- Intrusion Detection in User-Drawn ROI -------------------------
            is_intrusion_assigned = any("intrusion" in str(p).lower() for p in (self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]))
            if is_intrusion_assigned:
                intru_cfg = get_config_for_model(self.model_configs, "intrusion.pt") or get_config_for_model(self.model_configs, "intrusion") or {}
                intru_classes = intru_cfg.get("enabled_classes") if isinstance(intru_cfg, dict) else None
                if intru_classes is None:
                    intru_classes = ["person", "truck"]

                has_roi = bool(self.roi_polygon and len(self.roi_polygon) == 2)
                if has_roi:
                    r_min_x = min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * self.width
                    r_max_x = max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * self.width
                    r_min_y = min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * self.height
                    r_max_y = max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * self.height

                    for tid, trk in list(self._ema_tracks.items()):
                        c_trk = trk.get('cls', '')
                        if not any(match_class(c_trk, ic) for ic in intru_classes):
                            continue
                        if (now_t - trk.get('last_seen', 0.0)) > 0.40:
                            continue
                        bx = trk.get('box', [])
                        if len(bx) != 4:
                            continue
                        cx = (bx[0] + bx[2]) / 2.0
                        cy = (bx[1] + bx[3]) / 2.0
                        gx, gy = cx, bx[3]

                        # Check if object centroid or base touches drawn ROI
                        in_roi = ((r_min_x <= cx <= r_max_x and r_min_y <= cy <= r_max_y) or
                                  (r_min_x <= gx <= r_max_x and r_min_y <= gy <= r_max_y))

                        if in_roi:
                            # Highlight box in Alert Red with INTRUSION label on video
                            matched_b = False
                            for b in display_boxes:
                                ob = b.get('box', [0, 0, 0, 0])
                                if b.get('trk_id') == tid or (abs(ob[0] - bx[0]) < 20 and abs(ob[1] - bx[1]) < 20):
                                    b['color'] = (20, 20, 237)
                                    b['label'] = f"VIOLATION: INTRUSION ({c_trk}) {trk.get('conf', 0.8):.2f}"
                                    b['sub'] = f"PERIMETER / INTRUSION IN ROI"
                                    matched_b = True
                                    break
                            if not matched_b:
                                display_boxes.append({
                                    'box': bx,
                                    'label': f"VIOLATION: INTRUSION ({c_trk}) {trk.get('conf', 0.8):.2f}",
                                    'sub': "PERIMETER / INTRUSION IN ROI",
                                    'color': (20, 20, 237),
                                    'cls': c_trk,
                                    'conf': trk.get('conf', 0.8),
                                    'trk_id': tid
                                })

                            # Trigger alert with global 30-second cooldown
                            cd_key = f"intrusion_{c_trk}"
                            if check_and_record_alert_cooldown(self.cam_id, cd_key, 30.0):
                                alert_msg = f"VIOLATION: Intrusion / Perimeter Intrusion Detected ({c_trk.capitalize()} in ROI)"
                                print(f"[ALERT-TRIGGER-INTRUSION] Camera {self.cam_id}: {alert_msg}", flush=True)
                                self._save_alert(alert_msg, f, display_boxes=display_boxes, violating_box=bx, violating_label=f"VIOLATION: INTRUSION ({c_trk})")

            # -- Intrusion in Non-Working Hours --------------------------------
            is_nwh_assigned = any("non_working" in str(p).lower() or "off_hours" in str(p).lower() for p in (self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]))
            if is_nwh_assigned:
                nwh_cfg = get_config_for_model(self.model_configs, "non_working_hours.pt") or get_config_for_model(self.model_configs, "non_working_hours") or {}
                nwh_classes = nwh_cfg.get("enabled_classes") if isinstance(nwh_cfg, dict) else None
                if nwh_classes is None:
                    nwh_classes = ["person", "truck"]

                start_str = str(nwh_cfg.get("off_hours_start", "18:00") or "18:00") if isinstance(nwh_cfg, dict) else "18:00"
                end_str = str(nwh_cfg.get("off_hours_end", "06:00") or "06:00") if isinstance(nwh_cfg, dict) else "06:00"

                def _to_mins(t_str, def_val):
                    try:
                        s = str(t_str).strip().upper()
                        is_pm = "PM" in s
                        is_am = "AM" in s
                        s = s.replace("PM", "").replace("AM", "").strip()
                        parts = [int(p) for p in s.split(":")[:2]]
                        h = parts[0]
                        m = parts[1] if len(parts) > 1 else 0
                        if is_pm and h < 12:
                            h += 12
                        elif is_am and h == 12:
                            h = 0
                        return (h * 60 + m) % 1440
                    except Exception:
                        return def_val

                start_mins = _to_mins(start_str, 18 * 60)
                end_mins = _to_mins(end_str, 6 * 60)

                now_dt = datetime.now()
                now_mins = now_dt.hour * 60 + now_dt.minute

                if start_mins > end_mins:
                    # Overnight schedule (e.g. 18:00 to 06:00 / 6:00 PM to 6:00 AM)
                    is_off_hours = (now_mins >= start_mins or now_mins <= end_mins)
                else:
                    # Same-day schedule (e.g. 09:00 to 18:00)
                    is_off_hours = (start_mins <= now_mins <= end_mins)

                if is_off_hours:
                    has_roi = bool(self.roi_polygon and len(self.roi_polygon) == 2)
                    if has_roi:
                        r_min_x = min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * self.width
                        r_max_x = max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * self.width
                        r_min_y = min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * self.height
                        r_max_y = max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * self.height

                    for tid, trk in list(self._ema_tracks.items()):
                        c_trk = trk.get('cls', '')
                        if not any(match_class(c_trk, nc) for nc in nwh_classes):
                            continue
                        if (now_t - trk.get('last_seen', 0.0)) > 0.40:
                            continue
                        bx = trk.get('box', [])
                        if len(bx) != 4:
                            continue
                        cx = (bx[0] + bx[2]) / 2.0
                        cy = (bx[1] + bx[3]) / 2.0
                        gx, gy = cx, bx[3]

                        if has_roi:
                            in_nwh_zone = ((r_min_x <= cx <= r_max_x and r_min_y <= cy <= r_max_y) or
                                           (r_min_x <= gx <= r_max_x and r_min_y <= gy <= r_max_y))
                        else:
                            in_nwh_zone = True

                        if in_nwh_zone:
                            matched_b = False
                            for b in display_boxes:
                                ob = b.get('box', [0, 0, 0, 0])
                                if b.get('trk_id') == tid or (abs(ob[0] - bx[0]) < 20 and abs(ob[1] - bx[1]) < 20):
                                    b['color'] = (20, 20, 237)
                                    b['label'] = f"VIOLATION: NON-WORKING HOURS ({c_trk}) {trk.get('conf', 0.8):.2f}"
                                    b['sub'] = f"OFF-HOURS INTRUSION DETECTED"
                                    matched_b = True
                                    break
                            if not matched_b:
                                display_boxes.append({
                                    'box': bx,
                                    'label': f"VIOLATION: NON-WORKING HOURS ({c_trk}) {trk.get('conf', 0.8):.2f}",
                                    'sub': "OFF-HOURS INTRUSION DETECTED",
                                    'color': (20, 20, 237),
                                    'cls': c_trk,
                                    'conf': trk.get('conf', 0.8),
                                    'trk_id': tid
                                })

                            cd_key = f"nwh_{c_trk}"
                            if check_and_record_alert_cooldown(self.cam_id, cd_key, 30.0):
                                alert_msg = f"VIOLATION: Intrusion in Non-Working Hours Detected ({c_trk.capitalize()}{' in ROI' if has_roi else ''})"
                                print(f"[ALERT-TRIGGER-NWH] Camera {self.cam_id}: {alert_msg}", flush=True)
                                self._save_alert(alert_msg, f, display_boxes=display_boxes, violating_box=bx, violating_label=f"VIOLATION: NON-WORKING HOURS ({c_trk})")

            # -- Vehicle Intrusion Detection in User-Drawn ROI / Restricted Zone -
            is_veh_intru_assigned = any("vehicle_intrusion" in str(p).lower() or "vehicle-intrusion" in str(p).lower() for p in (self.model_paths if isinstance(self.model_paths, list) else [self.model_paths]))
            if is_veh_intru_assigned and not is_speed_camera:
                veh_intru_cfg = get_config_for_model(self.model_configs, "vehicle_intrusion.pt") or get_config_for_model(self.model_configs, "vehicle_intrusion") or {}
                veh_intru_classes = veh_intru_cfg.get("enabled_classes") if isinstance(veh_intru_cfg, dict) else None
                if veh_intru_classes is None:
                    veh_intru_classes = ["car", "truck", "bus", "motorcycle"]

                has_roi = bool(self.roi_polygon and len(self.roi_polygon) == 2)
                if has_roi:
                    r_min_x = min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * self.width
                    r_max_x = max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * self.width
                    r_min_y = min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * self.height
                    r_max_y = max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * self.height

                for tid, trk in list(self._ema_tracks.items()):
                    c_trk = trk.get('cls', '')
                    if not any(match_class(c_trk, vc) for vc in veh_intru_classes):
                        continue
                    if (now_t - trk.get('last_seen', 0.0)) > 0.40:
                        continue
                    bx = trk.get('box', [])
                    if len(bx) != 4:
                        continue
                    cx = (bx[0] + bx[2]) / 2.0
                    cy = (bx[1] + bx[3]) / 2.0
                    gx, gy = cx, bx[3]

                    if has_roi:
                        in_veh_zone = ((r_min_x <= cx <= r_max_x and r_min_y <= cy <= r_max_y) or
                                       (r_min_x <= gx <= r_max_x and r_min_y <= gy <= r_max_y))
                    else:
                        in_veh_zone = True

                    if in_veh_zone:
                        matched_b = False
                        for b in display_boxes:
                            ob = b.get('box', [0, 0, 0, 0])
                            if b.get('trk_id') == tid or (abs(ob[0] - bx[0]) < 20 and abs(ob[1] - bx[1]) < 20):
                                b['color'] = (20, 20, 237)
                                b['label'] = f"VIOLATION: VEHICLE INTRUSION ({c_trk}) {trk.get('conf', 0.8):.2f}"
                                b['sub'] = f"RESTRICTED VEHICLE ZONE"
                                matched_b = True
                                break
                        if not matched_b:
                            display_boxes.append({
                                'box': bx,
                                'label': f"VIOLATION: VEHICLE INTRUSION ({c_trk}) {trk.get('conf', 0.8):.2f}",
                                'sub': "RESTRICTED VEHICLE ZONE",
                                'color': (20, 20, 237),
                                'cls': c_trk,
                                'conf': trk.get('conf', 0.8),
                                'trk_id': tid
                            })

                        cd_key = f"veh_intrusion_{c_trk}"
                        if check_and_record_alert_cooldown(self.cam_id, cd_key, 30.0):
                            alert_msg = f"VIOLATION: Vehicle Intrusion Detected ({c_trk.capitalize()}{' in ROI' if has_roi else ''})"
                            print(f"[ALERT-TRIGGER-VEH-INTRUSION] Camera {self.cam_id}: {alert_msg}", flush=True)
                            self._save_alert(alert_msg, f, display_boxes=display_boxes, violating_box=bx, violating_label=f"VIOLATION: VEHICLE INTRUSION ({c_trk})")

            # -- Unauthorized Activity (Parking Area) Alert Triggering --
            if is_unauth_assigned and not is_speed_camera:
                for tid, trk in list(self._ema_tracks.items()):
                    act = trk.get('unauth_activity')
                    if not act:
                        continue
                    if (now_t - trk.get('last_seen', 0.0)) > 0.40:
                        continue
                    bx = trk.get('box', [])
                    if len(bx) != 4:
                        continue

                    # Respect enabled/selected classes
                    if camera_selected_classes and not any(match_class(act, sc) for sc in camera_selected_classes):
                        continue

                    # Ensure display box is highlighted in Reliance Alert Red with clear label
                    matched_b = False
                    for b in display_boxes:
                        ob = b.get('box', [0, 0, 0, 0])
                        if b.get('trk_id') == tid or (abs(ob[0] - bx[0]) < 20 and abs(ob[1] - bx[1]) < 20):
                            b['color'] = (20, 20, 237)
                            b['label'] = f"VIOLATION: UNAUTHORIZED ACTIVITY ({act.replace('_', ' ').upper()}) {trk.get('unauth_conf', trk.get('conf', 0.8)):.2f}"
                            b['sub'] = f"PARKING AREA: {trk.get('unauth_sub', act.upper())}"
                            matched_b = True
                            break
                    if not matched_b:
                        display_boxes.append({
                            'box': bx,
                            'label': f"VIOLATION: UNAUTHORIZED ACTIVITY ({act.replace('_', ' ').upper()}) {trk.get('unauth_conf', trk.get('conf', 0.8)):.2f}",
                            'sub': f"PARKING AREA: {trk.get('unauth_sub', act.upper())}",
                            'color': (20, 20, 237),
                            'cls': act,
                            'conf': trk.get('unauth_conf', trk.get('conf', 0.8)),
                            'trk_id': tid
                        })

                    # Trigger alert with global 30-second cooldown
                    cd_key = f"unauth_{act}_{self.cam_id}"
                    if check_and_record_alert_cooldown(self.cam_id, cd_key, 30.0):
                        has_roi = bool(self.roi_polygon and len(self.roi_polygon) == 2)
                        alert_msg = f"VIOLATION: Unauthorized Activity in Parking Area Detected ({act.replace('_', ' ').title()}{' in ROI' if has_roi else ''})"
                        print(f"[ALERT-TRIGGER-UNAUTH] Camera {self.cam_id}: {alert_msg}", flush=True)
                        self._save_alert(alert_msg, f, display_boxes=display_boxes, violating_box=bx, violating_label=f"VIOLATION: UNAUTHORIZED ACTIVITY ({act.replace('_', ' ').upper()})")

            with self._box_lock:
                self._tracked_boxes = display_boxes

        # -- Over Speed Alert Trigger (ONLY triggers on dedicated speed cameras when measured speed > limit) --
        if is_speed_camera:
            for tid, trk in list(self._ema_tracks.items()):
                if trk.get('should_alert_overspeed') and not trk.get('overspeed_alerted'):
                    trk['overspeed_alerted'] = True
                    spd = trk.get('speed_kmh', 0.0)
                    alert_cls = f"OVER SPEED: {spd:.1f} km/h (Limit: {speed_limit:.0f} km/h)"
                    print(f"[ALERT-TRIGGER-SPEED] Over speed alert generated: cam={self.cam_id}, vehicle_id={tid}, class={trk.get('cls')}, speed={spd:.1f} km/h", flush=True)
                    self._save_alert(alert_cls, f, display_boxes=display_boxes)

    def _save_alert(self, class_name, frame, vehicle_label=None, display_boxes=None, violating_box=None, violating_label=None):
        try:
            now_dt = datetime.now()
            ts = now_dt.strftime("%Y%m%d_%H%M%S")
            if vehicle_label:
                safe_cls_filename = re.sub(r'[^a-zA-Z0-9_-]', '_', str(vehicle_label).lower().strip().replace(" ", "_"))
            else:
                disp_name = "NO-PPE" if str(class_name).lower() == "none" else str(class_name)
                safe_cls_filename = re.sub(r'[^a-zA-Z0-9_-]', '_', disp_name)
            filename = f"cam{self.cam_id}_{ts}_{safe_cls_filename}.jpg"
            adir = os.path.join(BASE_DIR, "alerts")
            os.makedirs(adir, exist_ok=True)

            # Build fully annotated high-visibility evidence image
            alert_img = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_LINEAR) if frame is not None else np.zeros((self.height, self.width, 3), dtype=np.uint8)
            f_h, f_w = alert_img.shape[:2]

            # 1. Draw User-Drawn ROI Zone (if configured)
            has_roi = bool(self.roi_polygon and len(self.roi_polygon) == 2)
            if has_roi:
                try:
                    rx1 = int(min(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                    rx2 = int(max(self.roi_polygon[0][0], self.roi_polygon[1][0]) * f_w)
                    ry1 = int(min(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)
                    ry2 = int(max(self.roi_polygon[0][1], self.roi_polygon[1][1]) * f_h)

                    is_intrusion = any(k in str(class_name).lower() for k in ("intrusion", "off-hours", "restricted", "unauthorized"))
                    roi_color = (20, 20, 237) if is_intrusion else (0, 180, 255) # Red for intrusion, else Amber

                    roi_overlay = alert_img.copy()
                    cv2.rectangle(roi_overlay, (rx1, ry1), (rx2, ry2), roi_color, -1)
                    cv2.addWeighted(roi_overlay, 0.14, alert_img, 0.86, 0, alert_img)
                    cv2.rectangle(alert_img, (rx1, ry1), (rx2, ry2), roi_color, 2)

                    roi_tag = "RESTRICTED ROI ZONE"
                    (rw, rh), _ = cv2.getTextSize(roi_tag, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
                    cv2.rectangle(alert_img, (rx1, max(0, ry1 - rh - 6)), (rx1 + rw + 8, ry1), roi_color, -1)
                    cv2.putText(alert_img, roi_tag, (rx1 + 4, ry1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
                except Exception:
                    pass

            # 2. Render all active bounding boxes (including Bold Reliance Red violation boxes)
            raw_boxes = display_boxes if display_boxes is not None else getattr(self, '_tracked_boxes', [])
            boxes_to_render = [dict(b) for b in raw_boxes] if raw_boxes else []
            if violating_box and len(violating_box) == 4:
                found_v = False
                for b in boxes_to_render:
                    ob = b.get('box', [0, 0, 0, 0])
                    if abs(ob[0] - violating_box[0]) < 20 and abs(ob[1] - violating_box[1]) < 20:
                        b['color'] = (20, 20, 237)
                        if violating_label:
                            b['label'] = violating_label
                        found_v = True
                        break
                if not found_v:
                    boxes_to_render.append({
                        'box': violating_box,
                        'label': violating_label or f"VIOLATION: {class_name}",
                        'sub': "RESTRICTED SECURITY ZONE",
                        'color': (20, 20, 237),
                        'cls': str(class_name)
                    })
            if boxes_to_render:
                DetectorWorker._draw_boxes(alert_img, boxes_to_render)

            # 3. If speed camera, draw speed lines & HUD
            if getattr(self, 'is_speed_camera', False):
                la_y = int(f_h * getattr(self, 'line_a_ratio', 0.40))
                lb_y = int(f_h * getattr(self, 'line_b_ratio', 0.75))
                cv2.line(alert_img, (int(f_w * 0.05), la_y), (int(f_w * 0.95), la_y), (255, 200, 0), 2)
                cv2.putText(alert_img, "LINE A (GATE ENTRY)", (int(f_w * 0.06), la_y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 200, 0), 1, cv2.LINE_AA)
                cv2.line(alert_img, (int(f_w * 0.05), lb_y), (int(f_w * 0.95), lb_y), (0, 255, 100), 2)
                cv2.putText(alert_img, "LINE B (GANTRY ROAD)", (int(f_w * 0.06), lb_y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 255, 100), 1, cv2.LINE_AA)

            # 4. Standardized Violation Type String
            lbl = vehicle_label or class_name
            lbl_str = str(lbl).strip()
            if "OVER SPEED" in lbl_str.upper() or "CROWD" in lbl_str.upper():
                type_of_alert_str = lbl_str
            elif lbl_str.upper().startswith("VIOLATION:"):
                type_of_alert_str = lbl_str if "detected" in lbl_str.lower() else f"{lbl_str} Detected"
            elif lbl_str.lower().endswith("detected"):
                type_of_alert_str = f"VIOLATION: {lbl_str}"
            else:
                type_of_alert_str = f"VIOLATION: {lbl_str} Detected"

            # 5. Top Alert Evidence Header Strip across snapshot
            banner_h = 28
            banner_overlay = alert_img.copy()
            cv2.rectangle(banner_overlay, (0, 0), (f_w, banner_h), (18, 18, 200), -1) # Dark crimson alert header
            cv2.addWeighted(banner_overlay, 0.88, alert_img, 0.12, 0, alert_img)
            cv2.rectangle(alert_img, (0, 0), (f_w, banner_h), (50, 50, 240), 1)

            banner_text = f"ALERT: {type_of_alert_str} | CAM {self.cam_id} ({self.location}) | {now_dt.strftime('%H:%M:%S')}"
            cv2.putText(alert_img, banner_text, (8, banner_h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

            img_saved = cv2.imwrite(os.path.join(adir, filename), alert_img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            print(f"[ALERT-IMG] Saved snapshot {filename}, ok={img_saved}", flush=True)

            # Local or relative HTTP URL for cross-port browser compatibility
            base_url = os.getenv("LOCAL_BASE_URL", "").rstrip("/")
            image_path = f"{base_url}/hls/alerts/{filename}" if base_url else f"/hls/alerts/{filename}"

            lbl = vehicle_label or class_name
            lbl_str = str(lbl).strip()
            if "OVER SPEED" in lbl_str.upper() or "CROWD" in lbl_str.upper():
                type_of_alert_str = lbl_str
            elif lbl_str.upper().startswith("VIOLATION:"):
                type_of_alert_str = lbl_str if "detected" in lbl_str.lower() else f"{lbl_str} Detected"
            elif lbl_str.lower().endswith("detected"):
                type_of_alert_str = f"VIOLATION: {lbl_str}"
            else:
                type_of_alert_str = f"VIOLATION: {lbl_str} Detected"

            # 1. Append to alerts.json for immediate UI dashboard update
            alerts_json_file = os.path.join(adir, "alerts.json")
            alert_entry = {
                "id":           int(time.time() * 1000),
                "camera_id":    str(self.cam_id),
                "camera":       str(self.cam_id),
                "location":     str(self.location),
                "type_of_alert": type_of_alert_str,
                "event":        type_of_alert_str,
                "image":        image_path,
                "image_path":   image_path,
                "created_at":   now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "time":         now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "is_negative":  True
            }
            try:
                data = []
                if os.path.exists(alerts_json_file):
                    with open(alerts_json_file, "r") as jf:
                        try: data = json.load(jf)
                        except: data = []
                if not isinstance(data, list): data = []
                data.insert(0, alert_entry)
                data = data[:100]
                with open(alerts_json_file, "w") as jf:
                    json.dump(data, jf, indent=2)
                print(f"[ALERT-JSON] Appended alert to alerts.json: {type_of_alert_str}", flush=True)
            except Exception as jerr:
                print(f"[ALERT-JSON-ERR] Failed updating alerts.json: {jerr}", flush=True)

            # 2. Store directly into PostgreSQL DB (psycopg2 or psql CLI fallback)
            conn = self._get_db_conn()
            db_stored = False
            if conn:
                try:
                    cur = conn.cursor()
                    ensure_alerts_schema(cur)
                    insert_alert_db(cur, self.cam_id, self.location, type_of_alert_str, image_path, now_dt)
                    conn.commit()
                    cur.close()
                    db_stored = True
                    print(f"[ALERT-DB] Alert stored successfully in DB: cam={self.cam_id}, class={type_of_alert_str}, location={self.location}", flush=True)
                except Exception as dbe:
                    print(f"[ALERT-DB-ERR] DB insert error: {dbe}", flush=True)
                    try: conn.rollback()
                    except: pass
                    try: self._db_conn.close()
                    except: pass
                    self._db_conn = None

            if not db_stored:
                ok = insert_alert_via_psql(self.cam_id, self.location, type_of_alert_str, image_path, now_dt)
                if ok:
                    print(f"[ALERT-DB] Alert stored successfully via psql CLI: cam={self.cam_id}, class={type_of_alert_str}, location={self.location}", flush=True)
        except Exception as e:
            print(f"[ALERT-ERR] Failed to save alert: {e}", flush=True)

    def _get_connecting_frame(self):
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        cv2.putText(frame, "Connecting to Camera...", 
                   (int(self.width*0.2), int(self.height*0.5)), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
        return frame

    def _capture_thread(self, cap, cap_stop_evt):
        consecutive_fails = 0
        is_local_file = os.path.isfile(str(self.rtsp_url)) or not str(self.rtsp_url).lower().startswith("rtsp")

        while not self._stop_event.is_set() and not cap_stop_evt.is_set():
            try:
                ret, f = cap.read()
                if not ret or f is None:
                    if is_local_file:
                        # Auto-loop video file from beginning
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        time.sleep(0.02)
                        continue
                    consecutive_fails += 1
                    time.sleep(0.02)
                    if consecutive_fails > 150:
                        print(f"[WARN] Camera {self.cam_id} RTSP stream disconnected, requesting reconnect...", flush=True)
                        break
                    continue
                consecutive_fails = 0
                with self._frame_lock:
                    self._latest_raw_frame = f.copy()
                    self._last_frame_time  = time.time()
                    self._cap_ok = True

                # Yield minimally to OS scheduler
                time.sleep(0.005 if not is_local_file else 0.015)
            except Exception:
                time.sleep(0.02)
            except Exception:
                time.sleep(0.02)

    # --------------------------------------------------------------------------
    # DRAWING HELPER
    # --------------------------------------------------------------------------
    @staticmethod
    def _draw_boxes(pf, display_boxes):
        """
        Draw all bounding boxes and labels onto pf (in-place).

        Features:
          -  Area-based z-order so small PPE boxes render on top of large person boxes.
          -  Thick border (2 px) + black inner glow (1 px outline trick) for visibility.
          -  Larger, bolder labels with padded background panels.
          -  Vertical label stacking to avoid overlapping label text when two boxes
            share the same top-left corner region.
        """
        if not display_boxes:
            return

        f_h, f_w = pf.shape[:2]

        # Sort: large boxes first (person/body) -> drawn below; small boxes last -> drawn on top
        sorted_boxes = sorted(
            display_boxes,
            key=lambda b: max(0, b['box'][2] - b['box'][0]) * max(0, b['box'][3] - b['box'][1]),
            reverse=True
        )

        # Track used label regions to stack them vertically
        used_label_slots = []   # list of (lx1, ly1, lx2, ly2)

        def find_free_label_y(lx1, lx2, preferred_y1, preferred_y2, step=2):
            """Shift label upward until it doesn't collide with any occupied slot."""
            ly1, ly2 = preferred_y1, preferred_y2
            h = ly2 - ly1
            for _ in range(60):
                collision = False
                for (ux1, uy1, ux2, uy2) in used_label_slots:
                    # Check horizontal overlap
                    if lx1 < ux2 and lx2 > ux1:
                        # Check vertical overlap
                        if ly1 < uy2 and ly2 > uy1:
                            collision = True
                            break
                if not collision:
                    break
                ly1 = max(0, ly1 - step)
                ly2 = ly1 + h
            return ly1, ly2

        for t_box in sorted_boxes:
            try:
                x1, y1, x2, y2 = [int(v) for v in t_box['box']]
                x1 = max(0, min(f_w - 1, x1))
                y1 = max(0, min(f_h - 1, y1))
                x2 = max(0, min(f_w - 1, x2))
                y2 = max(0, min(f_h - 1, y2))

                label_text = t_box['label']
                color_val  = t_box['color']

                # Draw box: black shadow first (offset 1px) then coloured border
                cv2.rectangle(pf, (x1+1, y1+1), (x2+1, y2+1), (0, 0, 0), 2)      # shadow
                cv2.rectangle(pf, (x1, y1), (x2, y2), color_val, 2)               # main

                sub_text = t_box.get('sub')
                if sub_text:
                    (tw1, th1), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.40, 1)
                    (tw2, th2), _ = cv2.getTextSize(sub_text, cv2.FONT_HERSHEY_SIMPLEX, 0.40, 2)
                    max_w = max(tw1, tw2) + 12
                    pref_bg_y1 = max(0, y1 - 34)
                    pref_bg_y2 = y1
                    pref_bg_x1 = x1
                    pref_bg_x2 = min(f_w - 1, x1 + max_w)
                    bg_y1, bg_y2 = find_free_label_y(pref_bg_x1, pref_bg_x2, pref_bg_y1, pref_bg_y2)
                    cv2.rectangle(pf, (pref_bg_x1, bg_y1), (pref_bg_x2, bg_y2), color_val, -1)
                    cv2.putText(pf, label_text, (pref_bg_x1 + 4, bg_y1 + 13), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 0), 1, cv2.LINE_AA)
                    cv2.putText(pf, sub_text, (pref_bg_x1 + 4, bg_y1 + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 2, cv2.LINE_AA)
                    used_label_slots.append((pref_bg_x1, bg_y1, pref_bg_x2, bg_y2))
                else:
                    # Standard PPE single-line label
                    (tw, th), baseline = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, LABEL_FONT_SCALE, LABEL_THICKNESS)
                    pad = LABEL_BOX_PADDING
                    pref_bg_y1 = y1 - th - 2*pad
                    pref_bg_y2 = y1
                    pref_bg_x1 = x1
                    pref_bg_x2 = min(f_w - 1, x1 + tw + 2*pad)
                    if pref_bg_y1 < 0:
                        pref_bg_y1 = y1
                        pref_bg_y2 = y1 + th + 2*pad
                    bg_y1, bg_y2 = find_free_label_y(pref_bg_x1, pref_bg_x2, pref_bg_y1, pref_bg_y2)
                    bg_x1, bg_x2 = pref_bg_x1, pref_bg_x2
                    text_y = bg_y1 + th + pad - 1
                    cv2.rectangle(pf, (bg_x1, bg_y1), (bg_x2, bg_y2), color_val, -1)
                    cv2.putText(pf, label_text, (bg_x1 + pad, text_y),
                                cv2.FONT_HERSHEY_SIMPLEX, LABEL_FONT_SCALE,
                                (0, 0, 0), LABEL_THICKNESS + 1, cv2.LINE_AA)
                    cv2.putText(pf, label_text, (bg_x1 + pad, text_y),
                                cv2.FONT_HERSHEY_SIMPLEX, LABEL_FONT_SCALE,
                                (255, 255, 255), LABEL_THICKNESS, cv2.LINE_AA)
                    used_label_slots.append((bg_x1, bg_y1, bg_x2, bg_y2))
            except Exception:
                pass

    def run(self):
        ffmpeg, cap = None, None
        cap_stop_evt = threading.Event()
        cap_t = None

        def cleanup_subthreads():
            nonlocal cap, ffmpeg, cap_t
            cap_stop_evt.set()
            if cap_t and cap_t.is_alive():
                cap_t.join(timeout=1.0)
                cap_t = None
            if cap:
                try: cap.release()
                except Exception: pass
                cap = None
            if ffmpeg:
                try: ffmpeg.stdin.close()
                except Exception: pass
                try: ffmpeg.kill(); ffmpeg.wait(timeout=1.0)
                except Exception: pass
                ffmpeg = None

        try:
            if not getattr(self, 'is_speed_worker', False):
                GLOBAL_INFERENCE_SCHEDULER.register_worker(self)

            while not self._stop_event.is_set():
                cleanup_subthreads()
                cap_stop_evt.clear()

                if self._stop_event.is_set():
                    break

                try:
                    print(f"[WORKER-TIMER] Camera {self.cam_id} creating FFmpeg process...", flush=True)
                    t_ff_start = time.time()
                    ffmpeg = self._create_ffmpeg()
                    print(f"[WORKER-TIMER] Camera {self.cam_id} FFmpeg process created in {int((time.time() - t_ff_start)*1000)}ms", flush=True)

                    is_local_file = os.path.isfile(str(self.rtsp_url)) or not str(self.rtsp_url).lower().startswith("rtsp")
                    print(f"[WORKER-TIMER] Camera {self.cam_id} connecting to {'local video file' if is_local_file else 'RTSP'} ({self.rtsp_url}) at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...", flush=True)
                    t_conn_start = time.time()
                    if is_local_file:
                        cap = cv2.VideoCapture(str(self.rtsp_url))
                    else:
                        cap = cv2.VideoCapture(str(self.rtsp_url), cv2.CAP_FFMPEG)
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                    retry_count = 0
                    while not cap.isOpened() and retry_count < 10 and not self._stop_event.is_set():
                        time.sleep(0.5)
                        if is_local_file:
                            cap = cv2.VideoCapture(str(self.rtsp_url))
                        else:
                            cap = cv2.VideoCapture(str(self.rtsp_url), cv2.CAP_FFMPEG)
                            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        retry_count += 1

                    if self._stop_event.is_set() or not cap.isOpened():
                        if not cap.isOpened():
                            print(f"[ERROR] Camera {self.cam_id} could not open video stream: {self.rtsp_url}", flush=True)
                        break

                    print(f"[WORKER-TIMER] Camera {self.cam_id} {'video file' if is_local_file else 'RTSP'} connected in {int((time.time() - t_conn_start)*1000)}ms at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

                    if self.is_speed_worker and getattr(self, 'speed_tracker_engine', None) is not None:
                        # ----------------------------------------------------------
                        # DEDICATED SPEED TRACKER PIPELINE (MATCHES run_speed_tracker.py)
                        # ----------------------------------------------------------
                        src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                        if src_fps <= 0 or src_fps > 120:
                            src_fps = 25.0
                        
                        target_interval = 1.0 / self.fps
                        next_frame_time = time.time()
                        frame_idx = 0

                        while not self._stop_event.is_set():
                            if is_local_file:
                                now = time.time()
                                if now < next_frame_time:
                                    time.sleep(max(0.001, next_frame_time - now))
                                next_frame_time += target_interval
                                if now - next_frame_time > 0.5:
                                    next_frame_time = now + target_interval

                                # Skip intermediate frame for exact parity with CLI --skip 2
                                cap.grab()
                                frame_idx += 1

                            ret, raw_frame = cap.read()
                            if not ret or raw_frame is None:
                                if is_local_file:
                                    cap.release()
                                    cap = cv2.VideoCapture(str(self.rtsp_url))
                                    frame_idx = 0
                                    continue
                                else:
                                    print(f"[SPEED-WORKER] Camera {self.cam_id} RTSP stream finished or disconnected.", flush=True)
                                    break

                            frame_idx += 1
                            curr_t = (frame_idx / src_fps) if is_local_file else time.time()

                            with self._frame_lock:
                                self._latest_raw_frame = raw_frame
                                self._last_frame_time = time.time()

                            # Downscale frame to worker dimensions if needed for high-speed inference
                            if (raw_frame.shape[1], raw_frame.shape[0]) != (self.width, self.height):
                                proc_frame = cv2.resize(raw_frame, (self.width, self.height), interpolation=cv2.INTER_LINEAR)
                            else:
                                proc_frame = raw_frame

                            pf, violations = self.speed_tracker_engine.process_frame(proc_frame, curr_t)
                            if (self.width, self.height) != (pf.shape[1], pf.shape[0]):
                                pf = cv2.resize(pf, (self.width, self.height), interpolation=cv2.INTER_LINEAR)

                            # Immediately encode and cache latest annotated JPEG frame in memory (zero latency, zero black screen)
                            ret_j, j_bytes = cv2.imencode('.jpg', pf, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                            if ret_j:
                                with self._frame_lock:
                                    self._latest_jpeg_frame = j_bytes.tobytes()

                            self.vehicle_counts = self.speed_tracker_engine.counts

                            # Update live speed records from tracker
                            if not hasattr(self, 'speed_records'): self.speed_records = []
                            for tid, trk in list(self.speed_tracker_engine.tracker.tracks.items()):
                                spd = trk.get("speed_kmh") or trk.get("disp_speed")
                                if spd is not None and not trk.get("_stat_logged", False):
                                    trk["_stat_logged"] = True
                                    is_over = bool(spd > self.speed_limit_kmh)
                                    self.speed_records.append({
                                        "vehicle_id": tid,
                                        "cls": trk.get("label", "vehicle"),
                                        "speed_kmh": round(spd, 1),
                                        "is_over_speed": is_over,
                                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    })
                                    if len(self.speed_records) > 200:
                                        self.speed_records = self.speed_records[-200:]

                            # Handle any overspeed violations triggered this frame
                            for v in violations:
                                if not hasattr(self, 'overspeed_count'): self.overspeed_count = 0
                                self.overspeed_count += 1
                                v_spd = v.get("speed_kmh", 0.0)
                                v_lbl = v.get("label", "vehicle")
                                alert_cls = f"OVER SPEED: {v_spd:.1f} km/h (Limit: {self.speed_limit_kmh:.0f} km/h)"
                                print(f"[ALERT] [ALERT-TRIGGER-SPEED] Standalone Engine Over speed alert: cam={self.cam_id}, vehicle_id={v.get('track_id')}, class={v_lbl}, speed={v_spd:.1f} km/h", flush=True)
                                self._save_alert(alert_cls, pf, vehicle_label=v_lbl)

                            if ffmpeg.poll() is not None:
                                break

                            try:
                                ffmpeg.stdin.write(pf.tobytes())
                                ffmpeg.stdin.flush()
                            except Exception:
                                break
                    elif self.is_crowd_worker and getattr(self, 'crowd_engine', None) is not None:
                        # ----------------------------------------------------------
                        # DEDICATED CROWD MONITORING PIPELINE
                        # ----------------------------------------------------------
                        cap_t = threading.Thread(target=self._capture_thread, args=(cap, cap_stop_evt), daemon=True)
                        cap_t.start()

                        target_interval = 1.0 / self.fps
                        next_frame_time = time.time()
                        frame_idx = 0

                        while not self._stop_event.is_set():
                            now = time.time()
                            if now < next_frame_time:
                                time.sleep(max(0.001, next_frame_time - now))
                                continue
                            next_frame_time += target_interval
                            if now - next_frame_time > 0.5:
                                next_frame_time = now + target_interval

                            if time.time() - getattr(self, '_last_frame_time', now) > 15.0 and self._latest_raw_frame is not None:
                                print(f"[WARN] Camera {self.cam_id} crowd worker frame timeout (>15s), reconnecting...", flush=True)
                                break

                            with self._frame_lock:
                                raw_frame = self._latest_raw_frame

                            if raw_frame is None:
                                time.sleep(0.02)
                                continue

                            frame_idx += 1

                            # Downscale frame to worker dimensions if needed
                            if (raw_frame.shape[1], raw_frame.shape[0]) != (self.width, self.height):
                                proc_frame = cv2.resize(raw_frame, (self.width, self.height), interpolation=cv2.INTER_LINEAR)
                            else:
                                proc_frame = raw_frame.copy()

                            pf, metrics = self.crowd_engine.process_frame(proc_frame, return_metrics=True)
                            if (self.width, self.height) != (pf.shape[1], pf.shape[0]):
                                pf = cv2.resize(pf, (self.width, self.height), interpolation=cv2.INTER_LINEAR)

                            self.crowd_stats = metrics

                            # Immediately encode and cache latest annotated JPEG frame in memory
                            ret_j, j_bytes = cv2.imencode('.jpg', pf, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                            if ret_j:
                                with self._frame_lock:
                                    self._latest_jpeg_frame = j_bytes.tobytes()

                            # Trigger alert if crowd threshold reached (with global 30-second cooldown)
                            if metrics.get("is_alert"):
                                cd_key = f"crowd_alert_cam{self.cam_id}"
                                if not hasattr(self, 'alert_cooldowns'):
                                    self.alert_cooldowns = {}
                                if check_and_record_alert_cooldown(self.cam_id, cd_key, 30.0):
                                    self.alert_cooldowns[cd_key] = time.time()
                                    cnt = metrics.get("in_roi_count", 0)
                                    print(f"[ALERT] [ALERT-TRIGGER-CROWD] Crowd Gathering alert: cam={self.cam_id}, count={cnt} (threshold={self.crowd_engine.crowd_threshold})", flush=True)
                                    self._save_alert(f"CROWD ALERT: {cnt} Persons in ROI", pf)

                            if ffmpeg.poll() is not None:
                                break

                            try:
                                ffmpeg.stdin.write(pf.tobytes())
                                ffmpeg.stdin.flush()
                            except Exception:
                                break
                    else:
                        # ----------------------------------------------------------
                        # LOCATION CAMERAS PIPELINE (CENTRAL INFERENCE SCHEDULER)
                        # ----------------------------------------------------------
                        cap_t = threading.Thread(target=self._capture_thread, args=(cap, cap_stop_evt), daemon=True)
                        cap_t.start()

                        f_int          = 1.0 / self.fps
                        next_frame_time = time.time()

                        while not self._stop_event.is_set():
                            now = time.time()
                            if now < next_frame_time:
                                time.sleep(max(0.001, next_frame_time - now))
                                continue
                            next_frame_time += f_int
                            if now - next_frame_time > 0.3:
                                next_frame_time = now + f_int

                            if time.time() - getattr(self, '_last_frame_time', now) > 15.0 and self._latest_raw_frame is not None:
                                print(f"[WARN] Camera {self.cam_id} frame timeout (>15s), reconnecting...", flush=True)
                                break

                            with self._frame_lock:
                                raw_frame = self._latest_raw_frame

                            if raw_frame is None:
                                time.sleep(0.02)
                                continue

                            pf = cv2.resize(raw_frame, (self.width, self.height), interpolation=cv2.INTER_LINEAR)
                            f_h, f_w = pf.shape[:2]

                            # Draw ROI boundary if configured
                            if self.roi_polygon and len(self.roi_polygon) == 2:
                                try:
                                    min_x = min(self.roi_polygon[0][0], self.roi_polygon[1][0])
                                    max_x = max(self.roi_polygon[0][0], self.roi_polygon[1][0])
                                    min_y = min(self.roi_polygon[0][1], self.roi_polygon[1][1])
                                    max_y = max(self.roi_polygon[0][1], self.roi_polygon[1][1])
                                    rx1, ry1 = int(min_x * f_w), int(min_y * f_h)
                                    rx2, ry2 = int(max_x * f_w), int(max_y * f_h)
                                    cv2.rectangle(pf, (rx1, ry1), (rx2, ry2), (0, 255, 0), 2)
                                except: pass

                            # Draw latest tracked bounding boxes from Central Inference Scheduler
                            with self._box_lock:
                                display_boxes = list(self._tracked_boxes)

                            DetectorWorker._draw_boxes(pf, display_boxes)

                            # Immediately encode and cache latest annotated JPEG frame in memory
                            ret_j, j_bytes = cv2.imencode('.jpg', pf, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                            if ret_j:
                                with self._frame_lock:
                                    self._latest_jpeg_frame = j_bytes.tobytes()

                            if ffmpeg.poll() is not None:
                                break

                            try:
                                ffmpeg.stdin.write(pf.tobytes())
                                ffmpeg.stdin.flush()
                            except:
                                break
                except:
                    traceback.print_exc()
        finally:
            cleanup_subthreads()
            if not getattr(self, 'is_speed_worker', False) and not getattr(self, 'is_crowd_worker', False):
                GLOBAL_INFERENCE_SCHEDULER.unregister_worker(self)
