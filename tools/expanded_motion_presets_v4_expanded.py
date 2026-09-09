"""Safe, data-driven procedural motion blueprints.

The source motion library is used to choose semantics, rhythm, side, and bounded
amplitudes. Source bone rotations and absolute poses are never applied. The
returned states are normalized by character height and are suitable for a
target-native IK solver.
"""

import hashlib
import math


CATALOG_VERSION = "expanded-safe-presets-v4-expanded"

LOWER_CHANNELS = (
    "weight_shift", "side_step", "forward_step", "back_step", "walk_cycle",
    "brisk_walk", "jog_cycle", "march", "squat", "side_lunge", "pivot_step",
    "soft_jump", "dance_step", "controlled_kick", "crouch_shift", "heel_tap",
    "diagonal_step", "step_touch", "toe_tap", "knee_lift", "calf_raise",
    "split_stance_rock", "shuffle_step", "box_step", "skater_step",
    "low_side_kick", "hamstring_curl", "crossover_step", "pendulum_step",
    "chasse_step", "turn_step", "quick_step",
)
TORSO_CHANNELS = (
    "neutral", "sway", "twist", "bow", "athletic_lean", "side_lean",
    "counter_rotate", "rise_fall", "body_wave", "look_back_twist",
    "chest_open", "chest_contract", "diagonal_lean", "pelvic_shift",
    "shoulder_roll", "torso_circle", "recoil", "reach_follow", "spiral",
    "breathing",
)
ARM_CHANNELS = (
    "natural_swing", "single_reach", "double_reach", "overhead_open", "wave",
    "clap", "cross_body", "near_face", "outward_sweep", "downward_sweep",
    "alternating_pump", "presentation", "balance_open", "push", "pull",
    "celebration", "dance_flow", "compact_gesture", "arm_circle", "guarded_extension",
    "single_overhead_reach", "alternating_reach", "diagonal_reach",
    "lateral_reach", "forward_scoop", "chest_open", "elbow_open",
    "shoulder_roll", "forearm_wave", "double_wave", "point_gesture",
    "salute", "hands_to_hips", "boxer_guard", "low_windmill",
    "high_low_opposition", "rowing", "offering", "side_punch",
    "wrist_flourish",
)
HEAD_CHANNELS = (
    "follow_torso", "look_side", "look_back", "nod", "counterbalance",
    "scan", "soft_tilt", "rhythmic_follow", "glance_up", "glance_down",
    "double_take", "chin_tuck", "gaze_track", "small_circle",
    "stabilize_gaze", "nod_turn",
)
RHYTHMS = ("single", "double", "triple", "cyclic_slow", "cyclic_medium", "accented")

DEEP_LOWER = {"squat", "side_lunge", "crouch_shift", "skater_step"}
DYNAMIC_LOWER = {"brisk_walk", "jog_cycle", "soft_jump", "controlled_kick", "dance_step", "shuffle_step", "skater_step", "low_side_kick", "chasse_step", "quick_step"}
TURNING_LOWER = {"pivot_step", "dance_step", "box_step", "crossover_step", "turn_step"}
WIDE_ARMS = {"double_reach", "overhead_open", "outward_sweep", "balance_open", "arm_circle", "celebration", "lateral_reach", "chest_open", "elbow_open", "low_windmill", "high_low_opposition"}
CROSSING_ARMS = {"clap", "cross_body", "near_face", "push", "pull", "guarded_extension", "forward_scoop", "boxer_guard", "rowing", "offering", "side_punch"}
HIGH_ARMS = {"overhead_open", "celebration", "arm_circle", "single_overhead_reach", "diagonal_reach", "high_low_opposition"}
FORWARD_ARMS = {"single_reach", "double_reach", "push", "guarded_extension", "alternating_reach", "diagonal_reach", "forward_scoop", "point_gesture", "rowing", "offering", "side_punch"}
TORSO_EXTREMES = {"bow", "body_wave", "side_lean", "look_back_twist", "diagonal_lean", "torso_circle", "spiral"}
GARMENT_UNSAFE_LOWER = {"squat", "side_lunge", "soft_jump", "controlled_kick", "crouch_shift", "skater_step", "low_side_kick", "crossover_step", "chasse_step"}
GARMENT_UNSAFE_ARMS = CROSSING_ARMS | HIGH_ARMS | {"double_reach", "shoulder_roll", "forearm_wave", "double_wave", "low_windmill"}
LOW_RISK_LOWER = set(LOWER_CHANNELS) - GARMENT_UNSAFE_LOWER - {"jog_cycle", "quick_step"}
LOW_RISK_ARMS = set(ARM_CHANNELS) - GARMENT_UNSAFE_ARMS - {"lateral_reach"}
NARROW_HIP_UNSAFE_LOWER = DEEP_LOWER | {
    "controlled_kick", "low_side_kick", "knee_lift", "hamstring_curl",
    "crossover_step", "chasse_step", "soft_jump",
}


def stable_int(value):
    return int(hashlib.sha1(value.encode("utf-8")).hexdigest()[:16], 16)


def clamp(value, low, high):
    return max(low, min(high, value))


def smooth01(value):
    value = clamp(value, 0.0, 1.0)
    return value * value * (3.0 - 2.0 * value)


def envelope(t, start=0.03, full=0.20, release=0.78, end=0.97):
    if t <= start or t >= end:
        return 0.0
    if t < full:
        return smooth01((t - start) / max(1e-6, full - start))
    if t <= release:
        return 1.0
    return 1.0 - smooth01((t - release) / max(1e-6, end - release))


def rhythm_wave(t, rhythm, phase=0.0):
    cycles = {
        "single": 0.5, "double": 1.0, "triple": 1.5,
        "cyclic_slow": 1.0, "cyclic_medium": 2.0, "accented": 1.5,
    }[rhythm]
    wave = math.sin(2.0 * math.pi * cycles * t + phase)
    if rhythm == "accented":
        wave = 0.72 * wave + 0.28 * math.sin(4.0 * math.pi * cycles * t + phase * 0.5)
    return wave * math.sin(math.pi * t) ** 0.65


def empty_state():
    return {
        "torso": [0.0, 0.0, 0.0], "yaw": 0.0, "pitch": 0.0,
        "head_yaw": 0.0, "head_pitch": 0.0,
        "hand_l": [0.0, 0.0, 0.0], "hand_r": [0.0, 0.0, 0.0],
        "foot_l": [0.0, 0.0, 0.0], "foot_r": [0.0, 0.0, 0.0],
        "foot_yaw_l": 0.0, "foot_yaw_r": 0.0,
        "hand_blend_l": 0.0, "hand_blend_r": 0.0,
    }


def add_vec(target, key, x=0.0, y=0.0, z=0.0):
    target[key][0] += x
    target[key][1] += y
    target[key][2] += z


def side_values(blueprint):
    side = blueprint.get("leading_side", "left")
    return ("l", 1.0) if side == "left" else ("r", -1.0)


def lower_state(state, blueprint, t, e, wave):
    channel = blueprint["lower"]
    side, sign = side_values(blueprint)
    other = "r" if side == "l" else "l"
    stride = blueprint["stride"]
    lift = blueprint["lift"]
    intensity = blueprint["lower_intensity"]
    pulse = max(0.0, math.sin(2.0 * math.pi * t)) ** 2 * e
    alternate = max(0.0, -math.sin(2.0 * math.pi * t)) ** 2 * e
    if channel == "weight_shift":
        add_vec(
            state, "torso", sign * 0.070 * intensity * e,
            0.010 * intensity * e, -0.020 * intensity * e,
        )
        state["yaw"] -= sign * math.radians(8.0) * intensity * e
    elif channel == "stable_stance":
        # Compatibility fallback: preserve the target's validated neutral leg
        # chain while other channels carry the action.
        pass
    elif channel == "side_step":
        add_vec(state, f"foot_{side}", sign * stride * e, -0.018 * e, lift * pulse)
        add_vec(state, "torso", sign * stride * 0.45 * e, 0.0, -0.018 * e)
    elif channel in {"forward_step", "back_step"}:
        direction = -1.0 if channel == "forward_step" else 1.0
        add_vec(state, f"foot_{side}", sign * 0.018 * e, direction * stride * e, lift * pulse)
        add_vec(state, "torso", sign * 0.012 * e, direction * stride * 0.38 * e, -0.012 * e)
    elif channel in {"walk_cycle", "brisk_walk", "jog_cycle", "march"}:
        rate = {"walk_cycle": 1.0, "brisk_walk": 1.5, "jog_cycle": 2.0, "march": 1.25}[channel]
        gait = math.sin(2.0 * math.pi * rate * t) * e
        left = max(0.0, gait) ** 2
        right = max(0.0, -gait) ** 2
        add_vec(state, "foot_l", 0.0, -stride * left, lift * left)
        add_vec(state, "foot_r", 0.0, -stride * right, lift * right)
        add_vec(state, "torso", 0.012 * gait, -0.018 * (left + right), -0.014 * (left + right))
        state["yaw"] += math.radians(5.0) * gait
    elif channel in {"squat", "crouch_shift"}:
        depth = (0.15 if channel == "squat" else 0.10) * intensity
        add_vec(state, "torso", sign * (0.0 if channel == "squat" else 0.035) * e, 0.018 * e, -depth * e)
        state["pitch"] += math.radians(7.0 if channel == "squat" else 12.0) * e
    elif channel == "narrow_stance_dip":
        # Symmetric shallow flexion for rigs whose narrow hip chain cannot
        # safely reproduce a lateral or deep squat without knee valgus.
        add_vec(state, "torso", 0.0, 0.006 * intensity * e, -0.040 * intensity * e)
        state["pitch"] += math.radians(3.0) * intensity * e
    elif channel == "side_lunge":
        add_vec(state, f"foot_{side}", sign * stride * 1.25 * e, -0.012 * e, lift * pulse)
        add_vec(state, "torso", sign * stride * 0.55 * e, 0.015 * e, -0.095 * intensity * e)
        state["yaw"] -= sign * math.radians(7.0) * e
    elif channel == "pivot_step":
        # A pivot is a planted turn, not a fixed-foot torso twist. Rotate the
        # stepping foot with the pelvis and let the support foot follow less.
        add_vec(state, f"foot_{side}", sign * 0.038 * e, -0.030 * e, lift * pulse * 0.45)
        state[f"foot_yaw_{side}"] += sign * math.radians(16.0) * e
        state[f"foot_yaw_{other}"] += sign * math.radians(7.0) * e
        state["yaw"] += sign * math.radians(19.0) * e
        add_vec(state, "torso", sign * 0.018 * e, 0.0, -0.010 * e)
    elif channel == "soft_jump":
        airborne = math.sin(math.pi * t) ** 2
        add_vec(state, "torso", 0.0, -0.025 * e, 0.075 * intensity * airborne * e)
        add_vec(state, "foot_l", -0.012 * e, -0.015 * e, 0.045 * airborne * e)
        add_vec(state, "foot_r", 0.012 * e, -0.015 * e, 0.045 * airborne * e)
    elif channel == "dance_step":
        add_vec(state, "torso", 0.070 * wave * intensity, -0.012 * abs(wave), -0.022 * abs(wave))
        state["yaw"] += math.radians(14.0) * wave
        add_vec(state, "foot_l", 0.035 * max(0.0, wave), -0.025 * max(0.0, wave), lift * 0.55 * max(0.0, wave))
        add_vec(state, "foot_r", -0.035 * max(0.0, -wave), -0.025 * max(0.0, -wave), lift * 0.55 * max(0.0, -wave))
    elif channel == "controlled_kick":
        kick = max(0.0, math.sin(math.pi * clamp((t - 0.20) / 0.55, 0.0, 1.0))) ** 2
        add_vec(state, f"foot_{side}", sign * 0.025 * kick, -stride * 0.80 * kick, lift * 1.35 * kick)
        add_vec(state, "torso", -sign * 0.025 * kick, 0.018 * kick, -0.018 * kick)
    elif channel == "heel_tap":
        add_vec(state, f"foot_{side}", sign * stride * 0.55 * e, -stride * 0.35 * e, lift * pulse * 0.65)
        add_vec(state, "torso", -sign * 0.025 * e, 0.0, -0.012 * e)
    elif channel == "diagonal_step":
        add_vec(state, f"foot_{side}", sign * stride * 0.72 * e, -stride * 0.72 * e, lift * pulse)
        add_vec(state, "torso", sign * stride * 0.26 * e, -stride * 0.22 * e, -0.012 * e)
        state["yaw"] -= sign * math.radians(7.0) * intensity * e
    elif channel == "step_touch":
        touch = max(0.0, math.sin(2.0 * math.pi * t)) ** 2 * e
        add_vec(state, f"foot_{side}", sign * stride * 0.72 * e, -0.015 * e, lift * 0.45 * touch)
        add_vec(state, f"foot_{other}", sign * stride * 0.22 * e, 0.0, lift * 0.18 * touch)
        add_vec(state, "torso", sign * stride * 0.32 * e, 0.0, -0.010 * e)
    elif channel == "toe_tap":
        add_vec(state, f"foot_{side}", sign * stride * 0.38 * e, -stride * 0.58 * e, lift * 0.32 * pulse)
        state[f"foot_yaw_{side}"] += sign * math.radians(8.0) * e
        add_vec(state, "torso", -sign * 0.018 * e, 0.0, -0.008 * e)
    elif channel == "knee_lift":
        lift_wave = max(0.0, math.sin(math.pi * t)) ** 2 * e
        add_vec(state, f"foot_{side}", sign * 0.015 * lift_wave, -0.045 * lift_wave, lift * 1.18 * lift_wave)
        add_vec(state, "torso", -sign * 0.022 * lift_wave, 0.012 * lift_wave, -0.010 * lift_wave)
    elif channel == "calf_raise":
        rise = max(0.0, math.sin(math.pi * t)) ** 2 * e
        add_vec(state, "torso", 0.0, 0.0, 0.034 * intensity * rise)
        add_vec(state, "foot_l", 0.0, -0.010 * rise, 0.018 * rise)
        add_vec(state, "foot_r", 0.0, -0.010 * rise, 0.018 * rise)
    elif channel == "split_stance_rock":
        rock = wave * e
        add_vec(state, f"foot_{side}", sign * 0.012 * e, -stride * 0.62 * e, lift * 0.25 * pulse)
        add_vec(state, f"foot_{other}", -sign * 0.010 * e, stride * 0.38 * e, lift * 0.15 * alternate)
        add_vec(state, "torso", sign * 0.015 * rock, -0.035 * rock, -0.012 * abs(rock))
    elif channel == "shuffle_step":
        shuffle = math.sin(4.0 * math.pi * t) * e
        add_vec(state, "foot_l", 0.035 * shuffle, -0.025 * max(0.0, shuffle), lift * 0.42 * max(0.0, shuffle))
        add_vec(state, "foot_r", 0.035 * shuffle, -0.025 * max(0.0, -shuffle), lift * 0.42 * max(0.0, -shuffle))
        add_vec(state, "torso", 0.045 * shuffle, -0.012 * abs(shuffle), -0.010 * abs(shuffle))
    elif channel == "box_step":
        phase = 2.0 * math.pi * t
        add_vec(state, "foot_l", 0.035 * math.sin(phase) * e, -stride * 0.45 * max(0.0, math.cos(phase)) * e, lift * 0.38 * max(0.0, math.sin(phase)) * e)
        add_vec(state, "foot_r", -0.035 * math.sin(phase) * e, stride * 0.32 * max(0.0, -math.cos(phase)) * e, lift * 0.38 * max(0.0, -math.sin(phase)) * e)
        state["yaw"] += math.radians(9.0) * math.sin(phase) * e
    elif channel == "skater_step":
        skate = wave * e
        add_vec(state, "torso", 0.070 * skate * intensity, -0.015 * abs(skate), -0.045 * abs(skate))
        add_vec(state, "foot_l", 0.055 * max(0.0, skate), 0.025 * max(0.0, skate), lift * 0.55 * max(0.0, -skate))
        add_vec(state, "foot_r", 0.055 * max(0.0, skate), 0.025 * max(0.0, -skate), lift * 0.55 * max(0.0, skate))
    elif channel == "low_side_kick":
        kick = max(0.0, math.sin(math.pi * clamp((t - 0.18) / 0.58, 0.0, 1.0))) ** 2
        add_vec(state, f"foot_{side}", sign * stride * 0.72 * kick, -0.018 * kick, lift * 0.72 * kick)
        add_vec(state, "torso", -sign * 0.030 * kick, 0.0, -0.012 * kick)
    elif channel == "hamstring_curl":
        curl = max(0.0, math.sin(math.pi * t)) ** 2 * e
        add_vec(state, f"foot_{side}", sign * 0.012 * curl, stride * 0.48 * curl, lift * 0.82 * curl)
        add_vec(state, "torso", -sign * 0.020 * curl, -0.008 * curl, -0.008 * curl)
    elif channel == "crossover_step":
        cross = max(0.0, math.sin(math.pi * t)) ** 2 * e
        add_vec(state, f"foot_{side}", -sign * stride * 0.42 * cross, -stride * 0.30 * cross, lift * 0.48 * cross)
        add_vec(state, "torso", sign * 0.035 * cross, 0.0, -0.010 * cross)
        state["yaw"] += sign * math.radians(9.0) * cross
    elif channel == "pendulum_step":
        pendulum = math.sin(2.0 * math.pi * t) * e
        add_vec(state, "foot_l", 0.050 * max(0.0, pendulum), -0.020 * abs(pendulum), lift * 0.45 * max(0.0, pendulum))
        add_vec(state, "foot_r", 0.050 * max(0.0, pendulum), -0.020 * abs(pendulum), lift * 0.45 * max(0.0, -pendulum))
        add_vec(state, "torso", 0.055 * pendulum, 0.0, -0.014 * abs(pendulum))
    elif channel == "chasse_step":
        chasse = math.sin(2.0 * math.pi * 1.5 * t) * e
        add_vec(state, "foot_l", 0.045 * chasse, -0.022 * max(0.0, chasse), lift * 0.42 * max(0.0, chasse))
        add_vec(state, "foot_r", 0.045 * chasse, -0.022 * max(0.0, -chasse), lift * 0.42 * max(0.0, -chasse))
        add_vec(state, "torso", 0.058 * chasse, -0.010 * abs(chasse), -0.012 * abs(chasse))
    elif channel == "turn_step":
        turn = smooth01(clamp((t - 0.12) / 0.72, 0.0, 1.0)) * e
        add_vec(state, f"foot_{side}", sign * 0.040 * turn, -0.040 * turn, lift * 0.35 * pulse)
        state[f"foot_yaw_{side}"] += sign * math.radians(18.0) * turn
        state[f"foot_yaw_{other}"] += sign * math.radians(9.0) * turn
        state["yaw"] += sign * math.radians(24.0) * intensity * turn
    elif channel == "quick_step":
        quick = math.sin(6.0 * math.pi * t) * e
        add_vec(state, "foot_l", 0.0, -stride * 0.34 * max(0.0, quick), lift * 0.48 * max(0.0, quick))
        add_vec(state, "foot_r", 0.0, -stride * 0.34 * max(0.0, -quick), lift * 0.48 * max(0.0, -quick))
        add_vec(state, "torso", 0.012 * quick, -0.012 * abs(quick), -0.010 * abs(quick))


def torso_state(state, blueprint, t, e, wave):
    channel = blueprint["torso_channel"]
    _, sign = side_values(blueprint)
    gain = blueprint["torso_intensity"]
    if channel == "sway":
        add_vec(state, "torso", 0.045 * gain * wave, 0.0, -0.012 * abs(wave))
        state["yaw"] += math.radians(8.0) * wave
    elif channel == "twist":
        state["yaw"] += sign * math.radians(24.0) * gain * e
    elif channel == "bow":
        state["pitch"] += math.radians(24.0) * gain * e
        add_vec(state, "torso", 0.0, 0.020 * e, -0.050 * gain * e)
    elif channel == "athletic_lean":
        state["pitch"] += math.radians(13.0) * gain * e
        add_vec(state, "torso", 0.0, -0.025 * e, -0.022 * e)
    elif channel == "side_lean":
        add_vec(state, "torso", sign * 0.045 * gain * e, 0.0, -0.018 * e)
        state["yaw"] -= sign * math.radians(6.0) * e
    elif channel == "counter_rotate":
        state["yaw"] -= sign * math.radians(16.0) * gain * wave
        add_vec(state, "torso", -sign * 0.025 * wave, 0.0, 0.0)
    elif channel == "rise_fall":
        add_vec(state, "torso", 0.0, 0.0, 0.026 * gain * wave)
    elif channel == "body_wave":
        state["pitch"] += math.radians(9.0) * gain * wave
        add_vec(state, "torso", 0.025 * wave, -0.018 * wave, 0.018 * math.sin(4.0 * math.pi * t) * e)
    elif channel == "look_back_twist":
        state["yaw"] += sign * math.radians(30.0) * gain * e
        add_vec(state, "torso", sign * 0.018 * e, 0.0, 0.0)
    elif channel == "chest_open":
        state["pitch"] -= math.radians(7.0) * gain * e
        add_vec(state, "torso", 0.0, 0.018 * e, 0.012 * gain * e)
    elif channel == "chest_contract":
        state["pitch"] += math.radians(9.0) * gain * e
        add_vec(state, "torso", 0.0, -0.015 * e, -0.018 * gain * e)
    elif channel == "diagonal_lean":
        add_vec(state, "torso", sign * 0.038 * gain * e, -0.028 * gain * e, -0.018 * e)
        state["yaw"] -= sign * math.radians(8.0) * gain * e
        state["pitch"] += math.radians(8.0) * gain * e
    elif channel == "pelvic_shift":
        add_vec(state, "torso", 0.052 * gain * wave, 0.0, -0.010 * abs(wave))
        state["yaw"] -= math.radians(5.0) * wave
    elif channel == "shoulder_roll":
        state["pitch"] += math.radians(5.0) * gain * math.sin(4.0 * math.pi * t) * e
        state["yaw"] += math.radians(7.0) * gain * wave
    elif channel == "torso_circle":
        angle = 2.0 * math.pi * t
        add_vec(state, "torso", 0.028 * gain * math.sin(angle) * e, -0.022 * gain * math.cos(angle) * e, -0.010 * e)
        state["yaw"] += math.radians(8.0) * gain * math.sin(angle) * e
        state["pitch"] += math.radians(7.0) * gain * math.cos(angle) * e
    elif channel == "recoil":
        accent = max(0.0, math.sin(2.0 * math.pi * t)) ** 2 * e
        add_vec(state, "torso", 0.0, 0.030 * gain * accent, -0.014 * accent)
        state["pitch"] -= math.radians(9.0) * gain * accent
    elif channel == "reach_follow":
        state["yaw"] += sign * math.radians(15.0) * gain * e
        state["pitch"] += math.radians(6.0) * gain * e
        add_vec(state, "torso", sign * 0.026 * e, -0.020 * e, -0.010 * e)
    elif channel == "spiral":
        state["yaw"] += sign * math.radians(20.0) * gain * wave
        state["pitch"] += math.radians(7.0) * gain * math.sin(4.0 * math.pi * t) * e
        add_vec(state, "torso", sign * 0.025 * wave, -0.012 * abs(wave), 0.0)
    elif channel == "breathing":
        breath = math.sin(math.pi * t) * e
        state["pitch"] -= math.radians(3.5) * gain * breath
        add_vec(state, "torso", 0.0, 0.008 * breath, 0.012 * gain * breath)


def arm_state(state, blueprint, t, e, wave):
    channel = blueprint["arms"]
    side, sign = side_values(blueprint)
    other = "r" if side == "l" else "l"
    gain = blueprint["arm_intensity"]
    active = f"hand_{side}"
    passive = f"hand_{other}"
    state[f"hand_blend_{side}"] = max(state[f"hand_blend_{side}"], clamp(gain * e, 0.0, 1.0))
    if channel == "natural_swing":
        add_vec(state, "hand_l", 0.0, -0.055 * wave, 0.025 * wave)
        add_vec(state, "hand_r", 0.0, 0.055 * wave, -0.025 * wave)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.30 * e
    elif channel == "single_reach":
        add_vec(state, active, sign * 0.055 * gain * e, -0.16 * gain * e, 0.20 * gain * e)
    elif channel == "double_reach":
        for key, s in (("hand_l", 1.0), ("hand_r", -1.0)):
            # Reach forward toward the midline. Moving farther outward while
            # descending produces the characteristic shoulder/elbow twist.
            add_vec(state, key, -s * 0.028 * gain * e, -0.13 * gain * e, 0.11 * gain * e)
        state["hand_blend_l"] = state["hand_blend_r"] = min(1.0, gain * e)
    elif channel == "overhead_open":
        add_vec(state, "hand_l", -0.055 * gain * e, -0.07 * e, 0.31 * gain * e)
        add_vec(state, "hand_r", 0.055 * gain * e, -0.07 * e, 0.31 * gain * e)
        state["hand_blend_l"] = state["hand_blend_r"] = min(1.0, gain * e)
    elif channel == "wave":
        oscillation = math.sin(6.0 * math.pi * t) * envelope(t, 0.20, 0.30, 0.70, 0.82)
        add_vec(state, active, sign * 0.045 * oscillation, -0.10 * gain * e, 0.25 * gain * e)
    elif channel == "clap":
        together = max(0.0, math.sin(2.0 * math.pi * t)) ** 2 * e
        add_vec(state, "hand_l", -0.040 * together, -0.09 * together, 0.12 * together)
        add_vec(state, "hand_r", 0.040 * together, -0.09 * together, 0.12 * together)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.72 * together
    elif channel == "cross_body":
        add_vec(state, active, -sign * 0.11 * gain * e, -0.09 * e, 0.10 * e)
    elif channel == "near_face":
        add_vec(state, active, -sign * 0.025 * e, -0.075 * e, 0.23 * gain * e)
    elif channel in {"outward_sweep", "balance_open"}:
        # The production Rigify solver uses +X for the anatomical left side.
        add_vec(state, "hand_l", 0.15 * gain * e, -0.055 * e, 0.12 * e)
        add_vec(state, "hand_r", -0.15 * gain * e, -0.055 * e, 0.12 * e)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.72 * e
    elif channel == "downward_sweep":
        add_vec(state, active, sign * 0.08 * gain * e, -0.10 * e, -0.045 * e)
    elif channel == "alternating_pump":
        add_vec(state, "hand_l", 0.0, -0.075 * max(0.0, wave), 0.13 * max(0.0, wave))
        add_vec(state, "hand_r", 0.0, -0.075 * max(0.0, -wave), 0.13 * max(0.0, -wave))
        state["hand_blend_l"] = state["hand_blend_r"] = 0.55 * e
    elif channel == "presentation":
        add_vec(state, active, sign * 0.13 * gain * e, -0.08 * e, 0.10 * e)
    elif channel in {"push", "guarded_extension"}:
        add_vec(state, active, sign * 0.035 * e, -0.14 * gain * e, 0.10 * e)
        add_vec(state, passive, -sign * 0.020 * e, -0.075 * e, 0.09 * e)
        state[f"hand_blend_{other}"] = max(state[f"hand_blend_{other}"], 0.42 * e)
    elif channel == "pull":
        add_vec(state, active, sign * 0.055 * e, 0.055 * gain * e, 0.12 * e)
    elif channel == "celebration":
        add_vec(state, "hand_l", -0.06 * e, -0.05 * e, 0.29 * gain * e)
        add_vec(state, "hand_r", 0.06 * e, -0.05 * e, 0.29 * gain * e)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.90 * e
    elif channel == "dance_flow":
        add_vec(state, "hand_l", -0.08 * wave, -0.08 * e, 0.15 * (0.5 + 0.5 * wave) * e)
        add_vec(state, "hand_r", -0.08 * wave, -0.08 * e, 0.15 * (0.5 - 0.5 * wave) * e)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.62 * e
    elif channel == "arm_circle":
        angle = 2.0 * math.pi * t
        add_vec(
            state, active,
            sign * 0.10 * gain * math.sin(angle) * e,
            -0.09 * gain * e,
            0.16 * gain * (1.0 - math.cos(angle)) * e,
        )
    elif channel == "single_overhead_reach":
        add_vec(state, active, sign * 0.035 * gain * e, -0.070 * e, 0.285 * gain * e)
        add_vec(state, passive, -sign * 0.018 * e, -0.035 * e, 0.040 * e)
        state[f"hand_blend_{other}"] = max(state[f"hand_blend_{other}"], 0.24 * e)
    elif channel == "alternating_reach":
        left = max(0.0, wave) * e
        right = max(0.0, -wave) * e
        add_vec(state, "hand_l", 0.030 * left, -0.145 * gain * left, 0.115 * left)
        add_vec(state, "hand_r", -0.030 * right, -0.145 * gain * right, 0.115 * right)
        state["hand_blend_l"] = 0.70 * left
        state["hand_blend_r"] = 0.70 * right
    elif channel == "diagonal_reach":
        add_vec(state, active, sign * 0.095 * gain * e, -0.115 * gain * e, 0.205 * gain * e)
        add_vec(state, passive, -sign * 0.030 * e, -0.045 * e, 0.025 * e)
    elif channel == "lateral_reach":
        add_vec(state, active, sign * 0.165 * gain * e, -0.035 * e, 0.105 * gain * e)
    elif channel == "forward_scoop":
        scoop = max(0.0, math.sin(math.pi * t)) * e
        add_vec(state, "hand_l", -0.025 * scoop, -0.115 * gain * scoop, 0.075 * scoop)
        add_vec(state, "hand_r", 0.025 * scoop, -0.115 * gain * scoop, 0.075 * scoop)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.64 * scoop
    elif channel == "chest_open":
        add_vec(state, "hand_l", 0.125 * gain * e, 0.025 * e, 0.105 * e)
        add_vec(state, "hand_r", -0.125 * gain * e, 0.025 * e, 0.105 * e)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.68 * e
    elif channel == "elbow_open":
        add_vec(state, "hand_l", 0.105 * gain * e, -0.020 * e, 0.135 * e)
        add_vec(state, "hand_r", -0.105 * gain * e, -0.020 * e, 0.135 * e)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.62 * e
    elif channel == "shoulder_roll":
        roll = math.sin(4.0 * math.pi * t) * e
        add_vec(state, "hand_l", 0.025 * roll, 0.015 * roll, 0.045 * roll)
        add_vec(state, "hand_r", 0.025 * roll, -0.015 * roll, -0.045 * roll)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.35 * e
    elif channel == "forearm_wave":
        flutter = math.sin(8.0 * math.pi * t) * envelope(t, 0.15, 0.25, 0.75, 0.88)
        add_vec(state, active, sign * (0.055 + 0.025 * flutter) * e, -0.075 * e, 0.205 * gain * e)
    elif channel == "double_wave":
        flutter = math.sin(6.0 * math.pi * t) * envelope(t, 0.18, 0.28, 0.72, 0.84)
        add_vec(state, "hand_l", 0.035 * flutter, -0.075 * e, 0.225 * gain * e)
        add_vec(state, "hand_r", 0.035 * flutter, -0.075 * e, 0.225 * gain * e)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.75 * e
    elif channel == "point_gesture":
        add_vec(state, active, sign * 0.028 * e, -0.155 * gain * e, 0.105 * e)
    elif channel == "salute":
        add_vec(state, active, -sign * 0.018 * e, -0.055 * e, 0.245 * gain * e)
    elif channel == "hands_to_hips":
        add_vec(state, "hand_l", -0.070 * gain * e, 0.030 * e, 0.040 * e)
        add_vec(state, "hand_r", 0.070 * gain * e, 0.030 * e, 0.040 * e)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.58 * e
    elif channel == "boxer_guard":
        add_vec(state, "hand_l", -0.030 * e, -0.075 * e, 0.175 * gain * e)
        add_vec(state, "hand_r", 0.030 * e, -0.075 * e, 0.175 * gain * e)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.66 * e
    elif channel == "low_windmill":
        angle = 2.0 * math.pi * t
        add_vec(state, "hand_l", 0.105 * math.sin(angle) * e, -0.060 * e, 0.095 * (1.0 - math.cos(angle)) * e)
        add_vec(state, "hand_r", -0.105 * math.sin(angle) * e, -0.060 * e, 0.095 * (1.0 + math.cos(angle)) * e)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.60 * e
    elif channel == "high_low_opposition":
        add_vec(state, active, sign * 0.065 * gain * e, -0.055 * e, 0.265 * gain * e)
        add_vec(state, passive, -sign * 0.095 * gain * e, -0.045 * e, -0.025 * e)
        state[f"hand_blend_{other}"] = max(state[f"hand_blend_{other}"], 0.55 * e)
    elif channel == "rowing":
        row = max(0.0, math.sin(math.pi * t)) * e
        add_vec(state, "hand_l", -0.030 * row, -0.115 * row, 0.095 * row)
        add_vec(state, "hand_r", 0.030 * row, -0.115 * row, 0.095 * row)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.64 * row
    elif channel == "offering":
        add_vec(state, "hand_l", -0.020 * e, -0.115 * gain * e, 0.095 * e)
        add_vec(state, "hand_r", 0.020 * e, -0.115 * gain * e, 0.095 * e)
        state["hand_blend_l"] = state["hand_blend_r"] = 0.60 * e
    elif channel == "side_punch":
        add_vec(state, active, sign * 0.135 * gain * e, -0.085 * gain * e, 0.115 * e)
        add_vec(state, passive, -sign * 0.025 * e, -0.055 * e, 0.135 * e)
        state[f"hand_blend_{other}"] = max(state[f"hand_blend_{other}"], 0.52 * e)
    elif channel == "wrist_flourish":
        flourish = math.sin(6.0 * math.pi * t) * e
        add_vec(state, active, sign * (0.050 + 0.025 * flourish) * e, -0.055 * e, 0.145 * gain * e)
    else:  # compact_gesture
        add_vec(state, active, sign * 0.045 * wave, -0.065 * e, 0.095 * e)


def head_state(state, blueprint, t, e, wave):
    channel = blueprint["head"]
    _, sign = side_values(blueprint)
    gain = blueprint["head_intensity"]
    if channel == "follow_torso":
        state["head_yaw"] += state["yaw"] * 0.38
        state["head_pitch"] += state["pitch"] * 0.25
    elif channel == "look_side":
        state["head_yaw"] += sign * math.radians(22.0) * gain * e
    elif channel == "look_back":
        state["head_yaw"] += sign * math.radians(38.0) * gain * e
    elif channel == "nod":
        state["head_pitch"] += math.radians(11.0) * gain * wave
    elif channel == "counterbalance":
        state["head_yaw"] -= state["yaw"] * 0.55
        state["head_pitch"] -= state["pitch"] * 0.20
    elif channel == "scan":
        state["head_yaw"] += math.radians(24.0) * gain * wave
    elif channel == "soft_tilt":
        state["head_pitch"] += math.radians(7.0) * gain * wave
        state["head_yaw"] += sign * math.radians(8.0) * e
    elif channel == "rhythmic_follow":
        state["head_yaw"] += math.radians(13.0) * gain * wave
        state["head_pitch"] += math.radians(5.0) * gain * math.sin(4.0 * math.pi * t) * e
    elif channel == "glance_up":
        state["head_pitch"] -= math.radians(10.0) * gain * e
        state["head_yaw"] += sign * math.radians(4.0) * e
    elif channel == "glance_down":
        state["head_pitch"] += math.radians(12.0) * gain * e
        state["head_yaw"] -= sign * math.radians(4.0) * e
    elif channel == "double_take":
        turn = math.sin(3.0 * math.pi * t) * e
        state["head_yaw"] += sign * math.radians(20.0) * gain * turn
    elif channel == "chin_tuck":
        state["head_pitch"] += math.radians(8.0) * gain * e
    elif channel == "gaze_track":
        state["head_yaw"] += math.radians(18.0) * gain * math.sin(math.pi * (t - 0.5)) * e
        state["head_pitch"] += math.radians(5.0) * gain * wave
    elif channel == "small_circle":
        angle = 2.0 * math.pi * t
        state["head_yaw"] += math.radians(9.0) * gain * math.sin(angle) * e
        state["head_pitch"] += math.radians(6.0) * gain * math.cos(angle) * e
    elif channel == "stabilize_gaze":
        state["head_yaw"] -= state["yaw"] * 0.72
        state["head_pitch"] -= state["pitch"] * 0.42
    elif channel == "nod_turn":
        state["head_yaw"] += sign * math.radians(14.0) * gain * e
        state["head_pitch"] += math.radians(8.0) * gain * wave


def enforce_compatibility(blueprint, character_profile=None):
    """Return a bounded copy and list every automatic conflict resolution."""
    item = dict(blueprint)
    changes = []
    lower, torso, arms = item["lower"], item["torso_channel"], item["arms"]
    if lower in DEEP_LOWER and torso in {"bow", "body_wave"}:
        item["torso_channel"] = "athletic_lean"
        changes.append("deep_lower_with_flexion->athletic_lean")
    if lower in DEEP_LOWER:
        item["lower_intensity"] = min(item["lower_intensity"], 0.78)
        item["torso_intensity"] = min(item["torso_intensity"], 0.66)
        if arms in FORWARD_ARMS:
            item["arm_intensity"] = min(item["arm_intensity"], 0.58)
            changes.append("deep_lower_caps_forward_reach")
        elif arms in WIDE_ARMS:
            item["arm_intensity"] = min(item["arm_intensity"], 0.62)
            changes.append("deep_lower_caps_wide_arms")
    if lower == "soft_jump" and arms in CROSSING_ARMS:
        item["arms"] = "balance_open"
        changes.append("jump_with_crossing_arms->balance_open")
    if lower in DYNAMIC_LOWER and arms in HIGH_ARMS:
        item["arm_intensity"] = min(item["arm_intensity"], 0.72)
        changes.append("dynamic_lower_caps_high_arms")
    if lower in DYNAMIC_LOWER and arms in WIDE_ARMS:
        item["arm_intensity"] = min(item["arm_intensity"], 0.66)
        changes.append("dynamic_lower_caps_wide_arms")
    if lower in TURNING_LOWER and item["torso_channel"] == "counter_rotate":
        item["torso_channel"] = "neutral"
        changes.append("opposed_turn_channels->neutral_torso")
    if arms in CROSSING_ARMS and item["torso_channel"] in {"twist", "look_back_twist"}:
        item["arm_intensity"] = min(item["arm_intensity"], 0.68)
        changes.append("cross_body_with_twist_caps_arms")
    if arms in HIGH_ARMS and item["torso_channel"] in TORSO_EXTREMES:
        item["torso_intensity"] = min(item["torso_intensity"], 0.58)
        item["arm_intensity"] = min(item["arm_intensity"], 0.64)
        changes.append("high_arms_caps_extreme_torso")
    if lower in {"controlled_kick", "side_lunge"} and arms in CROSSING_ARMS | HIGH_ARMS:
        item["arms"] = "balance_open"
        item["arm_intensity"] = min(item["arm_intensity"], 0.66)
        changes.append("single_leg_excursion_replaces_conflicting_arms")
    if lower in DEEP_LOWER and arms in CROSSING_ARMS:
        item["arms"] = "presentation"
        item["arm_intensity"] = min(item["arm_intensity"], 0.60)
        changes.append("deep_lower_replaces_crossing_arms")
    if lower in DYNAMIC_LOWER and item["torso_channel"] == "body_wave":
        fallback = ("weight_shift", "side_step", "heel_tap")[
            stable_int(str(item.get("preset_id", "body_wave")) + ":lower") % 3
        ]
        item["lower"] = fallback
        item["lower_intensity"] = min(item["lower_intensity"], 0.82)
        item["stride"] = min(item["stride"], 0.112)
        item["lift"] = min(item["lift"], 0.055)
        item["torso_intensity"] = min(item["torso_intensity"], 0.72)
        changes.append(f"body_wave_replaces_dynamic_lower->{fallback}")
    profile = character_profile or {}
    hip_ratio = float(profile.get("hip_width_ratio", 0.14))
    if item["lower"] in NARROW_HIP_UNSAFE_LOWER and hip_ratio < 0.115:
        fallback = ("step_touch", "side_step", "heel_tap", "split_stance_rock")[
            stable_int(str(item.get("preset_id", "narrow_hip")) + ":hip") % 4
        ]
        item["lower"] = fallback
        item["lower_intensity"] = min(item["lower_intensity"], 0.82)
        item["stride"] = min(item["stride"], 0.102)
        item["lift"] = min(item["lift"], 0.050)
        changes.append(f"narrow_hip_profile_replaces_lateral_or_deep_lower->{fallback}")
        if item["arms"] in {"double_reach", "push", "guarded_extension"}:
            item["arms"] = "presentation"
            item["arm_intensity"] = min(item["arm_intensity"], 0.55)
            changes.append("narrow_hip_replaces_symmetric_forward_arms")
    if profile.get("has_skirt_or_robe") and item["lower"] in GARMENT_UNSAFE_LOWER:
        fallback = ("weight_shift", "side_step", "pivot_step", "heel_tap")[
            stable_int(str(item.get("preset_id", "garment"))) % 4
        ]
        item["lower"] = fallback
        item["lower_intensity"] = min(item["lower_intensity"], 0.84)
        item["stride"] = min(item["stride"], 0.105)
        item["lift"] = min(item["lift"], 0.052)
        changes.append(f"skirt_replaces_leg_excursion->{fallback}")
    if profile.get("has_long_sleeve_or_cape") and item["arms"] in GARMENT_UNSAFE_ARMS:
        fallback = ("natural_swing", "presentation", "outward_sweep", "compact_gesture")[
            stable_int(str(item.get("preset_id", "sleeve")) + ":arms") % 4
        ]
        item["arms"] = fallback
        item["arm_intensity"] = min(item["arm_intensity"], 0.70)
        changes.append(f"long_sleeve_replaces_collision_prone_arms->{fallback}")
    if profile.get("has_armor_or_bulky_shoulders"):
        item["arm_intensity"] = min(item["arm_intensity"], 0.68)
        if item["arms"] in CROSSING_ARMS | HIGH_ARMS:
            item["arms"] = "presentation"
            changes.append("bulky_replaces_crossing_or_high_arms")
    arm_ratio = float(profile.get("arm_length_ratio", 0.30))
    if arm_ratio < 0.27 and item["arms"] in FORWARD_ARMS | WIDE_ARMS:
        item["arm_intensity"] = min(item["arm_intensity"], 0.56)
        changes.append("short_arm_profile_caps_reach")
    # Increase visible motion only for channels with a large geometric safety
    # margin. Risky reaches, deep flexion and airborne poses keep their old cap.
    if item["lower"] in LOW_RISK_LOWER:
        item["lower_intensity"] = min(0.98, item["lower_intensity"] * 1.08)
        item["stride"] = min(0.158, item["stride"] * 1.06)
    if item["arms"] in LOW_RISK_ARMS:
        item["arm_intensity"] = min(0.96, item["arm_intensity"] * 1.08)
    if item["torso_channel"] in {"sway", "twist", "athletic_lean", "rise_fall", "neutral"}:
        item["torso_intensity"] = min(0.94, item["torso_intensity"] * 1.06)
    item["head_intensity"] = min(0.92, item["head_intensity"] * 1.04)
    item["compatibility_repairs"] = changes
    return item


def compose_state(blueprint, t, character_profile=None):
    item = enforce_compatibility(blueprint, character_profile)
    t = clamp(t, 0.0, 1.0)
    t = smooth01(t + item.get("phase_bias", 0.0) * math.sin(math.pi * t))
    e = envelope(t)
    wave = rhythm_wave(t, item["rhythm"], item.get("phase", 0.0))
    state = empty_state()
    lower_state(state, item, t, e, wave)
    torso_state(state, item, t, e, wave)
    arm_state(state, item, t, e, wave)
    head_state(state, item, t, e, wave)
    # Give compatible multi-channel actions a visibly larger silhouette while
    # keeping risky flexion/reach combinations at their validated magnitude.
    if (
        item["lower"] in LOW_RISK_LOWER
        and item["arms"] in LOW_RISK_ARMS
        and item["torso_channel"] not in TORSO_EXTREMES
    ):
        coordination_gain = 1.10
    elif item["lower"] not in DEEP_LOWER and item["arms"] not in CROSSING_ARMS:
        coordination_gain = 1.04
    else:
        coordination_gain = 1.0
    for key in ("torso", "hand_l", "hand_r", "foot_l", "foot_r"):
        state[key] = [value * coordination_gain for value in state[key]]
    for key in ("yaw", "pitch", "head_yaw", "head_pitch", "foot_yaw_l", "foot_yaw_r"):
        state[key] *= coordination_gain
    settle = 1.0 - smooth01((t - 0.86) / 0.10) if t > 0.86 else 1.0
    settle = clamp(settle, 0.0, 1.0)
    for key in ("torso", "hand_l", "hand_r", "foot_l", "foot_r"):
        state[key] = [value * settle for value in state[key]]
    for key in (
        "yaw", "pitch", "head_yaw", "head_pitch", "foot_yaw_l", "foot_yaw_r",
        "hand_blend_l", "hand_blend_r",
    ):
        state[key] *= settle
    return state


def trajectory_descriptor(blueprint, frames=91):
    samples = [compose_state(blueprint, index / (frames - 1)) for index in range(frames)]
    keys = ("torso", "hand_l", "hand_r", "foot_l", "foot_r")
    descriptor = []
    for key in keys:
        for axis in range(3):
            values = [state[key][axis] for state in samples]
            peak = max(range(len(values)), key=lambda i: abs(values[i]))
            descriptor.extend((min(values), max(values), peak / (frames - 1)))
    for key in ("yaw", "pitch", "head_yaw", "head_pitch", "foot_yaw_l", "foot_yaw_r"):
        values = [state[key] for state in samples]
        peak = max(range(len(values)), key=lambda i: abs(values[i]))
        descriptor.extend((min(values), max(values), peak / (frames - 1)))
    return tuple(round(value, 6) for value in descriptor)


def trajectory_fingerprint(blueprint):
    payload = ",".join(f"{value:.6f}" for value in trajectory_descriptor(blueprint))
    return hashlib.sha1(payload.encode("ascii")).hexdigest()[:24]


def validate_trajectory(blueprint, frames=181):
    """Reject discontinuous, implausible, or effectively static presets."""
    samples = [compose_state(blueprint, index / (frames - 1)) for index in range(frames)]

    def feature(state):
        values = []
        for key in ("torso", "hand_l", "hand_r", "foot_l", "foot_r"):
            values.extend(state[key])
        values.extend((
            state["yaw"] * 0.28, state["pitch"] * 0.28,
            state["head_yaw"] * 0.20, state["head_pitch"] * 0.20,
            state["foot_yaw_l"] * 0.12, state["foot_yaw_r"] * 0.12,
            state["hand_blend_l"] * 0.12, state["hand_blend_r"] * 0.12,
        ))
        return values

    features = [feature(state) for state in samples]
    base = features[0]
    amplitudes = [sum((value - start) ** 2 for value, start in zip(item, base)) ** 0.5 for item in features]
    velocities = [
        sum((b - a) ** 2 for a, b in zip(first, second)) ** 0.5
        for first, second in zip(features, features[1:])
    ]
    accelerations = [abs(second - first) for first, second in zip(velocities, velocities[1:])]
    max_hand = max(
        sum(value * value for value in state[key]) ** 0.5
        for state in samples for key in ("hand_l", "hand_r")
    )
    max_foot = max(
        sum(value * value for value in state[key]) ** 0.5
        for state in samples for key in ("foot_l", "foot_r")
    )
    min_foot_z = min(state[key][2] for state in samples for key in ("foot_l", "foot_r"))
    max_yaw = max(abs(state["yaw"]) for state in samples)
    max_pitch = max(abs(state["pitch"]) for state in samples)
    max_foot_yaw = max(
        abs(state[key]) for state in samples for key in ("foot_yaw_l", "foot_yaw_r")
    )
    min_foot_side_separation = min(
        state["foot_l"][0] - state["foot_r"][0] for state in samples
    )
    max_hand_side_separation = max(
        state["hand_l"][0] - state["hand_r"][0] for state in samples
    )
    terminal = max(6, round(frames * 0.04))
    terminal_velocity = max(velocities[-terminal:], default=0.0)
    report = {
        "amplitude_normalized": max(amplitudes),
        "max_frame_velocity_normalized": max(velocities, default=0.0),
        "max_frame_acceleration_normalized": max(accelerations, default=0.0),
        "start_end_delta_normalized": sum((a - b) ** 2 for a, b in zip(features[0], features[-1])) ** 0.5,
        "terminal_velocity_normalized": terminal_velocity,
        "max_hand_offset_normalized": max_hand,
        "max_foot_offset_normalized": max_foot,
        "min_foot_height_normalized": min_foot_z,
        "max_yaw_deg": math.degrees(max_yaw),
        "max_pitch_deg": math.degrees(max_pitch),
        "max_foot_yaw_deg": math.degrees(max_foot_yaw),
        "minimum_foot_side_offset_difference": min_foot_side_separation,
        "maximum_hand_side_offset_difference": max_hand_side_separation,
    }
    failures = []
    if report["amplitude_normalized"] < 0.075:
        failures.append("nearly_static")
    if report["max_frame_velocity_normalized"] > 0.10:
        failures.append("frame_velocity")
    if report["max_frame_acceleration_normalized"] > 0.060:
        failures.append("frame_acceleration")
    if report["start_end_delta_normalized"] > 0.035 or terminal_velocity > 0.0035:
        failures.append("unnatural_recovery")
    if max_hand > 0.34:
        failures.append("hand_reach")
    if max_foot > 0.24 or min_foot_z < -1e-6:
        failures.append("foot_reach_or_ground")
    if max_yaw > math.radians(58.0) or max_pitch > math.radians(40.0):
        failures.append("torso_joint_limit")
    if max_foot_yaw > math.radians(24.0):
        failures.append("foot_yaw_limit")
    if min_foot_side_separation < -0.14:
        failures.append("leg_crossing_proxy")
    if blueprint["arms"] in {"outward_sweep", "balance_open"} and max_hand_side_separation < 0.12:
        failures.append("outward_arm_semantics")
    report["status"] = "passed" if not failures else "failed"
    report["failures"] = failures
    return report


def blueprint_signature(blueprint):
    fields = (
        blueprint["lower"], blueprint["torso_channel"], blueprint["arms"],
        blueprint["head"], blueprint["rhythm"], blueprint["leading_side"],
        f"{blueprint['lower_intensity']:.4f}", f"{blueprint['torso_intensity']:.4f}",
        f"{blueprint['arm_intensity']:.4f}", f"{blueprint['phase']:.4f}",
    )
    return hashlib.sha1("|".join(fields).encode("utf-8")).hexdigest()[:24]
