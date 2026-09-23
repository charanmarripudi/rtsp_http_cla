
import os
import sys
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# Copy of start_raw_stream
def start_raw_stream(i, u):
    cid = str(i)
    print(f"Attempting to start stream {cid} with URL {u}")
    # Remove old stream directory if exists and clean it
    sd = os.path.join(os.path.join(BASE_DIR, "hls"), f"stream{i}_raw")
    print(f"Stream dir: {sd}")
    os.makedirs(sd, exist_ok=True)
    for f in os.listdir(sd):
        try:
            os.remove(os.path.join(sd, f))
        except Exception:
            pass
    
    # Start FFmpeg
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-y",
        "-rtsp_transport", "tcp",
        "-probesize", "1M",
        "-analyzeduration", "1M",
        "-i", u,
        "-an",
        "-vf", "scale=854:-2",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-b:v", "800k",
        "-maxrate", "1M",
        "-bufsize", "2M",
        "-threads", "2",
        "-pix_fmt", "yuv420p",
        "-g", "60",
        "-keyint_min", "60",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "6",
        "-hls_flags", "delete_segments+independent_segments+discont_start+omit_endlist+temp_file",
        "-hls_start_number_source", "datetime",
        "-hls_segment_filename", os.path.join(sd, "segment_%d.ts"),
        os.path.join(sd, "playlist.m3u8")
    ]
    print(f"Running FFmpeg command: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd)
    print(f"FFmpeg PID: {proc.pid}")
    return proc

# Let's try to start stream 1 manually
stream_url = "rtsp://admin:Algo_1212@192.168.96.231:554/stream1"
proc = start_raw_stream(1, stream_url)

import time
time.sleep(5)

# Check if it's running
if proc.poll() is None:
    print("Stream 1 is running fine!")
else:
    print(f"Stream 1 exited with code: {proc.poll()}")

sd = os.path.join(os.path.join(BASE_DIR, "hls"), "stream1_raw")
if os.path.exists(sd):
    print(f"Stream 1 dir contents: {os.listdir(sd)}")
