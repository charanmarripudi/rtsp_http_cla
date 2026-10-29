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

    echo "$(date) Generated streams.json" \
        >> "$LOG_DIR/system.log"
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

        rm -f "$stream_dir"/*.ts
        rm -f "$stream_dir"/*.m3u8

        echo "$(date '+%H:%M:%S') stream${idx} STARTING" \
            >> "$LOG_DIR/gst_${idx}.log"

        ffmpeg \
        -hide_banner \
        -loglevel warning \
        -rtsp_transport tcp \
        -timeout 10000000 \
        -i "$rtsp_url" \
        -an \
        -c:v libx264 \
        -preset ultrafast \
        -tune zerolatency \
        -pix_fmt yuv420p \
        -g 30 \
        -keyint_min 30 \
        -sc_threshold 0 \
        -f hls \
        -hls_time 2 \
        -hls_list_size 10 \
        -hls_flags append_list+independent_segments \
        -hls_allow_cache 0 \
        -hls_segment_filename "$stream_dir/segment_%05d.ts" \
        "$stream_dir/playlist.m3u8" \
        >> "$LOG_DIR/gst_${idx}.log" 2>&1

        echo "$(date '+%H:%M:%S') stream${idx} CRASHED - reconnecting in 5s" \
            >> "$LOG_DIR/gst_${idx}.log"

        sleep 5
    done
}

###########################################################
# START SYSTEM
###########################################################

start() {

    generate_streams_json

    #######################################################
    # START PYTHON SERVER
    #######################################################

    nohup python3 "$SCRIPT_DIR/server.py" \
        >> "$LOG_DIR/server.log" 2>&1 &

    echo $! > "$SCRIPT_DIR/server.pid"

    sleep 2

    #######################################################
    # START STREAMS
    #######################################################

    local idx=0

    while IFS= read -r rtsp_url; do

        start_stream "$idx" "$rtsp_url" &

        echo $! > "$SCRIPT_DIR/stream${idx}.pid"

        echo "Started stream $idx"

        idx=$((idx+1))

    done < <(read_streams)
}

###########################################################
# STOP SYSTEM
###########################################################

stop() {

    echo "Stopping..."

    pkill -9 -f ffmpeg
    pkill -9 -f server.py
    pkill -9 -f cloudflared
    pkill -9 -f run_background.sh
    pkill -9 -f launch.sh

    rm -f "$SCRIPT_DIR"/*.pid

    echo "Stopped"
}

###########################################################
# MAIN
###########################################################

case "$1" in
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
    *)
        echo "Usage: $0 {start|stop|restart}"
        ;;
esac




