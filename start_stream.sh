#!/bin/bash
# ─────────────────────────────────────────────────
#  RTSP → HLS streamer + HTTPS server (single script)
#  Usage: ./start_stream.sh
#  Stop:  Ctrl+C  (GStreamer exits → server auto-killed)
# ─────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HLS_DIR="$SCRIPT_DIR/hls"
LOG_DIR="$SCRIPT_DIR/logs"
RTSP_URL="rtsp://admin:Algo_1212@192.168.96.231:554/stream1"
HTTP_PORT=8445
MY_IP=$(ipconfig getifaddr en0)

# ── Cert check ────────────────────────────────────
if [ ! -f "$SCRIPT_DIR/certs/cert.pem" ]; then
  echo "  SSL certificate not found. Run ./gen_cert.sh first."
  exit 1
fi

mkdir -p "$HLS_DIR" "$LOG_DIR"
rm -f "$HLS_DIR"/*.ts "$HLS_DIR"/*.m3u8

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "    RTSP → HLS Stream (HTTPS)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  RTSP source : $RTSP_URL"
echo "  Stream URL  : https://$MY_IP:$HTTP_PORT/hls/playlist.m3u8"
echo "  Web player  : https://$MY_IP:$HTTP_PORT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Start HTTPS server in background ──────────────
python3 "$SCRIPT_DIR/server.py" > "$LOG_DIR/server.log" 2>&1 &
SERVER_PID=$!
echo "    HTTPS server PID: $SERVER_PID"
echo $SERVER_PID > "$SCRIPT_DIR/server.pid"

# ── GStreamer pipeline (H264 passthrough) ──────────
gst-launch-1.0 -e \
  rtspsrc location="$RTSP_URL" \
    protocols=tcp \
    latency=100 \
    drop-on-latency=true \
    buffer-mode=2 \
  name=src \
  src. ! queue max-size-buffers=10 leaky=downstream ! \
    rtph264depay ! \
    h264parse config-interval=-1 ! \
    "video/x-h264,stream-format=byte-stream,alignment=au" ! \
  hlssink2 \
    location="$HLS_DIR/segment_%05d.ts" \
    playlist-location="$HLS_DIR/playlist.m3u8" \
    target-duration=2 \
    max-files=10 \
    playlist-length=5 \
    send-keyframe-requests=true \
  > "$LOG_DIR/gst.log" 2>&1

echo "     GStreamer exited. Stopping HTTPS server..."
kill $SERVER_PID 2>/dev/null
rm -f "$SCRIPT_DIR/server.pid"


