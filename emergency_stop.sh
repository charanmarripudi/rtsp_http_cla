#!/bin/bash
# Emergency stop script - kills all RTSP streaming processes

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
HLS_DIR="$SCRIPT_DIR/hls"

echo " EMERGENCY STOP - Killing all processes..."
echo "==========================================="

# Kill all related processes aggressively
pkill -9 -f "gst-launch.*hlssink2" 2>/dev/null
pkill -9 -f "rtspsrc" 2>/dev/null
pkill -9 -f "server.py" 2>/dev/null
pkill -9 -f "cloudflared" 2>/dev/null
pkill -9 -f "lt --port" 2>/dev/null
pkill -9 -f "ngrok" 2>/dev/null

# Kill by saved PIDs
for pid_file in "$SCRIPT_DIR"/*.pid; do
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file" 2>/dev/null)
        if [ -n "$pid" ]; then
            kill -9 "$pid" 2>/dev/null
            rm -f "$pid_file"
        fi
    fi
done

# Clean up files
rm -rf "$HLS_DIR"/*.ts "$HLS_DIR"/*.m3u8 2>/dev/null
rm -rf "$HLS_DIR"/stream*/ 2>/dev/null

echo " All processes killed and files cleaned!"
echo ""
echo " To restart: ./launch.sh"