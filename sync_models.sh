#!/bin/bash
# ==============================================================================
# sync_models.sh - Sync AI Models between Local Mac and Raspberry Pi
# Automatically adds new models and removes deleted models on the Raspberry Pi
# ==============================================================================
set -e

PI_LOCAL_IP="10.246.47.179"
PI_TAILSCALE_HOST="raspberrypi.tail43e835.ts.net"
PI_TAILSCALE_IP="100.75.167.102"
PI_USER="algo"
PI_PASS="QuantyxEdgeBSK@70"
PI_MODELS_DIR="/home/algo/Documents/rtsp_http_cla/models"

# 1. Determine Host Connection (Local Wi-Fi vs. Tailscale)
if nc -z -w 2 "$PI_LOCAL_IP" 22 2>/dev/null; then
    PI_HOST="$PI_LOCAL_IP"
    echo "[CONNECT] Using Local Network IP: $PI_HOST"
elif nc -z -w 2 "$PI_TAILSCALE_HOST" 22 2>/dev/null; then
    PI_HOST="$PI_TAILSCALE_HOST"
    echo "[CONNECT] Using Tailscale Gateway: $PI_HOST"
else
    PI_HOST="$PI_TAILSCALE_IP"
    echo "[CONNECT] Using Tailscale IP: $PI_HOST"
fi

echo "=========================================================="
echo "  SYNCING MODELS FROM LOCAL MAC TO RASPBERRY PI"
echo "=========================================================="

SSHPASS_CMD=""
if command -v sshpass >/dev/null 2>&1; then
    SSHPASS_CMD="sshpass -p '$PI_PASS'"
fi

if [ -n "$SSHPASS_CMD" ]; then
    eval $SSHPASS_CMD rsync -avz --delete -e "ssh -o StrictHostKeyChecking=no" ./models/ "$PI_USER@$PI_HOST:$PI_MODELS_DIR/"
else
    rsync -avz --delete -e "ssh -o StrictHostKeyChecking=no" ./models/ "$PI_USER@$PI_HOST:$PI_MODELS_DIR/"
fi

echo "[DONE] Models synchronized successfully."
