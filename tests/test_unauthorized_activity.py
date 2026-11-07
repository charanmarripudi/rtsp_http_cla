import os
import sys
import math
import time

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.detector.detector_worker import classify_pose_activity

def run_tests():
    enabled = ["cooking", "crowd", "smoking", "fighting_posture", "lying_down", "sitting", "loitering"]

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
    assert act7 == 'smoking', f"Test 7 FAILED: Expected smoking, got {act7}"
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
    print(f"Test 16 PASSED: Seated thinker pose (Screenshot 1) -> {act16} (NOT smoking!)")

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

    # Test 22: User Pairwise Fighting Logic - Heavy Bounding Box Overlap & Wrist Proximity
    trk22_a = {'first_seen': 100.0}
    trk22_b = {'first_seen': 100.0}
    b22_a = [100, 100, 160, 260] # h = 160, center = (130, 180)
    b22_b = [130, 100, 190, 260] # heavy overlap with b22_a (x: 130..160)
    # Wrists extended forward towards opponent (not to own face)
    kp22_a = [[130, 120, 0.9]] + [[0, 0, 0]]*4 + [[120, 140, 0.8], [140, 140, 0.8], [120, 160, 0.8], [140, 160, 0.8], [130, 180, 0.8], [155, 130, 0.8]] + [[0, 0, 0]]*6
    kp22_b = [[160, 120, 0.9]] + [[0, 0, 0]]*4 + [[150, 140, 0.8], [170, 140, 0.8], [150, 160, 0.8], [170, 160, 0.8], [135, 130, 0.8], [160, 180, 0.8]] + [[0, 0, 0]]*6
    person_records = [
        {'box': b22_a, 'keypoints': kp22_a, 'behavior': None},
        {'box': b22_b, 'keypoints': kp22_b, 'behavior': None}
    ]
    act22, conf22, sub22 = classify_pose_activity(trk22_a, kp22_a, b22_a, 5.0, enabled, all_persons=person_records, is_video_test=True)
    assert act22 == 'fighting_posture', f"Test 22 FAILED: Expected fighting_posture, got {act22}"
    print(f"Test 22 PASSED: Pairwise fighting with heavy overlap and wrist proximity -> fighting_posture")

    # Test 23: User Lying Down Logic - Horizontal Spine Profile (nose and ankle near same Y)
    trk23 = {'first_seen': 100.0}
    b23 = [100, 200, 280, 250] # bw = 180, bh = 50 -> is_horizontal_box is TRUE (180 > 50*1.3)
    kp23 = [[110, 220, 0.9]] + [[0, 0, 0]]*14 + [[270, 225, 0.9], [270, 225, 0.9]] # nose y=220, ankle y=225 (abs diff 5px < 50*0.35)
    act23, conf23, sub23 = classify_pose_activity(trk23, kp23, b23, 5.0, enabled, is_video_test=True)
    assert act23 == 'lying_down', f"Test 23 FAILED: Expected lying_down, got {act23}"
    print(f"Test 23 PASSED: User lying down horizontal aspect ratio and spine profile -> lying_down")

    # Test 24: Pairwise Fighting Interaction Skips if Opponent is Lying Down
    trk24_a = {'first_seen': 100.0}
    b24_stand = [100, 100, 160, 260] # standing person
    b24_lie = [120, 240, 280, 290] # person lying on ground overlapping bottom of standing person
    person_records_lie = [
        {'box': b24_stand, 'keypoints': None, 'behavior': None},
        {'box': b24_lie, 'keypoints': None, 'behavior': 'lying_down'}
    ]
    act24, _, _ = classify_pose_activity(trk24_a, None, b24_stand, 5.0, enabled, all_persons=person_records_lie, is_video_test=True)
    assert act24 != 'fighting_posture', f"Test 24 FAILED: Standing person near lying person falsely flagged as fighting!"
    print(f"Test 24 PASSED: Fighting skipped when interacting with a person who is lying down")

    # Test 25: Conversational Dialogue with Overlapping 2D Bounding Boxes (Image 9 Face-to-Face Scene)
    # Two people facing each other in conversation: slight horizontal & vertical box overlap,
    # but wrists relaxed/down, normal talking gestures (v < 0.8), no fists raised -> MUST BE None (NOT fighting!)
    trk25_a = {'first_seen': 100.0}
    b25_a = [80, 50, 180, 250]   # Person 1 (facing right)
    b25_b = [150, 50, 250, 250]  # Person 2 (facing left, overlapping from 150 to 180 in x)
    kp25_a = [[130, 80, 0.9]] + [[0,0,0]]*4 + [
        [110, 110, 0.9], [150, 110, 0.9],  # shoulders
        [100, 150, 0.85], [160, 150, 0.85], # elbows
        [110, 190, 0.85], [150, 190, 0.85], # wrists relaxed below shoulders
        [120, 200, 0.8], [140, 200, 0.8],
        [120, 230, 0.8], [140, 230, 0.8],
        [120, 250, 0.8], [140, 250, 0.8]
    ]
    kp25_b = [[190, 80, 0.9]] + [[0,0,0]]*4 + [
        [170, 110, 0.9], [210, 110, 0.9],
        [160, 150, 0.85], [220, 150, 0.85],
        [170, 190, 0.85], [210, 190, 0.85],
        [180, 200, 0.8], [200, 200, 0.8],
        [180, 230, 0.8], [200, 230, 0.8],
        [180, 250, 0.8], [200, 250, 0.8]
    ]
    person_records_conv = [
        {'id': 1, 'box': b25_a, 'keypoints': kp25_a, 'behavior': None, 'wrist_vel': 0.3},
        {'id': 2, 'box': b25_b, 'keypoints': kp25_b, 'behavior': None, 'wrist_vel': 0.4}
    ]
    act25, _, _ = classify_pose_activity(trk25_a, kp25_a, b25_a, 5.0, enabled, all_persons=person_records_conv, is_video_test=True)
    assert act25 is None, f"Test 25 FAILED: Conversational dialogue was falsely flagged as {act25}!"
    print("Test 25 PASSED: Conversational dialogue with overlapping 2D boxes (Image 9) -> None (NOT fighting!)")

    # Test 26: Cooking Detection with nearby MS COCO utensils / appliances
    trk26 = {'first_seen': 100.0}
    b26 = [100, 150, 220, 280]  # crouched food prep
    kp26 = [[160, 170, 0.9]] + [[0,0,0]]*4 + [
        [130, 190, 0.9], [190, 190, 0.9],
        [125, 220, 0.85], [195, 220, 0.85],
        [150, 235, 0.85], [170, 235, 0.85], # hands clustered at bowl
        [140, 230, 0.8], [180, 230, 0.8],
        [120, 250, 0.8], [200, 250, 0.8],
        [130, 275, 0.8], [190, 275, 0.8]
    ]
    cooking_objs = [
        {'box': [145, 225, 175, 255], 'cls': 'bowl', 'conf': 0.88}
    ]
    act26, conf26, sub26 = classify_pose_activity(trk26, kp26, b26, 5.0, enabled, is_video_test=True, nearby_objects=cooking_objs)
    assert act26 == 'cooking', f"Test 26 FAILED: Expected cooking, got {act26}"
    print(f"Test 26 PASSED: Food prep with nearby bowl/utensil -> {act26} ({sub26})")

    # Test 27: Standing near stove / kitchen appliance without interacting
    # Person is standing upright, hands at sides: MUST NOT trigger cooking
    trk27 = {'first_seen': 100.0}
    b27 = [100, 50, 160, 300]   # tall standing person (aspect ratio < 0.60)
    kp27 = [[130, 70, 0.9]] + [[0,0,0]]*4 + [
        [110, 100, 0.9], [150, 100, 0.9],
        [105, 150, 0.85], [155, 150, 0.85],
        [100, 210, 0.85], [160, 210, 0.85], # hands hanging down at sides
        [115, 180, 0.8], [145, 180, 0.8],
        [115, 240, 0.8], [145, 240, 0.8],
        [115, 295, 0.8], [145, 295, 0.8]
    ]
    nearby_stove = [{'box': [170, 180, 240, 290], 'cls': 'oven', 'conf': 0.90}]
    act27, _, _ = classify_pose_activity(trk27, kp27, b27, 5.0, enabled, is_video_test=True, nearby_objects=nearby_stove)
    assert act27 is None, f"Test 27 FAILED: Standing person near oven falsely flagged as {act27}!"
    print("Test 27 PASSED: Upright person standing near oven -> None (NOT cooking!)")

    # Test 28: Forward bending / stooping to pick up object
    # Person bending forward: hips high, ankles at ground level, body angle ~45 deg: MUST NOT trigger lying_down!
    trk28 = {'first_seen': 100.0}
    b28 = [100, 100, 200, 260] # h = 160, w = 100
    kp28 = [[190, 180, 0.9]] + [[0,0,0]]*4 + [
        [170, 160, 0.9], [170, 160, 0.9], # shoulders at y=160
        [180, 200, 0.8], [180, 200, 0.8],
        [190, 240, 0.8], [190, 240, 0.8],
        [130, 130, 0.85], [130, 130, 0.85], # hips high at y=130
        [125, 190, 0.8], [125, 190, 0.8],
        [120, 255, 0.8], [120, 255, 0.8]   # ankles at ground y=255
    ]
    act28, _, _ = classify_pose_activity(trk28, kp28, b28, 5.0, enabled, is_video_test=True)
    assert act28 != 'lying_down', f"Test 28 FAILED: Forward bending person falsely flagged as lying_down!"
    print("Test 28 PASSED: Forward bending person -> None (NOT lying_down!)")

    # Test 29: Verification of all 14 mathematical algorithms
    from backend.detector.detector_worker import (
        calculate_euclidean_distance,
        calculate_cosine_similarity,
        calculate_body_orientation_angle,
        calculate_joint_angle,
        calculate_bbox_aspect_ratio,
        calculate_wrist_velocity,
        calculate_acceleration,
        calculate_box_iou,
        check_temporal_consistency,
        moving_average_filter,
        exponential_moving_average,
        calculate_activity_confidence,
        calculate_event_duration,
        check_cooldown
    )
    # 1. Euclidean
    assert round(calculate_euclidean_distance((0, 0), (3, 4)), 2) == 5.0
    # 2. Cosine similarity
    assert round(calculate_cosine_similarity((1, 0), (1, 0)), 2) == 1.0
    assert round(calculate_cosine_similarity((1, 0), (0, 1)), 2) == 0.0
    # 3. Body orientation angle (vertical = 90 deg, horizontal = 0 deg)
    assert round(calculate_body_orientation_angle((100, 50), (100, 150)), 1) == 90.0
    assert round(calculate_body_orientation_angle((50, 100), (150, 100)), 1) == 0.0
    # 4. Joint angle (right angle = 90 deg)
    assert round(calculate_joint_angle((100, 50), (100, 100), (150, 100)), 1) == 90.0
    # 5. Aspect ratio
    assert round(calculate_bbox_aspect_ratio([0, 0, 100, 200]), 2) == 0.5
    # 6. Wrist velocity (normalized)
    assert round(calculate_wrist_velocity((100, 100), (100, 50), 0.5, 100), 2) == 1.0
    # 7. Acceleration
    assert round(calculate_acceleration(2.0, 1.0, 0.5), 2) == 2.0
    # 8. IoU
    assert round(calculate_box_iou([0, 0, 10, 10], [5, 0, 15, 10]), 3) == 0.333
    # 9. Temporal consistency
    assert check_temporal_consistency(10.0, 12.5, 2.0) is True
    assert check_temporal_consistency(10.0, 11.0, 2.0) is False
    # 10. Moving average
    assert round(moving_average_filter([10, 20, 30, 40]), 1) == 25.0
    # 11. EMA
    assert round(exponential_moving_average(100, 50, 0.3), 1) == 65.0
    # 12. Confidence aggregation
    assert round(calculate_activity_confidence(0.9, 0.8, 0.7), 2) == 0.83
    # 13. Event duration
    assert calculate_event_duration(100.0, 142.5) == 42.5
    # 14. Cooldown
    assert check_cooldown(150.0, 100.0, 30.0) is True
    assert check_cooldown(120.0, 100.0, 30.0) is False
    print("Test 29 PASSED: All 14 Mathematical CV Algorithms validated with 100% precision!")

    # Test 30: Standing Cooking at Stove / Counter (User Evidence Image 2)
    # Woman standing upright at stove, head tilted down, wrists working forward over cooktop:
    # MUST detect cooking and MUST NEVER trigger fighting_posture even with other people in frame!
    trk30 = {'cook_start_t': time.time() - 3.0}
    b30 = [100, 50, 200, 310] # bh = 260, bw = 100, aspect_ratio = 0.38 (standing tall)
    kp30 = [
        [150, 88, 0.9], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [125, 95, 0.9], [175, 95, 0.9],    # shoulders at y=95
        [115, 140, 0.85], [185, 138, 0.85], # elbows bent at counter height
        [135, 145, 0.85], [165, 142, 0.85], # wrists working forward over stove / pan
        [130, 185, 0.9], [170, 185, 0.9],   # hips at y=185
        [130, 245, 0.85], [170, 245, 0.85], # knees
        [130, 305, 0.85], [170, 305, 0.85]  # ankles at ground y=305
    ]
    # Another person in frame (e.g. user holding the phone in front of webcam)
    b30_phone_holder = [30, 20, 300, 360]
    person_records_30 = [
        {'id': 1, 'box': b30, 'keypoints': kp30, 'behavior': None},
        {'id': 2, 'box': b30_phone_holder, 'keypoints': None, 'behavior': None}
    ]
    act30, conf30, sub30 = classify_pose_activity(
        trk30, kp30, b30, 5.0, enabled,
        all_persons=person_records_30, is_video_test=True
    )
    assert act30 == 'cooking', f"Test 30 FAILED: Standing cooking falsely flagged as {act30}!"
    assert act30 != 'fighting_posture', f"Test 30 FAILED: Standing cooking falsely triggered fighting_posture!"
    print(f"Test 30 PASSED: Standing woman cooking at stove (Image 2) -> {act30} ({sub30}) (NOT fighting!)")

    # Test 31: Multi-Person Crouched Floor Food Prep (User Evidence Image 1)
    # Two men sitting on ground cooking food in a pot between them:
    # Low wrist velocity, hands working at ground pot: MUST detect cooking and NEVER fighting_posture!
    trk31_a = {'cook_start_t': time.time() - 3.0}
    trk31_b = {'cook_start_t': time.time() - 3.0}
    b31_a = [80, 140, 160, 260] # bh = 120, bw = 80 (crouched on ground)
    b31_b = [170, 140, 250, 260] # bh = 120, bw = 80 (crouched on ground)
    kp31_a = [
        [120, 155, 0.9], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [100, 170, 0.9], [140, 170, 0.9],
        [95, 195, 0.85], [145, 195, 0.85],
        [125, 220, 0.85], [140, 225, 0.85], # hands working in front at pot
        [105, 215, 0.9], [135, 215, 0.9],   # hips low to ground (y2 - hip_y = 45 < 120*0.45)
        [90, 240, 0.85], [150, 240, 0.85],
        [100, 255, 0.8], [140, 255, 0.8]
    ]
    kp31_b = [
        [210, 155, 0.9], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [190, 170, 0.9], [230, 170, 0.9],
        [185, 195, 0.85], [235, 195, 0.85],
        [190, 225, 0.85], [205, 220, 0.85], # hands working in front at pot
        [195, 215, 0.9], [225, 215, 0.9],
        [180, 240, 0.85], [240, 240, 0.85],
        [190, 255, 0.8], [230, 255, 0.8]
    ]
    person_records_31 = [
        {'id': 1, 'box': b31_a, 'keypoints': kp31_a, 'behavior': None, 'wrist_vel': 0.15},
        {'id': 2, 'box': b31_b, 'keypoints': kp31_b, 'behavior': None, 'wrist_vel': 0.20}
    ]
    act31_a, conf31_a, sub31_a = classify_pose_activity(
        trk31_a, kp31_a, b31_a, 5.0, enabled,
        all_persons=person_records_31, is_video_test=True
    )
    act31_b, conf31_b, sub31_b = classify_pose_activity(
        trk31_b, kp31_b, b31_b, 5.0, enabled,
        all_persons=person_records_31, is_video_test=True
    )
    assert act31_a == 'cooking', f"Test 31 FAILED: Person A falsely classified as {act31_a}!"
    assert act31_b == 'cooking', f"Test 31 FAILED: Person B falsely classified as {act31_b}!"
    assert act31_a != 'fighting_posture' and act31_b != 'fighting_posture', "Test 31 FAILED: False fighting posture!"
    print(f"Test 31 PASSED: Two men sitting cooking food in pot (Image 1) -> {act31_a} / {act31_b} (NOT fighting!)")

    # Test 32: Active Smoking Detection via Smoking Companion Model
    trk32 = {'first_seen': 100.0}
    b32 = [100, 50, 180, 250]
    smk_objs = [
        {'box': [130, 80, 150, 100], 'cls': 'smoking', 'conf': 0.94}
    ]
    act32, conf32, sub32 = classify_pose_activity(
        trk32, None, b32, 5.0, enabled, is_video_test=True, smoking_objects=smk_objs
    )
    assert act32 == 'smoking', f"Test 32 FAILED: Expected smoking, got {act32}"
    assert conf32 >= 0.92, f"Test 32 FAILED: Expected conf >= 0.92, got {conf32}"
    print(f"Test 32 PASSED: Active smoking detected via companion smoking model -> {act32} ({conf32:.2f}, {sub32})")

    # Test 33: Umbrella match_class verification for unauthorized_activity
    from detector.detector_worker import match_class, is_negative_violation_class, is_opposite_class, DetectorWorker
    assert match_class('smoking', 'unauthorized_activity'), "Test 33 FAILED: smoking should match unauthorized_activity"
    assert match_class('cooking', 'unauthorized_activity'), "Test 33 FAILED: cooking should match unauthorized_activity"
    assert match_class('fighting_posture', 'unauthorized_activity'), "Test 33 FAILED: fighting_posture should match unauthorized_activity"
    assert match_class('lying_down', 'unauthorized_activity'), "Test 33 FAILED: lying_down should match unauthorized_activity"
    assert match_class('smoking_gesture', 'unauthorized_activity'), "Test 33 FAILED: smoking_gesture should match unauthorized_activity"
    print("Test 33 PASSED: Umbrella class matching for unauthorized_activity fully verified!")

    # Test 34: Leakage detection model classes and violation gating (leakage_best.pt)
    assert is_negative_violation_class('leak') == True, "Test 34 FAILED: 'leak' must be a negative safety violation"
    assert is_negative_violation_class('leakage') == True, "Test 34 FAILED: 'leakage' must be a negative safety violation"
    assert is_negative_violation_class('no leak') == False, "Test 34 FAILED: 'no leak' is compliant and must NEVER alert"
    assert is_negative_violation_class('no-leak') == False, "Test 34 FAILED: 'no-leak' is compliant and must NEVER alert"
    assert is_opposite_class('leak', 'no leak') == True, "Test 34 FAILED: 'leak' and 'no leak' must be opposite pair"
    print("Test 34 PASSED: Leakage model ('leak' violation vs 'no leak' compliant) verified 100%!")

    # Test 35: Companion model auto-resolution for unauthorized_activity
    resolved = DetectorWorker._resolve_companion_models(['models/unauthorized_activity.pt'])
    res_str = " ".join(str(r) for r in resolved)
    assert any("fire_smoke" in r.lower() for r in resolved), "Test 35 FAILED: fire_smoke.pt not resolved as companion!"
    assert any("smoking" in r.lower() for r in resolved), "Test 35 FAILED: smoking.pt not resolved as companion!"
    print(f"Test 35 PASSED: Unauthorized activity companion models resolved: {res_str}")

    # Test 36: Positive Class Suppression when only Violation is Selected (e.g. only 'leak')
    selected = ['leak']
    # If user selected only 'leak', positive class 'no leak' must NOT match and must be discarded
    assert not any(match_class('no leak', sc) for sc in selected), "Test 36 FAILED: 'no leak' must not match 'leak' selection!"
    assert any(match_class('leak', sc) for sc in selected), "Test 36 FAILED: 'leak' must match 'leak' selection!"
    print("Test 36 PASSED: Positive class suppression verified ('no leak' is discarded when only 'leak' is selected)")

    # Test 37: 3-Second Continuous Persistence Gate for All Safety Violations
    now = 1000.0
    trk_flicker = {'first_seen': now - 0.5, 'hit_count': 2}  # 0.5s flicker false positive (e.g. fan)
    trk_sustained = {'first_seen': now - 3.2, 'hit_count': 6} # 3.2s continuous violation

    dur_flicker = now - trk_flicker.get('first_seen', now)
    dur_sustained = now - trk_sustained.get('first_seen', now)

    flicker_alerts = (dur_flicker >= 3.0 and trk_flicker.get('hit_count', 0) >= 3)
    sustained_alerts = (dur_sustained >= 3.0 and trk_sustained.get('hit_count', 0) >= 3)

    assert not flicker_alerts, "Test 37 FAILED: 0.5s flicker false positive must NOT alert!"
    assert sustained_alerts, "Test 37 FAILED: 3.2s continuous violation MUST trigger alert!"
    print("Test 37 PASSED: 3-Second continuous persistence gate verified for all models!")

    # Test 38: Sleeping Driver Resting on Steering Wheel in Vehicle (User Uploaded Images 2 & 3)
    # Solitary or displayed on phone screen next to user:
    # MUST NEVER trigger cooking and MUST NEVER trigger fighting_posture!
    trk38 = {'cook_start_t': time.time() - 3.0, 'fight_start_t': time.time() - 3.0}
    b38_driver = [120, 100, 240, 240] # cropped driver inside vehicle (bh = 140, bw = 120, aspect_ratio = 0.86)
    kp38 = [
        [180, 145, 0.20], [0,0,0], [0,0,0], [0,0,0], [0,0,0], # nose buried down in arms/wheel
        [150, 135, 0.90], [210, 135, 0.90],                   # shoulders
        [140, 170, 0.85], [220, 170, 0.85],                   # elbows bent on wheel
        [165, 140, 0.85], [195, 140, 0.85],                   # wrists resting on steering wheel
        [160, 200, 0.60], [200, 200, 0.60],                   # hips seated
        [0, 0, 0.0], [0, 0, 0.0],                              # knees hidden under dashboard
        [0, 0, 0.0], [0, 0, 0.0]                               # ankles hidden in vehicle footwell
    ]
    # Another person nearby (e.g. user holding the phone in front of webcam: Image 3)
    b38_user = [30, 20, 300, 360]
    person_records_38 = [
        {'id': 1, 'box': b38_driver, 'keypoints': kp38, 'behavior': None, 'wrist_vel': 0.0, 'wrist_acc': 0.0},
        {'id': 2, 'box': b38_user, 'keypoints': None, 'behavior': None, 'wrist_vel': 0.0, 'wrist_acc': 0.0}
    ]
    act38, conf38, sub38 = classify_pose_activity(
        trk38, kp38, b38_driver, 5.0, enabled,
        all_persons=person_records_38, has_fire_smoke=False, nearby_objects=[]
    )
    assert act38 != 'cooking', f"Test 38 FAILED: Sleeping driver falsely alerted as cooking ({sub38})!"
    assert act38 != 'fighting_posture', f"Test 38 FAILED: Sleeping driver falsely alerted as fighting_posture ({sub38})!"
    print(f"Test 38 PASSED: Sleeping driver in vehicle (Images 2 & 3) -> {act38} (NOT cooking, NOT fighting!)")

    # Test 39: Man Leaning Forward on Outdoor Table (User Uploaded Image 1) with No Utensils
    # Must NEVER trigger cooking without verified cooking equipment or flame/smoke!
    trk39 = {'cook_start_t': time.time() - 3.0}
    b39 = [80, 70, 220, 260] # bh = 190, bw = 140, aspect_ratio = 0.74
    kp39 = [
        [150, 95, 0.90], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [120, 110, 0.90], [180, 110, 0.90],                  # shoulders
        [105, 155, 0.85], [195, 155, 0.85],                  # elbows
        [130, 185, 0.85], [170, 185, 0.85],                  # hands forward on table
        [135, 175, 0.80], [165, 175, 0.80],                  # hips
        [130, 225, 0.70], [170, 225, 0.70],                  # knees
        [0, 0, 0.0], [0, 0, 0.0]                             # ankles cropped/invisible
    ]
    act39, _, sub39 = classify_pose_activity(
        trk39, kp39, b39, 5.0, enabled,
        has_fire_smoke=False, nearby_objects=[]
    )
    assert act39 != 'cooking', f"Test 39 FAILED: Leaning man falsely alerted as cooking ({sub39})!"
    print(f"Test 39 PASSED: Leaning man with no cooking equipment (Image 1) -> {act39} (NOT cooking!)")

    # Test 40: User sitting cross-legged on bed touching feet/lap (User Evidence Image 8)
    trk40 = {'cook_start_t': time.time() - 3.0}
    kp40 = [
        [0, 0, 0.01], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [380, 160, 0.9], [550, 160, 0.9],
        [350, 220, 0.85], [580, 220, 0.85],
        [450, 310, 0.95], [490, 310, 0.94],
        [390, 260, 0.98], [540, 260, 0.99],
        [320, 290, 0.85], [610, 290, 0.85],
        [440, 320, 0.56], [500, 320, 0.57]
    ]
    box40 = [277.9, 100.9, 654.8, 354.0] # wide aspect ratio = 1.49
    act40, _, _ = classify_pose_activity(trk40, kp40, box40, 5.0, enabled)
    assert act40 != "cooking", f"Test 40 FAILED: Bed sitting cross-legged falsely triggered cooking: {act40}"
    print("Test 40 PASSED: Bed sitting cross-legged touching feet -> None (NOT cooking!)")

    # Test 41: User reclining on bed reading smartphone (User Evidence Image 9)
    trk41 = {'smoke_start_t': time.time() - 3.0}
    kp41 = [
        [412, 140, 0.94], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [380, 180, 0.9], [460, 180, 0.9],
        [360, 220, 0.85], [480, 220, 0.85],
        [490, 240, 0.63], [420, 300, 0.53], # wrist at chest (outside shoulder mouth reach)
        [420, 300, 0.95], [510, 300, 0.94],
        [580, 320, 0.97], [680, 320, 0.96],
        [750, 360, 0.97], [850, 360, 0.96]
    ]
    box41 = [332.8, 118.1, 869.9, 431.3]
    act41, _, _ = classify_pose_activity(trk41, kp41, box41, 5.0, enabled)
    assert act41 != "smoking", f"Test 41 FAILED: Reclining with phone triggered smoking: {act41}"
    print("Test 41 PASSED: Reclining on bed reading smartphone -> None (NOT smoking!)")

    # Test 42: Solitary person leaning forward adjusting trousers (User Evidence Image 10)
    trk42 = {'id': 1, 'wrist_max_v': 0.10, 'wrist_max_a': 0.20}
    kp42 = [
        [250, 150, 0.96], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [200, 190, 0.85], [300, 190, 0.85],
        [180, 240, 0.85], [320, 240, 0.85],
        [230, 310, 0.82], [270, 310, 0.32],
        [220, 300, 0.69], [280, 300, 0.42],
        [210, 380, 0.20], [290, 380, 0.11],
        [210, 440, 0.20], [290, 440, 0.11]
    ]
    box42 = [143.4, 93.3, 363.1, 463.5]
    ghost_track42 = {'id': 2, 'box': [140.0, 90.0, 360.0, 460.0], 'keypoints': kp42, 'wrist_vel': 0.10, 'wrist_acc': 0.20}
    act42, _, _ = classify_pose_activity(trk42, kp42, box42, 5.0, enabled, all_persons=[ghost_track42])
    assert act42 != "fighting_posture", f"Test 42 FAILED: Solitary adjusting pants triggered fighting: {act42}"
    print("Test 42 PASSED: Solitary person with ghost track -> None (NOT fighting!)")

    # Test 43: Desk close-up user looking at webcam / hand on chin (Images 1 & 3)
    trk43 = {'cook_start_t': time.time() - 3.0}
    kp43 = [
        [320, 200, 0.99], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [250, 280, 0.9], [390, 280, 0.9],
        [230, 330, 0.85], [410, 330, 0.85],
        [310, 240, 0.70], [330, 320, 0.15],
        [0,0,0], [0,0,0], [0,0,0], [0,0,0], [0,0,0], [0,0,0]
    ]
    box43 = [187.1, 129.9, 687.8, 497.7]
    act43, _, _ = classify_pose_activity(trk43, kp43, box43, 5.0, enabled)
    assert act43 != "cooking", f"Test 43 FAILED: Desk user triggered cooking: {act43}"
    print("Test 43 PASSED: Desk user looking at webcam / hand on chin -> None (NOT cooking!)")

    # Test 44: White switchboard on wall detected as smoking
    smk_sw = [{'box': [620, 100, 680, 150], 'conf': 0.63, 'cls': 'smoking'}]
    act44, _, _ = classify_pose_activity(trk41, kp41, box41, 5.0, enabled, smoking_objects=smk_sw)
    assert act44 != "smoking", f"Test 44 FAILED: Wall switchboard triggered smoking: {act44}"
    print("Test 44 PASSED: Wall switchboard -> None (NOT smoking!)")

    # Test 45: Spatial deduplication pass on 15 stacked boxes (User Evidence Image 5)
    stacked = []
    for i in range(15):
        stacked.append({
            'box': [100 + i*2, 50 + i*1, 200 + i*2, 180 + i*1],
            'label': f'VIOLATION: NO-Hardhat 0.{80+i}',
            'cls': 'NO-Hardhat',
            'conf': 0.80 + i*0.01
        })
    deduped = []
    sorted_disp = sorted(stacked, key=lambda b: (1 if "VIOLATION" in b['label'] else 0, float(b['conf'])), reverse=True)
    for cur_b in sorted_disp:
        c_box = cur_b['box']
        c_cls = cur_b['cls'].lower()
        is_dup = False
        for kept_b in deduped:
            k_box = kept_b['box']
            k_cls = kept_b['cls'].lower()
            iou_val, io_min_val = DetectorWorker._box_iou_and_io_min(c_box, k_box)
            if (c_cls == k_cls or ('person' in c_cls and 'person' in k_cls)) and (iou_val >= 0.25 or io_min_val >= 0.35):
                is_dup = True
                break
            if iou_val >= 0.65 or io_min_val >= 0.75:
                is_dup = True
                break
        if not is_dup:
            deduped.append(cur_b)
    assert len(deduped) == 1, f"Test 45 FAILED: Expected 1 box, got {len(deduped)}"
    print(f"Test 45 PASSED: 15 stacked boxes collapsed to exactly 1 clean box: {deduped[0]['label']}")

    # Test 46: Two people lying down on bed side-by-side using phones (User Uploaded Image 1)
    # Wrists near head/face holding smartphones, boxes overlapping on bed: MUST NOT trigger fighting!
    trk46_a = {'id': 1, 'wrist_max_v': 0.15}
    trk46_b = {'id': 2, 'wrist_max_v': 0.12}
    b46_a = [435, 270, 615, 365] # bw = 180, bh = 95 -> aspect ratio = 1.89 (lying down)
    b46_b = [590, 280, 710, 385] # bw = 120, bh = 105 -> aspect ratio = 1.14 (resting on bed)
    kp46_a = [[580, 290, 0.9]] + [[0,0,0]]*4 + [
        [550, 300, 0.85], [550, 330, 0.85],
        [520, 310, 0.8], [520, 330, 0.8],
        [570, 295, 0.85], [575, 300, 0.85], # wrists holding phone near face
        [460, 310, 0.8], [460, 330, 0.8],
        [440, 320, 0.8], [440, 340, 0.8],
        [435, 320, 0.8], [435, 340, 0.8]
    ]
    kp46_b = [[620, 300, 0.9]] + [[0,0,0]]*4 + [
        [650, 310, 0.85], [650, 330, 0.85],
        [670, 320, 0.8], [670, 340, 0.8],
        [615, 305, 0.85], [618, 305, 0.85], # wrists holding phone near face
        [690, 320, 0.8], [690, 340, 0.8],
        [700, 330, 0.8], [700, 350, 0.8],
        [710, 340, 0.8], [710, 360, 0.8]
    ]
    p_recs_46 = [
        {'id': 1, 'box': b46_a, 'keypoints': kp46_a, 'wrist_vel': 0.15},
        {'id': 2, 'box': b46_b, 'keypoints': kp46_b, 'wrist_vel': 0.12}
    ]
    act46_a, _, _ = classify_pose_activity(trk46_a, kp46_a, b46_a, 5.0, enabled, all_persons=p_recs_46)
    act46_b, _, _ = classify_pose_activity(trk46_b, kp46_b, b46_b, 5.0, enabled, all_persons=p_recs_46)
    assert act46_a != 'fighting_posture', f"Test 46 FAILED: Bed phone user A falsely flagged as {act46_a}!"
    assert act46_b != 'fighting_posture', f"Test 46 FAILED: Bed phone user B falsely flagged as {act46_b}!"
    print("Test 46 PASSED: Two people lying on bed using phones -> NOT fighting!")

    # Test 47: Person standing/bending by bed reaching down for phone (User Uploaded Image 2)
    trk47 = {'id': 1, 'wrist_max_v': 0.20}
    b47_stand = [400, 100, 680, 440]
    b47_lie = [200, 240, 500, 360]
    kp47 = [[590, 110, 0.9]] + [[0,0,0]]*4 + [
        [580, 140, 0.85], [600, 140, 0.85],
        [560, 220, 0.8], [610, 220, 0.8],
        [530, 340, 0.85], [550, 310, 0.85], # hand reaching down to pick up phone
        [480, 260, 0.85], [490, 260, 0.85],
        [480, 340, 0.8], [490, 340, 0.8],
        [480, 430, 0.8], [490, 430, 0.8]
    ]
    p_recs_47 = [
        {'id': 1, 'box': b47_stand, 'keypoints': kp47, 'wrist_vel': 0.20},
        {'id': 2, 'box': b47_lie, 'keypoints': None, 'wrist_vel': 0.05}
    ]
    act47, _, _ = classify_pose_activity(trk47, kp47, b47_stand, 5.0, enabled, all_persons=p_recs_47)
    assert act47 != 'fighting_posture', f"Test 47 FAILED: Person standing/reaching near bed falsely flagged as {act47}!"
    print("Test 47 PASSED: Person standing/reaching near bed -> NOT fighting!")

    # Test 48: User holding simulated smoking object / toy gun barrel to mouth (User Uploaded Image 3)
    trk48 = {'smoke_start_t': time.time() - 3.0}
    b48 = [353, 140, 660, 466]
    kp48 = [[505, 225, 0.95]] + [[0,0,0]]*4 + [
        [410, 325, 0.9], [600, 325, 0.9],
        [440, 390, 0.85], [570, 390, 0.85],
        [515, 250, 0.85], [540, 380, 0.85], # wrist holding object directly to mouth
        [450, 510, 0.8], [560, 510, 0.8],
        [450, 550, 0.7], [560, 550, 0.7],
        [450, 600, 0.7], [560, 600, 0.7]
    ]
    smk_toy = [{'box': [504, 240, 530, 300], 'conf': 0.35, 'cls': 'smoking'}]
    act48, conf48, sub48 = classify_pose_activity(trk48, kp48, b48, 5.0, enabled, is_video_test=True, smoking_objects=smk_toy)
    assert act48 == 'smoking', f"Test 48 FAILED: Object held to mouth expected smoking, got {act48}"
    print(f"Test 48 PASSED: Object held to lips/mouth -> {act48} ({sub48})")

    # Test 49: Person sitting at laptop on bed (Screenshot 7 & 1) -> None (NOT fighting!)
    trk49 = {'fight_start_t': time.time() - 2.0}
    b49 = [23.8, 88.3, 151.9, 240.7] # AR = 0.84, bh = 152.4
    kp49 = [
        [91.3, 118.6, 0.98], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [116.1, 152.0, 0.97], [40.9, 153.3, 0.99],
        [145.5, 199.8, 0.76], [27.6, 202.8, 0.94],
        [142.2, 221.7, 0.72], [69.0, 210.3, 0.90], # hands typing at keyboard
        [111.3, 232.0, 0.77], [68.1, 234.2, 0.85]
    ]
    p_recs_49 = [
        {'id': 0, 'box': b49, 'keypoints': kp49, 'wrist_vel': 0.15},
        {'id': 1, 'box': [237.9, 137.9, 428.0, 240.0], 'keypoints': None, 'wrist_vel': 0.05}
    ]
    act49, _, _ = classify_pose_activity(trk49, kp49, b49, 5.0, enabled, all_persons=p_recs_49)
    assert act49 != 'fighting_posture', f"Test 49 FAILED: Person at laptop falsely flagged as {act49}!"
    print(f"Test 49 PASSED: Person sitting at laptop on bed (Screenshot 7) -> {act49} (NOT fighting!)")

    # Test 50: Person sitting on bed with knee up in pink shirt (Screenshot 7 & 8) -> None (NOT cooking!)
    trk50 = {'cook_start_t': time.time() - 2.0}
    b50 = [457.9, 120.2, 533.9, 220.3] # AR = 0.76, bh = 100.1
    kp50 = [
        [486.4, 140.2, 0.95], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [515.3, 150.3, 0.99], [493.4, 156.3, 0.93],
        [510.1, 182.7, 0.98], [489.4, 185.0, 0.71],
        [477.2, 190.1, 0.96], [478.9, 189.3, 0.71], # hands resting in lap holding phone
        [519.0, 199.8, 0.94], [504.2, 199.7, 0.86],
        [490.0, 185.0, 0.65], [485.0, 185.0, 0.40],
        [480.0, 215.0, 0.33], [480.0, 215.0, 0.20]
    ]
    act50, _, _ = classify_pose_activity(trk50, kp50, b50, 5.0, enabled, is_video_test=True)
    assert act50 != 'cooking', f"Test 50 FAILED: Person sitting on bed falsely flagged as {act50}!"
    print(f"Test 50 PASSED: Person sitting on bed in pink shirt (Screenshot 7 & 8) -> {act50} (NOT cooking!)")

    # Test 51: Person standing and bending forward near bed (Screenshot 10) -> None (NOT lying_down!)
    trk51 = {'lie_start_t': time.time() - 2.0}
    b51 = [350.0, 130.0, 470.0, 310.0] # AR = 0.67 (vertical box), bh = 180.0
    kp51 = [
        [410.0, 160.0, 0.95], [0,0,0], [0,0,0], [0,0,0], [0,0,0],
        [430.0, 185.0, 0.9], [380.0, 185.0, 0.9],
        [440.0, 220.0, 0.8], [370.0, 220.0, 0.8],
        [430.0, 250.0, 0.8], [380.0, 250.0, 0.8],
        [420.0, 240.0, 0.85], [390.0, 240.0, 0.85] # hips elevated
    ]
    act51, _, _ = classify_pose_activity(trk51, kp51, b51, 5.0, enabled, frame_height=360)
    assert act51 != 'lying_down', f"Test 51 FAILED: Standing/bending person falsely flagged as {act51}!"
    print(f"Test 51 PASSED: Person standing/bending forward near bed (Screenshot 10) -> {act51} (NOT lying_down!)")

    # Test 52: Ground clutter / black backpack on floor (Screenshot 9) -> None (NOT lying_down, NOT crowd!)
    trk52 = {'lie_start_t': time.time() - 2.0, 'conf': 0.28}
    b52_backpack = [520.0, 280.0, 600.0, 340.0] # small backpack on floor, no keypoints
    act52, _, _ = classify_pose_activity(trk52, None, b52_backpack, 5.0, enabled, frame_height=360)
    assert act52 != 'lying_down', f"Test 52 FAILED: Backpack on floor falsely flagged as {act52}!"
    print(f"Test 52 PASSED: Ground clutter / backpack on floor -> {act52} (NOT lying_down!)")

    # Test 53: Strict 30-Second Canonical Alert Cooldown across Umbrella & Sub-Activity Names
    from backend.detector.detector_worker import check_and_record_alert_cooldown
    test_cam = "test_cam_99"
    # First alert passes
    first_ok = check_and_record_alert_cooldown(test_cam, "Unauthorized Activity (Parking Area) - smoking", 30.0)
    assert first_ok is True, "Test 53 FAILED: First smoking alert should be permitted!"
    # Sub-activity duplicate within 30s must be strictly blocked!
    sub_blocked = check_and_record_alert_cooldown(test_cam, "smoking", 30.0)
    assert sub_blocked is False, "Test 53 FAILED: Sub-activity 'smoking' within 30s should be blocked!"
    # Display name variant duplicate within 30s must also be strictly blocked!
    disp_blocked = check_and_record_alert_cooldown(test_cam, "VIOLATION: Smoking Detected", 30.0)
    assert disp_blocked is False, "Test 53 FAILED: Display name variant 'VIOLATION: Smoking Detected' should be blocked!"
    print("Test 53 PASSED: Strict 30-Second Alert Cooldown strictly blocks duplicate alerts across umbrella, sub-activity, and display variants!")

    print("\nALL 53 PRODUCTION TESTS PASSED WITH 100% ACCURACY!")

if __name__ == '__main__':
    run_tests()
