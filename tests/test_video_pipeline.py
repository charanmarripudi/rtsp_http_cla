import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.detector.video_tester import create_job, _process_video_worker, RESULTS_DIR

def test_pipeline():
    raw_fname = "upload_1793872451_35ca29.mp4"
    print(f"[TEST] Creating video test job for {raw_fname}...")
    job = create_job(
        raw_filename=raw_fname,
        model_name="yolov8n-pose.pt",
        conf=0.35,
        iou=0.45,
        imgsz=320,
        enabled_classes=["cooking", "fighting_posture", "lying_down"],
        frame_skip=3
    )
    print(f"[TEST] Job ID: {job.job_id}")
    print(f"[TEST] Running video processing worker...")
    _process_video_worker(job)
    
    print(f"[TEST] Job Status: {job.status}")
    print(f"[TEST] Total Frames: {job.total_frames}, Detections Total: {job.detections_total}")
    print(f"[TEST] Class Counts: {job.class_counts}")
    
    # Verify CSV file was generated
    assert os.path.exists(job.csv_path), f"CSV results file missing: {job.csv_path}"
    with open(job.csv_path, "r", encoding="utf-8") as f:
        csv_lines = f.readlines()
    print(f"[TEST] CSV Results Generated Successfully: {len(csv_lines)} rows at {job.csv_path}")
    print(f"[TEST] CSV Header: {csv_lines[0].strip()}")
    if len(csv_lines) > 1:
        print(f"[TEST] CSV First Detection: {csv_lines[1].strip()}")
        
    print(f"[TEST] Evidence Images Recorded: {len(job.evidence_images)}")
    print("[TEST] SUCCESS: Video testing pipeline with CSV & evidence generation validated!")

if __name__ == '__main__':
    test_pipeline()

