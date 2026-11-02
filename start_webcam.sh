#!/bin/bash
# -----------------------------------------------------------------------------
# Convenience script to start MediaMTX + MacBook Webcam RTSP publisher in one go
# -----------------------------------------------------------------------------

cd "$(dirname "$0")"

# 1. Start MediaMTX in the background if not already running on port 8554
if ! lsof -i :8554 >/dev/null 2>&1; then
    echo "[1/2] Starting MediaMTX RTSP Server in background..."
    ./mediamtx > /tmp/mediamtx.log 2>&1 &
    sleep 1.5
else
    echo "[1/2] MediaMTX RTSP Server is already active on port 8554."
fi

# 2. Start webcam publisher via FFmpeg
echo "[2/2] Streaming MacBook webcam to rtsp://127.0.0.1:8554/webcam..."
echo "      (Press Ctrl+C anytime to stop webcam streaming)"
echo ""

hpcl_env/bin/ffmpeg \
    -hide_banner \
    -loglevel warning \
    -f avfoundation \
    -framerate 30 \
    -video_size 1280x720 \
    -i "default" \
    -vf "hflip,format=yuv420p" \
    -c:v libx264 \
    -preset ultrafast \
    -tune zerolatency \
    -pix_fmt yuv420p \
    -rtsp_transport tcp \
    -f rtsp \
    rtsp://127.0.0.1:8554/webcam

