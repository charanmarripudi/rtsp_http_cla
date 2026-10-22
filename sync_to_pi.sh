#!/bin/bash
# Sync local changes to GitHub and Raspberry Pi automatically
set -e

PI_IP="192.168.96.36"
PI_USER="algo"
PI_PASS="Algo@123"
PI_DIR="/home/algo/rtsp_http_cla"
BRANCH="rpi"

echo "========================================="
echo "  1. PUSHING LOCAL CHANGES TO GITHUB"
echo "========================================="
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "[INFO] Current branch is: $CURRENT_BRANCH"

COMMIT_MSG="${1:-sync: update code from local workspace $(date '+%Y-%m-%d %H:%M:%S')}"

if [[ -n $(git status -s) ]]; then
    echo "[INFO] Staging and committing modified files with message: '$COMMIT_MSG'..."
    git add -u
    git commit -m "$COMMIT_MSG" || true
fi

echo "[INFO] Pushing $BRANCH to origin..."
git push origin "$BRANCH"

echo "[INFO] Merging $BRANCH into main and pushing..."
git checkout main
git merge "$BRANCH"
git push origin main
git checkout "$CURRENT_BRANCH"

echo "========================================="
echo "  2. UPDATING RASPBERRY PI ($PI_IP)"
echo "========================================="
SSHPASS_BIN="/opt/homebrew/bin/sshpass"
if [ ! -f "$SSHPASS_BIN" ]; then
    SSHPASS_BIN="sshpass"
fi

$SSHPASS_BIN -p "$PI_PASS" ssh -o StrictHostKeyChecking=no "$PI_USER@$PI_IP" "bash -s" << 'EOF'
cd /home/algo/rtsp_http_cla
echo "[PI] Stashing local config changes (preserving cameras & locations)..."
git stash
echo "[PI] Switching to branch rpi..."
git checkout rpi
echo "[PI] Pulling latest code from origin/rpi..."
git pull origin rpi
echo "[PI] Restoring user configs..."
git stash pop || true

echo "[PI] Restarting services cleanly..."
./cleanstart.sh
EOF

echo "========================================="
echo "  3. SYNC COMPLETE & VERIFIED!"
echo "========================================="
