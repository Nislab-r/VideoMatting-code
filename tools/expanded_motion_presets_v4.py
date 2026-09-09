"""Safe, data-driven procedural motion blueprints.

The source motion library is used to choose semantics, rhythm, side, and bounded
amplitudes. Source bone rotations and absolute poses are never applied. The
returned states are normalized by character height and are suitable for a
target-native IK solver.
"""

import hashlib
import math


CATALOG_VERSION = "expanded-safe-presets-v4"

LOWER_CHANNELS = (
    "weight_shift", "side_step", "forward_step", "back_step", "walk_cycle",
    "brisk_walk", "jog_cycle", "march", "squat", "side_lunge", "pivot_step",
    "soft_jump", "dance_step", "controlled_kick", "crouch_shift", "heel_tap",
)
TORSO_CHANNELS = (
    "neutral", "sway", "twist", "bow", "athletic_lean", "side_lean",
    "counter_rotate", "rise_fall", "body_wave", "look_back_twist",
)
ARM_CHANNELS = (
    "natural_swing", "single_reach", "double_reach", "overhead_open", "wave",
    "clap", "cross_body", "near_face", "outward_sweep", "downward_sweep",
    "alternating_pump", "presentation", "balance_open", "push", "pull",
    "celebration", "dance_flow", "compact_gesture", "arm_circle", "guarded_extension",
)
HEAD_CHANNELS = (
    "follow_torso", "look_side", "look_back", "nod", "counterbalance",
    "scan", "soft_tilt", "rhythmic_follow",
)
RHYTHMS = ("single", "double", "triple", "cyclic_slow", "cyclic_medium", "accented")

DEEP_LOWER = {"squat", "side_lunge", "crouch_shift"}
DYNAMIC_LOWER = {"brisk_walk", "jog_cycle", "soft_jump", "controlled_kick", "dance_step"}
TURNING_LOWER = {"pivot_step", "dance_step"}
WIDE_ARMS = {"double_reach", "overhead_open", "outward_sweep", "balance_open", "arm_circle", "celebration"}
CROSSING_ARMS = {"clap", "cross_body", "near_face", "push", "pull", "guarded_extension"}
HIGH_ARMS = {"overhead_open", "celebration", "arm_circle"}
FORWARD_ARMS = {"single_reach", "double_reach", "push", "guarded_extension"}
TORSO_EXTREMES = {"bow", "body_wave", "side_lean", "look_back_twist"}
GARMENT_UNSAFE_LOWER = {"squat", "side_lunge", "soft_jump", "controlled_kick", "crouch_shift"}
GARMENT_UNSAFE_ARMS = {"double_reach", "overhead_open", "clap", "cross_body", "near_face", "push", "pull", "celebration", "arm_circle", "guarded_extension"}
LOW_RISK_LOWER = {"weight_shift", "side_step", "forward_step", "back_step", "walk_cycle", "brisk_walk", "march", "pivot_step", "heel_tap"}
LOW_RISK_ARMS = {"natural_swing", "single_reach", "wave", "outward_sweep", "downward_sweep", "alternating_pump", "presentation", "balance_open", "dance_flow", "compact_gesture"}


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
        add_vec(state, active, sign * 0.10 * math.sin(angle) * e, -0.09 * e, 0.16 * (1.0 - math.cos(angle)) * e)
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
    if lower in DEEP_LOWER and hip_ratio < 0.11:
        item["lower"] = "stable_stance"
        item["lower_intensity"] = 0.0
        item["torso_channel"] = "neutral"
        changes.append("narrow_hip_profile_replaces_deep_lower_with_stable_stance")
        if item["arms"] in {"double_reach", "push", "guarded_extension"}:
            item["arms"] = "presentation"
            item["arm_intensity"] = min(item["arm_intensity"], 0.55)
            changes.append("narrow_hip_deep_lower_replaces_symmetric_forward_arms")
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
