#!/bin/bash
# ==============================================================================
# sync_models.sh - Sync AI Models between Local Mac and Raspberry Pi
# Automatically adds new models and removes deleted models on the Raspberry Pi
# ==============================================================================
set -e

PI_LOCAL_IP="192.168.96.36"
PI_TAILSCALE_HOST="raspberrypi.tail486a43.ts.net"
PI_USER="algo"
PI_PASS="Algo@123"
PI_MODELS_DIR="/home/algo/rtsp_http_cla/models"

# 1. Determine Host Connection (Local Wi-Fi vs. Tailscale)
if nc -z -w 2 "$PI_LOCAL_IP" 22 2>/dev/null; then
    PI_HOST="$PI_LOCAL_IP"
    echo "[CONNECT] Using Local Network IP: $PI_HOST"
else
    PI_HOST="$PI_TAILSCALE_HOST"
    echo "[CONNECT] Using Tailscale Gateway: $PI_HOST"
fi

echo "=========================================================="
echo "  SYNCING MODELS FROM LOCAL MAC TO RASPBERRY PI"
echo "=========================================================="

SSHPASS_BIN="/opt/homebrew/bin/sshpass"
if [ ! -f "$SSHPASS_BIN" ]; then
    SSHPASS_BIN="sshpass"
fi

# 2. Run rsync with --delete to perfectly mirror the models directory
$SSHPASS_BIN -p "$PI_PASS" rsync -avz --delete \
    -e "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5" \
    --exclude=".DS_Store" \
    models/ "$PI_USER@$PI_HOST:$PI_MODELS_DIR/"

echo ""
echo "=========================================================="
echo "  CURRENT ACTIVE MODELS ON RASPBERRY PI:"
echo "=========================================================="
$SSHPASS_BIN -p "$PI_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "$PI_USER@$PI_HOST" \
    "python3 -c \"import os; print('\n'.join(['  [ACTIVE] ' + f for f in sorted(os.listdir('$PI_MODELS_DIR')) if f.endswith('.pt')]))\""

echo ""
echo "[SUCCESS] Models synchronized!"
echo "[NOTE] To see the updated models in the UI, simply refresh your browser (F5 / Cmd+R)."
