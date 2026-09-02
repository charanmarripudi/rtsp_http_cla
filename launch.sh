#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

LOG_DIR="$SCRIPT_DIR/logs"
HLS_DIR="$SCRIPT_DIR/hls"
STREAMS_CONF="$SCRIPT_DIR/streams.conf"

mkdir -p "$LOG_DIR"
mkdir -p "$HLS_DIR"

###########################################################
# READ STREAMS
###########################################################

read_streams() {
    grep -v '^\s*#' "$STREAMS_CONF" | grep -v '^\s*$'
}

###########################################################
# GENERATE streams.json
###########################################################

generate_streams_json() {

    local idx=0
    local json="["
    local first=true

    while IFS= read -r url; do

        $first || json+=","
        first=false

        #json+="{\"id\":$idx,\"label\":\"Camera $((idx+1))\",\"hls\":\"/hls/stream${idx}/playlist.m3u8\"}"
        json+="{\"id\":$idx,\"label\":\"Camera $((idx+1))\",\"hls\":\"/hls/stream${idx}_raw/playlist.m3u8\"}"

        idx=$((idx+1))

    done < <(read_streams)

    json+="]"

    echo "$json" > "$HLS_DIR/streams.json"

    echo "$(date) Generated streams.json" >> "$LOG_DIR/system.log"
}

###########################################################
# START STREAM
###########################################################

start_stream() {

    local idx=$1
    local rtsp_url=$2
    #local stream_dir="$HLS_DIR/stream${idx}"
    local stream_dir="$HLS_DIR/stream${idx}_raw"

    mkdir -p "$stream_dir"

    while true; do

        rm -f "$stream_dir"/*.ts "$stream_dir"/*.m3u8

        echo "$(date '+%H:%M:%S') stream${idx} STARTING" >> "$LOG_DIR/gst_${idx}.log"

        ffmpeg \
            -hide_banner \
            -loglevel warning \
            -rtsp_transport tcp \
            -i "$rtsp_url" \
            -an \
            -vf "scale=854:480" \
            -c:v libx264 \
            -preset ultrafast \
            -tune zerolatency \
            -pix_fmt yuv420p \
            -b:v 500k \
            -maxrate 500k \
            -bufsize 1000k \
            -r 12 \
            -g 24 \
            -keyint_min 24 \
            -force_key_frames "expr:gte(t,n_forced*2)" \
            -sc_threshold 0 \
            -fflags +genpts \
            -f hls \
            -hls_time 2 \
            -hls_list_size 6 \
            -hls_flags delete_segments+append_list+independent_segments \
            -hls_allow_cache 0 \
            -hls_segment_filename "$stream_dir/segment_%05d.ts" \
            "$stream_dir/playlist.m3u8"
            >> "$LOG_DIR/gst_${idx}.log" 2>&1

        echo "$(date '+%H:%M:%S') stream${idx} CRASHED - restarting in 5s..." >> "$LOG_DIR/gst_${idx}.log"

        sleep 5
    done
}

###########################################################
# CLEANUP OLD SEGMENTS (safety net — runs every 30s)
# Keeps only the 10 newest .ts files per stream
# This backs up ffmpeg's delete_segments in case of edge cases
###########################################################

cleanup_segments() {
    while true; do
        sleep 30
        for stream_dir in "$HLS_DIR"/stream*/; do
            if [ -d "$stream_dir" ]; then
                local count
                count=$(ls "$stream_dir"*.ts 2>/dev/null | wc -l)
                if [ "$count" -gt 10 ]; then
                    ls -t "$stream_dir"*.ts 2>/dev/null \
                        | tail -n +11 \
                        | xargs rm -f 2>/dev/null
                fi
            fi
        done
    done
}

###########################################################
# WAIT FOR STREAMS TO PRODUCE FIRST SEGMENTS
###########################################################

wait_for_streams() {

    local stream_count=$1
    echo "Waiting for streams to produce first segments..."

    for i in $(seq 1 40); do

        local ready=0

        for idx in $(seq 0 $((stream_count - 1))); do
            local seg_count
            #seg_count=$(ls "$HLS_DIR/stream${idx}/"*.ts 2>/dev/null | wc -l)
            seg_count=$(ls "$HLS_DIR/stream${idx}_raw/"*.ts 2>/dev/null | wc -l)
            if [ "$seg_count" -ge 2 ]; then
                ready=$((ready + 1))
            fi
        done

        if [ "$ready" -ge "$stream_count" ]; then
            echo "All $stream_count streams ready."
            return
        fi

        echo "  Waiting... ($ready/$stream_count ready)"
        sleep 1
    done

    echo "Warning: not all streams ready after 40s, continuing anyway."
}

###########################################################
# GET TAILSCALE PERMANENT URL
###########################################################

get_tailscale_url() {

    local url
    url=$(tailscale funnel status 2>/dev/null \
        | grep -o 'https://[^ ]*\.ts\.net' \
        | head -1)

    if [ -n "$url" ]; then
        echo "$url"
        return
    fi

    url=$(tailscale status --json 2>/dev/null \
        | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    name = d.get('Self', {}).get('DNSName', '').rstrip('.')
    if name:
        print('https://' + name)
except:
    pass
" 2>/dev/null)

    echo "$url"
}

###########################################################
# START SYSTEM
###########################################################

start() {

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  STARTING SYSTEM"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Configurations now persist between runs. Server.py manages streams.json.
    # generate_streams_json

    #######################################################
    # START PYTHON SERVER
    #######################################################

    echo "Starting HTTP server..."

    PYTHON_BIN="python3"
    if [ -f "$SCRIPT_DIR/rpi_env/bin/python3" ]; then
        PYTHON_BIN="$SCRIPT_DIR/rpi_env/bin/python3"
    elif [ -f "$SCRIPT_DIR/venv/bin/python3" ]; then
        PYTHON_BIN="$SCRIPT_DIR/venv/bin/python3"
    elif [ -f "$SCRIPT_DIR/hpcl_env/bin/python3" ]; then
        PYTHON_BIN="$SCRIPT_DIR/hpcl_env/bin/python3"
    fi

    nohup $PYTHON_BIN -u "$SCRIPT_DIR/server.py" \
        >> "$LOG_DIR/server.log" 2>&1 &

    echo $! > "$SCRIPT_DIR/server.pid"

    sleep 2

    echo "Server started (PID $(cat "$SCRIPT_DIR/server.pid"))"

    #######################################################
    # START FFMPEG STREAMS (Managed by server.py on startup)
    #######################################################
    echo "Camera streams will be managed and initialized by server.py..."

    #######################################################
    # START CLEANUP LOOP (disk safety net)
    #######################################################

    cleanup_segments &
    echo $! > "$SCRIPT_DIR/cleanup.pid"

    #######################################################
    # START TAILSCALE FUNNEL (PERMANENT PUBLIC URL)
    #######################################################

    echo "Starting Tailscale Funnel..."

    tailscale funnel --bg 8080 >> "$LOG_DIR/tailscale.log" 2>&1

    FUNNEL_EXIT=$?

    if [ $FUNNEL_EXIT -ne 0 ]; then
        echo ""
        echo "  ⚠  Tailscale Funnel failed to start."
        echo "     Check: tailscale funnel status"
        echo "     Or run manually: tailscale funnel 8080"
        echo "     Log: $LOG_DIR/tailscale.log"
        PUBLIC_URL="(tailscale funnel not started - run manually)"
    else
        sleep 2
        PUBLIC_URL=$(get_tailscale_url)
        if [ -z "$PUBLIC_URL" ]; then
            PUBLIC_URL="(run: tailscale funnel status  to get your URL)"
        fi
    fi

    echo "$PUBLIC_URL" > "$HLS_DIR/public_url.txt"

    #######################################################
    # GET LOCAL IP
    #######################################################

    MY_IP=$(ipconfig getifaddr en0 2>/dev/null \
        || hostname -I 2>/dev/null | awk '{print $1}')

    #######################################################
    # PRINT INFO
    #######################################################

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  SYSTEM RUNNING"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Local  : http://$MY_IP:8080"
    echo "  Public : $PUBLIC_URL"
    echo "  (URL is permanent — same on every restart)"
    echo ""
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│  ACCESS LINKS                                           │"
    echo "│                                                         │"
    echo "│  DASHBOARD  : $PUBLIC_URL/index.html"
    echo "│                                                         │"
    echo "│  CAMERA 1   : $PUBLIC_URL/stream.html?s=0"
    echo "│  CAMERA 2   : $PUBLIC_URL/stream.html?s=1"
    echo "│  CAMERA 3   : $PUBLIC_URL/stream.html?s=2"
    echo "│                                                         │"
    echo "│  HLS (ThingsBoard):                                     │"
    # echo "│  STREAM 0   : $PUBLIC_URL/hls/stream0/playlist.m3u8"
    # echo "│  STREAM 1   : $PUBLIC_URL/hls/stream1/playlist.m3u8"
    # echo "│  STREAM 2   : $PUBLIC_URL/hls/stream2/playlist.m3u8"
    echo "│  STREAM 0   : $PUBLIC_URL/hls/stream0_raw/playlist.m3u8"
    echo "│  STREAM 1   : $PUBLIC_URL/hls/stream1_raw/playlist.m3u8"
    echo "│  STREAM 2   : $PUBLIC_URL/hls/stream2_raw/playlist.m3u8"
    echo "└─────────────────────────────────────────────────────────┘"
    echo ""

}

###########################################################
# STOP SYSTEM
###########################################################

stop() {

    echo "Stopping system..."

    pkill -9 -f ffmpeg          2>/dev/null
    pkill -9 -f server.py       2>/dev/null
    pkill -9 -f cleanup_segments 2>/dev/null

    # Kill cleanup loop by PID if saved
    if [ -f "$SCRIPT_DIR/cleanup.pid" ]; then
        kill "$(cat "$SCRIPT_DIR/cleanup.pid")" 2>/dev/null
    fi

    # Disable Tailscale Funnel cleanly
    tailscale funnel --bg=false 8080 2>/dev/null \
        && echo "Tailscale Funnel stopped." \
        || echo "Tailscale Funnel was not running."

    rm -f "$SCRIPT_DIR"/*.pid

    echo "Stopped."
}

###########################################################
# STATUS CHECK
###########################################################

status() {

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  SYSTEM STATUS"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if pgrep -f server.py > /dev/null; then
        echo "  Server    : running"
    else
        echo "  Server    : not running"
    fi

    local ffmpeg_count
    ffmpeg_count=$(pgrep -c -f ffmpeg 2>/dev/null || echo 0)
    echo "  Streams   : $ffmpeg_count ffmpeg process(es) running"

    echo ""
    echo "  Disk usage per stream:"
    for stream_dir in "$HLS_DIR"/stream*/; do
        if [ -d "$stream_dir" ]; then
            local seg_count
            seg_count=$(ls "$stream_dir"*.ts 2>/dev/null | wc -l)
            local disk_use
            disk_use=$(du -sh "$stream_dir" 2>/dev/null | cut -f1)
            echo "    $(basename "$stream_dir") : $seg_count segments, $disk_use"
        fi
    done

    echo ""
    echo "  Tailscale Funnel:"
    tailscale funnel status 2>/dev/null || echo "  (not running)"

    if [ -f "$HLS_DIR/public_url.txt" ]; then
        echo ""
        echo "  Public URL : $(cat "$HLS_DIR/public_url.txt")"
    fi

    echo ""
}

###########################################################
# MAIN
###########################################################

case "${1:-start}" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        stop
        sleep 2
        start
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        ;;
esac
