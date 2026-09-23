#!/bin/bash
# Cloudflare Tunnel Setup for RTSP Streaming
# This gives you a reliable public URL

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"

echo " Setting up Cloudflare Tunnel for reliable public access..."
echo ""

# Check if cloudflared is installed
if ! command -v cloudflared &> /dev/null; then
    echo " Installing cloudflared..."
    brew install cloudflare/cloudflare/cloudflared
fi

echo " Login to Cloudflare (follow the browser prompt):"
cloudflared tunnel login

echo ""
echo "  Creating tunnel..."
cloudflared tunnel create rtsp-stream

echo ""
echo " Creating DNS record (replace YOUR-DOMAIN.com with your actual domain):"
echo "   You need to add a CNAME record in your DNS settings:"
echo "   rtsp.YOUR-DOMAIN.com → [tunnel-id].cfargotunnel.com"
echo ""
echo "   Run this command after setting up DNS:"
echo "   cloudflared tunnel route dns rtsp-stream rtsp.YOUR-DOMAIN.com"
echo ""
echo " Config file created at: $SCRIPT_DIR/cloudflared.yml"
echo ""
echo " To start with custom domain:"
echo "   cloudflared tunnel --config $SCRIPT_DIR/cloudflared.yml run rtsp-stream"
echo ""
echo " For quick testing (temporary URL):"
echo "   ./launch.sh  (uses trycloudflare.com)"