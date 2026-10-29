#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  sync_to_pi.sh — Sync local code & docs to GitHub and Raspberry Pi
#  Usage:  ./sync_to_pi.sh ["Commit message"]
# ─────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PI_LOCAL_IP="10.246.47.179"
PI_TAILSCALE_HOST="raspberrypi.tail43e835.ts.net"
PI_TAILSCALE_IP="100.75.167.102"
PI_USER="algo"
PI_PASS="QuantyxEdgeBSK@70"
PI_DIR="/home/algo/Documents/rtsp_http_cla"
BRANCH="rpi"

# Test which host is reachable (Local LAN -> Tailscale Hostname -> Tailscale IP)
if nc -z -w 2 "$PI_LOCAL_IP" 22 2>/dev/null; then
    PI_HOST="$PI_LOCAL_IP"
elif nc -z -w 2 "$PI_TAILSCALE_HOST" 22 2>/dev/null; then
    PI_HOST="$PI_TAILSCALE_HOST"
else
    PI_HOST="$PI_TAILSCALE_IP"
fi

echo "========================================="
echo "  1. PUSHING LOCAL CHANGES TO GITHUB"
echo "========================================="
if [ -d "$SCRIPT_DIR/.git" ]; then
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "rpi")
    echo "[INFO] Current branch is: $CURRENT_BRANCH"
    COMMIT_MSG="${1:-sync: update code from local workspace $(date '+%Y-%m-%d %H:%M:%S')}"

    if [[ -n $(git status -s 2>/dev/null) ]]; then
        echo "[INFO] Staging and committing modified files: '$COMMIT_MSG'..."
        git add -A
        git commit -m "$COMMIT_MSG" || true
    fi

    echo "[INFO] Pushing to GitHub origin..."
    git push -u origin "$CURRENT_BRANCH"
else
    echo "[INFO] Local git repo not initialized yet. Skipping git push."
fi

echo "========================================="
echo "  2. DIRECT SYNCING FILES TO RASPBERRY PI ($PI_HOST)"
echo "========================================="
echo "[INFO] Syncing backend, frontend, docs, and scripts to Pi..."

SSHPASS_CMD=""
if command -v sshpass >/dev/null 2>&1; then
    SSHPASS_CMD="sshpass -p '$PI_PASS'"
fi

# Exclude unnecessary heavy or local files during rsync
RSYNC_EXCLUDES=(
    --exclude ".DS_Store"
    --exclude "__pycache__"
    --exclude "*.pyc"
    --exclude "hls/*.ts"
    --exclude "hls/*.m3u8"
    --exclude "logs/*"
    --exclude "f4d3e73"
    --exclude ".git"
)

if [ -n "$SSHPASS_CMD" ]; then
    eval $SSHPASS_CMD rsync -avz --delete "${RSYNC_EXCLUDES[@]}" -e "ssh -o StrictHostKeyChecking=no" "$SCRIPT_DIR/" "$PI_USER@$PI_HOST:$PI_DIR/"
    
    echo "========================================="
    echo "  3. RESTARTING SERVER ON PI"
    echo "========================================="
    eval $SSHPASS_CMD ssh -o StrictHostKeyChecking=no "$PI_USER@$PI_HOST" "bash -s" << EOF
cd $PI_DIR
chmod +x cleanstart.sh launch.sh
./cleanstart.sh
EOF
else
    echo "[INFO] sshpass not found. Using standard rsync (you may be prompted for password)..."
    rsync -avz "${RSYNC_EXCLUDES[@]}" -e "ssh -o StrictHostKeyChecking=no" "$SCRIPT_DIR/" "$PI_USER@$PI_HOST:$PI_DIR/"
    
    echo "========================================="
    echo "  3. RESTARTING SERVER ON PI"
    echo "========================================="
    ssh -o StrictHostKeyChecking=no "$PI_USER@$PI_HOST" "cd $PI_DIR && chmod +x cleanstart.sh launch.sh && ./cleanstart.sh"
fi

echo "========================================="
echo "  SYNC & RESTART COMPLETE!"
echo "========================================="
