#!/bin/bash
# Emergency cleanup - run this after reboot or manual process kill

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
HLS_DIR="$SCRIPT_DIR/hls"

echo " Emergency cleanup for RTSP streaming system"
echo "=============================================="

# Clean up PID files
rm -f "$SCRIPT_DIR"/*.pid 2>/dev/null

# Clean up HLS segments
rm -rf "$HLS_DIR"/*.ts "$HLS_DIR"/*.m3u8 "$HLS_DIR"/stream*/ 2>/dev/null

# Clean up temp files
rm -f "$SCRIPT_DIR/cloudflared.yml" "$SCRIPT_DIR/.cloudflared-cert.json" 2>/dev/null

echo " Cleanup complete"
echo ""
echo "Now run: ./launch.sh"