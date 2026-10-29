#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  cleanstart.sh  — kill everything stale, then launch fresh
#  Usage:  ./cleanstart.sh
# ─────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  CLEAN STOP — killing all stale processes"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Kill by PID files first (clean shutdown)
for pidfile in "$SCRIPT_DIR"/*.pid; do
    [ -f "$pidfile" ] || continue
    pid=$(cat "$pidfile" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "  Stopping PID $pid ($(basename $pidfile))"
        kill "$pid" 2>/dev/null
    fi
    rm -f "$pidfile"
done

sleep 2

# Force kill anything still running (belt + suspenders)
echo "  Force killing any remaining ffmpeg / uvicorn / server / detector..."
pkill -9 -f ffmpeg            2>/dev/null
pkill -9 -f uvicorn           2>/dev/null
pkill -9 -f "server.py"       2>/dev/null
pkill -9 -f "server:app"      2>/dev/null
pkill -9 -f "start_detection" 2>/dev/null
pkill -9 -f "detector_worker" 2>/dev/null
pkill -9 -f "launch.sh"       2>/dev/null

sleep 3

# Confirm nothing left
REMAINING=$(ps aux | grep -E "ffmpeg|uvicorn|server\.py|start_detection|detector_worker" | grep -v grep | wc -l | tr -d ' ')
if [ "$REMAINING" -gt "0" ]; then
    echo "  WARNING: $REMAINING process(es) still running:"
    ps aux | grep -E "ffmpeg|uvicorn|server\.py|start_detection|detector_worker" | grep -v grep
else
    echo "   All clear — no stale processes"
fi

# Check for ports in use
echo ""
echo "  Checking ports 8080 / 8090..."
for port in 8080 8090; do
    pid_on_port=$(lsof -ti tcp:$port 2>/dev/null)
    if [ -n "$pid_on_port" ]; then
        echo "  WARNING: port $port still in use by PID $pid_on_port — killing..."
        kill -9 $pid_on_port 2>/dev/null
        sleep 1
    else
        echo "   Port $port is free"
    fi
done

sleep 2

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  STARTING FRESH"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

exec "$SCRIPT_DIR/launch.sh" start
