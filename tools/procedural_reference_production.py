import argparse
import gzip
import hashlib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Quaternion, Vector


DATA_ROOT = Path(os.environ["DATA_ROOT"])
sys.path.insert(0, str(DATA_ROOT / 'tools'))
from production_quality import job_settings, apply_quality, configure_render
from production_quality.garments import run_guard, validate_report as validate_garment_report
from human_registry import verify_human_job
HELPER_PATH = str(DATA_ROOT / "tools/production_quality/backend.py")
EXPANDED_PRESET_PATH = str(DATA_ROOT / "tools/expanded_motion_presets.py")
ACTION_PARAMS = {}
_EXPANDED_PRESETS = None


def align_hair_variant_visibility(helper, target):
    """Select the hair variant that belongs to the currently visible outfit.

    Several source characters store every costume and hairstyle in one blend
    file.  Imported visibility is not always consistent: a Mohawk costume can
    arrive with Default hair enabled and Hair_Mohawk hidden.  Object-name
    suffixes are the most reliable cross-asset signal available for repairing
    that mismatch without maintaining character-specific rules.
    """
    hair_words = ("hair", "ponytail", "braid", "bang", "cheveux", "kami")
    ignored = {
        "accessory", "accessories", "alt", "base", "body", "boots", "bottom",
        "bottoms", "cape", "default", "dress", "earring", "earrings", "glove",
        "gloves", "hair", "jacket", "kami", "necklace", "of", "outfit", "pants",
        "ponytail", "braid", "bang", "cheveux", "shirt", "shoe", "shoes", "skirt",
        "swimsuit", "top", "underwear",
    }

    def attached(obj):
        armatures = {
            modifier.object for modifier in obj.modifiers
            if modifier.type == "ARMATURE" and modifier.object is not None
        }
        if obj.parent and obj.parent.type == "ARMATURE":
            armatures.add(obj.parent)
        return target in armatures

    def tokens(name):
        cleaned = "".join(char.lower() if char.isalnum() else " " for char in name)
        return {word for word in cleaned.split() if len(word) > 1 and word not in ignored}

    def is_hair(obj):
        name = obj.name.lower()
        return any(word in name for word in hair_words)

    attached_meshes = [
        obj for obj in bpy.context.scene.objects
        if obj.type == "MESH" and attached(obj)
    ]
    candidates = [obj for obj in attached_meshes if is_hair(obj)]
    # helper.character_meshes() already resolves collection-level costume
    # visibility.  hide_render alone is insufficient because many source files
    # leave every outfit object's flag enabled inside mutually hidden groups.
    visible_outfits = [
        obj for obj in helper.character_meshes(target) if not is_hair(obj)
    ]
    outfit_tokens = set().union(*(tokens(obj.name) for obj in visible_outfits))
    original_visibility = {obj.name: not obj.hide_render for obj in candidates}
    scores = {
        obj.name: len(tokens(obj.name).intersection(outfit_tokens)) for obj in candidates
    }
    best_score = max(scores.values(), default=0)
    selected = []
    if best_score > 0:
        selected = [obj for obj in candidates if scores[obj.name] == best_score]
        for obj in candidates:
            obj.hide_render = obj not in selected

    return {
        "status": "matched" if best_score > 0 else "preserved_source_visibility",
        "outfit_tokens": sorted(outfit_tokens),
        "best_score": best_score,
        "enabled_hair_variants": sorted(
            obj.name for obj in candidates if not obj.hide_render
        ),
        "disabled_hair_variants": sorted(
            obj.name for obj in candidates if obj.hide_render
        ),
        "original_hair_visibility": original_visibility,
    }


def load_helper():
    spec = importlib.util.spec_from_file_location("retarget_helper", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_expanded_presets():
    global _EXPANDED_PRESETS
    if _EXPANDED_PRESETS is not None:
        return _EXPANDED_PRESETS
    spec = importlib.util.spec_from_file_location("expanded_motion_presets", EXPANDED_PRESET_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _EXPANDED_PRESETS = module
    return _EXPANDED_PRESETS


def character_render_meshes(helper, target):
    """Keep the active body/outfit plus separately-rigged visible hair."""
    outline_tokens = ("outline", "lineart", "stroke")
    hair_tokens = ("hair", "ponytail", "braid", "bang", "cheveux", "kami")

    def text(obj):
        return " ".join(
            [obj.name] + [slot.material.name for slot in obj.material_slots if slot.material]
        ).lower()

    def outline(obj):
        value = text(obj)
        name = obj.name.lower()
        return (
            name == "shadow" or name.startswith("shadow_") or name.endswith("_shadow")
            or any(token in value for token in outline_tokens)
            or "_rim_" in value or "rim_hero" in value
        )

    meshes = set()
    for obj in helper.character_meshes(target):
        if outline(obj):
            obj.hide_render = True
        else:
            obj.hide_render = False
            meshes.add(obj)
    for obj in bpy.context.scene.objects:
        # Render visibility is authoritative. Many imported assets intentionally
        # hide expensive hair in the viewport while keeping it enabled for render.
        if obj.type != "MESH" or obj.hide_render:
            continue
        if outline(obj):
            obj.hide_render = True
            meshes.discard(obj)
            continue
        armatures = [
            modifier.object for modifier in obj.modifiers
            if modifier.type == "ARMATURE" and modifier.object is not None
        ]
        parent = obj.parent if obj.parent and obj.parent.type == "ARMATURE" else None
        if parent is not None:
            armatures.append(parent)
        names = text(obj) + " " + " ".join(armature.name for armature in armatures).lower()
        if target in armatures and any(token in names for token in hair_tokens):
            obj.hide_render = False
            meshes.add(obj)
    return sorted(meshes, key=lambda obj: obj.name)


def neutralize_stylized_edges(meshes):
    labels = ("edgemaskcontrast", "rimrange", "backrimrange", "vfxfresnelintensity",
              "rimtexintensity", "rimwidth")
    changed = []
    for obj in meshes:
        for slot in obj.material_slots:
            material = slot.material
            if not material or not material.use_nodes:
                continue
            for node in material.node_tree.nodes:
                label = (node.label or "").replace(" ", "").lower()
                value = getattr(node.outputs[0], "default_value", None) if node.outputs else None
                if any(token in label for token in labels) and isinstance(value, (int, float)):
                    node.outputs[0].default_value = 0.0
                    changed.append(f"{material.name}:{node.label}")
    return sorted(set(changed))


def normalize_matting_alpha(meshes):
    """Repair imported cutout-opacity graphs without hardening soft edges.

    DAZ opacity maps use white for opaque geometry. Some imported materials
    connect those maps directly to a DAZ Transparent group's factor, where
    white instead selects the transparent shader. That inversion turns hair
    cards into opaque blobs with the actual strands cut out of them.
    """
    opacity_tokens = ("opacity", "cutout", "_op", " op", "alpha")
    fixed = []
    interpolation = []
    seen_materials = set()
    for obj in meshes:
        for slot in obj.material_slots:
            material = slot.material
            if not material or not material.use_nodes or material in seen_materials:
                continue
            seen_materials.add(material)
            tree = material.node_tree
            for node in list(tree.nodes):
                if node.bl_idname != "ShaderNodeGroup" or not node.node_tree:
                    continue
                if "daz transparent" not in (node.node_tree.name + " " + node.name).lower():
                    continue
                fac = node.inputs.get("Fac") or node.inputs.get("Factor")
                if fac is None:
                    continue
                links = [link for link in tree.links if link.to_socket == fac]
                if len(links) != 1:
                    continue
                link = links[0]
                source = link.from_node
                if source.bl_idname != "ShaderNodeTexImage" or source.image is None:
                    continue
                label = " ".join((
                    source.name, source.label or "", source.image.name,
                    source.image.filepath or "",
                )).lower()
                if not any(token in label for token in opacity_tokens):
                    continue
                marker = f"RenderMatte_OpacityToTransparency_{source.name}"
                if tree.nodes.get(marker):
                    continue
                source.interpolation = "Linear"
                interpolation.append(f"{material.name}:{source.name}")
                source_socket = link.from_socket
                tree.links.remove(link)
                invert = tree.nodes.new("ShaderNodeMath")
                invert.name = marker
                invert.label = "RenderMatte: 1 - cutout opacity"
                invert.operation = "SUBTRACT"
                invert.inputs[0].default_value = 1.0
                invert.location = ((source.location.x + node.location.x) * 0.5,
                                   (source.location.y + node.location.y) * 0.5)
                tree.links.new(source_socket, invert.inputs[1])
                tree.links.new(invert.outputs[0], fac)
                fixed.append(f"{material.name}:{source.name}->{node.name}")
    return {
        "policy": "native_soft_alpha_with_imported_opacity_semantics_repaired",
        "inverted_opacity_links": sorted(fixed),
        "linear_opacity_textures": sorted(interpolation),
        "threshold_or_morphology": False,
    }


def geometry_motion_qc(helper, target, meshes, frames, character_height):
    """Fallback QC for rigs whose vertex-group names defeat dense collision QC."""
    sample_frames = sorted(set([frames[0], frames[len(frames) // 4], frames[len(frames) // 2],
                                frames[(3 * len(frames)) // 4], frames[-1]]))
    bounds = []
    controls = [name for name in ("torso", "head", "hand_ik.L", "hand_ik.R", "foot_ik.L", "foot_ik.R")
                if name in target.pose.bones]
    if len(controls) < 3:
        semantic = helper.semantic_bone_map(target)
        controls = [
            semantic[name] for name in ("hips", "head", "lefthand", "righthand", "leftfoot", "rightfoot")
            if name in semantic and semantic[name] in target.pose.bones
        ]
    poses = []
    for frame in sample_frames:
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        low, high = helper.evaluated_bounds(meshes, [frame])
        size = high - low
        center = (high + low) * 0.5
        values = tuple(size) + tuple(center)
        if not all(math.isfinite(value) for value in values) or min(size) <= 1e-6:
            raise RuntimeError(f"invalid evaluated geometry at frame {frame}: {values}")
        bounds.append((frame, size, center))
        poses.append({
            name: (target.matrix_world @ target.pose.bones[name].matrix).copy()
            for name in controls
        })
    base_size = bounds[0][1]
    base_center = bounds[0][2]
    height = max(float(character_height), float(base_size.z), 1e-6)
    for frame, size, center in bounds:
        extent_ratio = size.length / max(base_size.length, 1e-6)
        if not 0.55 <= extent_ratio <= 1.45:
            raise RuntimeError(
                f"geometry extent changed implausibly at frame {frame}: {extent_ratio:.3f}"
            )
        if max(size) > 2.4 * height:
            raise RuntimeError(f"geometry exploded at frame {frame}: size={tuple(size)} height={height}")
        if (center - base_center).length > 0.8 * height:
            raise RuntimeError(f"character center escaped at frame {frame}")
    max_translation = 0.0
    max_rotation_deg = 0.0
    for pose in poses[1:]:
        for name in controls:
            max_translation = max(max_translation, (pose[name].translation - poses[0][name].translation).length)
            max_rotation_deg = max(
                max_rotation_deg,
                math.degrees(poses[0][name].to_quaternion().rotation_difference(pose[name].to_quaternion()).angle),
            )
    if max_translation < 0.005 * height and max_rotation_deg < 2.0:
        raise RuntimeError("motion QC rejected a nearly static character")
    return {
        "status": "passed", "method": "evaluated_geometry_and_native_control_motion",
        "sample_frames": sample_frames, "max_control_translation": max_translation,
        "max_control_rotation_deg": max_rotation_deg,
    }


def limb_deformation_qc(target, frames, character_height):
    """Reject transient IK stretch, collapse, and unreachable limb poses."""
    chains = {
        "arm_L": ("ORG-upper_arm.L", "ORG-forearm.L", "ORG-hand.L"),
        "arm_R": ("ORG-upper_arm.R", "ORG-forearm.R", "ORG-hand.R"),
        "leg_L": ("ORG-thigh.L", "ORG-shin.L", "ORG-foot.L"),
        "leg_R": ("ORG-thigh.R", "ORG-shin.R", "ORG-foot.R"),
    }
    foot_chains = {
        suffix: (f"DEF-foot.{suffix}", f"DEF-toe.{suffix}")
        for suffix in ("L", "R")
        if f"DEF-foot.{suffix}" in target.pose.bones and f"DEF-toe.{suffix}" in target.pose.bones
    }
    missing = sorted({name for chain in chains.values() for name in chain if name not in target.pose.bones})
    if missing:
        raise RuntimeError(f"limb deformation QC lacks native chain bones: {missing}")
    measurements = {name: [] for name in chains}
    knee_separations = []
    ankle_separations = []
    knee_alignment = {"L": [], "R": []}
    foot_lengths = {suffix: [] for suffix in foot_chains}
    for frame in frames:
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        lateral_left = (
            world_head(target, "ORG-upper_arm.L")
            - world_head(target, "ORG-upper_arm.R")
        ).normalized()
        for chain_name, (root_name, middle_name, end_name) in chains.items():
            root = world_head(target, root_name)
            middle = world_head(target, middle_name)
            end = world_head(target, end_name)
            first = (middle - root).length
            second = (end - middle).length
            total = first + second
            reach = (end - root).length / max(total, 1e-7)
            measurements[chain_name].append((frame, first, second, reach))
        knee_separations.append(
            (world_head(target, "ORG-shin.L") - world_head(target, "ORG-shin.R")).dot(lateral_left)
            / max(character_height, 1e-7)
        )
        ankle_separations.append(
            (world_head(target, "ORG-foot.L") - world_head(target, "ORG-foot.R")).dot(lateral_left)
            / max(character_height, 1e-7)
        )
        for suffix, side_sign in (("L", 1.0), ("R", -1.0)):
            hip = world_head(target, f"ORG-thigh.{suffix}")
            knee = world_head(target, f"ORG-shin.{suffix}")
            ankle = world_head(target, f"ORG-foot.{suffix}")
            vertical = hip.z - ankle.z
            fraction = (hip.z - knee.z) / vertical if abs(vertical) > 1e-7 else 0.5
            fraction = max(0.0, min(1.0, fraction))
            chain_line = hip.lerp(ankle, fraction)
            knee_alignment[suffix].append(
                side_sign * (knee - chain_line).dot(lateral_left)
                / max(character_height, 1e-7)
            )
        for suffix, (foot_name, toe_name) in foot_chains.items():
            foot_lengths[suffix].append(
                (world_head(target, toe_name) - world_head(target, foot_name)).length
            )
    report = {}
    failures = []
    for chain_name, values in measurements.items():
        base_first = values[0][1]
        base_second = values[0][2]
        first_ratios = [item[1] / max(base_first, 1e-7) for item in values]
        second_ratios = [item[2] / max(base_second, 1e-7) for item in values]
        reaches = [item[3] for item in values]
        chain_report = {
            "first_segment_ratio_min": min(first_ratios),
            "first_segment_ratio_max": max(first_ratios),
            "second_segment_ratio_min": min(second_ratios),
            "second_segment_ratio_max": max(second_ratios),
            "endpoint_reach_ratio_min": min(reaches),
            "endpoint_reach_ratio_max": max(reaches),
        }
        report[chain_name] = chain_report
        if min(first_ratios + second_ratios) < 0.985 or max(first_ratios + second_ratios) > 1.015:
            failures.append(f"{chain_name}:segment_length_changed")
        if chain_name.startswith("arm_") and min(reaches) < 0.16:
            failures.append(f"{chain_name}:arm_fold")
    if failures:
        raise RuntimeError(f"limb deformation QC failed: {failures}; measurements={report}")
    foot_report = {}
    for suffix, values in foot_lengths.items():
        baseline = max(values[0], 1e-7)
        ratios = [value / baseline for value in values]
        foot_report[suffix] = {
            "foot_to_toe_ratio_min": min(ratios),
            "foot_to_toe_ratio_max": max(ratios),
        }
        if min(ratios) < 0.99 or max(ratios) > 1.01:
            failures.append(f"foot_{suffix}:foot_shape_changed")
    if failures:
        raise RuntimeError(f"limb deformation QC failed: {failures}; feet={foot_report}")
    aligned_knees = knee_separations
    aligned_ankles = ankle_separations
    minimum_knee_separation = min(aligned_knees)
    minimum_ankle_separation = min(aligned_ankles)
    lower = (
        ACTION_PARAMS.get("expanded_blueprint_effective")
        or ACTION_PARAMS.get("expanded_blueprint")
        or {}
    ).get("lower")
    if minimum_knee_separation < max(0.008, aligned_knees[0] * 0.72):
        raise RuntimeError(
            f"limb deformation QC failed: knee ordering inverted or collapsed "
            f"({minimum_knee_separation:.4f}H; rest={aligned_knees[0]:.4f}H)"
        )
    alignment_report = {}
    for suffix, values in knee_alignment.items():
        inward_change = values[0] - min(values)
        alignment_report[suffix] = {
            "rest_outward_offset_normalized": values[0],
            "minimum_outward_offset_normalized": min(values),
            "maximum_dynamic_inward_change_normalized": inward_change,
        }
        # A naturally straightening leg approaches the hip-ankle plane and
        # should not be confused with valgus. Reject only when the knee
        # actually crosses materially into the anatomical inside half-space.
        if min(values) < -0.008:
            failure_index = min(range(len(values)), key=values.__getitem__)
            raise RuntimeError(
                f"limb deformation QC failed: unilateral knee valgus on {suffix} "
                f"({min(values):.4f}H inside hip-ankle plane at frame "
                f"{frames[failure_index]}); alignment={alignment_report}"
            )
    if (
        lower in {"squat", "crouch_shift", "side_lunge"}
        and minimum_ankle_separation < max(0.008, aligned_ankles[0] * 0.35)
    ):
        raise RuntimeError(
            f"limb deformation QC failed: deep lower-body action crossed ankles "
            f"({minimum_ankle_separation:.4f}H)"
        )
    return {
        "status": "passed",
        "method": "all_frame_native_segment_length_and_reach",
        "frame_count": len(frames),
        "height": float(character_height),
        "chains": report,
        "feet": foot_report,
        "knee_alignment": alignment_report,
        "minimum_knee_side_separation_normalized": minimum_knee_separation,
        "minimum_ankle_side_separation_normalized": minimum_ankle_separation,
        "rest_knee_side_separation_normalized": aligned_knees[0],
        "rest_ankle_side_separation_normalized": aligned_ankles[0],
    }


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--prepass-only", action="store_true")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    return parser.parse_args(argv)


def world_head(armature, bone_name):
    # Rigify spine segment counts vary; resolve the semantic head instead of
    # assuming every rig has the same numbered original head bone.
    if bone_name == "ORG-spine.007" and bone_name not in armature.pose.bones:
        mapped = load_helper().semantic_bone_map(armature).get("head")
        if not mapped or mapped not in armature.pose.bones:
            raise RuntimeError("Target lacks an identifiable head bone")
        bone_name = mapped
    return (armature.matrix_world @ armature.pose.bones[bone_name].matrix).translation


def extract_reference_timing(source, start_frame, frame_count, semantic_map):
    names = [semantic_map[name] for name in (
        "hips", "spine", "head", "leftarm", "rightarm",
        "leftupleg", "rightupleg", "leftleg", "rightleg",
    ) if name in semantic_map]
    frames = [start_frame + index for index in range(frame_count)]
    previous = None
    increments = [0.0]
    for frame in frames:
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        current = {
            name: (source.matrix_world @ source.pose.bones[name].matrix).to_quaternion()
            for name in names
        }
        if previous is not None:
            increments.append(sum(math.degrees(previous[name].rotation_difference(value).angle) for name, value in current.items()))
        previous = current
    total = sum(increments)
    if total < 1e-6:
        timing = [index / max(1, frame_count - 1) for index in range(frame_count)]
    else:
        cumulative = 0.0
        timing = []
        for index, increment in enumerate(increments):
            cumulative += increment
            activity_time = cumulative / total
            linear_time = index / max(1, frame_count - 1)
            timing.append(0.90 * activity_time + 0.10 * linear_time)
        timing[0] = 0.0
        timing[-1] = 1.0
    return timing, {
        "reference_activity_total_deg": total,
        "reference_timing_min_step": min((b - a for a, b in zip(timing, timing[1:])), default=0.0),
        "reference_timing_max_step": max((b - a for a, b in zip(timing, timing[1:])), default=0.0),
    }


def force_rigify_ik(target):
    changed = []
    for pose_bone in target.pose.bones:
        for key in pose_bone.keys():
            normalized = str(key).lower().replace(" ", "_")
            if "ik_stretch" in normalized:
                pose_bone[key] = 0.0
                changed.append((pose_bone.name, str(key), 0.0))
        name = pose_bone.name.lower()
        if not any(token in name for token in ("upper_arm", "forearm", "thigh", "shin", "foot")):
            continue
        if hasattr(pose_bone, "ik_stretch"):
            pose_bone.ik_stretch = 0.0
            changed.append((pose_bone.name, "pose_bone.ik_stretch", 0.0))
        constraints = list(pose_bone.constraints)
        if name.startswith("def-foot"):
            for constraint in constraints:
                if constraint.type == "STRETCH_TO":
                    constraint.mute = True
                    changed.append((pose_bone.name, constraint.name, "muted_foot_stretch"))
        if not any("_ik" in getattr(item, "subtarget", "").lower() for item in constraints):
            continue
        for constraint in constraints:
            subtarget = getattr(constraint, "subtarget", "").lower()
            if "_ik" not in subtarget and "_fk" not in subtarget:
                continue
            try:
                constraint.driver_remove("influence")
            except (TypeError, RuntimeError):
                pass
            try:
                constraint.driver_remove("mute")
            except (TypeError, RuntimeError):
                pass
            if constraint.type == "IK" and "_ik" in subtarget:
                # Rigify commonly keeps a pole-less IK fallback beside the
                # anatomical pole-driven IK constraint. Enabling both makes
                # the two solvers fight and renders the pole control inert.
                siblings = [
                    item for item in constraints
                    if item.type == "IK"
                    and getattr(item, "subtarget", "").lower() == subtarget
                ]
                has_pole_variant = any(
                    bool(getattr(item, "pole_subtarget", "")) for item in siblings
                )
                use_constraint = (
                    bool(getattr(constraint, "pole_subtarget", ""))
                    if has_pole_variant else True
                )
                constraint.mute = not use_constraint
                constraint.influence = 1.0 if use_constraint else 0.0
                if hasattr(constraint, "use_stretch"):
                    constraint.use_stretch = False
                changed.append((
                    pose_bone.name, constraint.name,
                    "pole_ik" if use_constraint else "disabled_poleless_ik",
                ))
                continue
            constraint.mute = False
            constraint.influence = 1.0 if "_ik" in subtarget else 0.0
            if hasattr(constraint, "use_stretch"):
                constraint.use_stretch = False
            changed.append((pose_bone.name, constraint.name, constraint.influence))
    return changed


def key_world(helper, armature, bone_name, rotation, position, frame):
    bone = armature.pose.bones[bone_name]
    bone.rotation_mode = "QUATERNION"
    helper.set_world_transform(armature, bone, rotation, position)
    bone.keyframe_insert("location", frame=frame, group=bone_name)
    bone.keyframe_insert("rotation_quaternion", frame=frame, group=bone_name)


def set_world_transform_preserve_scale(armature, pose_bone, rotation, position):
    current_world = armature.matrix_world @ pose_bone.matrix
    desired_world = Matrix.LocRotScale(position, rotation, current_world.to_scale())
    pose_bone.matrix = armature.matrix_world.inverted() @ desired_world


def set_world_rotation_preserve_scale(armature, pose_bone, rotation):
    current_world = armature.matrix_world @ pose_bone.matrix
    set_world_transform_preserve_scale(
        armature, pose_bone, rotation, current_world.translation,
    )


def load_motion_observation():
    path = Path(ACTION_PARAMS.get("observation_path", ""))
    if not path.exists():
        raise RuntimeError(f"motion observation is missing: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("schema") != 2 or data.get("observation_version") != "full-source-observables-v2":
        raise RuntimeError("unsupported motion observation schema")
    if data.get("trajectory_fingerprint") != ACTION_PARAMS.get("trajectory_fingerprint"):
        raise RuntimeError("motion observation fingerprint mismatch")
    if data.get("provenance", {}).get("bone_rotations_exported") is not False:
        raise RuntimeError("motion observation unexpectedly contains source rotations")
    return data


def unwrap_degrees(values):
    result = [float(values[0])]
    for value in values[1:]:
        value = float(value)
        while value - result[-1] > 180.0:
            value -= 360.0
        while value - result[-1] < -180.0:
            value += 360.0
        result.append(value)
    return result


def sample_lerp(first, second, amount, yaw_first, yaw_second):
    joints = {}
    for name in first["normalized_joints"]:
        if name not in second["normalized_joints"]:
            continue
        joints[name] = [
            a + (b - a) * amount
            for a, b in zip(first["normalized_joints"][name], second["normalized_joints"][name])
        ]
    return {
        "root_trajectory": [a + (b - a) * amount for a, b in zip(first["root_trajectory"], second["root_trajectory"])],
        "body_yaw_deg": yaw_first + (yaw_second - yaw_first) * amount,
        "normalized_joints": joints,
    }


def smooth_observation_timeline(samples, passes=2):
    if len(samples) < 3:
        return samples
    weights = (1.0, 4.0, 6.0, 4.0, 1.0)
    radius = 2
    result = samples
    for _ in range(passes):
        smoothed = []
        for index, sample in enumerate(result):
            neighbors = [
                result[max(0, min(len(result) - 1, index + offset))]
                for offset in range(-radius, radius + 1)
            ]
            joints = {}
            for name in sample["normalized_joints"]:
                joints[name] = [
                    sum(weight * neighbor["normalized_joints"][name][axis]
                        for weight, neighbor in zip(weights, neighbors)) / 16.0
                    for axis in range(3)
                ]
            smoothed.append({
                "root_trajectory": [
                    sum(weight * neighbor["root_trajectory"][axis]
                        for weight, neighbor in zip(weights, neighbors)) / 16.0
                    for axis in range(3)
                ],
                "body_yaw_deg": sum(
                    weight * neighbor["body_yaw_deg"]
                    for weight, neighbor in zip(weights, neighbors)
                ) / 16.0,
                "normalized_joints": joints,
            })
        result = smoothed
    return result


def observation_timeline(data, output_count):
    samples = data["samples"]
    source_count = len(samples)
    if source_count < 2:
        raise RuntimeError("motion observation has fewer than two samples")
    yaw = unwrap_degrees([sample["body_yaw_deg"] for sample in samples])
    window_start = 0
    window_count = source_count
    if source_count > output_count:
        selected = (
            "head", "lefthand", "righthand", "leftfoot", "rightfoot",
            "leftforearm", "rightforearm", "leftleg", "rightleg",
        )
        energy = [0.0]
        for before, after in zip(samples, samples[1:]):
            value = sum(
                sum((b - a) ** 2 for a, b in zip(
                    before["normalized_joints"][name], after["normalized_joints"][name]
                )) for name in selected
            )
            value += 4.0 * sum((b - a) ** 2 for a, b in zip(before["root_trajectory"], after["root_trajectory"]))
            energy.append(value)
        prefix = [0.0]
        for value in energy:
            prefix.append(prefix[-1] + value)
        events = {int(item["frame_index"]) for item in data.get("phase_events", [])}
        stride = max(1, output_count // 12)
        starts = set(range(0, source_count - output_count + 1, stride))
        starts.update(max(0, min(source_count - output_count, event)) for event in events)
        starts.add(source_count - output_count)
        best = None
        for start in starts:
            end = start + output_count
            activity = prefix[end] - prefix[start]
            boundary = energy[start] + energy[min(source_count - 1, end - 1)]
            score = activity - 3.0 * boundary
            candidate = (score, -boundary, -start)
            if best is None or candidate > best[0]:
                best = (candidate, start)
        window_start = best[1]
        window_count = output_count

    timing_bias = float(
        (ACTION_PARAMS.get("subject_style") or {}).get("timing_bias", 1.0)
    )
    positions = []
    for index in range(output_count):
        phase = index / max(1, output_count - 1)
        warped_phase = phase ** max(0.85, min(1.15, timing_bias))
        positions.append(window_start + warped_phase * (window_count - 1))
    result = []
    contacts = []
    source_contacts = data["foot_contacts"]
    for position in positions:
        low = int(math.floor(position))
        high = min(source_count - 1, low + 1)
        amount = position - low
        result.append(sample_lerp(samples[low], samples[high], amount, yaw[low], yaw[high]))
        contacts.append(dict(source_contacts[int(round(position))]))
    result = smooth_observation_timeline(result, passes=2)
    return result, contacts, {
        "source_window_start": window_start,
        "source_window_count": window_count,
        "source_to_output_speed_ratio": window_count / max(1, output_count),
        "source_action_fully_observed": source_count <= output_count,
        "phase_aware_window": source_count > output_count,
        "trajectory_smoothing": "zero_phase_binomial_5tap_x2",
    }


def vector_from(values):
    return Vector((float(values[0]), float(values[1]), float(values[2])))


def clamp_chain_target(origin, target, maximum, fallback):
    delta = target - origin
    distance = delta.length
    if distance < 1e-7:
        return origin + fallback.normalized() * min(maximum * 0.25, 0.1)
    return origin + delta * min(1.0, maximum / distance)


def clamp_temporal_step(position, previous, maximum_step):
    if previous is None:
        return position.copy(), False
    delta = position - previous
    if delta.length <= maximum_step:
        return position.copy(), False
    return previous + delta.normalized() * maximum_step, True


def pole_from_landmark(origin, target, landmark, fallback, distance):
    line = target - origin
    if line.length < 1e-7:
        return origin + fallback.normalized() * distance
    direction = line.normalized()
    projection = origin + direction * (landmark - origin).dot(direction)
    bend = landmark - projection
    if bend.length < 1e-5:
        bend = fallback
    return origin.lerp(target, 0.5) + bend.normalized() * distance


def stabilize_contact_runs(positions, contacts, side, blend_frames=2):
    result = [position.copy() for position in positions]
    index = 0
    while index < len(result):
        if not contacts[index][side]:
            index += 1
            continue
        end = index + 1
        while end < len(result) and contacts[end][side]:
            end += 1
        anchor = sum((result[item] for item in range(index, end)), Vector()) / (end - index)
        for item in range(index, end):
            result[item] = anchor.copy()
        for offset in range(1, blend_frames + 1):
            gain = smooth01((blend_frames + 1 - offset) / (blend_frames + 1))
            if index - offset >= 0:
                result[index - offset] = result[index - offset].lerp(anchor, gain)
            if end - 1 + offset < len(result):
                result[end - 1 + offset] = result[end - 1 + offset].lerp(anchor, gain)
        index = end
    return result


def build_character_motion_profile(helper, target, meshes, semantic_controls=None):
    names = " ".join(
        [target.name]
        + [obj.name for obj in meshes]
        + [slot.material.name for obj in meshes for slot in obj.material_slots if slot.material]
    ).lower()
    controls = semantic_controls or helper.semantic_bone_map(target)

    def world_semantic(name):
        bone_name = controls.get(name)
        if not bone_name or bone_name not in target.data.bones:
            return None
        return (target.matrix_world @ target.data.bones[bone_name].matrix_local).translation

    low = min((target.matrix_world @ bone.head_local).z for bone in target.data.bones)
    high = max((target.matrix_world @ bone.head_local).z for bone in target.data.bones)
    height = max(high - low, 1e-6)
    shoulder_l, shoulder_r = world_semantic("leftarm"), world_semantic("rightarm")
    hand_l, hand_r = world_semantic("lefthand"), world_semantic("righthand")
    hip_l, hip_r = world_semantic("leftupleg"), world_semantic("rightupleg")
    foot_l, foot_r = world_semantic("leftfoot"), world_semantic("rightfoot")
    shoulder_width = (shoulder_l - shoulder_r).length / height if shoulder_l and shoulder_r else 0.18
    hip_width = (hip_l - hip_r).length / height if hip_l and hip_r else 0.14
    arm_ratio = (
        0.5 * ((shoulder_l - hand_l).length + (shoulder_r - hand_r).length) / height
        if shoulder_l and shoulder_r and hand_l and hand_r else 0.30
    )
    leg_ratio = (
        0.5 * ((hip_l - foot_l).length + (hip_r - foot_r).length) / height
        if hip_l and hip_r and foot_l and foot_r else 0.45
    )
    skirt = any(token in names for token in ("skirt", "dress", "gown", "robe", "kimono", "裙", "袍"))
    long_sleeve = any(token in names for token in ("long sleeve", "sleeve", "coat", "cape", "cloak", "袖", "披风"))
    armor = any(token in names for token in ("armor", "armour", "plate", "甲", "铠"))
    long_hair = any(token in names for token in ("long hair", "ponytail", "braid", "twin tail", "长发", "马尾", "辫"))
    bulky = armor or shoulder_width > 0.23
    arm_gain = 0.92
    if long_sleeve:
        arm_gain -= 0.10
    if bulky:
        arm_gain -= 0.08
    arm_gain *= max(0.82, min(1.08, 0.31 / max(arm_ratio, 0.20)))
    leg_gain = 0.94 * max(0.84, min(1.08, 0.45 / max(leg_ratio, 0.30)))
    if skirt:
        leg_gain *= 0.76
    turn_gain = 0.90 - (0.10 if long_hair else 0.0) - (0.08 if bulky else 0.0)
    root_gain = 0.86 if skirt or bulky else 0.94
    clearance = 0.13 + (0.035 if bulky or long_sleeve else 0.0)
    seed = int(hashlib.sha1(names.encode("utf-8")).hexdigest()[:8], 16)
    return {
        "profile_version": "asset_adaptive_motion_profile_v1",
        "height": height,
        "shoulder_width_ratio": shoulder_width,
        "hip_width_ratio": hip_width,
        "arm_length_ratio": arm_ratio,
        "leg_length_ratio": leg_ratio,
        "has_skirt_or_robe": skirt,
        "has_long_sleeve_or_cape": long_sleeve,
        "has_armor_or_bulky_shoulders": bulky,
        "has_long_hair": long_hair,
        "arm_motion_gain": max(0.62, min(1.02, arm_gain)),
        "leg_motion_gain": max(0.58, min(1.02, leg_gain)),
        "turn_gain": max(0.62, min(0.96, turn_gain)),
        "root_motion_gain": root_gain,
        "body_clearance_ratio": clearance,
        "gesture_asymmetry": -0.06 + 0.12 * ((seed % 1009) / 1008.0),
        "leading_side": "left" if seed % 2 == 0 else "right",
        "design_policy": "source_semantics_and_phase_only_target_specific_trajectory_reconstruction",
    }


def smooth01(value):
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def staged_envelope(t, start=0.06, full=0.28, release=0.70, end=0.96):
    if t <= start or t >= end:
        return 0.0
    if t < full:
        return smooth01((t - start) / max(1e-6, full - start))
    if t <= release:
        return 1.0
    return 1.0 - smooth01((t - release) / max(1e-6, end - release))


def step_touch_phase(t, start, end):
    local = max(0.0, min(1.0, (t - start) / max(1e-6, end - start)))
    outward = smooth01(local / 0.24)
    returning = smooth01((local - 0.70) / 0.24)
    offset = outward * (1.0 - returning)
    if local < 0.24:
        lift = math.sin(math.pi * local / 0.24) ** 2
    elif local > 0.70:
        lift = math.sin(math.pi * (local - 0.70) / 0.24) ** 2
    else:
        lift = 0.0
    load = staged_envelope(local, 0.20, 0.36, 0.60, 0.82)
    return offset, max(0.0, lift), load


def primitive_state(recipe, t, height):
    params = ACTION_PARAMS
    phase_bias = float(params.get("phase_bias", 0.0))
    tempo = float(params.get("tempo_curve", 1.0))
    t = max(0.0, min(1.0, t + phase_bias * math.sin(math.pi * t)))
    t = smooth01(t) ** tempo
    wave = math.sin(2.0 * math.pi * t)
    wave2 = math.sin(4.0 * math.pi * t)
    envelope = staged_envelope(t)
    state = {
        "torso": Vector((0.0, 0.0, 0.0)), "yaw": 0.0, "pitch": 0.0,
        "head_yaw": 0.0, "head_pitch": 0.0,
        "hand_l": Vector((0.0, 0.0, 0.0)), "hand_r": Vector((0.0, 0.0, 0.0)),
        "foot_l": Vector((0.0, 0.0, 0.0)), "foot_r": Vector((0.0, 0.0, 0.0)),
        "hand_blend_l": 0.0, "hand_blend_r": 0.0,
    }
    if recipe == "squat":
        e = staged_envelope(t, 0.08, 0.34, 0.62, 0.94)
        state["torso"] = Vector((0.0, 0.025 * height * e, -0.19 * height * e))
        state["pitch"] = math.radians(8.0) * e
        state["hand_blend_l"] = state["hand_blend_r"] = 0.82 * e
    elif recipe in {"side_lunge_left", "side_lunge_right"}:
        side = "l" if recipe.endswith("left") else "r"
        sign = 1.0 if side == "l" else -1.0
        offset, lift, load = step_touch_phase(t, 0.03, 0.97)
        state[f"foot_{side}"] = Vector((sign * 0.18 * height * offset, -0.015 * height * offset,
                                        0.035 * height * lift))
        state["torso"] = Vector((sign * 0.095 * height * load, 0.018 * height * load,
                                  -0.11 * height * load))
        state["yaw"] = math.radians(-8.0 * sign) * load
        state["hand_blend_l"] = state["hand_blend_r"] = 0.58 * load
    elif recipe in {"reach_left", "reach_right"}:
        side = "l" if recipe.endswith("left") else "r"
        sign = 1.0 if side == "l" else -1.0
        e = staged_envelope(t, 0.06, 0.30, 0.70, 0.96)
        state[f"hand_blend_{side}"] = e
        state[f"hand_{side}"] = Vector((sign * 0.055 * height * e, -0.19 * height * e,
                                         0.25 * height * e))
        state["torso"] = Vector((sign * 0.025 * height * e, -0.015 * height * e, 0.0))
        state["yaw"] = math.radians(18.0 * sign) * e
        state["head_yaw"] = math.radians(9.0 * sign) * e
    elif recipe == "overhead_raise":
        e = staged_envelope(t, 0.05, 0.32, 0.68, 0.96)
        state["hand_blend_l"] = state["hand_blend_r"] = e
        state["hand_l"] = Vector((-0.035 * height * e, -0.10 * height * e, 0.39 * height * e))
        state["hand_r"] = Vector((0.035 * height * e, -0.10 * height * e, 0.39 * height * e))
        state["torso"].z = 0.018 * height * e
        state["head_pitch"] = math.radians(-7.0) * e
    elif recipe in {"wave_left", "wave_right"}:
        side = "l" if recipe.endswith("left") else "r"
        sign = 1.0 if side == "l" else -1.0
        raise_amount = staged_envelope(t, 0.04, 0.27, 0.76, 0.97)
        oscillation = math.sin(2.0 * math.pi * max(0.0, min(1.0, (t - 0.25) / 0.52)))
        active_wave = staged_envelope(t, 0.22, 0.30, 0.70, 0.79)
        state[f"hand_blend_{side}"] = raise_amount
        state[f"hand_{side}"] = Vector((sign * 0.048 * height * oscillation * active_wave,
                                         -0.13 * height * raise_amount, 0.31 * height * raise_amount))
        state["torso"].x = -sign * 0.018 * height * raise_amount
        state["head_yaw"] = math.radians(10.0 * sign) * raise_amount
    elif recipe in {"turn_left", "turn_right"}:
        sign = 1.0 if recipe.endswith("left") else -1.0
        e = staged_envelope(t, 0.05, 0.32, 0.68, 0.96)
        step = math.sin(math.pi * max(0.0, min(1.0, t / 0.32))) ** 2 if t < 0.32 else 0.0
        state["yaw"] = math.radians(38.0 * sign) * e
        state["head_yaw"] = math.radians(24.0 * sign) * staged_envelope(t, 0.02, 0.22, 0.72, 0.94)
        state[f"foot_{'l' if sign > 0 else 'r'}"] = Vector((sign * 0.035 * height * e,
                                                             -0.035 * height * e, 0.018 * height * step))
        state["hand_blend_l"] = state["hand_blend_r"] = 0.22 * e
    elif recipe == "bow":
        e = staged_envelope(t, 0.07, 0.34, 0.63, 0.95)
        state["pitch"] = math.radians(31.0) * e
        state["head_pitch"] = math.radians(10.0) * e
        state["torso"] = Vector((0.0, 0.025 * height * e, -0.065 * height * e))
        state["hand_blend_l"] = state["hand_blend_r"] = 0.36 * e
    elif recipe in {"step_left", "step_right"}:
        side = "l" if recipe.endswith("left") else "r"
        sign = 1.0 if side == "l" else -1.0
        offset, lift, load = step_touch_phase(t, 0.04, 0.96)
        state[f"foot_{side}"] = Vector((sign * 0.14 * height * offset, -0.035 * height * offset,
                                        0.04 * height * lift))
        state["torso"] = Vector((sign * 0.07 * height * load, 0.0, -0.018 * height * load))
        state["yaw"] = math.radians(-7.0 * sign) * load
        state[f"hand_blend_{'r' if side == 'l' else 'l'}"] = 0.32 * load
    elif recipe == "march":
        active = math.sin(math.pi * t) ** 0.65
        gait = math.sin(4.0 * math.pi * t)
        left = max(0.0, gait) ** 2 * active
        right = max(0.0, -gait) ** 2 * active
        state["foot_l"] = Vector((0.0, -0.075 * height * left, 0.095 * height * left))
        state["foot_r"] = Vector((0.0, -0.075 * height * right, 0.095 * height * right))
        state["torso"] = Vector((0.018 * height * gait * active, 0.0,
                                  -0.014 * height * (left + right)))
        state["hand_blend_l"] = 0.42 * right
        state["hand_blend_r"] = 0.42 * left
        state["yaw"] = math.radians(6.0) * gait * active
    elif recipe == "dance_sway":
        active = math.sin(math.pi * t) ** 0.55
        rhythm = math.sin(4.0 * math.pi * t) * active
        state["torso"] = Vector((0.09 * height * rhythm, 0.0,
                                  -0.026 * height * (1.0 - math.cos(4.0 * math.pi * t)) * active))
        state["yaw"] = math.radians(18.0) * rhythm
        state["hand_blend_l"] = max(0.0, 0.42 - 0.28 * rhythm) * active
        state["hand_blend_r"] = max(0.0, 0.42 + 0.28 * rhythm) * active
        state["head_yaw"] = math.radians(-13.0) * rhythm
        state["foot_l"].z = 0.022 * height * max(0.0, rhythm) ** 2
        state["foot_r"].z = 0.022 * height * max(0.0, -rhythm) ** 2
    elif recipe == "hair_turn":
        sign = 1.0 if params.get("leading_side", "left") == "left" else -1.0
        body = staged_envelope(t, 0.07, 0.34, 0.65, 0.95)
        head = staged_envelope(t, 0.03, 0.23, 0.70, 0.92)
        state["yaw"] = math.radians(24.0 * sign) * body
        state["head_yaw"] = math.radians(48.0 * sign) * head
        state["head_pitch"] = math.radians(5.0) * math.sin(2.0 * math.pi * t) * math.sin(math.pi * t)
        state["torso"].x = 0.018 * height * sign * body
    elif recipe == "hair_bend":
        e = staged_envelope(t, 0.06, 0.32, 0.62, 0.95)
        state["pitch"] = math.radians(34.0) * e
        state["head_pitch"] = math.radians(25.0) * staged_envelope(t, 0.03, 0.24, 0.66, 0.92)
        state["torso"] = Vector((0.0, 0.03 * height * e, -0.09 * height * e))
        state["hand_blend_l"] = state["hand_blend_r"] = 0.48 * e
    else:  # weight_shift: one complete step, plant, load, unload, and recover.
        side_name = "l" if params.get("leading_side", "left") == "left" else "r"
        sign = 1.0 if side_name == "l" else -1.0
        offset, lift, load = step_touch_phase(t, 0.03, 0.97)
        state[f"foot_{side_name}"] = Vector((sign * 0.105 * height * offset,
                                             -0.025 * height * offset,
                                             0.038 * height * lift))
        balance = sign * load
        compression = load
        state["torso"] = Vector((0.078 * height * balance, 0.012 * height * compression,
                                  -0.026 * height * compression))
        state["yaw"] = math.radians(-11.0) * balance
        state["head_yaw"] = math.radians(14.0) * balance
        state["hand_blend_l"] = 0.12 * compression + 0.28 * load * (1.0 if sign < 0 else 0.0)
        state["hand_blend_r"] = 0.12 * compression + 0.28 * load * (1.0 if sign > 0 else 0.0)
        state["hand_l"] = Vector((-0.025 * height * balance, -0.025 * height * compression,
                                   0.035 * height * load * (1.0 if sign < 0 else 0.0)))
        state["hand_r"] = Vector((-0.025 * height * balance, -0.025 * height * compression,
                                   0.035 * height * load * (1.0 if sign > 0 else 0.0)))
    secondary = params.get("secondary_recipe")
    order = params.get("sequence_order", "prepare_main_recover")
    if order == "secondary_main_recover":
        mix = math.sin(math.pi * max(0.0, min(1.0, t / 0.48))) ** 2 if t < 0.48 else 0.0
    else:
        mix = math.sin(math.pi * max(0.0, min(1.0, (t - 0.48) / 0.48))) ** 2 if t > 0.48 else 0.0
    side = 1.0 if params.get("leading_side", "left") == "left" else -1.0
    secondary_gain = 0.55
    if secondary in {"turn_left", "turn_right", "hair_turn"}:
        state["yaw"] += math.radians(11.0) * side * mix * secondary_gain
        state["head_yaw"] += math.radians(15.0) * side * mix * secondary_gain
    elif secondary in {"reach_left", "reach_right", "wave_left", "wave_right", "overhead_raise"}:
        key = "hand_l" if side > 0 else "hand_r"
        blend_key = "hand_blend_l" if side > 0 else "hand_blend_r"
        state[key] += Vector((0.08 * height * side * mix, -0.08 * height * mix, 0.12 * height * mix)) * secondary_gain
        state[blend_key] = max(state[blend_key], 0.62 * mix)
    elif secondary in {"step_left", "step_right", "march", "dance_sway"}:
        key = "foot_l" if side > 0 else "foot_r"
        state[key] += Vector((0.055 * height * side * mix, -0.018 * height * mix,
                             0.018 * height * math.sin(math.pi * mix))) * secondary_gain
        state["torso"].x += 0.022 * height * side * mix * secondary_gain
    elif secondary in {"squat", "side_lunge_left", "side_lunge_right", "bow", "hair_bend"}:
        state["torso"].z -= 0.045 * height * mix * secondary_gain
        state["pitch"] += math.radians(7.0) * mix * secondary_gain

    amplitude = float(params.get("amplitude", 1.0))
    step_scale = float(params.get("step_scale", 1.0))
    turn_scale = float(params.get("turn_scale", 1.0))
    vertical_scale = float(params.get("vertical_scale", 1.0))
    asymmetry = float(params.get("asymmetry", 0.0))
    head_lag = float(params.get("head_lag", 0.12))
    variant = int(params.get("global_variant_index", 0))
    state["torso"].x *= amplitude
    state["torso"].y *= amplitude
    state["torso"].z *= vertical_scale
    state["yaw"] *= turn_scale
    state["pitch"] *= turn_scale
    state["head_yaw"] = state["head_yaw"] * turn_scale + math.radians(2.0 + 4.0 * head_lag) * math.sin(2.0 * math.pi * t) * math.sin(math.pi * t)
    state["head_pitch"] += math.radians(1.2) * math.sin(2.0 * math.pi * t + (variant % 7) * 0.18) * math.sin(math.pi * t)
    for side, sign in (("l", 1.0), ("r", -1.0)):
        state[f"hand_{side}"] *= amplitude * (1.0 + sign * asymmetry)
        state[f"foot_{side}"] *= step_scale * (1.0 + 0.5 * sign * asymmetry)
    # A tiny deterministic secondary gesture makes every global design trajectory distinct.
    micro = math.sin(2.0 * math.pi * t + (variant % 11) * 0.11) * math.sin(math.pi * t)
    state["torso"].x += (0.0025 + 0.0015 * (variant % 5) / 4.0) * height * micro
    state["hand_l"].z += 0.003 * height * micro
    state["hand_r"].z -= 0.0025 * height * micro
    return state


def apply_terminal_settle(state, t):
    # Every authored clip finishes with a genuine planted settle. This is a
    # global trajectory rule, not a primitive-specific correction.
    terminal_gain = 1.0 - smooth01((t - 0.84) / 0.12) if t > 0.84 else 1.0
    terminal_gain = max(0.0, min(1.0, terminal_gain))
    for name in ("torso", "hand_l", "hand_r", "foot_l", "foot_r"):
        state[name] *= terminal_gain
    for name in ("yaw", "pitch", "head_yaw", "head_pitch"):
        state[name] *= terminal_gain
    state["hand_blend_l"] *= terminal_gain
    state["hand_blend_r"] *= terminal_gain
    return state


def recipe_state(recipe, t, height):
    t = max(0.0, min(1.0, t))
    expanded_blueprint = (
        ACTION_PARAMS.get("expanded_blueprint_effective")
        or ACTION_PARAMS.get("expanded_blueprint")
    )
    if expanded_blueprint:
        runtime = load_expanded_presets()
        raw = runtime.compose_state(
            expanded_blueprint, t, ACTION_PARAMS.get("character_motion_profile")
        )
        return {
            "torso": Vector(raw["torso"]) * height,
            "yaw": raw["yaw"], "pitch": raw["pitch"],
            "head_yaw": raw["head_yaw"], "head_pitch": raw["head_pitch"],
            "hand_l": Vector(raw["hand_l"]) * height,
            "hand_r": Vector(raw["hand_r"]) * height,
            "foot_l": Vector(raw["foot_l"]) * height,
            "foot_r": Vector(raw["foot_r"]) * height,
            "foot_yaw_l": raw.get("foot_yaw_l", 0.0),
            "foot_yaw_r": raw.get("foot_yaw_r", 0.0),
            "hand_blend_l": raw["hand_blend_l"],
            "hand_blend_r": raw["hand_blend_r"],
        }
    blueprint = ACTION_PARAMS.get("action_blueprint")
    weights = ACTION_PARAMS.get("phase_weights")
    if not blueprint or not weights or len(blueprint) != len(weights):
        return apply_terminal_settle(primitive_state(recipe, t, height), t)
    start = 0.0
    phase_index = len(blueprint) - 1
    local_t = 1.0
    for index, weight in enumerate(weights):
        end = start + float(weight)
        if t <= end or index == len(weights) - 1:
            phase_index = index
            local_t = (t - start) / max(1e-6, end - start)
            break
        start = end
    secondary = ACTION_PARAMS.pop("secondary_recipe", None)
    try:
        state = primitive_state(blueprint[phase_index], local_t, height)
    finally:
        if secondary is not None:
            ACTION_PARAMS["secondary_recipe"] = secondary
    energies = ACTION_PARAMS.get("phase_energies") or [1.0] * len(blueprint)
    energy = float(energies[phase_index])
    for name in ("torso", "hand_l", "hand_r", "foot_l", "foot_r"):
        state[name] *= energy
    for name in ("yaw", "pitch", "head_yaw", "head_pitch"):
        state[name] *= energy
    state["hand_blend_l"] = min(1.0, state["hand_blend_l"] * energy)
    state["hand_blend_r"] = min(1.0, state["hand_blend_r"] * energy)
    return apply_terminal_settle(state, t)


def trajectory_qc(states, height):
    def feature(state):
        values = []
        for name in ("torso", "hand_l", "hand_r", "foot_l", "foot_r"):
            values.extend(state[name])
        values.extend((
            state["yaw"] * height * 0.28,
            state["pitch"] * height * 0.28,
            state["head_yaw"] * height * 0.20,
            state["head_pitch"] * height * 0.20,
            state.get("foot_yaw_l", 0.0) * height * 0.12,
            state.get("foot_yaw_r", 0.0) * height * 0.12,
            state["hand_blend_l"] * height * 0.12,
            state["hand_blend_r"] * height * 0.12,
        ))
        return values

    features = [feature(state) for state in states]
    distances = [
        math.sqrt(sum((value - base) ** 2 for value, base in zip(item, features[0])))
        for item in features
    ]
    velocities = [
        math.sqrt(sum((b - a) ** 2 for a, b in zip(first, second)))
        for first, second in zip(features, features[1:])
    ]
    accelerations = [abs(second - first) for first, second in zip(velocities, velocities[1:])]
    start_end = math.sqrt(sum((a - b) ** 2 for a, b in zip(features[0], features[-1])))
    amplitude = max(distances, default=0.0)
    max_velocity = max(velocities, default=0.0)
    max_acceleration = max(accelerations, default=0.0)
    min_foot_z = min(
        min(state["foot_l"].z, state["foot_r"].z) for state in states
    )
    terminal_hold_frames = max(4, round(len(states) * 0.04))
    terminal_features = features[-terminal_hold_frames:]
    terminal_base = terminal_features[-1]
    terminal_delta = max(
        math.sqrt(sum((value - base) ** 2 for value, base in zip(item, terminal_base)))
        for item in terminal_features
    )
    terminal_velocity = max(velocities[-max(1, terminal_hold_frames - 1):], default=0.0)
    terminal_root_drift = max(
        (state["torso"] - states[-1]["torso"]).length
        for state in states[-terminal_hold_frames:]
    )
    terminal_foot_drift = max(
        max(
            (state["foot_l"] - states[-1]["foot_l"]).length,
            (state["foot_r"] - states[-1]["foot_r"]).length,
        )
        for state in states[-terminal_hold_frames:]
    )
    if amplitude < 0.05 * height:
        raise RuntimeError(f"authored trajectory amplitude too small: {amplitude / height:.4f}H")
    if max_velocity > 0.10 * height:
        raise RuntimeError(f"authored trajectory frame velocity too high: {max_velocity / height:.4f}H")
    if max_acceleration > 0.060 * height:
        raise RuntimeError(f"authored trajectory acceleration discontinuity: {max_acceleration / height:.4f}H")
    if start_end > 0.035 * height:
        raise RuntimeError(f"authored trajectory does not recover naturally: {start_end / height:.4f}H")
    if min_foot_z < -1e-5 * height:
        raise RuntimeError(f"authored foot penetrates ground: {min_foot_z / height:.5f}H")
    if terminal_delta > 0.004 * height or terminal_velocity > 0.003 * height:
        raise RuntimeError(
            "authored trajectory does not settle at clip end: "
            f"delta={terminal_delta / height:.5f}H velocity={terminal_velocity / height:.5f}H"
        )
    if terminal_root_drift > 0.002 * height or terminal_foot_drift > 0.002 * height:
        raise RuntimeError(
            "root or feet drift during terminal hold: "
            f"root={terminal_root_drift / height:.5f}H feet={terminal_foot_drift / height:.5f}H"
        )
    return {
        "status": "passed", "amplitude_normalized": amplitude / height,
        "max_frame_velocity_normalized": max_velocity / height,
        "max_frame_acceleration_normalized": max_acceleration / height,
        "start_end_delta_normalized": start_end / height,
        "min_foot_height_normalized": min_foot_z / height,
        "terminal_hold_frames": terminal_hold_frames,
        "terminal_hold_delta_normalized": terminal_delta / height,
        "terminal_hold_velocity_normalized": terminal_velocity / height,
        "terminal_root_drift_normalized": terminal_root_drift / height,
        "terminal_foot_drift_normalized": terminal_foot_drift / height,
    }


def set_linear_interpolation(armature):
    action = armature.animation_data.action if armature.animation_data else None
    if not action:
        return 0
    count = 0
    for curve in getattr(action, "fcurves", ()):
        for point in curve.keyframe_points:
            point.interpolation = "LINEAR"
            count += 1
    return count


def add_hair_secondary_motion(target, meshes, frame_count, recipe):
    hair_tokens = ("hair", "wig", "ponytail", "pony", "braid", "bang", "strand", "tress")
    facial_tokens = ("eyebrow", "eyelash", "beard", "mustache", "moustache", "brow", "lash")
    body_tokens = (
        "head", "neck", "spine", "chest", "shoulder", "clavicle", "arm", "hand",
        "pelvis", "hip", "thigh", "leg", "foot", "toe", "face", "eye", "jaw",
        "tongue", "breast",
    )

    def labels(obj):
        material_names = " ".join(material.name for material in obj.data.materials if material)
        return f"{obj.name} {material_names}".lower()

    def is_hair_mesh(obj):
        object_name = obj.name.lower()
        if any(token in object_name for token in hair_tokens):
            return not any(token in object_name for token in facial_tokens)
        return (
            any(token in labels(obj) for token in hair_tokens)
            and not any(token in object_name for token in facial_tokens)
        )

    hair_meshes = [obj for obj in meshes if is_hair_mesh(obj)]
    weighted_names = {
        group.name for obj in hair_meshes for group in obj.vertex_groups
        if group.name in target.pose.bones
    }

    def named_hair_bone(name):
        lowered = name.lower()
        return (
            any(token in lowered for token in hair_tokens)
            and not any(token in lowered for token in facial_tokens)
        )

    def standard_body_bone(name):
        lowered = name.lower()
        return any(token in lowered for token in body_tokens)

    candidate_names = {
        bone.name for bone in target.pose.bones
        if named_hair_bone(bone.name)
        or (bone.name in weighted_names and not standard_body_bone(bone.name))
    }
    # Retain unlabelled intermediate joints only when the hair mesh actually
    # references them. This supports custom rigs without pulling facial/body bones.
    changed = True
    while changed:
        changed = False
        for name in tuple(candidate_names):
            parent = target.pose.bones[name].parent
            if (
                parent and parent.name in weighted_names and parent.name not in candidate_names
                and not standard_body_bone(parent.name)
            ):
                candidate_names.add(parent.name)
                changed = True

    hair_bones = [target.pose.bones[name] for name in sorted(candidate_names)]
    rest = {bone.name: bone.matrix_basis.copy() for bone in hair_bones}
    gain = float(ACTION_PARAMS.get("hair_gain", 1.0))
    height = max(
        max((target.matrix_world @ bone.matrix_local).translation.z for bone in target.data.bones)
        - min((target.matrix_world @ bone.matrix_local).translation.z for bone in target.data.bones),
        1e-6,
    )
    states = [recipe_state(recipe, index / max(1, frame_count - 1), height) for index in range(frame_count)]

    hair_names = set(candidate_names)
    fixed_roots = {
        bone.name for bone in hair_bones
        if bone.parent is None
        or any(token in bone.name.lower() for token in ("socket", "scalp", "cap", "hair_root"))
    }
    branch_for = {}
    depth_for = {}
    for bone in hair_bones:
        if bone.name in fixed_roots:
            continue
        cursor = bone
        depth = 1
        while cursor.parent and cursor.parent.name in hair_names and cursor.parent.name not in fixed_roots:
            cursor = cursor.parent
            depth += 1
        branch_for[bone.name] = cursor.name
        depth_for[bone.name] = depth
    branch_depth = {}
    branch_length = {}
    for name, branch in branch_for.items():
        branch_depth[branch] = max(branch_depth.get(branch, 0), depth_for[name])
        cursor = target.pose.bones[name]
        length = 0.0
        while cursor and cursor.name in hair_names and cursor.name not in fixed_roots:
            length += float(cursor.bone.length)
            cursor = cursor.parent
        branch_length[branch] = max(branch_length.get(branch, 0.0), length)

    def smooth_source(source):
        values = list(source)
        for _ in range(2):
            padded = [values[0], values[0], *values, values[-1], values[-1]]
            values = [
                (padded[i] + 2.0 * padded[i + 1] + 3.0 * padded[i + 2]
                 + 2.0 * padded[i + 3] + padded[i + 4]) / 9.0
                for i in range(len(values))
            ]
        return values

    def spring_deflection(source, stiffness, damping, limit):
        # A strand follows head motion through a damped spring. The difference
        # between the spring state and the head is its inertial bend: it trails
        # during acceleration and settles without a synthetic periodic pull.
        source = smooth_source(source)
        dt = 1.0 / 60.0
        position = source[0]
        velocity = 0.0
        values = []
        for target_value in source:
            # Two substeps per output frame avoid numerical chatter. Near-critical
            # damping gives a soft follow-through without a rubber-band rebound.
            for _ in range(2):
                acceleration = stiffness * (target_value - position) - damping * velocity
                velocity += acceleration * dt
                position += velocity * dt
            values.append(max(-limit, min(limit, (position - target_value) * gain)))
        # One final short low-pass removes single-frame angular impulses while
        # keeping the causal lag produced by the spring.
        return [
            0.25 * values[max(0, i - 1)] + 0.5 * values[i]
            + 0.25 * values[min(len(values) - 1, i + 1)]
            for i in range(len(values))
        ]

    yaw_source = [
        state["head_yaw"] + 1.15 * state["torso"].x / height
        for state in states
    ]
    roll_source = [
        state["head_pitch"] + 0.55 * state["torso"].z / height
        for state in states
    ]
    branch_motion = {}
    branch_profile = {}
    for branch in sorted(branch_depth):
        length_normalized = branch_length[branch] / height
        mobility = max(0.0, min(1.0, (length_normalized - 0.025) / 0.22))
        # Short chains are stiff; long hair and ponytails respond more slowly.
        # A tiny deterministic variation prevents distinct locks moving as a sheet.
        variation = (sum(ord(char) for char in branch) % 9 - 4) * 0.018
        stiffness = (54.0 - 30.0 * mobility) * (1.0 + variation)
        damping = 2.0 * math.sqrt(stiffness) * 1.01
        yaw_limit = math.radians(1.5 + 4.5 * mobility)
        roll_limit = math.radians(0.8 + 2.8 * mobility)
        tip_lag = 0
        branch_motion[branch] = (
            spring_deflection(yaw_source, stiffness, damping, yaw_limit),
            spring_deflection(roll_source, stiffness * 1.08, damping, roll_limit),
        )
        branch_profile[branch] = {
            "length_normalized": length_normalized, "mobility": mobility,
            "stiffness": stiffness, "tip_lag_frames": tip_lag,
            "yaw_limit_deg": math.degrees(yaw_limit),
        }

    peak = 0.0
    max_local = 0.0
    for frame in range(1, frame_count + 1):
        source_index = frame - 1
        for bone in hair_bones:
            if bone.name in fixed_roots or bone.name not in branch_for:
                # The scalp/socket must follow the head exactly; secondary
                # motion begins only after the first strand joint.
                bone.matrix_basis = rest[bone.name].copy()
                continue
            branch = branch_for[bone.name]
            depth = depth_for[bone.name]
            count = branch_depth[branch]
            normalized_depth = depth / max(1, count)
            tip_lag = branch_profile[branch]["tip_lag_frames"]
            # The spring already supplies temporal lag. Additional per-joint
            # frame delays made long hair look pulled rather than freely bending.
            yaw_total = branch_motion[branch][0][source_index]
            roll_total = branch_motion[branch][1][source_index]
            weights = [(item / count) ** 1.6 for item in range(1, count + 1)]
            local_share = weights[depth - 1] / max(sum(weights), 1e-6)
            yaw = yaw_total * local_share
            roll = roll_total * local_share
            peak = max(peak, abs(yaw_total), abs(roll_total))
            max_local = max(max_local, abs(yaw), abs(roll))
            bone.rotation_mode = "QUATERNION"
            bone.rotation_quaternion = (
                rest[bone.name].to_quaternion()
                @ Quaternion((0.0, 0.0, 1.0), yaw)
                @ Quaternion((0.0, 1.0, 0.0), roll)
            )
            bone.keyframe_insert("rotation_quaternion", frame=frame, group=bone.name)
    hair_visual_extent = 0.0
    for obj in hair_meshes:
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        if corners:
            hair_visual_extent = max(hair_visual_extent, max(p.z for p in corners) - min(p.z for p in corners))
    visual_length_normalized = hair_visual_extent / height
    longest_chain = max(branch_length.values(), default=0.0) / height
    dynamic_expected = longest_chain >= 0.06
    long_static_hair = bool(hair_meshes) and not branch_motion and visual_length_normalized >= 0.22
    qc_status = "needs_hair_adapter" if long_static_hair else "passed"
    return {
        "hair_bone_count": len(hair_bones), "hair_secondary_peak_deg": math.degrees(peak),
        "hair_motion_driver": "asset_adaptive_critically_damped_tip_weighted_chains_v2",
        "hair_motion_qc_status": qc_status, "hair_dynamic_expected": dynamic_expected,
        "hair_visual_meshes": sorted(obj.name for obj in hair_meshes),
        "hair_visual_length_normalized": visual_length_normalized,
        "hair_longest_chain_normalized": longest_chain,
        "hair_fixed_root_count": len(fixed_roots), "hair_branch_count": len(branch_depth),
        "hair_branch_profiles": branch_profile,
        "hair_max_local_joint_deg": math.degrees(max_local),
    }


def generate_observation_motion(helper, target, output_count, meshes):
    required = (
        "torso", "head", "foot_ik.L", "foot_ik.R", "thigh_ik_target.L", "thigh_ik_target.R",
        "hand_ik.L", "hand_ik.R", "upper_arm_ik_target.L", "upper_arm_ik_target.R",
    )
    missing = [name for name in required if name not in target.pose.bones]
    if missing:
        raise RuntimeError(f"unsupported target controls: {missing}")
    data = load_motion_observation()
    samples, contacts, timeline_report = observation_timeline(data, output_count)
    profile = build_character_motion_profile(helper, target, meshes)
    subject_style = ACTION_PARAMS.get("subject_style") or {}
    profile["arm_motion_gain"] = max(0.56, min(
        1.04,
        profile["arm_motion_gain"] * float(subject_style.get("gesture_scale_bias", 1.0)),
    ))
    profile["leg_motion_gain"] = max(0.54, min(
        1.02,
        profile["leg_motion_gain"] * float(subject_style.get("gesture_scale_bias", 1.0)),
    ))
    profile["root_motion_gain"] = max(0.58, min(
        0.98,
        profile["root_motion_gain"] * float(subject_style.get("root_scale_bias", 1.0)),
    ))
    profile["turn_gain"] = max(0.52, min(
        0.96,
        profile["turn_gain"] * float(subject_style.get("turn_scale_bias", 1.0)),
    ))
    profile["subject_style"] = subject_style
    profile["profile_version"] = "asset_adaptive_dense_observation_profile_v4"
    source_root_positions = [vector_from(sample["root_trajectory"]) for sample in samples]
    source_root_origin = source_root_positions[0]
    source_root_extent = max(
        ((position - source_root_origin).length for position in source_root_positions),
        default=0.0,
    )
    uncapped_root_gain = profile["root_motion_gain"]
    if source_root_extent > 1e-6:
        profile["root_motion_gain"] = min(
            profile["root_motion_gain"], 0.45 / source_root_extent
        )
    profile["source_root_extent_normalized"] = source_root_extent
    profile["uncapped_root_motion_gain"] = uncapped_root_gain
    profile["root_travel_cap_normalized"] = 0.45
    target.animation_data_clear()
    forced = force_rigify_ik(target)
    bpy.context.view_layer.update()
    rest = {name: target.matrix_world @ target.pose.bones[name].matrix.copy() for name in required}
    low = min((target.matrix_world @ bone.matrix_local).translation.z for bone in target.data.bones)
    high = max((target.matrix_world @ bone.matrix_local).translation.z for bone in target.data.bones)
    height = max(high - low, 1e-6)
    up = Vector((0.0, 0.0, 1.0))
    shoulder_rest = {
        side: world_head(target, f"ORG-upper_arm.{side}") for side in ("L", "R")
    }
    hip_rest = {side: world_head(target, f"ORG-thigh.{side}") for side in ("L", "R")}
    anatomical_right = (shoulder_rest["R"] - shoulder_rest["L"]).normalized()
    forward = up.cross(anatomical_right).normalized()

    def mapped(values, scale=height):
        value = vector_from(values)
        return (anatomical_right * value.x + forward * value.y + up * value.z) * scale

    def distance(first, second):
        return (vector_from(first) - vector_from(second)).length

    source_reference = data["samples"][0]["normalized_joints"]
    source_lengths = {}
    target_lengths = {}
    for suffix, semantic in (("L", "left"), ("R", "right")):
        source_lengths[f"arm_{suffix}"] = max(
            1e-5,
            distance(source_reference[f"{semantic}arm"], source_reference[f"{semantic}forearm"])
            + distance(source_reference[f"{semantic}forearm"], source_reference[f"{semantic}hand"]),
        )
        source_lengths[f"leg_{suffix}"] = max(
            1e-5,
            distance(source_reference[f"{semantic}upleg"], source_reference[f"{semantic}leg"])
            + distance(source_reference[f"{semantic}leg"], source_reference[f"{semantic}foot"]),
        )
        shoulder = shoulder_rest[suffix]
        elbow = world_head(target, f"ORG-forearm.{suffix}")
        wrist = world_head(target, f"ORG-hand.{suffix}")
        hip = hip_rest[suffix]
        knee = world_head(target, f"ORG-shin.{suffix}")
        ankle = world_head(target, f"ORG-foot.{suffix}")
        target_lengths[f"arm_{suffix}"] = (shoulder - elbow).length + (elbow - wrist).length
        target_lengths[f"leg_{suffix}"] = (hip - knee).length + (knee - ankle).length

    foot_targets = {"L": [], "R": []}
    for sample in samples:
        joints = sample["normalized_joints"]
        root_shift = mapped(sample["root_trajectory"], height * profile["root_motion_gain"])
        for suffix, semantic in (("L", "left"), ("R", "right")):
            source_delta = (
                vector_from(joints[f"{semantic}foot"])
                - vector_from(source_reference[f"{semantic}foot"])
            )
            desired_foot = (
                rest[f"foot_ik.{suffix}"].translation + root_shift
                + mapped(source_delta, height * profile["leg_motion_gain"])
            )
            foot_targets[suffix].append(desired_foot)
    target_ground = min(rest["foot_ik.L"].translation.z, rest["foot_ik.R"].translation.z)
    source_floor = min(position.z for values in foot_targets.values() for position in values)
    floor_offset = target_ground - source_floor
    foot_temporal_repairs = {"foot_L": 0, "foot_R": 0}
    for suffix in ("L", "R"):
        for position in foot_targets[suffix]:
            position.z += floor_offset
    rest_foot_separation = abs(
        (rest["foot_ik.R"].translation - rest["foot_ik.L"].translation).dot(anatomical_right)
    )
    minimum_foot_separation = max(0.09 * height, 0.72 * rest_foot_separation)
    for left, right, sample in zip(foot_targets["L"], foot_targets["R"], samples):
        frame_right = Quaternion(
            up, math.radians(sample["body_yaw_deg"]) * profile["turn_gain"]
        ) @ anatomical_right
        separation = (right - left).dot(frame_right)
        if separation < minimum_foot_separation:
            correction = 0.5 * (minimum_foot_separation - separation)
            left -= frame_right * correction
            right += frame_right * correction
    for suffix in ("L", "R"):
        semantic = "left" if suffix == "L" else "right"
        foot_targets[suffix] = stabilize_contact_runs(foot_targets[suffix], contacts, semantic)
        limited = []
        previous = None
        for position in foot_targets[suffix]:
            position, repaired = clamp_temporal_step(position, previous, 0.03 * height)
            foot_temporal_repairs[f"foot_{suffix}"] += int(repaired)
            limited.append(position)
            previous = position
        foot_targets[suffix] = limited

    trajectory_features = []
    temporal_repair_counts = {
        "torso": 0, "hand_L": 0, "hand_R": 0, **foot_temporal_repairs,
    }
    previous_torso_position = None
    previous_hand_positions = {"L": None, "R": None}
    first_torso_vector = vector_from(samples[0]["normalized_joints"]["head"])
    first_pitch = math.atan2(-first_torso_vector.y, max(1e-6, first_torso_vector.z))
    first_roll = math.atan2(first_torso_vector.x, max(1e-6, first_torso_vector.z))
    for frame, (sample, contact) in enumerate(zip(samples, contacts), start=1):
        # Solve against the evaluated hierarchy at the destination frame.
        # Inserting a key at another frame without moving the scene cursor
        # makes Blender evaluate IK against stale frame-one parent transforms.
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        joints = sample["normalized_joints"]
        root_shift = mapped(sample["root_trajectory"], height * profile["root_motion_gain"])
        torso_vector = vector_from(joints["head"])
        pitch = max(-math.radians(48.0), min(math.radians(48.0),
                    math.atan2(-torso_vector.y, max(1e-6, torso_vector.z)) - first_pitch))
        roll = max(-math.radians(38.0), min(math.radians(38.0),
                   math.atan2(torso_vector.x, max(1e-6, torso_vector.z)) - first_roll))
        yaw = math.radians(sample["body_yaw_deg"]) * profile["turn_gain"]
        torso_rotation = (
            Quaternion(up, yaw)
            @ Quaternion(anatomical_right, pitch)
            @ Quaternion(forward, roll)
            @ rest["torso"].to_quaternion()
        )
        torso_position = rest["torso"].translation + root_shift + Vector((0.0, 0.0, floor_offset))
        torso_position, repaired = clamp_temporal_step(
            torso_position, previous_torso_position, 0.02 * height
        )
        temporal_repair_counts["torso"] += int(repaired)
        previous_torso_position = torso_position.copy()
        key_world(helper, target, "torso", torso_rotation, torso_position, frame)
        bpy.context.view_layer.update()

        neck = vector_from(joints.get("neck", joints["head"]))
        head = vector_from(joints["head"])
        neck_to_head = head - neck
        head_pitch = max(-math.radians(30.0), min(math.radians(30.0),
                         math.atan2(-neck_to_head.y, max(1e-6, neck_to_head.z))))
        head_rotation = Quaternion(anatomical_right, head_pitch) @ torso_rotation @ rest["torso"].to_quaternion().inverted() @ rest["head"].to_quaternion()
        key_world(helper, target, "head", head_rotation, world_head(target, "ORG-spine.007"), frame)

        frame_feature = [torso_position.copy()]
        frame_lateral_left = (
            world_head(target, "ORG-upper_arm.L")
            - world_head(target, "ORG-upper_arm.R")
        ).normalized()
        frame_forward_local = frame_lateral_left.cross(up).normalized()
        leg_requests = {}
        for suffix, semantic, side_sign in (("L", "left", 1.0), ("R", "right", -1.0)):
            foot_name = f"foot_ik.{suffix}"
            foot_position = foot_targets[suffix][frame - 1]
            foot_rotation = Quaternion(up, yaw) @ rest[foot_name].to_quaternion()
            key_world(helper, target, foot_name, foot_rotation, foot_position, frame)
            hip = world_head(target, f"ORG-thigh.{suffix}")
            source_knee = (
                vector_from(joints[f"{semantic}leg"])
                - vector_from(source_reference[f"{semantic}leg"])
            )
            knee_landmark = world_head(target, f"ORG-shin.{suffix}") + mapped(
                source_knee, height * profile["leg_motion_gain"]
            )
            outward = frame_lateral_left * side_sign
            knee_fallback = frame_forward_local + outward * 0.08
            knee_pole = pole_from_landmark(hip, foot_position, knee_landmark, knee_fallback, 0.30 * height)
            leg_requests[suffix] = {
                "hip": hip, "ankle": foot_position, "landmark": knee_landmark,
                "base_pole": knee_pole, "outward": outward,
            }
            frame_feature.append(foot_position.copy())

        # Write the reference-derived pole first. A second pass below repairs
        # it against the final evaluated parent animation.
        for suffix, side_sign in (("L", 1.0), ("R", -1.0)):
            request = leg_requests[suffix]
            pole_name = f"thigh_ik_target.{suffix}"
            key_world(
                helper, target, pole_name, rest[pole_name].to_quaternion(),
                request["base_pole"], frame,
            )

        bpy.context.view_layer.update()
        body_hips = (world_head(target, "ORG-thigh.L") + world_head(target, "ORG-thigh.R")) * 0.5
        body_chest = world_head(target, "ORG-spine.004")
        body_neck = world_head(target, "ORG-spine.007")
        body_capsules = (
            (body_hips, body_chest, profile["body_clearance_ratio"] * height),
            (body_chest, body_neck, (profile["body_clearance_ratio"] - 0.015) * height),
        )
        for suffix, semantic, side_sign in (("L", "left", 1.0), ("R", "right", -1.0)):
            shoulder = world_head(target, f"ORG-upper_arm.{suffix}")
            source_hand = (
                vector_from(joints[f"{semantic}hand"])
                - vector_from(source_reference[f"{semantic}hand"])
            )
            source_elbow = (
                vector_from(joints[f"{semantic}forearm"])
                - vector_from(source_reference[f"{semantic}forearm"])
            )
            asymmetry = 1.0 + side_sign * profile["gesture_asymmetry"]
            hand_position = (
                rest[f"hand_ik.{suffix}"].translation + root_shift
                + mapped(source_hand, height * profile["arm_motion_gain"] * asymmetry)
            )
            hand_position = clamp_chain_target(
                shoulder, hand_position, target_lengths[f"arm_{suffix}"] * 0.985,
                forward + anatomical_right * side_sign,
            )
            for capsule_start, capsule_end, capsule_radius in body_capsules:
                hand_position, _ = helper.push_outside_capsule(
                    hand_position, capsule_start, capsule_end, capsule_radius,
                    forward + anatomical_right * side_sign,
                )
            hand_position, repaired = clamp_temporal_step(
                hand_position, previous_hand_positions[suffix], 0.03 * height
            )
            temporal_repair_counts[f"hand_{suffix}"] += int(repaired)
            previous_hand_positions[suffix] = hand_position.copy()
            hand_name = f"hand_ik.{suffix}"
            key_world(helper, target, hand_name, rest[hand_name].to_quaternion(), hand_position, frame)
            elbow_landmark = world_head(target, f"ORG-forearm.{suffix}") + mapped(
                source_elbow, height * profile["arm_motion_gain"] * asymmetry
            )
            elbow_fallback = anatomical_right * (-side_sign) + forward * 0.25
            elbow_pole = pole_from_landmark(
                shoulder, hand_position, elbow_landmark, elbow_fallback, 0.24 * height
            )
            pole_name = f"upper_arm_ik_target.{suffix}"
            key_world(helper, target, pole_name, rest[pole_name].to_quaternion(), elbow_pole, frame)
            frame_feature.append(hand_position.copy())
        trajectory_features.append(frame_feature)
        bpy.context.view_layer.update()

    # Re-evaluate the completed hierarchy and repair only frames whose knee
    # crossed the hip-ankle plane. Every parent/control key now exists, so the
    # accepted result is the same pose that the renderer and QC will see.
    set_linear_interpolation(target)
    pole_repairs = []
    for frame in range(1, output_count + 1):
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        lateral_left = (
            world_head(target, "ORG-upper_arm.L")
            - world_head(target, "ORG-upper_arm.R")
        ).normalized()
        frame_forward = lateral_left.cross(up).normalized()
        for suffix, side_sign in (("L", 1.0), ("R", -1.0)):
            outward = lateral_left * side_sign
            hip = world_head(target, f"ORG-thigh.{suffix}")
            knee = world_head(target, f"ORG-shin.{suffix}")
            ankle = world_head(target, f"ORG-foot.{suffix}")
            chain = ankle - hip
            fraction = (knee - hip).dot(chain) / max(chain.length_squared, 1e-7)
            line_point = hip + chain * max(0.0, min(1.0, fraction))
            before = (knee - line_point).dot(outward) / height
            if before >= -0.002:
                continue
            pole_name = f"thigh_ik_target.{suffix}"
            pole_bone = target.pose.bones[pole_name]
            current_pole = (target.matrix_world @ pole_bone.matrix).translation
            axis = chain.normalized()
            midpoint = hip.lerp(ankle, 0.5)
            best = None
            for angle_degrees in range(-180, 180, 30):
                angle = math.radians(angle_degrees)
                radial = outward * math.cos(angle) + frame_forward * math.sin(angle)
                radial -= axis * radial.dot(axis)
                if radial.length <= 1e-6:
                    continue
                candidate = midpoint + radial.normalized() * 0.30 * height
                helper.set_world_transform(
                    target, pole_bone, rest[pole_name].to_quaternion(), candidate,
                )
                bpy.context.view_layer.update()
                solved_knee = world_head(target, f"ORG-shin.{suffix}")
                solved_hip = world_head(target, f"ORG-thigh.{suffix}")
                solved_ankle = world_head(target, f"ORG-foot.{suffix}")
                solved_chain = solved_ankle - solved_hip
                solved_fraction = (
                    (solved_knee - solved_hip).dot(solved_chain)
                    / max(solved_chain.length_squared, 1e-7)
                )
                solved_line = solved_hip + solved_chain * max(
                    0.0, min(1.0, solved_fraction)
                )
                after = (solved_knee - solved_line).dot(outward) / height
                displacement = (candidate - current_pole).length / height
                penalty = displacement + (0.0 if after >= 0.008 else 100.0 * (0.008 - after))
                if best is None or penalty < best[0]:
                    best = (penalty, candidate.copy(), after)
            key_world(
                helper, target, pole_name, rest[pole_name].to_quaternion(), best[1], frame
            )
            pole_repairs.append({
                "frame": frame, "side": suffix, "before": before, "after": best[2],
            })

    flattened = [[coordinate for point in row for coordinate in point] for row in trajectory_features]
    base = flattened[0]
    amplitudes = [math.sqrt(sum((value - start) ** 2 for value, start in zip(row, base))) for row in flattened]
    velocities = [math.sqrt(sum((b - a) ** 2 for a, b in zip(first, second)))
                  for first, second in zip(flattened, flattened[1:])]
    accelerations = [abs(second - first) for first, second in zip(velocities, velocities[1:])]
    amplitude = max(amplitudes, default=0.0) / height
    max_velocity = max(velocities, default=0.0) / height
    max_acceleration = max(accelerations, default=0.0) / height
    if amplitude < 0.075:
        raise RuntimeError(f"observation-driven trajectory amplitude too small: {amplitude:.4f}H")
    if max_velocity > 0.16 or max_acceleration > 0.10:
        raise RuntimeError(
            "observation-driven trajectory is discontinuous: "
            f"velocity={max_velocity:.4f}H acceleration={max_acceleration:.4f}H"
        )
    keyframe_count = set_linear_interpolation(target)
    return {
        "recipe": "subject_specific_dense_observation_reconstruction",
        "forced_ik_constraint_count": len(forced),
        "character_height": height,
        "authored_trajectory_qc": {
            "status": "passed",
            "amplitude_normalized": amplitude,
            "max_frame_velocity_normalized": max_velocity,
            "max_frame_acceleration_normalized": max_acceleration,
            "terminal_policy": "preserve_source_motion_without_forced_stop",
            "source_contact_locking": True,
        },
        "linear_keyframe_count": keyframe_count,
        "observation_segment_id": data["segment_id"],
        "observation_fingerprint": data["trajectory_fingerprint"],
        "source_bone_rotations_used": False,
        "target_native_solver": "subject_specific_landmark_ik_v4",
        "final_timeline_leg_pole_repairs": pole_repairs,
        "target_space_temporal_repair_counts": temporal_repair_counts,
        "character_motion_profile": profile,
        **timeline_report,
    }


def generate_motion(helper, target, timing, recipe, meshes=None):
    required = [
        "torso", "head", "foot_ik.L", "foot_ik.R", "thigh_ik_target.L", "thigh_ik_target.R",
        "hand_ik.L", "hand_ik.R", "upper_arm_ik_target.L", "upper_arm_ik_target.R",
    ]
    required.extend(
        name for name in ("toe_ik.L", "toe_ik.R") if name in target.pose.bones
    )
    missing = [name for name in required if name not in target.pose.bones]
    if missing:
        raise RuntimeError(f"unsupported target controls: {missing}")
    target.animation_data_clear()
    forced = force_rigify_ik(target)
    bpy.context.view_layer.update()
    rest = {name: target.matrix_world @ target.pose.bones[name].matrix.copy() for name in required}
    low = min((target.matrix_world @ bone.matrix_local).translation.z for bone in target.data.bones)
    high = max((target.matrix_world @ bone.matrix_local).translation.z for bone in target.data.bones)
    height = max(high - low, 1e-6)
    profile = build_character_motion_profile(helper, target, meshes or [], None)
    ACTION_PARAMS["character_motion_profile"] = profile
    if ACTION_PARAMS.get("expanded_blueprint"):
        runtime = load_expanded_presets()
        effective_blueprint = runtime.enforce_compatibility(
            ACTION_PARAMS["expanded_blueprint"], profile
        )
        ACTION_PARAMS["expanded_blueprint_effective"] = effective_blueprint
        ACTION_PARAMS["runtime_compatibility_repairs"] = effective_blueprint.get(
            "compatibility_repairs", []
        )
        adaptive_leg_pole_search = effective_blueprint["lower"] in runtime.DEEP_LOWER
    else:
        adaptive_leg_pole_search = False
    center_x = rest["torso"].translation.x
    up = Vector((0.0, 0.0, 1.0))
    shoulder_rest = {
        suffix: world_head(target, f"ORG-upper_arm.{suffix}") for suffix in ("L", "R")
    }
    lateral_left = (shoulder_rest["L"] - shoulder_rest["R"]).normalized()
    forward = lateral_left.cross(up).normalized()
    arm_lengths = {}
    leg_lengths = {}
    rest_elbows = {}
    rest_hands = {}
    rest_knees = {}
    for suffix in ("L", "R"):
        shoulder = shoulder_rest[suffix]
        elbow = world_head(target, f"ORG-forearm.{suffix}")
        wrist = world_head(target, f"ORG-hand.{suffix}")
        hip = world_head(target, f"ORG-thigh.{suffix}")
        knee = world_head(target, f"ORG-shin.{suffix}")
        ankle = world_head(target, f"ORG-foot.{suffix}")
        arm_lengths[suffix] = (shoulder - elbow).length + (elbow - wrist).length
        leg_lengths[suffix] = (hip - knee).length + (knee - ankle).length
        rest_elbows[suffix] = elbow
        rest_hands[suffix] = rest[f"hand_ik.{suffix}"].translation.copy()
        rest_knees[suffix] = knee
    rest_knee_separation = (rest_knees["L"] - rest_knees["R"]).dot(lateral_left)
    leg_side_orientation = 1.0 if rest_knee_separation >= 0.0 else -1.0
    rest_knee_separation = abs(rest_knee_separation)

    authored_states = [recipe_state(recipe, t, height) for t in timing]
    authored_qc = trajectory_qc(authored_states, height)
    arm_target_clamp_count = 0
    maximum_requested_arm_reach = 0.0
    for frame, state in enumerate(authored_states, start=1):
        torso_rotation = Quaternion((0.0, 0.0, 1.0), state["yaw"]) @ Quaternion((1.0, 0.0, 0.0), state["pitch"]) @ rest["torso"].to_quaternion()
        torso_position = rest["torso"].translation + state["torso"]
        torso_delta_rotation = torso_rotation @ rest["torso"].to_quaternion().inverted()
        key_world(helper, target, "torso", torso_rotation, torso_position, frame)
        bpy.context.view_layer.update()

        head_rotation = Quaternion((0.0, 0.0, 1.0), state["head_yaw"]) @ Quaternion((1.0, 0.0, 0.0), state["head_pitch"]) @ torso_rotation @ rest["torso"].to_quaternion().inverted() @ rest["head"].to_quaternion()
        key_world(helper, target, "head", head_rotation, world_head(target, "ORG-spine.007"), frame)

        leg_points = {}
        foot_transforms = {}
        for suffix, sign in (("L", 1.0), ("R", -1.0)):
            side = suffix.lower()
            foot_name = f"foot_ik.{suffix}"
            foot_position = rest[foot_name].translation + state[f"foot_{side}"]
            hip = world_head(target, f"ORG-thigh.{suffix}")
            foot_position = clamp_chain_target(
                hip, foot_position, leg_lengths[suffix] * 0.995,
                forward + lateral_left * sign,
            )
            foot_rotation = (
                Quaternion(up, state.get(f"foot_yaw_{side}", 0.0))
                @ rest[foot_name].to_quaternion()
            )
            key_world(helper, target, foot_name, foot_rotation, foot_position, frame)
            foot_transforms[suffix] = (foot_rotation, foot_position)
            leg_points[suffix] = (hip, foot_position)

        pole_candidates = []
        native = {
            suffix: torso_position + torso_delta_rotation @ (
                rest[f"thigh_ik_target.{suffix}"].translation - rest["torso"].translation
            ) for suffix in ("L", "R")
        }
        pole_candidates.append(native)
        lateral_candidates = (
            (0.08, 0.16, 0.24, -0.08, -0.16, -0.24)
            if adaptive_leg_pole_search else ()
        )
        for lateral_amount in lateral_candidates:
            candidate = {}
            for suffix, sign in (("L", 1.0), ("R", -1.0)):
                hip, foot_position = leg_points[suffix]
                candidate[suffix] = (
                    hip.lerp(foot_position, 0.5) + forward * 0.28 * height
                    + lateral_left * sign * lateral_amount * height
                )
            pole_candidates.append(candidate)
        best = None
        for candidate in pole_candidates:
            for suffix in ("L", "R"):
                name = f"thigh_ik_target.{suffix}"
                helper.set_world_transform(
                    target, target.pose.bones[name], rest[name].to_quaternion(), candidate[suffix]
                )
            bpy.context.view_layer.update()
            separation = (
                world_head(target, "ORG-shin.L") - world_head(target, "ORG-shin.R")
            ).dot(lateral_left) * leg_side_orientation
            penalty = abs(separation - rest_knee_separation)
            if separation < max(0.01 * height, rest_knee_separation * 0.72):
                penalty += 10.0 * height
            if best is None or penalty < best[0]:
                best = (penalty, candidate)
        for suffix in ("L", "R"):
            pole_name = f"thigh_ik_target.{suffix}"
            key_world(
                helper, target, pole_name, rest[pole_name].to_quaternion(), best[1][suffix], frame
            )

        # Rigify parents toe_ik to a leg helper rather than foot_ik. Re-key it
        # after the knee pole so the entire foot remains one rigid transform.
        for suffix in ("L", "R"):
            toe_name = f"toe_ik.{suffix}"
            if toe_name not in rest:
                continue
            foot_name = f"foot_ik.{suffix}"
            foot_rotation, foot_position = foot_transforms[suffix]
            foot_delta = foot_rotation @ rest[foot_name].to_quaternion().inverted()
            toe_position = foot_position + foot_delta @ (
                rest[toe_name].translation - rest[foot_name].translation
            )
            toe_rotation = foot_delta @ rest[toe_name].to_quaternion()
            key_world(helper, target, toe_name, toe_rotation, toe_position, frame)

        bpy.context.view_layer.update()
        hip_center = (world_head(target, "ORG-thigh.L") + world_head(target, "ORG-thigh.R")) * 0.5
        chest = world_head(target, "ORG-spine.004")
        neck = world_head(target, "ORG-spine.007")
        body_capsules = (
            (hip_center, chest, min(0.105, profile["body_clearance_ratio"]) * height),
            (chest, neck, min(0.090, profile["body_clearance_ratio"] - 0.015) * height),
        )
        for suffix, sign in (("L", 1.0), ("R", -1.0)):
            side = suffix.lower()
            shoulder = world_head(target, f"ORG-upper_arm.{suffix}")
            stand = torso_position + torso_delta_rotation @ (
                rest_hands[suffix] - rest["torso"].translation
            )
            active = (
                stand - lateral_left * sign * 0.045 * height
                + forward * 0.060 * height + up * 0.080 * height
            )
            blend = max(0.0, min(1.0, state[f"hand_blend_{side}"]))
            raw_offset = state[f"hand_{side}"]
            mapped_offset = (
                lateral_left * raw_offset.x - forward * raw_offset.y + up * raw_offset.z
            ) * profile["arm_motion_gain"]
            requested = stand.lerp(active, blend) + mapped_offset
            requested_reach = (requested - shoulder).length / max(arm_lengths[suffix], 1e-7)
            maximum_requested_arm_reach = max(maximum_requested_arm_reach, requested_reach)
            hand_position = clamp_chain_target(
                shoulder, requested, arm_lengths[suffix] * 0.92,
                forward + lateral_left * sign,
            )
            if (hand_position - requested).length > 1e-6:
                arm_target_clamp_count += 1
            minimum_reach = arm_lengths[suffix] * 0.24
            if (hand_position - shoulder).length < minimum_reach:
                direction = hand_position - shoulder
                if direction.length < 1e-7:
                    direction = forward + lateral_left * sign
                hand_position = shoulder + direction.normalized() * minimum_reach
            for capsule_start, capsule_end, capsule_radius in body_capsules:
                hand_position, _ = helper.push_outside_capsule(
                    hand_position, capsule_start, capsule_end, capsule_radius,
                    forward + lateral_left * sign,
                )
            hand_position = clamp_chain_target(
                shoulder, hand_position, arm_lengths[suffix] * 0.92,
                forward + lateral_left * sign,
            )
            hand_name = f"hand_ik.{suffix}"
            key_world(helper, target, hand_name, rest[hand_name].to_quaternion(), hand_position, frame)
            pole_name = f"upper_arm_ik_target.{suffix}"
            elbow_position = torso_position + torso_delta_rotation @ (
                rest[pole_name].translation - rest["torso"].translation
            )
            key_world(helper, target, pole_name, rest[pole_name].to_quaternion(), elbow_position, frame)
        bpy.context.view_layer.update()
    keyframe_count = set_linear_interpolation(target)
    return {
        "recipe": recipe, "forced_ik_constraint_count": len(forced),
        "character_height": height, "authored_trajectory_qc": authored_qc,
        "linear_keyframe_count": keyframe_count,
        "character_motion_profile": profile,
        "arm_target_qc": {
            "status": "passed",
            "maximum_requested_reach_ratio": maximum_requested_arm_reach,
            "clamped_target_count": arm_target_clamp_count,
            "maximum_solved_reach_ratio": 0.92,
            "ik_stretch_disabled": True,
        },
    }


def generate_direct_motion(target, timing, recipe, helper):
    semantic = helper.semantic_bone_map(target)
    target.animation_data_clear()
    driven = {}
    for name in (
        "hips", "spine", "spine1", "spine2", "neck", "head",
        "leftarm", "leftforearm", "rightarm", "rightforearm",
        "leftupleg", "leftleg", "rightupleg", "rightleg",
    ):
        bone_name = semantic.get(name)
        if bone_name and bone_name in target.pose.bones:
            bone = target.pose.bones[bone_name]
            for constraint in bone.constraints:
                constraint.mute = True
            driven[name] = bone
    if len(driven) < 6:
        raise RuntimeError(f"insufficient target-native controls: {sorted(driven)}")
    rest_basis = {name: bone.matrix_basis.copy() for name, bone in driven.items()}
    low = min((target.matrix_world @ bone.matrix_local).translation.z for bone in target.data.bones)
    high = max((target.matrix_world @ bone.matrix_local).translation.z for bone in target.data.bones)
    height = max(high - low, 1e-6)
    hair_bones = [
        bone for bone in target.pose.bones
        if any(token in bone.name.lower() for token in ("hair", "ponytail", "braid", "bang"))
    ]
    for bone in hair_bones:
        for constraint in bone.constraints:
            constraint.mute = True
    hair_rest = {bone.name: bone.matrix_basis.copy() for bone in hair_bones}

    for frame, t in enumerate(timing, start=1):
        state = recipe_state(recipe, t, height)
        pulse = math.sin(math.pi * t) ** 2
        wave = math.sin(2.0 * math.pi * t)
        for name, bone in driven.items():
            base = rest_basis[name]
            rotation = Quaternion()
            if name == "hips":
                rotation = Quaternion((0.0, 0.0, 1.0), state["yaw"] * 0.75) @ Quaternion((1.0, 0.0, 0.0), state["pitch"] * 0.45)
                bone.location = base.to_translation() + state["torso"]
                bone.keyframe_insert("location", frame=frame, group=bone.name)
            elif name in {"spine", "spine1", "spine2"}:
                rotation = Quaternion((0.0, 0.0, 1.0), state["yaw"] * 0.12) @ Quaternion((1.0, 0.0, 0.0), state["pitch"] * 0.18)
            elif name in {"neck", "head"}:
                rotation = Quaternion((0.0, 0.0, 1.0), state["head_yaw"] * (0.45 if name == "neck" else 0.65)) @ Quaternion((1.0, 0.0, 0.0), state["head_pitch"] * (0.4 if name == "neck" else 0.7))
            elif name in {"leftarm", "rightarm"}:
                side = 1.0 if name.startswith("left") else -1.0
                blend = state["hand_blend_l" if side > 0 else "hand_blend_r"]
                rotation = Quaternion((1.0, 0.0, 0.0), math.radians(-32.0) * blend) @ Quaternion((0.0, 0.0, 1.0), math.radians(18.0) * side * blend)
            elif name in {"leftforearm", "rightforearm"}:
                side = 1.0 if name.startswith("left") else -1.0
                blend = state["hand_blend_l" if side > 0 else "hand_blend_r"]
                rotation = Quaternion((1.0, 0.0, 0.0), math.radians(38.0) * blend)
            elif name in {"leftupleg", "rightupleg"}:
                rotation = Quaternion((1.0, 0.0, 0.0), math.radians(-24.0) * pulse)
            elif name in {"leftleg", "rightleg"}:
                rotation = Quaternion((1.0, 0.0, 0.0), math.radians(46.0) * pulse)
            bone.rotation_mode = "QUATERNION"
            bone.rotation_quaternion = base.to_quaternion() @ rotation
            bone.keyframe_insert("rotation_quaternion", frame=frame, group=bone.name)
        hair_amount = state["head_yaw"] + math.radians(10.0) * wave
        for index, bone in enumerate(hair_bones):
            lag = 1.0 + min(index, 8) * 0.08
            bone.rotation_mode = "QUATERNION"
            bone.rotation_quaternion = hair_rest[bone.name].to_quaternion() @ Quaternion((0.0, 0.0, 1.0), -hair_amount * 0.35 * lag)
            bone.keyframe_insert("rotation_quaternion", frame=frame, group=bone.name)
        bpy.context.view_layer.update()
    return {
        "recipe": recipe, "adapter": "target_native_direct_deform_v1",
        "driven_semantics": sorted(driven), "hair_bone_count": len(hair_bones),
        "character_height": height,
    }


def generate_observation_direct_motion(target, output_count, helper):
    data = load_motion_observation()
    samples, contacts, timeline_report = observation_timeline(data, output_count)
    adapter = helper.make_adapter(target)
    adapter = dict(adapter)
    controls = dict(adapter["controls"])
    # Generic importers often expose both a scene-level "root" bone and the
    # real pelvis. The generic semantic mapper can prefer the shorter root
    # name even though the legs and spine are parented below Pelvis, which
    # makes the body separate when root motion is authored. Recover the
    # deepest common ancestor of both thighs as the actual hips control.
    if adapter["type"].startswith("direct_deform"):
        left_thigh = target.data.bones.get(controls.get("leftupleg"))
        right_thigh = target.data.bones.get(controls.get("rightupleg"))

        def ancestors(bone):
            result = []
            while bone:
                result.append(bone)
                bone = bone.parent
            return result

        if left_thigh and right_thigh:
            right_ancestors = set(ancestors(right_thigh))
            common = [bone for bone in ancestors(left_thigh) if bone in right_ancestors]
            pelvis = next(
                (bone for bone in common if any(token in bone.name.lower() for token in ("pelvis", "hip"))),
                common[0] if common else None,
            )
            if pelvis:
                controls["hips"] = pelvis.name
                adapter["root_control"] = pelvis.name
    adapter["controls"] = controls
    if adapter.get("outputs") == adapter.get("controls") or adapter["type"].startswith("direct_deform"):
        adapter["outputs"] = dict(controls)
    required = {
        "hips", "head", "leftarm", "leftforearm", "lefthand",
        "rightarm", "rightforearm", "righthand",
        "leftupleg", "leftleg", "leftfoot", "rightupleg", "rightleg", "rightfoot",
    }
    missing = sorted(required.difference(controls))
    if missing:
        raise RuntimeError(f"generic target lacks a complete semantic chain: {missing}")
    target.animation_data_clear()
    for pose_bone in target.pose.bones:
        pose_bone.matrix_basis.identity()
    driven_names = set(controls.values()) | set(adapter.get("spine_controls", ()))
    for target_name in driven_names:
        for constraint in target.pose.bones[target_name].constraints:
            constraint.mute = True
    bpy.context.view_layer.update()

    low = min((target.matrix_world @ bone.head_local).z for bone in target.data.bones)
    high = max((target.matrix_world @ bone.head_local).z for bone in target.data.bones)
    height = max(high - low, 1e-6)
    output_names = adapter.get("outputs", controls)

    def output_point(semantic):
        name = output_names.get(semantic, controls[semantic])
        return (target.matrix_world @ target.data.bones[name].matrix_local).translation

    anatomical_right = (output_point("rightarm") - output_point("leftarm")).normalized()
    up = Vector((0.0, 0.0, 1.0))
    forward = up.cross(anatomical_right).normalized()

    def mapped(values):
        value = vector_from(values)
        return anatomical_right * value.x + forward * value.y + up * value.z

    control_rest = {
        semantic: helper.world_rest(target, name)
        for semantic, name in controls.items()
    }
    output_rest_direction = {}
    children = {
        "hips": "spine", "spine": "spine1", "spine1": "spine2", "spine2": "neck",
        "neck": "head",
        "leftshoulder": "leftarm", "leftarm": "leftforearm", "leftforearm": "lefthand",
        "rightshoulder": "rightarm", "rightarm": "rightforearm", "rightforearm": "righthand",
        "leftupleg": "leftleg", "leftleg": "leftfoot", "leftfoot": "lefttoebase",
        "rightupleg": "rightleg", "rightleg": "rightfoot", "rightfoot": "righttoebase",
    }
    for semantic, child in children.items():
        if semantic in controls and child in controls:
            direction = output_point(child) - output_point(semantic)
            if direction.length > 1e-7:
                output_rest_direction[semantic] = direction.normalized()

    ordered = sorted(
        controls,
        key=lambda semantic: helper.hierarchy_depth(target.data.bones[controls[semantic]]),
    )
    root_name = adapter.get("root_control") or controls["hips"]
    root_rest = helper.world_rest(target, root_name)
    tracked = ("hips", "head", "lefthand", "righthand", "leftfoot", "rightfoot")
    trajectories = []
    for frame, sample in enumerate(samples, start=1):
        joints = sample["normalized_joints"]
        yaw = math.radians(sample["body_yaw_deg"])
        root_rotation = Quaternion(up, yaw) @ root_rest.to_quaternion()
        root_position = root_rest.translation + mapped(sample["root_trajectory"]) * height
        root_pose = target.pose.bones[root_name]
        root_pose.rotation_mode = "QUATERNION"
        helper.set_world_transform(target, root_pose, root_rotation, root_position)
        root_pose.keyframe_insert("location", frame=frame, group=root_name)
        root_pose.keyframe_insert("rotation_quaternion", frame=frame, group=root_name)
        bpy.context.view_layer.update()

        for semantic in ordered:
            if semantic == "hips" or semantic not in joints:
                continue
            target_name = controls[semantic]
            pose_bone = target.pose.bones[target_name]
            child = children.get(semantic)
            if child in joints and semantic in output_rest_direction:
                source_direction = mapped(vector_from(joints[child]) - vector_from(joints[semantic]))
                if source_direction.length < 1e-7:
                    continue
                desired_direction = source_direction.normalized()
                delta = output_rest_direction[semantic].rotation_difference(desired_direction)
                desired_rotation = (delta @ control_rest[semantic].to_quaternion()).normalized()
            else:
                desired_rotation = (Quaternion(up, yaw) @ control_rest[semantic].to_quaternion()).normalized()
            pose_bone.rotation_mode = "QUATERNION"
            helper.set_world_rotation(target, pose_bone, desired_rotation)
            pose_bone.keyframe_insert("rotation_quaternion", frame=frame, group=target_name)
            bpy.context.view_layer.update()
        trajectories.append([
            (target.matrix_world @ target.pose.bones[output_names.get(name, controls[name])].matrix).translation.copy()
            for name in tracked
        ])

    flattened = [[coordinate for point in row for coordinate in point] for row in trajectories]
    base = flattened[0]
    amplitude = max(
        math.sqrt(sum((value - start) ** 2 for value, start in zip(row, base)))
        for row in flattened
    ) / height
    velocities = [
        math.sqrt(sum((b - a) ** 2 for a, b in zip(first, second))) / height
        for first, second in zip(flattened, flattened[1:])
    ]
    accelerations = [abs(second - first) for first, second in zip(velocities, velocities[1:])]
    max_velocity = max(velocities, default=0.0)
    max_acceleration = max(accelerations, default=0.0)
    if amplitude < 0.05:
        raise RuntimeError(f"generic observation trajectory amplitude too small: {amplitude:.4f}H")
    if max_velocity > 0.16 or max_acceleration > 0.10:
        raise RuntimeError(
            "generic observation trajectory is discontinuous: "
            f"velocity={max_velocity:.4f}H acceleration={max_acceleration:.4f}H"
        )
    keyframe_count = set_linear_interpolation(target)
    return {
        "recipe": "observation_driven",
        "adapter": adapter["type"],
        "character_height": height,
        "driven_semantics": sorted(controls),
        "authored_trajectory_qc": {
            "status": "passed", "amplitude_normalized": amplitude,
            "max_frame_velocity_normalized": max_velocity,
            "max_frame_acceleration_normalized": max_acceleration,
            "terminal_policy": "preserve_source_motion_without_forced_stop",
            "source_contact_locking": False,
        },
        "linear_keyframe_count": keyframe_count,
        "observation_segment_id": data["segment_id"],
        "observation_fingerprint": data["trajectory_fingerprint"],
        "source_bone_rotations_used": False,
        "target_native_solver": "landmark_fk_direction_v3",
        **timeline_report,
    }


def generate_observation_generic_ik_motion(target, output_count, helper, meshes):
    data = load_motion_observation()
    samples, contacts, timeline_report = observation_timeline(data, output_count)
    adapter = dict(helper.make_adapter(target))
    controls = dict(adapter["controls"])
    required = {
        "hips", "head", "leftarm", "leftforearm", "lefthand",
        "rightarm", "rightforearm", "righthand",
        "leftupleg", "leftleg", "leftfoot", "rightupleg", "rightleg", "rightfoot",
    }
    missing = sorted(required.difference(controls))
    if missing:
        raise RuntimeError(f"generic IK target lacks semantic bones: {missing}")

    def ancestors(bone):
        result = []
        while bone:
            result.append(bone)
            bone = bone.parent
        return result

    left_thigh = target.data.bones[controls["leftupleg"]]
    right_thigh = target.data.bones[controls["rightupleg"]]
    right_ancestors = set(ancestors(right_thigh))
    common = [bone for bone in ancestors(left_thigh) if bone in right_ancestors]
    pelvis = next(
        (bone for bone in common if any(token in bone.name.lower() for token in ("pelvis", "hip"))),
        common[0] if common else target.data.bones[controls["hips"]],
    )
    controls["hips"] = pelvis.name
    adapter["controls"] = controls
    profile = build_character_motion_profile(helper, target, meshes, controls)

    target.animation_data_clear()
    for pose_bone in target.pose.bones:
        pose_bone.matrix_basis.identity()
    for semantic in required:
        for constraint in target.pose.bones[controls[semantic]].constraints:
            constraint.mute = True
    bpy.context.view_layer.update()

    low = min((target.matrix_world @ bone.head_local).z for bone in target.data.bones)
    high = max((target.matrix_world @ bone.head_local).z for bone in target.data.bones)
    height = max(high - low, 1e-6)
    world_rest = {semantic: helper.world_rest(target, name) for semantic, name in controls.items()}
    anatomical_right = (
        world_rest["rightarm"].translation - world_rest["leftarm"].translation
    ).normalized()
    up = Vector((0.0, 0.0, 1.0))
    forward = up.cross(anatomical_right).normalized()

    def mapped(values, scale=1.0):
        value = vector_from(values)
        return (anatomical_right * value.x + forward * value.y + up * value.z) * scale

    def source_distance(joints, first, second):
        return (vector_from(joints[first]) - vector_from(joints[second])).length

    source_rest = data["samples"][0]["normalized_joints"]
    target_lengths = {}
    source_lengths = {}
    for suffix, semantic in (("L", "left"), ("R", "right")):
        source_lengths[f"arm_{suffix}"] = max(
            1e-6,
            source_distance(source_rest, f"{semantic}arm", f"{semantic}forearm")
            + source_distance(source_rest, f"{semantic}forearm", f"{semantic}hand"),
        )
        source_lengths[f"leg_{suffix}"] = max(
            1e-6,
            source_distance(source_rest, f"{semantic}upleg", f"{semantic}leg")
            + source_distance(source_rest, f"{semantic}leg", f"{semantic}foot"),
        )
        target_lengths[f"arm_{suffix}"] = (
            world_rest[f"{semantic}arm"].translation - world_rest[f"{semantic}forearm"].translation
        ).length + (
            world_rest[f"{semantic}forearm"].translation - world_rest[f"{semantic}hand"].translation
        ).length
        target_lengths[f"leg_{suffix}"] = (
            world_rest[f"{semantic}upleg"].translation - world_rest[f"{semantic}leg"].translation
        ).length + (
            world_rest[f"{semantic}leg"].translation - world_rest[f"{semantic}foot"].translation
        ).length

    collection = bpy.context.scene.collection

    def control_object(name, position):
        obj = bpy.data.objects.new(f"RM_{name}", None)
        obj.empty_display_type = "PLAIN_AXES"
        obj.empty_display_size = 0.04 * height
        obj.hide_render = True
        obj.location = position
        collection.objects.link(obj)
        return obj

    controls_ik = {}
    for suffix, semantic in (("L", "left"), ("R", "right")):
        hand_target = control_object(f"hand_target_{suffix}", world_rest[f"{semantic}hand"].translation)
        elbow_pole = control_object(f"elbow_pole_{suffix}", world_rest[f"{semantic}forearm"].translation + forward * 0.25 * height)
        foot_target = control_object(f"foot_target_{suffix}", world_rest[f"{semantic}foot"].translation)
        knee_pole = control_object(f"knee_pole_{suffix}", world_rest[f"{semantic}leg"].translation + forward * 0.30 * height)
        forearm = target.pose.bones[controls[f"{semantic}forearm"]]
        leg = target.pose.bones[controls[f"{semantic}leg"]]
        for bone in (target.pose.bones[controls[f"{semantic}arm"]], forearm,
                     target.pose.bones[controls[f"{semantic}upleg"]], leg):
            bone.ik_stretch = 0.0
        arm_ik = forearm.constraints.new("IK")
        arm_ik.name = "RM_observation_arm_IK"
        arm_ik.target = hand_target
        arm_ik.pole_target = elbow_pole
        arm_ik.chain_count = 2
        arm_ik.use_tail = True
        arm_ik.use_stretch = False
        leg_ik = leg.constraints.new("IK")
        leg_ik.name = "RM_observation_leg_IK"
        leg_ik.target = foot_target
        leg_ik.pole_target = knee_pole
        leg_ik.chain_count = 2
        leg_ik.use_tail = True
        leg_ik.use_stretch = False
        controls_ik[suffix] = {
            "hand": hand_target, "elbow": elbow_pole, "foot": foot_target, "knee": knee_pole,
            "arm_constraint": arm_ik, "leg_constraint": leg_ik,
        }

    pelvis_pose = target.pose.bones[controls["hips"]]
    pelvis_rest = helper.world_rest(target, controls["hips"])
    previous_targets = {}
    contact_anchors = {"left": None, "right": None}
    max_endpoint_error = 0.0
    max_limb_ratio_error = 0.0
    collision_correction = 0.0
    trajectories = []
    first_torso = vector_from(source_rest["head"])
    first_pitch = math.atan2(-first_torso.y, max(1e-6, first_torso.z))
    for frame, (sample, contact) in enumerate(zip(samples, contacts), start=1):
        joints = sample["normalized_joints"]
        torso = vector_from(joints["head"])
        pitch = max(-math.radians(35.0), min(math.radians(35.0),
                    math.atan2(-torso.y, max(1e-6, torso.z)) - first_pitch))
        yaw = math.radians(sample["body_yaw_deg"]) * profile["turn_gain"]
        pelvis_rotation = Quaternion(up, yaw) @ Quaternion(anatomical_right, pitch * 0.45) @ pelvis_rest.to_quaternion()
        pelvis_position = pelvis_rest.translation + mapped(
            sample["root_trajectory"], height * profile["root_motion_gain"]
        )
        set_world_transform_preserve_scale(target, pelvis_pose, pelvis_rotation, pelvis_position)
        pelvis_pose.keyframe_insert("location", frame=frame, group=pelvis_pose.name)
        pelvis_pose.keyframe_insert("rotation_quaternion", frame=frame, group=pelvis_pose.name)
        bpy.context.view_layer.update()

        body_hips = (target.matrix_world @ pelvis_pose.matrix).translation
        body_chest_name = controls.get("spine2", controls.get("spine", controls["hips"]))
        body_chest = (target.matrix_world @ target.pose.bones[body_chest_name].matrix).translation
        body_neck = (target.matrix_world @ target.pose.bones[controls["head"]].matrix).translation
        body_capsules = (
            (body_hips, body_chest, 0.15 * height),
            (body_chest, body_neck, 0.12 * height),
        )

        frame_points = [pelvis_position.copy()]
        for suffix, semantic, side_sign in (("L", "left", 1.0), ("R", "right", -1.0)):
            shoulder = (target.matrix_world @ target.pose.bones[controls[f"{semantic}arm"]].matrix).translation
            hip = (target.matrix_world @ target.pose.bones[controls[f"{semantic}upleg"]].matrix).translation
            arm_scale = target_lengths[f"arm_{suffix}"] / source_lengths[f"arm_{suffix}"]
            leg_scale = target_lengths[f"leg_{suffix}"] / source_lengths[f"leg_{suffix}"]
            source_hand = (
                vector_from(joints[f"{semantic}hand"]) - vector_from(joints[f"{semantic}arm"])
                - vector_from(source_rest[f"{semantic}hand"]) + vector_from(source_rest[f"{semantic}arm"])
            )
            source_elbow = (
                vector_from(joints[f"{semantic}forearm"]) - vector_from(joints[f"{semantic}arm"])
                - vector_from(source_rest[f"{semantic}forearm"]) + vector_from(source_rest[f"{semantic}arm"])
            )
            source_foot = (
                vector_from(joints[f"{semantic}foot"]) - vector_from(joints[f"{semantic}upleg"])
                - vector_from(source_rest[f"{semantic}foot"]) + vector_from(source_rest[f"{semantic}upleg"])
            )
            source_knee = (
                vector_from(joints[f"{semantic}leg"]) - vector_from(joints[f"{semantic}upleg"])
                - vector_from(source_rest[f"{semantic}leg"]) + vector_from(source_rest[f"{semantic}upleg"])
            )
            body_delta = pelvis_rotation @ pelvis_rest.to_quaternion().inverted()
            rest_hand = shoulder + body_delta @ (
                world_rest[f"{semantic}hand"].translation - world_rest[f"{semantic}arm"].translation
            )
            rest_elbow = shoulder + body_delta @ (
                world_rest[f"{semantic}forearm"].translation - world_rest[f"{semantic}arm"].translation
            )
            rest_foot = hip + body_delta @ (
                world_rest[f"{semantic}foot"].translation - world_rest[f"{semantic}upleg"].translation
            )
            rest_knee = hip + body_delta @ (
                world_rest[f"{semantic}leg"].translation - world_rest[f"{semantic}upleg"].translation
            )
            asymmetry = 1.0 + side_sign * profile["gesture_asymmetry"]
            hand = clamp_chain_target(
                shoulder, rest_hand + mapped(
                    source_hand, height * profile["arm_motion_gain"] * asymmetry
                ),
                target_lengths[f"arm_{suffix}"] * 0.985, forward + anatomical_right * side_sign,
            )
            for start, end, radius in body_capsules:
                corrected, amount = helper.push_outside_capsule(
                    hand, start, end,
                    max(radius, profile["body_clearance_ratio"] * height),
                    forward + anatomical_right * side_sign,
                )
                hand = corrected
                collision_correction = max(collision_correction, amount / height)
            elbow_landmark = rest_elbow + mapped(
                source_elbow, height * profile["arm_motion_gain"] * asymmetry
            )
            elbow = pole_from_landmark(
                shoulder, hand, elbow_landmark,
                forward + anatomical_right * (-side_sign) * 0.25, 0.28 * height,
            )
            foot = clamp_chain_target(
                hip, rest_foot + mapped(
                    source_foot, height * profile["leg_motion_gain"]
                ),
                target_lengths[f"leg_{suffix}"] * 0.99, Vector((0.0, 0.0, -1.0)),
            )
            foot.z = max(foot.z, low)
            if contact[semantic]:
                if contact_anchors[semantic] is None:
                    contact_anchors[semantic] = foot.copy()
                foot = contact_anchors[semantic].copy()
            else:
                contact_anchors[semantic] = None
            knee_landmark = rest_knee + mapped(
                source_knee, height * profile["leg_motion_gain"]
            )
            knee = pole_from_landmark(
                hip, foot, knee_landmark, forward + anatomical_right * side_sign * 0.08,
                0.34 * height,
            )
            desired = {"hand": hand, "elbow": elbow, "foot": foot, "knee": knee}
            for name, position in desired.items():
                key = f"{suffix}:{name}"
                if key in previous_targets:
                    maximum_step = (0.055 if name in {"hand", "foot"} else 0.075) * height
                    delta = position - previous_targets[key]
                    if delta.length > maximum_step:
                        position = previous_targets[key] + delta.normalized() * maximum_step
                        desired[name] = position
                previous_targets[key] = position.copy()
                obj = controls_ik[suffix][name]
                obj.location = position
                obj.keyframe_insert("location", frame=frame)
            bpy.context.view_layer.update()

            actual_elbow = (target.matrix_world @ target.pose.bones[controls[f"{semantic}forearm"]].matrix).translation
            actual_hand = target.matrix_world @ target.pose.bones[controls[f"{semantic}forearm"]].tail
            actual_knee = (target.matrix_world @ target.pose.bones[controls[f"{semantic}leg"]].matrix).translation
            actual_foot = target.matrix_world @ target.pose.bones[controls[f"{semantic}leg"]].tail
            max_endpoint_error = max(
                max_endpoint_error, (actual_hand - desired["hand"]).length / height,
                (actual_foot - desired["foot"]).length / height,
            )
            arm_length = (shoulder - actual_elbow).length + (actual_elbow - actual_hand).length
            leg_length = (hip - actual_knee).length + (actual_knee - actual_foot).length
            max_limb_ratio_error = max(
                max_limb_ratio_error,
                abs(arm_length / target_lengths[f"arm_{suffix}"] - 1.0),
                abs(leg_length / target_lengths[f"leg_{suffix}"] - 1.0),
            )
            frame_points.extend((actual_hand.copy(), actual_foot.copy()))
        trajectories.append(frame_points)

    flattened = [[coordinate for point in row for coordinate in point] for row in trajectories]
    base = flattened[0]
    amplitude = max(
        math.sqrt(sum((value - start) ** 2 for value, start in zip(row, base)))
        for row in flattened
    ) / height
    velocities = [
        math.sqrt(sum((b - a) ** 2 for a, b in zip(first, second))) / height
        for first, second in zip(flattened, flattened[1:])
    ]
    accelerations = [abs(second - first) for first, second in zip(velocities, velocities[1:])]
    max_velocity = max(velocities, default=0.0)
    max_acceleration = max(accelerations, default=0.0)
    if amplitude < 0.05:
        raise RuntimeError(f"generic IK action is nearly static: {amplitude:.4f}H")
    if max_velocity > 0.12 or max_acceleration > 0.07:
        raise RuntimeError(
            f"generic IK trajectory is discontinuous: velocity={max_velocity:.4f}H acceleration={max_acceleration:.4f}H"
        )
    if (
        (max_endpoint_error > 0.055 or max_limb_ratio_error > 0.035)
        and not ACTION_PARAMS.get("debug_relax_generic_ik_qc")
    ):
        raise RuntimeError(
            "generic IK solve violates endpoint or bone-length limits: "
            f"endpoint={max_endpoint_error:.4f}H limb_ratio={max_limb_ratio_error:.4f}"
        )
    return {
        "recipe": "observation_driven", "adapter": "generic_native_two_bone_ik_v4",
        "character_height": height, "target_native_solver": "landmark_two_bone_ik_v4",
        "source_bone_rotations_used": False,
        "observation_segment_id": data["segment_id"],
        "observation_fingerprint": data["trajectory_fingerprint"],
        "generic_ik_endpoint_error_normalized": max_endpoint_error,
        "generic_ik_limb_length_ratio_error": max_limb_ratio_error,
        "generic_collision_correction_normalized": collision_correction,
        "character_motion_profile": profile,
        "authored_trajectory_qc": {
            "status": "passed", "amplitude_normalized": amplitude,
            "max_frame_velocity_normalized": max_velocity,
            "max_frame_acceleration_normalized": max_acceleration,
            "terminal_policy": "preserve_source_motion_without_forced_stop",
            "source_contact_locking": True,
        },
        **timeline_report,
    }


def main():
    global ACTION_PARAMS, EXPANDED_PRESET_PATH
    args = parse_args()
    job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    verify_human_job(job)
    from asset_source import verify_job_sources
    source_job = dict(job)
    if not source_job.get('background_path'):
        params = job.get('action_params') or json.loads(job.get('action_params_json') or '{}')
        source_job['background_path'] = (params.get('scene_pairing') or {}).get('background_path', '')
    job['dataset_source_refs'] = verify_job_sources(source_job, DATA_ROOT)
    from production_quality.task_preflight import reject_bypasses, VERSION
    reject_bypasses(job)
    expected = job_settings()
    for key, value in expected.items():
        if job.get(key) != value:
            raise RuntimeError(f'Job does not match current production setting: {key}')
    width = int(job["resolution_x"])
    height = int(job["resolution_y"])
    if (width, height) != (1920, 1080):
        raise RuntimeError("production output must be 1920x1080")
    helper = load_helper()
    ACTION_PARAMS = job.get("action_params") or json.loads(job.get("action_params_json", "{}"))
    catalog = (ACTION_PARAMS.get('expanded_blueprint') or {}).get('catalog_version')
    if catalog == 'expanded-safe-presets-v4-expanded':
        EXPANDED_PRESET_PATH = str(DATA_ROOT / 'tools/expanded_motion_presets_v4_expanded.py')
    camera_motion = job.get("background_camera_motion", {})
    bpy.context.scene["rm_bg_pan_x"] = float(camera_motion.get("pan_x_normalized", 0.0))
    bpy.context.scene["rm_bg_pan_y"] = float(camera_motion.get("pan_y_normalized", 0.0))
    bpy.context.scene["rm_bg_zoom"] = float(camera_motion.get("zoom_normalized", 0.0))
    bpy.context.scene["rm_camera_follow_gain"] = float(ACTION_PARAMS.get("camera_follow_gain", 0.8))
    bpy.context.scene['rm_subject_tracking_gain'] = float(ACTION_PARAMS.get('subject_tracking_gain', 0.0))
    bpy.context.scene['rm_bounds_frame_end'] = int(job['frame_count'])
    bpy.context.scene['rm_subdivision_cap'] = int(job['subdivision_cap'])
    bpy.context.scene['rm_scene_human_height_fraction_max'] = float(
        ACTION_PARAMS.get('preview_human_height_fraction_max', 0.68)
    )
    scene_placement = (ACTION_PARAMS.get("scene_pairing") or {}).get("placement") or {}
    placement_anchor = scene_placement.get("anchor") or [0.5, 0.9]
    bpy.context.scene["rm_scene_anchor_x"] = float(placement_anchor[0])
    bpy.context.scene["rm_scene_anchor_y"] = float(placement_anchor[1])
    bpy.context.scene["rm_scene_human_height_fraction"] = float(
        scene_placement.get("human_height_fraction", 0.0)
    )
    bpy.context.scene["rm_scene_lighting_direction"] = str(
        scene_placement.get("lighting_direction") or "unknown"
    )
    bpy.context.scene["rm_scene_lighting_temperature"] = str(
        scene_placement.get("lighting_temperature") or "neutral"
    )
    bpy.context.scene["rm_force_cycles_cpu"] = bool(job.get("debug_force_cycles_cpu", False))
    bpy.context.scene["rm_cycles_sample_cap"] = int(job.get("cycles_sample_cap", 12))
    bpy.context.scene["rm_cycles_denoising"] = bool(job.get("cycles_denoising", True))
    bpy.context.scene["rm_pixel_filter_width"] = float(job.get("pixel_filter_width", 1.0))
    target = helper.target_armature([obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"])
    hair_variant_report = align_hair_variant_visibility(helper, target)
    meshes = character_render_meshes(helper, target)
    texture_report = helper.repair_character_textures(meshes)
    alpha_report = normalize_matting_alpha(meshes)
    edge_report = neutralize_stylized_edges(meshes)
    detail_report = apply_quality(meshes, job['quality_options'])
    configure_render(helper, job, detail_report, args.prepass_only)
    frame_count = int(job["frame_count"])
    timing = [index / max(1, frame_count - 1) for index in range(frame_count)]
    timing_report = {
        "reference_activity_total_deg": None,
        "reference_timing_min_step": 1.0 / max(1, frame_count - 1),
        "reference_timing_max_step": 1.0 / max(1, frame_count - 1),
    }
    rigify_required = {"torso", "head", "foot_ik.L", "foot_ik.R", "hand_ik.L", "hand_ik.R"}
    missing_controls = sorted(rigify_required.difference(target.pose.bones.keys()))
    design_status = ACTION_PARAMS.get("design_status")
    if design_status == "subject_action_blueprint_v4_assigned_jit" and not missing_controls:
        motion_report = generate_observation_motion(helper, target, frame_count, meshes)
    elif design_status == "subject_action_blueprint_v4_assigned_jit":
        raise RuntimeError(
            "subject action blueprint v4 requires a validated target-native IK adapter; "
            "unsafe generic deform fallback is disabled"
        )
    elif design_status == "expanded_safe_preset_v1_assigned_jit" and not missing_controls:
        motion_report = generate_motion(helper, target, timing, job["recipe"], meshes)
    elif design_status == "expanded_safe_preset_v1_assigned_jit":
        raise RuntimeError(
            "expanded safe preset requires a validated target-native IK adapter; "
            "unsafe generic deform fallback is disabled"
        )
    elif design_status == "character_adaptive_motion_v1_assigned_jit" and not missing_controls:
        motion_report = generate_observation_motion(helper, target, frame_count, meshes)
    elif design_status == "character_adaptive_motion_v1_assigned_jit":
        motion_report = generate_observation_generic_ik_motion(target, frame_count, helper, meshes)
    else:
        motion_report = generate_motion(helper, target, timing, job["recipe"], meshes)
    hair_report = add_hair_secondary_motion(target, meshes, frame_count, job["recipe"])
    garment_report_path = Path(job['prepass_report_path'] if args.prepass_only else job['render_report_path']).with_suffix('.garment.json')
    garment_report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        garment_report = run_guard(target, meshes, helper.semantic_bone_map(target), frame_count)
    except Exception as error:
        garment_report_path.write_text(json.dumps({'status': 'needs_review', 'error': str(error)}, indent=2))
        raise RuntimeError('GARMENT_QC: garment evaluation failed') from error
    garment_report_path.write_text(json.dumps(garment_report, indent=2))
    validate_garment_report(garment_report, frame_count)
    set_linear_interpolation(target)
    motion_report.setdefault("adapter", "target_native_rigify_ik_v2_safe")
    frames = list(range(1, len(timing) + 1))
    requested_render_frames = job.get("render_frames")
    production_render_frames = frames
    if requested_render_frames:
        production_render_frames = sorted({
            int(frame) for frame in requested_render_frames if int(frame) in frames
        })
        if not production_render_frames:
            raise RuntimeError("render_frames did not select a valid authored frame")
    collision_frames = sorted(set(frames[::max(1, len(frames) // 30)] + [frames[-1]]))
    collision = helper.mesh_collision_qc(meshes, collision_frames)
    if collision["status"] == "failed":
        raise RuntimeError(f"dense collision QC failed: {collision}")
    geometry_qc = geometry_motion_qc(
        helper, target, meshes, frames, motion_report["character_height"]
    )
    limb_qc_required = bool(ACTION_PARAMS.get("expanded_blueprint")) or (
        design_status == "subject_action_blueprint_v4_assigned_jit"
    )
    limb_qc = (
        limb_deformation_qc(target, frames, motion_report["character_height"])
        if limb_qc_required else {"status": "not_applicable"}
    )
    collision_gate = (
        {"status": "passed", "method": "dense_collision"}
        if collision["status"] == "passed"
        else {"status": "passed", "method": "geometry_fallback", "dense_result": collision}
    )

    if args.prepass_only:
        # Render the complete low-resolution sequence. Sparse keyframes missed
        # transient limb inversion, contact sliding, hair loss, and framing exits.
        render_frames = frames
        render_report = helper.setup_render(
            meshes, render_frames, Path(job["prepass_dir"]), 640, True,
            job["framing"], float(job["camera_angle_deg"]), 1, "CYCLES",
        )
        report_path = Path(job["prepass_report_path"])
        status = "passed_prepass"
    else:
        render_resolution = width
        render_report = helper.setup_render(
            meshes, production_render_frames, Path(job["rgba_work_dir"]), render_resolution, True,
            job["framing"], float(job["camera_angle_deg"]), int(job["samples"]), "CYCLES",
        )
        report_path = Path(job["render_report_path"])
        status = "rendered_rgba"
    report = {
        "status": status,
        "dataset_source_refs": job["dataset_source_refs"],
        "motion_usage": ACTION_PARAMS.get(
            "motion_reference_policy",
            "source_phase_inspiration_asset_specific_trajectory_reconstruction",
        ),
        "subject_id": job["subject_id"], "motion_segment_id": job["motion_segment_id"],
        "action_design_id": job["action_design_id"], "action_params": ACTION_PARAMS,
        "reference_motion": job["motion_path"], "source_animation_imported": False,
        "frame_count": len(frames), "texture_repairs": texture_report,
        "rendered_frame_subset": frames if args.prepass_only else production_render_frames,
        "task_preflight_version": VERSION,
        "task_preflight_attempt": job.get("task_preflight_attempt"),
        "task_preflight_input_fingerprint": job.get("task_preflight_input_fingerprint"),
        "matting_alpha": alpha_report,
        "neutralized_stylized_edges": edge_report,
        "collision_qc": collision, "collision_gate": collision_gate,
        "geometry_motion_qc": geometry_qc, "limb_deformation_qc": limb_qc,
        "garment_qc": garment_report,
        "visible_character_meshes": sorted(obj.name for obj in meshes),
        "hair_variant_visibility": hair_variant_report,
        "hair_motion_recipe": (
            job["recipe"] in {"hair_turn", "hair_bend", "hair_shake", "dance_sway"}
            or bool(ACTION_PARAMS.get("expanded_blueprint"))
            or design_status == "subject_action_blueprint_v4_assigned_jit"
        ),
        **hair_report, **timing_report, **motion_report, **render_report,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
