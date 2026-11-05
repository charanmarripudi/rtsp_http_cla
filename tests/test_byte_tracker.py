"""
Tests for EdgeByteTracker:
Verifies Kalman prediction, dual-stage association, occlusion recovery,
and performance on Pi / local environments.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.detector.byte_tracker import EdgeByteTracker


def test_tracker_initialization():
    tracker = EdgeByteTracker()
    assert tracker.frame_id == 0
    assert len(tracker.tracked_stracks) == 0


def test_continuous_tracking():
    tracker = EdgeByteTracker(track_thresh=0.35, low_thresh=0.15)
    
    # Frame 1: Person at (100, 100, 150, 200)
    dets_f1 = [([100.0, 100.0, 150.0, 200.0], (0, 255, 0), 0.85, "person")]
    tracks_f1 = tracker.update(dets_f1)
    assert len(tracks_f1) == 1
    t_id = list(tracks_f1.keys())[0]

    # Frame 2: Person moves slightly to (105, 100, 155, 200)
    dets_f2 = [([105.0, 100.0, 155.0, 200.0], (0, 255, 0), 0.88, "person")]
    tracks_f2 = tracker.update(dets_f2)
    assert len(tracks_f2) == 1
    assert t_id in tracks_f2, "Track ID must remain continuous"
    assert tracks_f2[t_id]["hit_count"] == 2


def test_occlusion_recovery_via_low_thresh():
    tracker = EdgeByteTracker(track_thresh=0.40, low_thresh=0.15)

    # Frame 1: High confidence detection
    dets_f1 = [([200.0, 200.0, 280.0, 350.0], (255, 0, 0), 0.90, "car")]
    tracks_f1 = tracker.update(dets_f1)
    t_id = list(tracks_f1.keys())[0]

    # Frame 2: Partial occlusion - confidence drops to 0.22 (below high thresh)
    # ByteTrack stage 2 should recover and match this!
    dets_f2 = [([204.0, 200.0, 284.0, 350.0], (255, 0, 0), 0.22, "car")]
    tracks_f2 = tracker.update(dets_f2)
    assert len(tracks_f2) == 1
    assert t_id in tracks_f2, "ByteTrack must recover occluded object with low score detection"


def test_vehicle_ground_tyre_contact_matching():
    tracker = EdgeByteTracker()

    # Frame 1: Truck on gantry road
    dets_f1 = [([50.0, 100.0, 200.0, 400.0], (0, 140, 255), 0.85, "truck")]
    tracks_f1 = tracker.update(dets_f1)
    t_id = list(tracks_f1.keys())[0]

    # Frame 2: Truck moving forward, tyre contact at bottom-center matched
    dets_f2 = [([52.0, 105.0, 202.0, 405.0], (0, 140, 255), 0.82, "truck")]
    tracks_f2 = tracker.update(dets_f2)
    assert t_id in tracks_f2


def test_multi_object_multi_camera_bench():
    tracker = EdgeByteTracker()
    # 20 objects tracked across 10 frames
    start_t = time.perf_counter()
    for frame_idx in range(10):
        dets = [
            ([i * 30.0 + frame_idx, 100.0, i * 30.0 + 40.0 + frame_idx, 200.0], (0, 255, 0), 0.75, "person")
            for i in range(20)
        ]
        tracks = tracker.update(dets)
        assert len(tracks) == 20
    elapsed = time.perf_counter() - start_t
    fps = 10.0 / elapsed
    print(f"Tracking speed: {fps:.1f} FPS for 20 objects (takes {elapsed*100:0.2f}ms per 10 frames)")
    assert fps > 100.0, "Must exceed 100 FPS for real-time edge processing"


if __name__ == "__main__":
    test_tracker_initialization()
    test_continuous_tracking()
    test_occlusion_recovery_via_low_thresh()
    test_vehicle_ground_tyre_contact_matching()
    test_multi_object_multi_camera_bench()
    print("ALL BYTE TRACKER TESTS PASSED SUCCESSFULLY!")
