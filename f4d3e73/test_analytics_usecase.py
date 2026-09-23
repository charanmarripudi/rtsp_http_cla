import json
import os
import sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

def read_streams_conf():
    STREAMS_CONF = os.path.join(BASE_DIR, "streams.conf")
    if not os.path.exists(STREAMS_CONF):
        return []
    try:
        with open(STREAMS_CONF, "r") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
        return lines
    except Exception as e:
        print(f"Error reading streams.conf: {e}")
        return []

def read_streams_metadata():
    STREAMS_JSON = os.path.join(BASE_DIR, "streams.json")
    if not os.path.exists(STREAMS_JSON):
        return []
    try:
        with open(STREAMS_JSON, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"Error reading streams.json: {e}")
        return []

CAMERA_MODELS_JSON = os.path.join(BASE_DIR, "camera_models.json")
running = {}

def get_cameras_with_models():
    urls = read_streams_conf()
    metadata = read_streams_metadata()
    cm = json.load(open(CAMERA_MODELS_JSON)) if os.path.exists(CAMERA_MODELS_JSON) else {}
    result = []
    for i, u in enumerate(urls):
        meta = metadata[i] if i < len(metadata) and isinstance(metadata[i], dict) else {}
        result.append({
            "id": i,
            "label": f"Camera {i+1}",
            "location": meta.get("location", f"Location {i+1}"),
            "location_id": meta.get("location_id", ""),
            "device_id": meta.get("device_id", ""),
            "device_ip": meta.get("device_ip", ""),
            "device_status": meta.get("device_status", "offline"),
            "rtsp": u,
            "models": cm.get(str(i), []),
            "detection_active": False
        })
    return result

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

def get_analytics_mapping_core(location: str = None, models: list = None): 
    """Core logic for analytics mapping.""" 
    cameras = get_cameras_with_models() 
    f_location = _norm_filter_value(location) 
    f_models = _as_list(models) 

    by_location = {} 
    by_usecase = {} 
    matching_cameras = 0 
    
    print("\n" + "="*80)
    print(f"PROCESSING CAMERAS (filtering by models: {f_models}):")
    print("="*80)
    for cam in cameras: 
        cam_location = cam["location"] 
        cam_models = cam["models"] 
        cam_location_key = _norm_filter_value(cam_location) 
        print(f"- Cam {cam['id']}: loc={repr(cam_location)}, models={repr(cam_models)}")
        
        if f_location and cam_location_key != f_location: 
            print("  → SKIP (location filter)")
            continue 
        if f_models: 
            if not any(_model_matches(model, cam_models) for model in f_models): 
                print("  → SKIP (model filter)")
                continue 
        print("  → INCLUDED")
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

print("="*80)
print("TESTING WITH {'usecase': 'nik_ppe_best.pt'}")
print("="*80)
analytics = get_analytics_mapping_core(models="nik_ppe_best.pt")
print("\nRESPONSE:")
print(json.dumps(analytics, indent=2))
