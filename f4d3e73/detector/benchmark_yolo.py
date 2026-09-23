import os
import sys
import time
import math
import statistics
import numpy as np

from pathlib import Path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import torch
from ultralytics import YOLO

def run_benchmark(model_path, imgsz=640, num_threads=None, warmup=5, iters=10, sample_image_path=None):
    if num_threads is not None:
        torch.set_num_threads(num_threads)
        if hasattr(torch, "set_num_interop_threads"):
            try:
                torch.set_num_interop_threads(1)
            except Exception:
                pass

    actual_threads = torch.get_num_threads()
    device = "cpu"

    print("=" * 65)
    print("        YOLO PERFORMANCE BENCHMARK ON RASPBERRY PI 4")
    print("=" * 65)
    print(f"PyTorch Version       : {torch.__version__}")
    print(f"Device                : {device.upper()}")
    print(f"Active PyTorch Threads: {actual_threads}")
    print(f"Model Path            : {model_path}")
    print(f"Image Size (imgsz)    : {imgsz}x{imgsz}")
    print(f"Warmup Iterations     : {warmup}")
    print(f"Measurement Iterations: {iters}")
    print("=" * 65)

    # 1. Load Model
    t_load_start = time.time()
    model = YOLO(model_path)
    load_ms = (time.time() - t_load_start) * 1000
    print(f"[1/4] Model loaded into memory in {load_ms:.1f}ms")
    print(f"      Classes ({len(model.names)}): {model.names}")

    # 2. Prepare Test Frame (Use real image if available, else synthetic 720p HD frame)
    if sample_image_path and os.path.exists(sample_image_path):
        import cv2
        frame = cv2.imread(sample_image_path)
        print(f"[2/4] Using sample image from: {sample_image_path} ({frame.shape[1]}x{frame.shape[0]})")
    else:
        frame = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
        print("[2/4] Using standard 1280x720 RGB test frame")

    # 3. Warmup Iterations
    print(f"\n[3/4] Running {warmup} warmup iterations...")
    for w in range(warmup):
        with torch.inference_mode():
            _ = model.predict(source=frame, imgsz=imgsz, conf=0.25, verbose=False)
        print(f"   Warmup {w+1}/{warmup} completed.")

    # 4. Measurement Iterations (Decomposed into Preprocessing, Inference, Postprocessing)
    print(f"\n[4/4] Running {iters} measurement iterations...\n")
    
    preprocess_times = []
    inference_times = []
    postprocess_times = []
    total_times = []

    for i in range(iters):
        t_total_start = time.time()

        with torch.inference_mode():
            # Run predict and extract internal UltraLytics timing profiler
            results = model.predict(source=frame, imgsz=imgsz, conf=0.25, verbose=False)
        
        t_total_ms = (time.time() - t_total_start) * 1000
        
        # Extract Ultralytics speed dictionary (preprocess, inference, postprocess in ms)
        speed = results[0].speed
        pre_ms = speed.get('preprocess', 0.0)
        inf_ms = speed.get('inference', 0.0)
        post_ms = speed.get('postprocess', 0.0)
        
        # If speed dict is available, use it; otherwise fallback to wall-clock
        preprocess_times.append(pre_ms)
        inference_times.append(inf_ms)
        postprocess_times.append(post_ms)
        total_times.append(t_total_ms)

        print(f" Iteration {i+1:2d}/{iters:2d} -> Total: {t_total_ms:7.1f}ms | Preprocess: {pre_ms:5.1f}ms | Inference: {inf_ms:7.1f}ms | Postprocess: {post_ms:5.1f}ms | Detected: {len(results[0].boxes)} boxes")

    def calc_stats(arr):
        if not arr: return 0.0, 0.0, 0.0, 0.0
        avg = statistics.mean(arr)
        min_v = min(arr)
        max_v = max(arr)
        std_v = statistics.stdev(arr) if len(arr) > 1 else 0.0
        return avg, min_v, max_v, std_v

    avg_tot, min_tot, max_tot, std_tot = calc_stats(total_times)
    avg_pre, min_pre, max_pre, std_pre = calc_stats(preprocess_times)
    avg_inf, min_inf, max_inf, std_inf = calc_stats(inference_times)
    avg_post, min_post, max_post, std_post = calc_stats(postprocess_times)

    print("\n" + "=" * 65)
    print("                    BENCHMARK RESULTS SUMMARY")
    print("=" * 65)
    print(f"{'Metric':<18} | {'Average':<10} | {'Min':<8} | {'Max':<8} | {'StdDev':<8}")
    print("-" * 65)
    print(f"{'Preprocessing':<18} | {avg_pre:8.1f}ms | {min_pre:6.1f}ms | {max_pre:6.1f}ms | {std_pre:6.1f}ms")
    print(f"{'Pure Inference':<18} | {avg_inf:8.1f}ms | {min_inf:6.1f}ms | {max_inf:6.1f}ms | {std_inf:6.1f}ms")
    print(f"{'Postprocessing':<18} | {avg_post:8.1f}ms | {min_post:6.1f}ms | {max_post:6.1f}ms | {std_post:6.1f}ms")
    print(f"{'TOTAL YOLO TIME':<18} | {avg_tot:8.1f}ms | {min_tot:6.1f}ms | {max_tot:6.1f}ms | {std_tot:6.1f}ms")
    print("=" * 65)
    print(f"Effective Standalone Inference Rate: {1000.0 / max(1.0, avg_tot):.2f} FPS")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Standalone YOLO Benchmark for Raspberry Pi 4 CPU")
    parser.add_argument("--model", type=str, default="models/hansung_ppe_violations.pt", help="Path to YOLO .pt model")
    parser.add_argument("--imgsz", type=int, default=640, help="Image inference size (e.g. 640, 512, 480, 416)")
    parser.add_argument("--threads", type=int, default=None, help="PyTorch CPU threads (e.g. 1, 2, 3, 4)")
    parser.add_argument("--warmup", type=int, default=5, help="Number of warmup iterations")
    parser.add_argument("--iters", type=int, default=10, help="Number of measurement iterations")
    parser.add_argument("--image", type=str, default=None, help="Optional path to test image")
    parser.add_argument("--sweep-threads", action="store_true", help="Automatically sweep PyTorch threads 1, 2, 3, 4")
    parser.add_argument("--sweep-imgsz", action="store_true", help="Automatically sweep imgsz 640, 576, 512, 480, 448, 416")

    args = parser.parse_args()

    m_path = args.model
    if not os.path.isabs(m_path):
        m_path = os.path.join(str(BASE_DIR), m_path)

    if not os.path.exists(m_path):
        print(f"Error: Model not found at {m_path}")
        sys.exit(1)

    if args.sweep_threads:
        print("\n>>> STARTING PYTORCH THREAD SWEEP (1 vs 2 vs 3 vs 4 threads) <<<\n")
        for th in [1, 2, 3, 4]:
            print(f"\n--- TESTING WITH {th} THREAD(S) (imgsz={args.imgsz}) ---")
            run_benchmark(m_path, imgsz=args.imgsz, num_threads=th, warmup=3, iters=5, sample_image_path=args.image)
    elif args.sweep_imgsz:
        print("\n>>> STARTING IMGSZ RESOLUTION SWEEP (640 -> 576 -> 512 -> 480 -> 448 -> 416) <<<\n")
        for s in [640, 576, 512, 480, 448, 416]:
            print(f"\n--- TESTING WITH IMGSZ={s} ---")
            run_benchmark(m_path, imgsz=s, num_threads=args.threads, warmup=3, iters=5, sample_image_path=args.image)
    else:
        run_benchmark(m_path, imgsz=args.imgsz, num_threads=args.threads, warmup=args.warmup, iters=args.iters, sample_image_path=args.image)
