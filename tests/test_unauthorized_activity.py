import math
import time

def calculate_joint_angle(p1, p2, p3):
    u = (p1[0] - p2[0], p1[1] - p2[1])
    v = (p3[0] - p2[0], p3[1] - p2[1])
    dot = u[0] * v[0] + u[1] * v[1]
    nu = math.hypot(u[0], u[1])
    nv = math.hypot(v[0], v[1])
    if nu < 1e-4 or nv < 1e-4:
        return 180.0
    cos_theta = max(-1.0, min(1.0, dot / (nu * nv)))
    return math.degrees(math.acos(cos_theta))

def classify_pose_activity(trk, kp_data, box, dwell_time, enabled_activities, all_persons=None, frame_height=360):
    if not enabled_activities:
        return None, 0.0, None

    now_t = time.time()

    def _is_enabled(act_name):
        return any(act_name.lower().replace('-', '_') in str(e).lower().replace('-', '_') for e in enabled_activities)

    x1, y1, x2, y2 = box if (box and len(box) == 4) else [0, 0, 1, 1]
    bw = max(1.0, x2 - x1)
    bh = max(1.0, y2 - y1)
    aspect_ratio = bw / bh
    is_bottom_cropped = (y2 >= frame_height * 0.90)

    has_kp = (kp_data is not None and len(kp_data) >= 17)

    # 1. Biomechanical Upright Orientation Check
    is_upright = False
    if has_kp:
        nose = kp_data[0]
        eyes_ears = [kp_data[i] for i in range(1, 5) if kp_data[i][2] > 0.20]
        head_y = nose[1] if nose[2] > 0.20 else (sum(p[1] for p in eyes_ears)/len(eyes_ears) if eyes_ears else None)
        l_sh, r_sh = kp_data[5], kp_data[6]
        l_hip, r_hip = kp_data[11], kp_data[12]

        sh_valid = (l_sh[2] > 0.20 and r_sh[2] > 0.20)
        sh_y = min(l_sh[1], r_sh[1]) if sh_valid else (l_sh[1] if l_sh[2] > 0.20 else r_sh[1])

        # If head keypoint is above shoulders, person is upright
        if head_y is not None and sh_y > 0 and head_y < sh_y:
            is_upright = True
        # If shoulders are aligned and hips are below shoulders
        elif (l_sh[2] > 0.20 or r_sh[2] > 0.20) and (l_hip[2] > 0.20 or r_hip[2] > 0.20):
            sh_mid_y = (l_sh[1] + r_sh[1]) / 2.0
            hip_mid_y = (l_hip[1] + r_hip[1]) / 2.0
            sh_mid_x = (l_sh[0] + r_sh[0]) / 2.0
            hip_mid_x = (l_hip[0] + r_hip[0]) / 2.0
            if (hip_mid_y - sh_mid_y) > abs(hip_mid_x - sh_mid_x) * 1.1:
                is_upright = True
        # If both shoulders detected horizontally with no hips (upper body portrait crop)
        elif sh_valid and abs(l_sh[0] - r_sh[0]) > bw * 0.30 and abs(l_sh[1] - r_sh[1]) < abs(l_sh[0] - r_sh[0]) * 0.45:
            is_upright = True

    # 2. Biomechanical Sitting Detection
    is_sitting = False
    if _is_enabled('sitting') and has_kp:
        l_sh, r_sh = kp_data[5], kp_data[6]
        l_hip, r_hip = kp_data[11], kp_data[12]
        l_knee, r_knee = kp_data[13], kp_data[14]
        l_ank, r_ank = kp_data[15], kp_data[16]

        # Left leg knee flexion (sitting on chair / curb / ground)
        if l_hip[2] > 0.20 and l_knee[2] > 0.20 and l_ank[2] > 0.15:
            l_knee_ang = calculate_joint_angle(l_hip, l_knee, l_ank)
            l_thigh_dy = abs(l_knee[1] - l_hip[1])
            l_thigh_dx = abs(l_knee[0] - l_hip[0])
            if 60.0 <= l_knee_ang <= 135.0 and (l_thigh_dy < l_thigh_dx * 0.85 or l_thigh_dy < bh * 0.25):
                is_sitting = True

        # Right leg knee flexion
        if not is_sitting and r_hip[2] > 0.20 and r_knee[2] > 0.20 and r_ank[2] > 0.15:
            r_knee_ang = calculate_joint_angle(r_hip, r_knee, r_ank)
            r_thigh_dy = abs(r_knee[1] - r_hip[1])
            r_thigh_dx = abs(r_knee[0] - r_hip[0])
            if 60.0 <= r_knee_ang <= 135.0 and (r_thigh_dy < r_thigh_dx * 0.85 or r_thigh_dy < bh * 0.25):
                is_sitting = True

        # Cross-legged floor sitting (e.g. Indian style sitting, as in Image 1)
        if not is_sitting and (l_knee[2] > 0.20 or r_knee[2] > 0.20) and (l_hip[2] > 0.20 or r_hip[2] > 0.20):
            if l_knee[2] > 0.20 and r_knee[2] > 0.20:
                knee_spread = abs(l_knee[0] - r_knee[0])
                if knee_spread > bw * 0.40:
                    hip_y = (l_hip[1] + r_hip[1]) / 2.0 if (l_hip[2] > 0.20 and r_hip[2] > 0.20) else (l_hip[1] if l_hip[2] > 0.20 else r_hip[1])
                    knee_y = (l_knee[1] + r_knee[1]) / 2.0
                    if abs(hip_y - knee_y) < bh * 0.28:
                        is_sitting = True

        # Torso-thigh angle (hip flexion) when seated on chair/bench
        if not is_sitting and (l_sh[2] > 0.20 or r_sh[2] > 0.20) and (l_hip[2] > 0.20 or r_hip[2] > 0.20) and (l_knee[2] > 0.20 or r_knee[2] > 0.20):
            sh_pt = l_sh if l_sh[2] > 0.20 else r_sh
            hip_pt = l_hip if l_hip[2] > 0.20 else r_hip
            knee_pt = l_knee if l_knee[2] > 0.20 else r_knee
            hip_ang = calculate_joint_angle(sh_pt, hip_pt, knee_pt)
            if 65.0 <= hip_ang <= 130.0:
                is_sitting = True

    # Aspect ratio / squat fallback for full body sitting (not bottom cropped)
    if not is_sitting and _is_enabled('sitting') and not is_bottom_cropped and 0.70 <= aspect_ratio <= 1.25 and bh > 35:
        if has_kp:
            l_hip, r_hip = kp_data[11], kp_data[12]
            if max(l_hip[2], r_hip[2]) > 0.20:
                hip_y = l_hip[1] if l_hip[2] > 0.20 else r_hip[1]
                if (hip_y - y1) > bh * 0.38:
                    is_sitting = True

    # 3. Lying Down (Torso Strictly Horizontal on Ground, NOT Upright)
    if _is_enabled('lying_down') and not is_upright:
        is_lying = False
        if has_kp:
            l_sh, r_sh = kp_data[5], kp_data[6]
            l_hip, r_hip = kp_data[11], kp_data[12]
            if (l_sh[2] > 0.15 or r_sh[2] > 0.15) and (l_hip[2] > 0.15 or r_hip[2] > 0.15):
                sh_x = (l_sh[0] + r_sh[0]) / 2.0 if (l_sh[2] > 0.15 and r_sh[2] > 0.15) else (l_sh[0] if l_sh[2] > 0.15 else r_sh[0])
                sh_y = (l_sh[1] + r_sh[1]) / 2.0 if (l_sh[2] > 0.15 and r_sh[2] > 0.15) else (l_sh[1] if l_sh[2] > 0.15 else r_sh[1])
                hip_x = (l_hip[0] + r_hip[0]) / 2.0 if (l_hip[2] > 0.15 and r_hip[2] > 0.15) else (l_hip[0] if l_hip[2] > 0.15 else r_hip[0])
                hip_y = (l_hip[1] + r_hip[1]) / 2.0 if (l_hip[2] > 0.15 and r_hip[2] > 0.15) else (l_hip[1] if l_hip[2] > 0.15 else r_hip[1])
                dx = abs(hip_x - sh_x)
                dy = abs(hip_y - sh_y)
                if dx > dy * 1.15 and dx > bw * 0.20:
                    is_lying = True

        # Fallback for small / distant figure lying down (e.g. mud video)
        if not is_lying and not is_bottom_cropped and aspect_ratio >= 1.25 and bh >= 14:
            is_lying = True

        if is_lying:
            l_start = trk.get('lie_start_t') or now_t
            trk['lie_start_t'] = l_start
            if (now_t - l_start) >= 0.8:
                return 'lying_down', 0.89, 'Horizontal Torso Orientation on Ground'
        else:
            trk['lie_start_t'] = None

    # 4. Fighting Posture (Multi-Person Combat Guard Stance / Physical Altercation)
    if _is_enabled('fighting_posture') and has_kp and not is_sitting:
        l_sh, r_sh = kp_data[5], kp_data[6]
        l_wr, r_wr = kp_data[9], kp_data[10]
        l_el, r_el = kp_data[7], kp_data[8]
        nose = kp_data[0]
        sh_y = min(l_sh[1], r_sh[1]) if (l_sh[2] > 0.20 and r_sh[2] > 0.20) else (l_sh[1] if l_sh[2] > 0.20 else r_sh[1])

        near_combatant = False
        if all_persons and len(all_persons) > 1:
            for ob in all_persons:
                if ob == box:
                    continue
                ox1, oy1, ox2, oy2 = ob
                dist_cen = math.hypot((x1 + x2)/2.0 - (ox1 + ox2)/2.0, (y1 + y2)/2.0 - (oy1 + oy2)/2.0)
                if dist_cen < max(bw, bh) * 1.8:
                    near_combatant = True
                    break

        # A true fighting guard stance requires fists/wrists raised to SHOULDER OR HEAD LEVEL:
        # Fists protect the chin/face (y_wrist <= sh_y + bh * 0.04)
        l_fist_raised = (l_wr[2] > 0.20 and sh_y > 0 and l_wr[1] <= sh_y + bh * 0.04)
        r_fist_raised = (r_wr[2] > 0.20 and sh_y > 0 and r_wr[1] <= sh_y + bh * 0.04)

        l_guard_bent = (l_el[2] > 0.20 and calculate_joint_angle(l_sh, l_el, l_wr) < 125.0) if l_fist_raised else False
        r_guard_bent = (r_el[2] > 0.20 and calculate_joint_angle(r_sh, r_el, r_wr) < 125.0) if r_fist_raised else False

        both_hands_guard = (l_fist_raised and r_fist_raised and (l_guard_bent or r_guard_bent))

        one_punch_extended = False
        if near_combatant:
            if l_fist_raised and l_el[2] > 0.20 and calculate_joint_angle(l_sh, l_el, l_wr) > 140.0:
                one_punch_extended = True
            elif r_fist_raised and r_el[2] > 0.20 and calculate_joint_angle(r_sh, r_el, r_wr) > 140.0:
                one_punch_extended = True

        is_fighting = False
        if near_combatant and (both_hands_guard or one_punch_extended or (l_fist_raised and r_fist_raised)):
            is_fighting = True
        elif not near_combatant and both_hands_guard and (nose[2] > 0.20 and (l_wr[1] < nose[1] + bh * 0.10 and r_wr[1] < nose[1] + bh * 0.10)):
            is_fighting = True

        if is_fighting:
            f_start = trk.get('fight_start_t') or now_t
            trk['fight_start_t'] = f_start
            if (now_t - f_start) >= 0.8:
                return 'fighting_posture', 0.88, 'Combat Guard Stance / Physical Altercation'
        else:
            trk['fight_start_t'] = None

    # 5. Smoking Gesture (Hand-to-Mouth + Bent Elbow + Forearm Raised)
    if _is_enabled('smoking_gesture') and has_kp:
        nose = kp_data[0]
        l_wr, r_wr = kp_data[9], kp_data[10]
        l_el, r_el = kp_data[7], kp_data[8]
        l_sh, r_sh = kp_data[5], kp_data[6]

        sh_y = min(l_sh[1], r_sh[1]) if (l_sh[2] > 0.20 and r_sh[2] > 0.20) else (l_sh[1] if l_sh[2] > 0.20 else r_sh[1])
        face_x, face_y = nose[0], nose[1]
        face_conf = nose[2]
        if face_conf < 0.25 and (l_sh[2] > 0.20 and r_sh[2] > 0.20):
            face_x = (l_sh[0] + r_sh[0]) / 2.0
            face_y = min(l_sh[1], r_sh[1]) - bh * 0.16
            face_conf = 0.5

        if face_conf >= 0.25:
            mouth_y = face_y + bh * 0.05
            is_smoking = False

            for wr, el, sh in [(l_wr, l_el, l_sh), (r_wr, r_el, r_sh)]:
                if wr[2] > 0.25 and el[2] > 0.20 and sh[2] > 0.20:
                    dist = math.hypot(wr[0] - face_x, wr[1] - mouth_y)
                    is_at_mouth_height = (face_y - bh * 0.06 <= wr[1] <= sh_y + bh * 0.02)
                    wrist_above_elbow = (wr[1] < el[1] + bh * 0.03)
                    elbow_ang = calculate_joint_angle(sh, el, wr)
                    is_chin_prop = (is_sitting and wr[1] > mouth_y and abs(wr[0] - face_x) < bw * 0.15)

                    if dist < min(bh * 0.15, bw * 0.32) and is_at_mouth_height and wrist_above_elbow and elbow_ang < 105.0 and not is_chin_prop:
                        is_smoking = True
                        break

            if is_smoking:
                s_start = trk.get('smoke_start_t') or now_t
                trk['smoke_start_t'] = s_start
                if (now_t - s_start) >= 1.5:
                    return 'smoking_gesture', 0.88, 'Persistent Hand-to-Mouth Smoking Gesture'
            else:
                trk['smoke_start_t'] = None

    # 6. Sitting Confirmation
    if is_sitting:
        sit_start = trk.get('sit_start_t') or now_t
        trk['sit_start_t'] = sit_start
        if (now_t - sit_start) >= 0.8:
            return 'sitting', 0.85, 'Seated Posture with Bent Knee Angles'
    else:
        trk['sit_start_t'] = None

    # 7. Loitering
    if _is_enabled('loitering'):
        if dwell_time >= 30.0:
            return 'loitering', 0.88, f'Dwell Time {int(dwell_time)}s >= 30s'

    return None, 0.0, None

def run_tests():
    enabled = ["sitting", "lying_down", "loitering", "smoking_gesture", "fighting_posture"]
    
    # -------------------------------------------------------------
    # Test 1: User holding phone sitting at desk (Image 4 & 6)
    # Wrists at mid-chest (y=175), shoulders at y=150, bh=140, bw=180
    # -------------------------------------------------------------
    trk1 = {'fight_start_t': time.time() - 5.0, 'sit_start_t': None}
    kp1 = [[100, 110, 0.9]] + [[0,0,0]]*4 + [[70, 150, 0.85], [130, 150, 0.85], [50, 195, 0.8], [150, 195, 0.8], [80, 175, 0.85], [120, 175, 0.85]] + [[0,0,0]]*6
    box1 = [10, 80, 190, 220]
    act1, _, _ = classify_pose_activity(trk1, kp1, box1, 10.0, enabled)
    assert act1 is None, f"Test 1 FAILED: Expected None, got {act1}"
    print("Test 1 PASSED: User holding phone at desk -> None (No false fight/smoke/lying)")

    # -------------------------------------------------------------
    # Test 2: User upper body portrait sitting upright (Image 2)
    # Box aspect ratio 1.66 (bw=200, bh=120), head at top, shoulders horizontal
    # -------------------------------------------------------------
    trk2 = {'lie_start_t': time.time() - 5.0}
    kp2 = [[100, 70, 0.85]] + [[0,0,0]]*4 + [[50, 110, 0.85], [150, 110, 0.85]] + [[0,0,0]]*10
    box2 = [20, 50, 220, 170]
    act2, _, _ = classify_pose_activity(trk2, kp2, box2, 10.0, enabled)
    assert act2 is None, f"Test 2 FAILED: Expected None, got {act2}"
    print("Test 2 PASSED: User portrait upright -> None (No false lying down)")

    # -------------------------------------------------------------
    # Test 3: Person lying in mud (Image 6)
    # Low-res figure, aspect ratio 1.63 (bw=36, bh=22), not upright
    # -------------------------------------------------------------
    trk3 = {'lie_start_t': time.time() - 1.0}
    box3 = [200, 250, 236, 272]  # bw=36, bh=22
    act3, conf3, sub3 = classify_pose_activity(trk3, None, box3, 5.0, enabled)
    assert act3 == 'lying_down', f"Test 3 FAILED: Expected lying_down, got {act3}"
    print(f"Test 3 PASSED: Person in mud -> {act3} ({sub3})")

    # -------------------------------------------------------------
    # Test 4: Man sitting cross-legged with hand on chin (Image 1)
    # Knees spread wide (x=40, x=160), hips at y=170, knees at y=180, hand on chin
    # -------------------------------------------------------------
    trk4 = {'sit_start_t': time.time() - 1.0, 'smoke_start_t': time.time() - 1.0}
    # kp indices: 0:nose, 5:l_sh, 6:r_sh, 7:l_el, 8:r_el, 9:l_wr, 10:r_wr, 11:l_hip, 12:r_hip, 13:l_knee, 14:r_knee, 15:l_ank, 16:r_ank
    kp4 = [[100, 70, 0.9]] + [[0,0,0]]*4 + [
        [70, 105, 0.9], [130, 105, 0.9],  # shoulders
        [50, 140, 0.8], [140, 130, 0.8],  # elbows
        [50, 170, 0.8], [105, 88, 0.85],  # wrists (right wrist propping chin under jaw)
        [80, 165, 0.85], [120, 165, 0.85], # hips
        [40, 180, 0.85], [160, 180, 0.85], # knees (spread wide)
        [70, 190, 0.8], [130, 190, 0.8]    # ankles
    ]
    box4 = [30, 50, 170, 200]  # bw=140, bh=150
    act4, conf4, sub4 = classify_pose_activity(trk4, kp4, box4, 5.0, enabled)
    assert act4 == 'sitting', f"Test 4 FAILED: Expected sitting, got {act4}"
    print(f"Test 4 PASSED: Man sitting cross-legged with hand on chin -> {act4} (NOT smoking!)")

    # -------------------------------------------------------------
    # Test 5: Boy pulling jacket / adjusting shirt (Image 5)
    # Wrists at chest level (y=130), nose at y=70, shoulders at y=105
    # -------------------------------------------------------------
    trk5 = {'smoke_start_t': time.time() - 5.0}
    kp5 = [[100, 70, 0.9]] + [[0,0,0]]*4 + [
        [65, 105, 0.9], [135, 105, 0.9],
        [40, 125, 0.8], [160, 125, 0.8],
        [60, 130, 0.85], [140, 130, 0.85], # hands pulling jacket at chest
        [80, 180, 0.8], [120, 180, 0.8],
        [80, 240, 0.8], [120, 240, 0.8],
        [80, 300, 0.8], [120, 300, 0.8]
    ]
    box5 = [35, 50, 165, 310]
    act5, _, _ = classify_pose_activity(trk5, kp5, box5, 5.0, enabled)
    assert act5 is None, f"Test 5 FAILED: Expected None, got {act5}"
    print("Test 5 PASSED: Boy pulling jacket -> None (No false smoking)")

    # -------------------------------------------------------------
    # Test 6: Karate / Martial Arts Combat (Image 4)
    # Two people close together, fists raised at face/shoulder level
    # -------------------------------------------------------------
    trk6 = {'fight_start_t': time.time() - 1.0}
    kp6 = [[90, 80, 0.9]] + [[0,0,0]]*4 + [
        [70, 110, 0.85], [110, 110, 0.85],
        [60, 130, 0.85], [120, 130, 0.85],
        [75, 100, 0.85], [105, 100, 0.85], # fists raised at head/shoulder level (guard)
        [80, 170, 0.8], [100, 170, 0.8],
        [75, 230, 0.8], [105, 230, 0.8],
        [70, 290, 0.8], [110, 290, 0.8]
    ]
    box6_a = [50, 60, 130, 300]
    box6_b = [140, 60, 220, 300]
    act6, conf6, sub6 = classify_pose_activity(trk6, kp6, box6_a, 5.0, enabled, all_persons=[box6_a, box6_b])
    assert act6 == 'fighting_posture', f"Test 6 FAILED: Expected fighting_posture, got {act6}"
    print(f"Test 6 PASSED: Karate sparring fighters -> {act6} ({sub6})")

    # -------------------------------------------------------------
    # Test 7: Real smoking gesture
    # Hand at lips/mouth (x=102, y=78, nose=100, 72), wrist above elbow
    # -------------------------------------------------------------
    trk7 = {'smoke_start_t': time.time() - 2.0}
    kp7 = [[100, 72, 0.9]] + [[0,0,0]]*4 + [
        [70, 110, 0.85], [130, 110, 0.85],
        [60, 160, 0.8], [140, 135, 0.8],  # right elbow at y=135
        [60, 200, 0.8], [103, 79, 0.9],    # right wrist at mouth (y=79 < y=135)
        [80, 180, 0.8], [120, 180, 0.8],
        [80, 240, 0.8], [120, 240, 0.8],
        [80, 300, 0.8], [120, 300, 0.8]
    ]
    box7 = [50, 50, 150, 310]
    act7, conf7, sub7 = classify_pose_activity(trk7, kp7, box7, 5.0, enabled)
    assert act7 == 'smoking_gesture', f"Test 7 FAILED: Expected smoking_gesture, got {act7}"
    print(f"Test 7 PASSED: Real smoking gesture -> {act7} ({sub7})")

    print("\nALL 7 TESTS PASSED ACCURATELY!")

if __name__ == '__main__':
    run_tests()
