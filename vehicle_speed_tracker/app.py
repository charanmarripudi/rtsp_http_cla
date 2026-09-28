"""
Interactive Web Application for Terminal Vehicle Speed & Count Tracking
Run with:
    streamlit run app.py
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from collections import Counter

import cv2
import numpy as np
import streamlit as st

from speed_tracker import VehicleSpeedTracker, CLASS_COLORS

st.set_page_config(page_title="Terminal Vehicle Speed & Count Monitor", page_icon="🚛", layout="wide")

st.title("🚛 Terminal Tank Truck Speed & Count Tracking")
st.caption("Monitor vehicle counts and enforce 10 km/h speed limits between Gate and Gantry. Optimized for Raspberry Pi 4 & Edge AI.")

CURR_DIR = Path(__file__).resolve().parent
default_weights = CURR_DIR.parent / "Vehicle_Detector_Tracking_Counter" / "best.pt"
if not default_weights.is_file():
    default_weights = CURR_DIR.parent / "rtsp_rpi" / "models" / "yolov8n.pt"

# ── Sidebar Settings ──────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")
    
    input_mode = st.radio("Input Source Mode", ["Upload Video File", "Live RTSP Stream"], index=0)
    
    weights_path = st.text_input("YOLO Weights Path", value=str(default_weights))
    
    st.subheader("🏎️ Speed Limit & Calibration")
    speed_limit = st.slider("Speed Limit (km/h)", min_value=5.0, max_value=40.0, value=10.0, step=0.5)
    road_distance = st.slider("Gate-Gantry Distance (Meters)", min_value=5.0, max_value=100.0, value=20.0, step=1.0)
    confidence = st.slider("Detection Confidence", min_value=0.10, max_value=0.90, value=0.30, step=0.05)
    imgsz = st.select_slider("Inference Image Size (px)", options=[320, 384, 416, 480, 640], value=416)
    
    st.divider()
    st.subheader("📏 Timing Line Calibration")
    st.caption("Line A (Gate Entry Line)")
    la_y = st.slider("Line A Y Position (%)", 0, 100, 40)
    la_x1 = st.slider("Line A Start X (%)", 0, 100, 10)
    la_x2 = st.slider("Line A End X (%)", 0, 100, 90)

    st.caption("Line B (Gantry Road Line)")
    lb_y = st.slider("Line B Y Position (%)", 0, 100, 75)
    lb_x1 = st.slider("Line B Start X (%)", 0, 100, 10)
    lb_x2 = st.slider("Line B End X (%)", 0, 100, 90)

# Build configuration dict
config = {
    "model_path": weights_path,
    "speed_limit_kmh": speed_limit,
    "road_distance_meters": road_distance,
    "confidence_threshold": confidence,
    "imgsz": imgsz,
    "line_a": {"name": "Gate Entry", "x1_pct": la_x1, "y1_pct": la_y, "x2_pct": la_x2, "y2_pct": la_y},
    "line_b": {"name": "Gantry Road", "x1_pct": lb_x1, "y1_pct": lb_y, "x2_pct": lb_x2, "y2_pct": lb_y},
    "allowed_classes": ["truck", "car", "pickup truck", "bike", "tank truck", "vehicle"],
    "alerts_dir": str(CURR_DIR / "alerts"),
    "enable_anpr_hook": False
}

# ── Execution Logic ───────────────────────────────────────────────────────────
if input_mode == "Upload Video File":
    uploaded = st.file_uploader("Upload Test Video", type=["mp4", "avi", "mov", "mkv"])
    if uploaded is None:
        st.info("Upload an MP4 / AVI video to test vehicle tracking, counting, and speed estimation.")
        st.stop()

    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix or ".mp4") as tf:
        tf.write(uploaded.getbuffer())
        video_path = tf.name

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    st.caption(f"Video: {width}x{height} | {fps:.1f} FPS | {total_frames} frames")

    tracker_engine = VehicleSpeedTracker(config)

    col1, col2 = st.columns([3, 1])
    video_placeholder = col1.empty()
    progress_bar = col1.progress(0, text="Processing video...")
    
    metric_count = col2.empty()
    metric_violations = col2.empty()
    violations_table = col2.empty()

    all_violations = []

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as out_f:
        out_path = out_f.name
    writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))

    frame_idx = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            frame_idx += 1
            curr_time = frame_idx / fps

            annotated_frame, violations = tracker_engine.process_frame(frame, curr_time)
            writer.write(annotated_frame)

            # Live real-time visual playback on screen
            video_placeholder.image(
                cv2.cvtColor(annotated_frame, cv2.COLOR_BGR2RGB),
                channels="RGB",
                use_container_width=True
            )

            if violations:
                all_violations.extend(violations)

            # Update UI metrics
            total_v = sum(tracker_engine.counts.values())
            metric_count.metric("Total Vehicles Counted", total_v)
            metric_violations.metric("Speed Violations (>10 km/h)", len(all_violations), delta_color="inverse")

            if all_violations:
                violations_table.dataframe(
                    [{"Vehicle": v["label"], "Speed (km/h)": f"{v['speed_kmh']:.1f}", "Time": v["timestamp"][-8:]} for v in all_violations],
                    hide_index=True,
                    use_container_width=True
                )

            progress_bar.progress(min(1.0, frame_idx / max(1, total_frames)), text=f"Live Processing frame {frame_idx}/{total_frames}")
    finally:
        cap.release()
        writer.release()
        Path(video_path).unlink(missing_ok=True)

    progress_bar.progress(1.0, text="Processing Complete!")
    st.download_button(
        "📥 Download Processed Annotated Video",
        data=Path(out_path).read_bytes(),
        file_name="speed_tracked_video.mp4",
        mime="video/mp4"
    )
    Path(out_path).unlink(missing_ok=True)

    st.success(f"Finished! Counted {sum(tracker_engine.counts.values())} vehicles with {len(all_violations)} speed violations.")

elif input_mode == "Live RTSP Stream":
    rtsp_url = st.text_input("RTSP Stream URL", value="rtsp://192.168.96.72:8554/petrolbunk")
    st.info(f"To run live RTSP monitoring in terminal with zero latency:\n\n```bash\npython3 run_speed_tracker.py --source {rtsp_url} --limit {speed_limit} --distance {road_distance}\n```")
