import os
import sys
import math
import time

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.detector.detector_worker import classify_pose_activity

def run_tests():
    enabled = ["cooking", "crowd", "smoking_gesture", "fighting_posture", "lying_down", "sitting", "loitering"]

    # Test 1: User holding phone in hands at desk (Image 6 & Image 4)
    # Solitary person in frame holding phone with both hands: MUST NEVER trigger fighting_posture!
    trk1 = {'fight_start_t': time.time() - 2.0}
    kp1 = [[100, 50, 0.9]] + [[0,0,0]]*4 + [
        [70, 90, 0.9], [130, 90, 0.9],
        [60, 140, 0.8], [140, 140, 0.8],
        [85, 95, 0.85], [115, 95, 0.85],  # Wrists together holding phone near chest
        [80, 200, 0.8], [120, 200, 0.8],
        [80, 280, 0.8], [120, 280, 0.8],
        [80, 350, 0.8], [120, 350, 0.8]
    ]
    box1 = [50, 40, 150, 360]
    act1, _, _ = classify_pose_activity(trk1, kp1, box1, 5.0, enabled, all_persons=[box1])
    assert act1 is None, f"Test 1 FAILED: Expected None (Holding Phone), got {act1}"
    print("Test 1 PASSED: Solitary user holding phone at desk -> None (NOT fighting!)")

    # Test 2: User portrait upright
    trk2 = {}
    kp2 = [[100, 60, 0.9]] + [[0,0,0]]*4 + [
        [70, 100, 0.9], [130, 100, 0.9],
        [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [0,0,0], [0,0,0], [0,0,0], [0,0,0], [0,0,0], [0,0,0]
    ]
    box2 = [50, 50, 150, 200]
    act2, _, _ = classify_pose_activity(trk2, kp2, box2, 5.0, enabled)
    assert act2 is None, f"Test 2 FAILED: Expected None, got {act2}"
    print("Test 2 PASSED: User portrait upright -> None")

    # Test 3: Person lying in mud (low aspect ratio, horizontal torso)
    trk3 = {'lie_start_t': time.time() - 3.0}
    box3 = [50, 280, 200, 330]
    act3, conf3, sub3 = classify_pose_activity(trk3, None, box3, 5.0, enabled)
    assert act3 == 'lying_down', f"Test 3 FAILED: Expected lying_down, got {act3}"
    print(f"Test 3 PASSED: Person in mud -> {act3}")

    # Test 4: Man sitting cross-legged
    trk4 = {'sit_start_t': time.time() - 3.0}
    kp4 = [[100, 60, 0.9]] + [[0,0,0]]*4 + [
        [70, 95, 0.9], [130, 95, 0.9],
        [55, 130, 0.8], [145, 130, 0.8],
        [70, 155, 0.85], [130, 155, 0.85],
        [80, 165, 0.85], [120, 165, 0.85],
        [40, 180, 0.85], [160, 180, 0.85],
        [70, 190, 0.8], [130, 190, 0.8]
    ]
    box4 = [30, 50, 170, 200]
    act4, conf4, sub4 = classify_pose_activity(trk4, kp4, box4, 5.0, enabled)
    assert act4 == 'sitting', f"Test 4 FAILED: Expected sitting, got {act4}"
    print(f"Test 4 PASSED: Man sitting cross-legged -> {act4}")

    # Test 5: Boy pulling jacket / adjusting shirt (Image 5 middle person)
    trk5 = {'smoke_start_t': time.time() - 5.0}
    kp5 = [[100, 70, 0.9]] + [[0,0,0]]*4 + [
        [65, 105, 0.9], [135, 105, 0.9],
        [40, 125, 0.8], [160, 125, 0.8],
        [60, 130, 0.85], [140, 130, 0.85],
        [80, 180, 0.8], [120, 180, 0.8],
        [80, 240, 0.8], [120, 240, 0.8],
        [80, 300, 0.8], [120, 300, 0.8]
    ]
    box5 = [35, 50, 165, 310]
    act5, _, _ = classify_pose_activity(trk5, kp5, box5, 5.0, enabled)
    assert act5 is None, f"Test 5 FAILED: Expected None, got {act5}"
    print("Test 5 PASSED: Boy pulling jacket -> None")

    # Test 6: Karate / Martial Arts Combat between 2 combatants (Image 4)
    trk6 = {'fight_start_t': time.time() - 2.0}
    kp6 = [[90, 80, 0.9]] + [[0,0,0]]*4 + [
        [70, 110, 0.85], [110, 110, 0.85],
        [60, 130, 0.85], [120, 130, 0.85],
        [75, 100, 0.85], [105, 100, 0.85],
        [80, 170, 0.8], [100, 170, 0.8],
        [75, 230, 0.8], [105, 230, 0.8],
        [70, 290, 0.8], [110, 290, 0.8]
    ]
    box6_a = [50, 60, 130, 300]
    box6_b = [140, 60, 220, 300]
    act6, conf6, sub6 = classify_pose_activity(trk6, kp6, box6_a, 5.0, enabled, all_persons=[box6_a, box6_b])
    assert act6 == 'fighting_posture', f"Test 6 FAILED: Expected fighting_posture, got {act6}"
    print(f"Test 6 PASSED: Karate sparring fighters -> {act6}")

    # Test 7: Real smoking gesture (wrist directly at lips, elbow below wrist, acute angle)
    trk7 = {'smoke_start_t': time.time() - 3.0}
    kp7 = [[100, 72, 0.9]] + [[0,0,0]]*4 + [
        [70, 110, 0.85], [130, 110, 0.85],
        [60, 160, 0.8], [140, 135, 0.8],
        [60, 200, 0.8], [103, 76, 0.9],
        [80, 180, 0.8], [120, 180, 0.8],
        [80, 240, 0.8], [120, 240, 0.8],
        [80, 300, 0.8], [120, 300, 0.8]
    ]
    box7 = [50, 50, 150, 310]
    act7, conf7, sub7 = classify_pose_activity(trk7, kp7, box7, 5.0, enabled)
    assert act7 == 'smoking_gesture', f"Test 7 FAILED: Expected smoking_gesture, got {act7}"
    print(f"Test 7 PASSED: Real smoking gesture -> {act7}")

    # Test 8: STANDING person with legs spread (Image 7 / Maroon shirt boy)
    trk8 = {'sit_start_t': time.time() - 5.0}
    kp8 = [
        [262.0, 108.6, 0.9], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [251.8, 135.4, 0.9], [210.6, 132.1, 1.0],
        [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [249.8, 226.8, 1.0], [219.1, 226.5, 1.0],
        [259.5, 290.9, 1.0], [204.8, 291.9, 1.0],
        [266.8, 356.3, 1.0], [188.1, 363.1, 1.0]
    ]
    box8 = [172.5, 78.7, 291.0, 373.7]
    act8, _, _ = classify_pose_activity(trk8, kp8, box8, 5.0, enabled)
    assert act8 is None, f"Test 8 FAILED: Expected None (Standing), got {act8}"
    print("Test 8 PASSED: Standing boy with legs spread (Image 7) -> None (NOT sitting!)")

    # Test 9: STANDING person upright (Image 9 / Black jacket)
    trk9 = {'sit_start_t': time.time() - 5.0}
    kp9 = [
        [448.5, 335.4, 0.9], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [467.1, 355.9, 1.0], [435.1, 358.2, 1.0],
        [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [466.9, 414.4, 1.0], [443.1, 415.5, 1.0],
        [482.8, 458.7, 1.0], [432.7, 459.1, 1.0],
        [497.0, 503.6, 1.0], [426.5, 503.5, 1.0]
    ]
    box9 = [420.3, 317.9, 510.2, 519.9]
    act9, _, _ = classify_pose_activity(trk9, kp9, box9, 5.0, enabled)
    assert act9 is None, f"Test 9 FAILED: Expected None (Standing), got {act9}"
    print("Test 9 PASSED: Standing man in black jacket (Image 9) -> None (NOT sitting!)")

    # Test 10: Prone person lying down on ground with horizontal torso (stationary >= 3.0s)
    trk10 = {'lie_start_t': time.time() - 3.5}
    kp10 = [
        [150.0, 300.0, 0.8], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [180.0, 305.0, 0.8], [180.0, 315.0, 0.8], # shoulders
        [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [250.0, 308.0, 0.8], [250.0, 318.0, 0.8], # hips (dx = 70, dy = 3)
        [300.0, 310.0, 0.8], [300.0, 320.0, 0.8],
        [350.0, 312.0, 0.8], [350.0, 322.0, 0.8]
    ]
    box10 = [140.0, 290.0, 360.0, 335.0]
    act10, conf10, sub10 = classify_pose_activity(trk10, kp10, box10, 5.0, enabled)
    assert act10 == 'lying_down', f"Test 10 FAILED: Expected lying_down, got {act10}"
    print(f"Test 10 PASSED: Horizontal prone person -> {act10}")

    # Test 11: Mid-air acrobatic backflip / somersault (Image 8)
    # Moving dynamically across frames (high centroid displacement): MUST NOT trigger lying_down!
    trk11 = {
        'lie_start_t': time.time() - 0.5,
        'last_lying_cen': (150.0, 100.0)
    }
    box11 = [180.0, 180.0, 320.0, 260.0]
    act11, _, _ = classify_pose_activity(trk11, kp10, box11, 0.5, enabled)
    assert act11 is None, f"Test 11 FAILED: Expected None (Mid-air flip), got {act11}"
    print("Test 11 PASSED: Mid-air backflip / jumping flip (Image 8) -> None (NOT lying_down!)")

    # Test 12: Crowd gathering in parking bay (3+ distinct persons clustering together)
    trk12 = {'crowd_start_t': time.time() - 4.0}
    b12_a = [100.0, 100.0, 160.0, 260.0]
    b12_b = [180.0, 100.0, 240.0, 260.0]
    b12_c = [260.0, 100.0, 320.0, 260.0]
    act12, conf12, sub12 = classify_pose_activity(trk12, None, b12_a, 5.0, enabled, all_persons=[b12_a, b12_b, b12_c])
    assert act12 == 'crowd', f"Test 12 FAILED: Expected crowd, got {act12}"
    print(f"Test 12 PASSED: 3 persons gathering in bay -> {act12} ({sub12})")

    # Test 13: Unauthorized Cooking (deep crouched tending stance over ground/stove)
    trk13 = {'cook_start_t': time.time() - 2.5}
    kp13 = [
        [100, 120, 0.9], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [80, 140, 0.9], [120, 140, 0.9],   # shoulders
        [70, 175, 0.8], [130, 175, 0.8],   # elbows
        [90, 215, 0.85], [110, 215, 0.85], # wrists near ground/stove
        [85, 180, 0.9], [115, 180, 0.9],   # hips (low)
        [60, 200, 0.85], [140, 200, 0.85], # knees flexed
        [75, 220, 0.8], [125, 220, 0.8]    # ankles
    ]
    box13 = [55, 110, 145, 230]
    act13, conf13, sub13 = classify_pose_activity(trk13, kp13, box13, 5.0, enabled)
    assert act13 == 'cooking', f"Test 13 FAILED: Expected cooking, got {act13}"
    print(f"Test 13 PASSED: Crouched cooking posture -> {act13} ({sub13})")

    # Test 14: Unauthorized Cooking with Companion Fire / Smoke Detection
    trk14 = {'cook_start_t': time.time() - 2.5}
    act14, conf14, sub14 = classify_pose_activity(trk14, None, box1, 5.0, enabled, has_fire_smoke=True)
    assert act14 == 'cooking', f"Test 14 FAILED: Expected cooking, got {act14}"
    print(f"Test 14 PASSED: Cooking with open flame/smoke companion -> {act14} ({sub14})")

    # Test 15: User sitting in office chair / wide bust crop (Screenshot 2) -> None (NOT lying_down!)
    trk15 = {'lie_start_t': time.time() - 5.0}
    kp15 = [
        [150, 60, 0.9], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [80, 110, 0.9], [220, 110, 0.9], # wide shoulders
        [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [0,0,0], [0,0,0], [0,0,0], [0,0,0], [0,0,0], [0,0,0] # no hips/legs visible
    ]
    box15 = [20, 40, 280, 170] # aspect ratio 260/130 = 2.0
    act15, _, _ = classify_pose_activity(trk15, kp15, box15, 5.0, enabled)
    assert act15 is None, f"Test 15 FAILED: Expected None for desk user, got {act15}"
    print(f"Test 15 PASSED: Desk user in office chair (Screenshot 2) -> None (NOT lying_down!)")

    # Test 16: Seated contemplation with hand resting under chin / elbow on knee (Screenshot 1)
    trk16 = {'sit_start_t': time.time() - 3.0, 'smoke_start_t': time.time() - 3.0}
    kp16 = [
        [100, 60, 0.9], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [70, 95, 0.9], [130, 95, 0.9],      # shoulders
        [60, 140, 0.8], [120, 140, 0.8],    # elbows resting on knees
        [95, 75, 0.85], [130, 140, 0.85],   # left wrist under chin, right wrist on lap
        [75, 150, 0.85], [125, 150, 0.85],  # hips
        [60, 145, 0.85], [140, 145, 0.85],  # knees raised
        [70, 185, 0.8], [130, 185, 0.8]     # ankles
    ]
    box16 = [40, 50, 160, 195]
    act16, conf16, sub16 = classify_pose_activity(trk16, kp16, box16, 5.0, enabled)
    assert act16 == 'sitting', f"Test 16 FAILED: Expected sitting (NOT smoking), got {act16}"
    print(f"Test 16 PASSED: Seated thinker pose (Screenshot 1) -> {act16} (NOT smoking_gesture!)")

    # Test 17: Martial arts high kick / karate sparring (Screenshot 3)
    trk17 = {'fight_start_t': time.time() - 2.0}
    kp17 = [
        [100, 80, 0.9], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [80, 110, 0.9], [120, 110, 0.9],
        [70, 130, 0.8], [130, 130, 0.8],
        [85, 105, 0.8], [115, 105, 0.8],    # guard hands up
        [85, 160, 0.85], [115, 160, 0.85],  # hips
        [70, 180, 0.85], [150, 150, 0.85],  # right leg kicked up
        [75, 230, 0.8], [180, 140, 0.8]     # right foot at chest/hip level
    ]
    box17_a = [60, 60, 190, 240]
    box17_b = [170, 60, 260, 240] # adjacent sparring partner
    act17, conf17, sub17 = classify_pose_activity(trk17, kp17, box17_a, 5.0, enabled, all_persons=[box17_a, box17_b])
    assert act17 == 'fighting_posture', f"Test 17 FAILED: Expected fighting_posture, got {act17}"
    print(f"Test 17 PASSED: Martial arts high kick / karate (Screenshot 3) -> {act17}")

    # Test 18: Overlapping user torso + phone screen (Screenshots 8, 9, 10)
    # Merges overlapping boxes and rejects false crowd alert
    trk18 = {'crowd_start_t': time.time() - 3.0}
    b18_user = [10, 50, 300, 320]
    b18_phone = [100, 120, 200, 310] # phone held in front of chest (heavily overlapping)
    b18_arm = [20, 100, 140, 300]   # arm overlapping torso
    act18, conf18, sub18 = classify_pose_activity(trk18, None, b18_user, 5.0, enabled, all_persons=[b18_user, b18_phone, b18_arm])
    assert act18 is None, f"Test 18 FAILED: Expected None (Deduplicated), got {act18}"
    print(f"Test 18 PASSED: Overlapping phone screen + user (Screenshots 8, 9, 10) -> None (NOT crowd!)")

    # Test 19: Walking actor outdoors in jacket/shirt with ankles cropped by frame bottom (Image 2)
    # Must NEVER be detected as cooking!
    trk19 = {'cook_start_t': time.time() - 3.0}
    kp19 = [
        [150, 60, 0.95],                     # nose
        [155, 55, 0.9], [145, 55, 0.9], [165, 58, 0.8], [135, 58, 0.8], # eyes/ears
        [110, 105, 0.95], [190, 105, 0.95], # shoulders (wide upright)
        [95, 160, 0.85], [205, 160, 0.85],  # elbows
        [85, 220, 0.85], [215, 220, 0.85],  # wrists hanging on left & right sides (separated)
        [125, 210, 0.9], [175, 210, 0.9],   # hips (upright torso: hip_y - sh_y = 105px)
        [120, 270, 0.7], [180, 270, 0.7],   # knees
        [0, 0, 0.0], [0, 0, 0.0]            # ankles cropped by frame edge
    ]
    box19 = [80, 40, 220, 290] # bh = 250, bw = 140, aspect_ratio = 0.56
    act19, conf19, sub19 = classify_pose_activity(trk19, kp19, box19, 5.0, enabled, is_video_test=True, has_fire_smoke=False)
    assert act19 is None, f"Test 19 FAILED: Walking actor falsely detected as {act19}!"
    print(f"Test 19 PASSED: Walking actor outdoors (Image 2) -> None (NOT cooking!)")

    # Test 20: Opposite Safety Class Matching across all positive/negative pairs
    from detector_worker import is_opposite_class, is_negative_violation_class
    test_opposite_pairs = [
        ("Fire_Extinguisher", "no-Fire_Extinguisher", True),
        ("Sand-Bucket", "no-Sand-Bucket", True),
        ("wheel-choke", "no-wheel-choke", True),
        ("Spark_Arrestor", "no-Spark_Arrestor", True),
        ("fire_hose", "no-fire_hose", True),
        ("firehose_box", "no-fire_hose", True),
        ("reflective_tape_floor", "no-tape", True),
        ("reflective_tape_vehicles", "no-reflective_tape", True),
        ("traffic_tape_cone", "no-traffic_tape", True),
        ("Hardhat", "NO-Hardhat", True),
        ("Safety Vest", "NO-Safety Vest", True),
        ("Mask", "NO-Mask", True),
        ("Fire_Extinguisher", "no-Sand-Bucket", False),
        ("person", "no-Fire_Extinguisher", False),
        ("car", "no-Spark_Arrestor", False),
    ]
    for p1, p2, expected in test_opposite_pairs:
        res = is_opposite_class(p1, p2)
        assert res == expected, f"is_opposite_class({p1}, {p2}) expected {expected}, got {res}"
    print(f"Test 20 PASSED: Opposite class matching validated for all 15 safety pairs!")

    # Test 21: Negative Violation vs Positive Compliant Equipment Classification
    assert not is_negative_violation_class("Spark_Arrestor")
    assert not is_negative_violation_class("Fire_Extinguisher")
    assert not is_negative_violation_class("Sand-Bucket")
    assert not is_negative_violation_class("wheel-choke")
    assert not is_negative_violation_class("fire_hose")
    assert not is_negative_violation_class("firehose_box")
    assert not is_negative_violation_class("reflective_tape_floor")
    assert is_negative_violation_class("no-Spark_Arrestor")
    assert is_negative_violation_class("no-Fire_Extinguisher")
    assert is_negative_violation_class("no-Sand-Bucket")
    assert is_negative_violation_class("no-wheel-choke")
    assert is_negative_violation_class("no-fire_hose")
    assert is_negative_violation_class("no-tape")
    print(f"Test 21 PASSED: Positive vs negative class violation gating verified 100%!")

    print("\nALL 21 PRODUCTION TESTS PASSED WITH 100% ACCURACY!")

if __name__ == '__main__':
    run_tests()
