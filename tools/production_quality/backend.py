import argparse
import json
import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree


CORE_BONES = {
    "hips", "spine", "spine1", "spine2", "neck", "head",
    "leftshoulder", "leftarm", "leftforearm", "lefthand",
    "rightshoulder", "rightarm", "rightforearm", "righthand",
    "leftupleg", "leftleg", "leftfoot", "lefttoebase",
    "rightupleg", "rightleg", "rightfoot", "righttoebase",
}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job")
    parser.add_argument("--motion")
    parser.add_argument("--output")
    parser.add_argument("--start", type=float, default=1.0)
    parser.add_argument("--frames", type=int, default=90)
    parser.add_argument("--resolution", type=int, default=960)
    parser.add_argument("--render-all", action="store_true")
    parser.add_argument("--framing", default="fullbody")
    parser.add_argument("--camera-angle", type=float, default=0.0)
    parser.add_argument("--samples", type=int, default=16)
    parser.add_argument("--engine", choices=("AUTO", "WORKBENCH", "EEVEE", "CYCLES"), default="AUTO")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    return parser.parse_args(argv)


def clean_name(name):
    value = name.split(":")[-1].lower()
    for token in (" ", "_", "-", "."):
        value = value.replace(token, "")
    aliases = {
        "pelvis": "hips", "root": "hips",
        "upperarml": "leftarm", "lowerarml": "leftforearm", "handl": "lefthand",
        "upperarmr": "rightarm", "lowerarmr": "rightforearm", "handr": "righthand",
        "thighl": "leftupleg", "calfl": "leftleg", "footl": "leftfoot",
        "thighr": "rightupleg", "calfr": "rightleg", "footr": "rightfoot",
    }
    return aliases.get(value, value)


def semantic_name(name):
    raw = name.split(":")[-1].lower().replace("-", "_")
    compact = clean_name(name)
    if compact in CORE_BONES:
        return compact
    rigify = {
        "root.x": "hips", "spine_01.x": "spine", "spine_02.x": "spine1",
        "spine_03.x": "spine2", "neck.x": "neck", "head.x": "head",
        "shoulder.l": "leftshoulder", "arm.l": "leftarm",
        "forearm.l": "leftforearm", "hand.l": "lefthand",
        "thigh.l": "leftupleg", "leg.l": "leftleg", "foot.l": "leftfoot",
        "toes_01.l": "lefttoebase",
        "shoulder.r": "rightshoulder", "arm.r": "rightarm",
        "forearm.r": "rightforearm", "hand.r": "righthand",
        "thigh.r": "rightupleg", "leg.r": "rightleg", "foot.r": "rightfoot",
        "toes_01.r": "righttoebase",
    }
    if raw in rigify:
        return rigify[raw]
    unreal = {
        "pelvis": "hips", "spine_01": "spine", "spine_02": "spine1",
        "spine_03": "spine2", "neck_01": "neck", "head": "head",
        "clavicle_l": "leftshoulder", "upperarm_l": "leftarm",
        "lowerarm_l": "leftforearm", "hand_l": "lefthand",
        "thigh_l": "leftupleg", "calf_l": "leftleg", "foot_l": "leftfoot",
        "ball_l": "lefttoebase",
        "clavicle_r": "rightshoulder", "upperarm_r": "rightarm",
        "lowerarm_r": "rightforearm", "hand_r": "righthand",
        "thigh_r": "rightupleg", "calf_r": "rightleg", "foot_r": "rightfoot",
        "ball_r": "righttoebase",
    }
    if raw in unreal:
        return unreal[raw]
    deform_rigify = {
        "def_spine": "hips", "def_spine.001": "spine", "def_spine.002": "spine1",
        "def_spine.003": "spine2", "def_spine.006": "neck", "def_spine.007": "head",
        "def_shoulder.l": "leftshoulder", "def_upper_arm.l": "leftarm",
        "def_forearm.l": "leftforearm", "def_hand.l": "lefthand",
        "def_thigh.l": "leftupleg", "def_shin.l": "leftleg",
        "def_foot.l": "leftfoot", "def_toe.l": "lefttoebase",
        "def_shoulder.r": "rightshoulder", "def_upper_arm.r": "rightarm",
        "def_forearm.r": "rightforearm", "def_hand.r": "righthand",
        "def_thigh.r": "rightupleg", "def_shin.r": "rightleg",
        "def_foot.r": "rightfoot", "def_toe.r": "righttoebase",
    }
    return deform_rigify.get(raw)


def semantic_bone_map(armature):
    candidates = {}
    for bone in armature.data.bones:
        semantic = semantic_name(bone.name)
        if not semantic:
            continue
        raw = bone.name.lower()
        penalty = sum(token in raw for token in ("_ik", "_fk", "twist", "pole", "stretch", "track"))
        penalty += 2 if raw.startswith("c_") else 0
        score = (bone.use_deform, -penalty, -len(raw))
        if semantic not in candidates or score > candidates[semantic][0]:
            candidates[semantic] = (score, bone.name)
    return {semantic: item[1] for semantic, item in candidates.items()}


def target_armature(initial_armatures):
    scored = []
    for arm in initial_armatures:
        bound = 0
        for obj in bpy.context.scene.objects:
            if obj.type != "MESH":
                continue
            if obj.parent == arm:
                bound += 1
            bound += sum(1 for mod in obj.modifiers if mod.type == "ARMATURE" and mod.object == arm)
        named = len(semantic_bone_map(arm))
        scored.append((bound, named, len(arm.data.bones), arm))
    if not scored:
        raise RuntimeError("no target armature")
    return max(scored, key=lambda item: item[:3])[-1]


def source_armature(imported_names):
    candidates = [
        obj for obj in bpy.context.scene.objects
        if obj.type == "ARMATURE" and obj.name in imported_names
    ]
    if not candidates:
        raise RuntimeError("motion FBX has no armature")
    return max(candidates, key=lambda arm: (
        bool(arm.animation_data and arm.animation_data.action),
        len(arm.data.bones),
    ))


def character_meshes(armature):
    bound_meshes = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or obj.name.lower().startswith("cs_"):
            continue
        bound = obj.parent == armature or any(
            mod.type == "ARMATURE" and mod.object == armature for mod in obj.modifiers
        )
        if bound:
            bound_meshes.append(obj)
    if not bound_meshes:
        raise RuntimeError("target armature has no bound meshes")

    # Outfit variants are commonly kept in disabled collections while their
    # object-level hide_render flag remains false. Only the active view-layer
    # combination may participate in bounds, collision QC, or rendering.
    result = [obj for obj in bound_meshes if obj.visible_get() and not obj.hide_render]
    if not result:
        raise RuntimeError("target armature has no visible bound meshes")
    return result


def repair_character_textures(meshes):
    images = set()
    seen_trees = set()

    def collect(tree):
        if tree in seen_trees:
            return
        seen_trees.add(tree)
        for node in tree.nodes:
            image = getattr(node, "image", None)
            if image:
                images.add(image)
            child = getattr(node, "node_tree", None)
            if child:
                collect(child)

    for obj in meshes:
        for slot in obj.material_slots:
            material = slot.material
            if not material or not material.use_nodes or not material.node_tree:
                continue
            collect(material.node_tree)
    asset_dir = Path(bpy.data.filepath).parent
    repaired = []
    unresolved = []
    local_files = [path for path in asset_dir.rglob("*") if path.is_file()]
    files_by_lower_name = {}
    for path in local_files:
        files_by_lower_name.setdefault(path.name.lower(), []).append(path)
    for image in images:
        if image.source != "FILE":
            continue
        if image.packed_file:
            if min(image.size) <= 0:
                unresolved.append({"image": image.name, "reason": "empty packed texture"})
            continue
        if not image.filepath:
            unresolved.append({"image": image.name, "reason": "empty image path"})
            continue
        resolved = Path(bpy.path.abspath(image.filepath))
        if resolved.is_file():
            if min(image.size) <= 0:
                try:
                    image.reload()
                except RuntimeError as error:
                    unresolved.append({"image": image.name, "reason": str(error)})
                if min(image.size) <= 0:
                    unresolved.append({"image": image.name, "reason": "unloaded image"})
            continue
        requested_name = Path(image.filepath).name
        candidates = list(asset_dir.rglob(requested_name))
        if not candidates:
            candidates = files_by_lower_name.get(requested_name.lower(), [])
        if candidates:
            if len(candidates) > 1:
                unresolved.append({"image": image.name, "reason": "ambiguous texture paths",
                                   "candidates": [str(p) for p in candidates]})
                continue
            candidate = sorted(candidates, key=lambda path: (len(path.parts), len(str(path))))[0]
            image.filepath = str(candidate)
            try:
                image.reload()
            except RuntimeError as error:
                unresolved.append({"image": image.name, "reason": str(error)})
                continue
            if min(image.size) <= 0:
                unresolved.append({"image": image.name, "reason": "empty reloaded image"})
                continue
            repaired.append({"image": image.name, "path": str(candidate)})
        else:
            unresolved.append({"image": image.name, "requested_path": image.filepath})
    if unresolved:
        raise RuntimeError(f"unresolved character textures: {unresolved[:8]}")
    return repaired


def capture_motion(source, source_frames, mapped_source_names):
    samples = []
    for frame in source_frames:
        whole = math.floor(frame)
        bpy.context.scene.frame_set(whole, subframe=frame - whole)
        bpy.context.view_layer.update()
        samples.append({
            name: {
                "world": source.matrix_world @ source.pose.bones[name].matrix.copy(),
                "basis_rotation": source.pose.bones[name].matrix_basis.to_quaternion(),
            }
            for name in mapped_source_names
        })
    return samples


def world_rest(armature, bone_name):
    return armature.matrix_world @ armature.data.bones[bone_name].matrix_local


def rigify_fk_adapter(armature):
    names = set(armature.data.bones.keys())
    required = {
        "torso", "spine_fk", "spine_fk.001", "spine_fk.002", "spine_fk.003", "spine_fk.004",
        "upper_arm_fk.L", "forearm_fk.L", "hand_fk.L",
        "upper_arm_fk.R", "forearm_fk.R", "hand_fk.R",
        "thigh_fk.L", "shin_fk.L", "foot_fk.L",
        "thigh_fk.R", "shin_fk.R", "foot_fk.R",
    }
    if not required.issubset(names):
        return None
    controls = {
        "hips": "torso",
        "spine": "spine_fk", "spine1": "spine_fk.002", "spine2": "spine_fk.004",
        "neck": "neck", "head": "head",
        "leftshoulder": "shoulder.L", "leftarm": "upper_arm_fk.L",
        "leftforearm": "forearm_fk.L", "lefthand": "hand_fk.L",
        "rightshoulder": "shoulder.R", "rightarm": "upper_arm_fk.R",
        "rightforearm": "forearm_fk.R", "righthand": "hand_fk.R",
        "leftupleg": "thigh_fk.L", "leftleg": "shin_fk.L", "leftfoot": "foot_fk.L",
        "rightupleg": "thigh_fk.R", "rightleg": "shin_fk.R", "rightfoot": "foot_fk.R",
    }
    optional = {
        "lefttoebase": "toe_fk.L" if "toe_fk.L" in names else "toe.L",
        "righttoebase": "toe_fk.R" if "toe_fk.R" in names else "toe.R",
    }
    controls.update({key: value for key, value in optional.items() if value in names})
    outputs = {
        "hips": "ORG-spine", "spine": "ORG-spine.001", "spine1": "ORG-spine.003",
        "spine2": "ORG-spine.004", "neck": "ORG-spine.006", "head": "ORG-spine.007",
        "leftshoulder": "ORG-shoulder.L", "leftarm": "ORG-upper_arm.L",
        "leftforearm": "ORG-forearm.L", "lefthand": "ORG-hand.L",
        "rightshoulder": "ORG-shoulder.R", "rightarm": "ORG-upper_arm.R",
        "rightforearm": "ORG-forearm.R", "righthand": "ORG-hand.R",
        "leftupleg": "ORG-thigh.L", "leftleg": "ORG-shin.L", "leftfoot": "ORG-foot.L",
        "rightupleg": "ORG-thigh.R", "rightleg": "ORG-shin.R", "rightfoot": "ORG-foot.R",
        "lefttoebase": "ORG-toe.L", "righttoebase": "ORG-toe.R",
    }
    outputs = {key: value for key, value in outputs.items() if value in names}
    return {
        "type": "rigify_fk_controls_v6_full_fk",
        "controls": controls,
        "outputs": outputs,
        "spine_controls": [f"spine_fk{suffix}" for suffix in ("", ".001", ".002", ".003", ".004")],
        "root_control": "root" if "root" in names else "torso",
        "extra_chains": {},
        "ik_end_controls": {},
        "ik_pole_controls": {},
        "spine_source_semantics": ["spine", "spine1", "spine2"],
    }


def rigify_complete_deform_adapter(armature):
    names = set(armature.data.bones.keys())
    required = {
        "DEF-spine", "DEF-spine.001", "DEF-spine.002", "DEF-spine.003",
        "DEF-spine.004", "DEF-spine.005", "DEF-spine.006", "DEF-spine.007",
        "DEF-upper_arm.L", "DEF-upper_arm.L.001", "DEF-forearm.L", "DEF-forearm.L.001",
        "DEF-upper_arm.R", "DEF-upper_arm.R.001", "DEF-forearm.R", "DEF-forearm.R.001",
        "DEF-thigh.L", "DEF-thigh.L.001", "DEF-shin.L", "DEF-shin.L.001",
        "DEF-thigh.R", "DEF-thigh.R.001", "DEF-shin.R", "DEF-shin.R.001",
    }
    if not required.issubset(names):
        return None
    controls = {
        "hips": "DEF-spine", "spine": "DEF-spine.001", "spine1": "DEF-spine.003",
        "spine2": "DEF-spine.004", "neck": "DEF-spine.006", "head": "DEF-spine.007",
        "leftshoulder": "DEF-shoulder.L", "leftarm": "DEF-upper_arm.L",
        "leftforearm": "DEF-forearm.L", "lefthand": "DEF-hand.L",
        "rightshoulder": "DEF-shoulder.R", "rightarm": "DEF-upper_arm.R",
        "rightforearm": "DEF-forearm.R", "righthand": "DEF-hand.R",
        "leftupleg": "DEF-thigh.L", "leftleg": "DEF-shin.L", "leftfoot": "DEF-foot.L",
        "rightupleg": "DEF-thigh.R", "rightleg": "DEF-shin.R", "rightfoot": "DEF-foot.R",
    }
    optional = {"lefttoebase": "DEF-toe.L", "righttoebase": "DEF-toe.R"}
    controls.update({key: value for key, value in optional.items() if value in names})
    extra_chains = {
        "leftarm": ["DEF-upper_arm.L.001"], "leftforearm": ["DEF-forearm.L.001"],
        "rightarm": ["DEF-upper_arm.R.001"], "rightforearm": ["DEF-forearm.R.001"],
        "leftupleg": ["DEF-thigh.L.001"], "leftleg": ["DEF-shin.L.001"],
        "rightupleg": ["DEF-thigh.R.001"], "rightleg": ["DEF-shin.R.001"],
    }
    return {
        "type": "rigify_complete_deform_world_v7",
        "controls": controls,
        "outputs": dict(controls),
        "spine_controls": [f"DEF-spine{suffix}" for suffix in ("", ".001", ".002", ".003", ".004", ".005", ".006", ".007")],
        "spine_source_semantics": ["hips", "spine", "spine1", "spine2", "neck", "head"],
        "root_control": "DEF-spine",
        "extra_chains": extra_chains,
        "ik_end_controls": {},
        "ik_pole_controls": {},
    }


def make_adapter(armature):
    # Prefer animator-facing FK controls whenever they exist. They preserve
    # Rigify's twist distribution, stretch limits and deformation constraints.
    # Direct DEF driving is only a fallback for rigs without usable controls.
    adapter = rigify_fk_adapter(armature)
    if adapter:
        return adapter
    adapter = rigify_complete_deform_adapter(armature)
    if adapter:
        return adapter
    direct = semantic_bone_map(armature)
    if len(CORE_BONES & direct.keys()) < 18:
        raise RuntimeError("unsupported target rig: no complete FK/control or deform skeleton adapter")
    return {
        "type": "direct_deform_parent_rest_v5",
        "controls": direct,
        "outputs": direct,
        "spine_controls": [],
        "root_control": direct.get("hips"),
        "extra_chains": {},
        "ik_end_controls": {},
        "ik_pole_controls": {},
        "spine_source_semantics": ["spine", "spine1", "spine2"],
    }


def rotation_delta(pose_matrix, rest_matrix):
    return (pose_matrix.to_quaternion() @ rest_matrix.to_quaternion().inverted()).normalized()


def set_world_rotation(armature, pose_bone, world_rotation):
    current_world = armature.matrix_world @ pose_bone.matrix
    desired_world = world_rotation.to_matrix().to_4x4()
    desired_world.translation = current_world.translation
    pose_bone.matrix = armature.matrix_world.inverted() @ desired_world


def slerp_chain(quaternions, position):
    if len(quaternions) == 1:
        return quaternions[0].copy()
    scaled = max(0.0, min(1.0, position)) * (len(quaternions) - 1)
    low = min(int(math.floor(scaled)), len(quaternions) - 2)
    return quaternions[low].slerp(quaternions[low + 1], scaled - low).normalized()


def quaternion_error_deg(first, second):
    angle = first.rotation_difference(second).angle
    return math.degrees(min(angle, (2.0 * math.pi) - angle))


def hierarchy_depth(bone):
    depth = 0
    while bone.parent:
        depth += 1
        bone = bone.parent
    return depth


def force_rigify_limb_constraints(armature):
    forced = []
    for pose_bone in armature.pose.bones:
        constraints = list(pose_bone.constraints)
        has_fk = any("_fk" in getattr(constraint, "subtarget", "").lower() for constraint in constraints)
        has_ik = any("_ik" in getattr(constraint, "subtarget", "").lower() for constraint in constraints)
        if not (has_fk and has_ik):
            continue
        for constraint in constraints:
            subtarget = getattr(constraint, "subtarget", "").lower()
            if "_fk" not in subtarget and "_ik" not in subtarget:
                continue
            try:
                constraint.driver_remove("influence")
            except (TypeError, RuntimeError):
                pass
            constraint.mute = False
            constraint.influence = 1.0 if "_fk" in subtarget else 0.0
            forced.append({
                "bone": pose_bone.name,
                "constraint": constraint.name,
                "subtarget": getattr(constraint, "subtarget", ""),
                "influence": constraint.influence,
            })
    return forced


def set_world_transform(armature, pose_bone, world_rotation, world_translation):
    desired_world = world_rotation.to_matrix().to_4x4()
    desired_world.translation = world_translation
    pose_bone.matrix = armature.matrix_world.inverted() @ desired_world


def push_outside_capsule(point, start, end, radius, fallback_direction):
    axis = end - start
    denominator = axis.length_squared
    if denominator < 1e-10:
        closest = start
    else:
        position = max(0.0, min(1.0, (point - start).dot(axis) / denominator))
        closest = start + axis * position
    offset = point - closest
    distance = offset.length
    if distance >= radius:
        return point, 0.0
    direction = offset.normalized() if distance > 1e-6 else fallback_direction.normalized()
    correction = radius - distance
    return point + direction * correction, correction


def retarget(target, source, output_frames, source_samples):
    adapter = make_adapter(target)
    source_by_semantic = semantic_bone_map(source)
    shared = sorted(CORE_BONES & adapter["controls"].keys() & source_by_semantic.keys())
    if len(shared) < 18:
        raise RuntimeError(f"insufficient core bone mapping: {len(shared)} {shared}")

    target.animation_data_clear()
    for pose_bone in target.pose.bones:
        pose_bone.matrix_basis.identity()
    forced_fk_constraints = []
    if "deform" in adapter["type"]:
        driven = set(adapter["controls"].values()) | set(adapter["spine_controls"])
        for names in adapter["extra_chains"].values():
            driven.update(names)
        for target_name in driven:
            for constraint in target.pose.bones[target_name].constraints:
                constraint.mute = True
    else:
        for name in ("upper_arm_parent.L", "upper_arm_parent.R", "thigh_parent.L", "thigh_parent.R"):
            pose_bone = target.pose.bones.get(name)
            if pose_bone and "IK_FK" in pose_bone:
                pose_bone["IK_FK"] = 0.0
                pose_bone["IK_Stretch"] = 0.0
                pose_bone["pole_vector"] = True
        if adapter["type"].startswith("rigify_fk_controls"):
            forced_fk_constraints = force_rigify_limb_constraints(target)
            for pose_bone in target.pose.bones:
                for constraint in pose_bone.constraints:
                    if constraint.type == "IK" and any(
                        token in pose_bone.name.lower()
                        for token in ("shin_ik", "thigh_ik", "forearm_ik", "upper_arm_ik")
                    ):
                        constraint.use_stretch = False
                        constraint.mute = not bool(getattr(constraint, "pole_subtarget", ""))
    bpy.context.view_layer.update()

    source_rest = {semantic: world_rest(source, name) for semantic, name in source_by_semantic.items() if semantic in CORE_BONES}
    target_rest = {
        semantic: world_rest(target, name)
        for semantic, name in adapter["outputs"].items()
        if semantic in CORE_BONES
    }
    control_rest = {
        name: world_rest(target, name)
        for name in (
            set(adapter["controls"].values())
            | set(adapter["spine_controls"])
            | {bone for chain in adapter["extra_chains"].values() for bone in chain}
            | set(adapter["ik_end_controls"].values())
            | set(adapter["ik_pole_controls"].values())
        )
    }

    source_hips = source_by_semantic["hips"]
    first_root = source_samples[0][source_hips]["world"].translation.copy()
    target_height = max((target.matrix_world @ b.head_local).z for b in target.data.bones) - min(
        (target.matrix_world @ b.head_local).z for b in target.data.bones
    )
    source_height = max((source.matrix_world @ b.head_local).z for b in source.data.bones) - min(
        (source.matrix_world @ b.head_local).z for b in source.data.bones
    )
    root_scale = target_height / source_height if source_height > 1e-6 else 1.0

    previous_source = {}
    previous_target = {}
    max_jump_deg = 0.0
    motion_deg = []
    angular_errors = []
    endpoint_semantics = {
        "head", "lefthand", "righthand", "leftfoot", "rightfoot",
    }
    source_endpoint_origin = {}
    target_endpoint_origin = {}
    previous_source_endpoint = {}
    previous_target_endpoint = {}
    previous_source_foot_world = {}
    previous_target_foot_world = {}
    endpoint_trajectory_errors = []
    planted_foot_target_slips = []
    angular_errors_by_semantic = {semantic: [] for semantic in shared}
    endpoint_errors_by_semantic = {
        semantic: [] for semantic in endpoint_semantics & set(shared)
    }
    planted_foot_slips_by_semantic = {"leftfoot": [], "rightfoot": []}
    collision_corrections_by_semantic = {"lefthand": [], "righthand": []}
    ordered_semantics = sorted(shared, key=lambda key: hierarchy_depth(target.data.bones[adapter["controls"][key]]))
    for output_frame, sample in zip(output_frames, source_samples):
        bpy.context.scene.frame_set(output_frame)
        bpy.context.view_layer.update()

        root_name = adapter["root_control"]
        root_pose = target.pose.bones[root_name]
        displacement_world = (sample[source_hips]["world"].translation - first_root) * root_scale
        displacement_local = target.matrix_world.inverted().to_3x3() @ displacement_world
        root_pose.location = displacement_local
        root_pose.keyframe_insert("location", frame=output_frame, group=root_name)

        for semantic in ordered_semantics:
            if semantic in {"spine", "spine1", "spine2"} and adapter["spine_controls"]:
                continue
            target_name = adapter["controls"][semantic]
            source_name = source_by_semantic[semantic]
            delta = rotation_delta(sample[source_name]["world"], source_rest[semantic])
            desired = (delta @ control_rest[target_name].to_quaternion()).normalized()
            pose_bone = target.pose.bones[target_name]
            pose_bone.rotation_mode = "QUATERNION"
            set_world_rotation(target, pose_bone, desired)
            pose_bone.keyframe_insert("rotation_quaternion", frame=output_frame, group=target_name)
            pose_bone.keyframe_insert("location", frame=output_frame, group=target_name)
            pose_bone.keyframe_insert("scale", frame=output_frame, group=target_name)
            bpy.context.view_layer.update()

            # Rigify may split one anatomical segment across serial DEF bones
            # (for example DEF-thigh.L and DEF-thigh.L.001). Applying the full
            # source delta to every segment compounds the rotation and twists
            # the skinned limb. Drive only the first segment; passive segments
            # keep their rest-local transform and inherit the parent motion.

        if adapter["spine_controls"]:
            spine_deltas = [
                rotation_delta(sample[source_by_semantic[key]]["world"], source_rest[key])
                for key in adapter["spine_source_semantics"]
            ]
            count = len(adapter["spine_controls"])
            for index, target_name in enumerate(adapter["spine_controls"]):
                delta = slerp_chain(spine_deltas, index / max(1, count - 1))
                desired = (delta @ control_rest[target_name].to_quaternion()).normalized()
                pose_bone = target.pose.bones[target_name]
                pose_bone.rotation_mode = "QUATERNION"
                set_world_rotation(target, pose_bone, desired)
                pose_bone.keyframe_insert("rotation_quaternion", frame=output_frame, group=target_name)
                pose_bone.keyframe_insert("location", frame=output_frame, group=target_name)
                pose_bone.keyframe_insert("scale", frame=output_frame, group=target_name)
                bpy.context.view_layer.update()

        for semantic, target_name in adapter["ik_end_controls"].items():
            if semantic not in source_by_semantic or target_name not in target.pose.bones:
                continue
            source_name = source_by_semantic[semantic]
            source_pose = sample[source_name]["world"]
            source_delta = rotation_delta(source_pose, source_rest[semantic])
            target_control_rest = control_rest[target_name]
            desired_rotation = (
                source_delta @ target_control_rest.to_quaternion()
            ).normalized()
            desired_translation = (
                target_control_rest.translation
                + (source_pose.translation - source_rest[semantic].translation) * root_scale
            )
            pose_bone = target.pose.bones[target_name]
            pose_bone.rotation_mode = "QUATERNION"
            set_world_transform(target, pose_bone, desired_rotation, desired_translation)
            pose_bone.keyframe_insert("rotation_quaternion", frame=output_frame, group=target_name)
            pose_bone.keyframe_insert("location", frame=output_frame, group=target_name)
            pose_bone.keyframe_insert("scale", frame=output_frame, group=target_name)
            bpy.context.view_layer.update()

            pole_name = adapter["ik_pole_controls"].get(semantic)
            chain_semantics = {
                "lefthand": ("leftarm", "leftforearm"),
                "righthand": ("rightarm", "rightforearm"),
                "leftfoot": ("leftupleg", "leftleg"),
                "rightfoot": ("rightupleg", "rightleg"),
            }
            if pole_name and pole_name in target.pose.bones and semantic in chain_semantics:
                upper_semantic, lower_semantic = chain_semantics[semantic]
                fk_hip = (
                    target.matrix_world @ target.pose.bones[adapter["controls"][upper_semantic]].matrix
                ).translation
                fk_knee = (
                    target.matrix_world @ target.pose.bones[adapter["controls"][lower_semantic]].matrix
                ).translation
                fk_ankle = (
                    target.matrix_world @ target.pose.bones[adapter["controls"][semantic]].matrix
                ).translation
                leg_axis = fk_ankle - fk_hip
                axis_length_squared = max(leg_axis.length_squared, 1e-8)
                projected = fk_hip + leg_axis * (
                    (fk_knee - fk_hip).dot(leg_axis) / axis_length_squared
                )
                pole_direction = fk_knee - projected
                if pole_direction.length < 1e-5:
                    pole_direction = Vector((0.0, -1.0, 0.0))
                else:
                    pole_direction.normalize()
                pole_translation = fk_knee + pole_direction * (0.35 * target_height)
                pole_bone = target.pose.bones[pole_name]
                pole_bone.rotation_mode = "QUATERNION"
                set_world_transform(
                    target, pole_bone, control_rest[pole_name].to_quaternion(), pole_translation,
                )
                pole_bone.keyframe_insert("rotation_quaternion", frame=output_frame, group=pole_name)
                pole_bone.keyframe_insert("location", frame=output_frame, group=pole_name)
                bpy.context.view_layer.update()

            contact_semantic = {
                "leftfoot": "lefttoebase", "rightfoot": "righttoebase",
            }.get(semantic, semantic)
            if contact_semantic not in source_by_semantic or contact_semantic not in target_rest:
                contact_semantic = semantic
            source_contact_pose = sample[source_by_semantic[contact_semantic]]["world"]
            target_output_name = adapter["outputs"].get(
                contact_semantic, adapter["controls"][contact_semantic]
            )
            desired_output_translation = (
                target_rest[contact_semantic].translation
                + (
                    source_contact_pose.translation - source_rest[contact_semantic].translation
                ) * root_scale
            )
            if semantic in collision_corrections_by_semantic:
                def output_position(key):
                    name = adapter["outputs"].get(key, adapter["controls"][key])
                    return (target.matrix_world @ target.pose.bones[name].matrix).translation

                hips_position = output_position("hips")
                torso_position = output_position("spine2")
                neck_position = output_position("neck")
                left_hip = output_position("leftupleg")
                left_knee = output_position("leftleg")
                right_hip = output_position("rightupleg")
                right_knee = output_position("rightleg")
                capsules = (
                    (hips_position, torso_position, 0.14 * target_height),
                    (torso_position, neck_position, 0.12 * target_height),
                    (left_hip, left_knee, 0.075 * target_height),
                    (right_hip, right_knee, 0.075 * target_height),
                )
                corrected = desired_output_translation.copy()
                correction_total = 0.0
                for _ in range(2):
                    for start, end, radius in capsules:
                        corrected, correction = push_outside_capsule(
                            corrected, start, end, radius, Vector((0.0, -1.0, 0.0)),
                        )
                        correction_total += correction
                desired_output_translation = corrected
                collision_corrections_by_semantic[semantic].append(
                    correction_total / max(target_height, 1e-6)
                )
            for _ in range(3):
                actual_output_translation = (
                    target.matrix_world @ target.pose.bones[target_output_name].matrix
                ).translation
                residual = desired_output_translation - actual_output_translation
                if residual.length < 1e-6:
                    break
                current_control_world = target.matrix_world @ pose_bone.matrix
                set_world_transform(
                    target, pose_bone, desired_rotation,
                    current_control_world.translation + residual,
                )
                bpy.context.view_layer.update()
            pose_bone.keyframe_insert("rotation_quaternion", frame=output_frame, group=target_name)
            pose_bone.keyframe_insert("location", frame=output_frame, group=target_name)
            pose_bone.keyframe_insert("scale", frame=output_frame, group=target_name)

        if {"lefthand", "righthand"}.issubset(adapter["ik_end_controls"]):
            def output_bone(key):
                name = adapter["outputs"].get(key, adapter["controls"][key])
                return target.pose.bones[name]

            def world_head(key):
                return (target.matrix_world @ output_bone(key).matrix).translation

            body_capsules = (
                (world_head("hips"), world_head("spine2"), 0.17 * target_height),
                (world_head("spine2"), world_head("neck"), 0.15 * target_height),
                (world_head("leftupleg"), world_head("leftleg"), 0.105 * target_height),
                (world_head("rightupleg"), world_head("rightleg"), 0.105 * target_height),
            )
            for semantic in ("lefthand", "righthand"):
                output_pose = output_bone(semantic)
                points = [
                    (target.matrix_world @ output_pose.matrix).translation,
                    target.matrix_world @ output_pose.tail,
                ]
                best_correction = Vector((0.0, 0.0, 0.0))
                for point in points:
                    corrected = point.copy()
                    for start, end, radius in body_capsules:
                        corrected, _ = push_outside_capsule(
                            corrected, start, end, radius, Vector((0.0, -1.0, 0.0)),
                        )
                    correction = corrected - point
                    if correction.length > best_correction.length:
                        best_correction = correction
                if best_correction.length > 0.0:
                    control_name = adapter["ik_end_controls"][semantic]
                    control = target.pose.bones[control_name]
                    control_world = target.matrix_world @ control.matrix
                    set_world_transform(
                        target, control, control_world.to_quaternion(),
                        control_world.translation + best_correction,
                    )
                    control.keyframe_insert("rotation_quaternion", frame=output_frame, group=control_name)
                    control.keyframe_insert("location", frame=output_frame, group=control_name)
                    bpy.context.view_layer.update()
                    collision_corrections_by_semantic[semantic].append(
                        best_correction.length / max(target_height, 1e-6)
                    )

            left_position = world_head("lefthand")
            right_position = world_head("righthand")
            separation = right_position - left_position
            minimum_separation = 0.055 * target_height
            if separation.length < minimum_separation:
                direction = (
                    separation.normalized()
                    if separation.length > 1e-6 else Vector((-1.0, 0.0, 0.0))
                )
                correction = direction * (0.5 * (minimum_separation - separation.length))
                for semantic, offset in (("lefthand", -correction), ("righthand", correction)):
                    control_name = adapter["ik_end_controls"][semantic]
                    control = target.pose.bones[control_name]
                    control_world = target.matrix_world @ control.matrix
                    set_world_transform(
                        target, control, control_world.to_quaternion(),
                        control_world.translation + offset,
                    )
                    control.keyframe_insert("rotation_quaternion", frame=output_frame, group=control_name)
                    control.keyframe_insert("location", frame=output_frame, group=control_name)
                    bpy.context.view_layer.update()
                    collision_corrections_by_semantic[semantic].append(
                        offset.length / max(target_height, 1e-6)
                    )

        for semantic in shared:
            source_name = source_by_semantic[semantic]
            source_delta = rotation_delta(sample[source_name]["world"], source_rest[semantic])
            if semantic in adapter["outputs"] and semantic in target_rest:
                output_name = adapter["outputs"][semantic]
                target_world = target.matrix_world @ target.pose.bones[output_name].matrix
                target_delta = rotation_delta(target_world, target_rest[semantic])
                angular_error = quaternion_error_deg(source_delta, target_delta)
                angular_errors.append(angular_error)
                angular_errors_by_semantic[semantic].append(angular_error)
            if semantic in previous_source:
                source_jump = quaternion_error_deg(previous_source[semantic], source_delta)
                target_name = adapter["controls"][semantic]
                target_world = target.matrix_world @ target.pose.bones[target_name].matrix
                target_delta_control = rotation_delta(target_world, control_rest[target_name])
                target_jump = quaternion_error_deg(previous_target[semantic], target_delta_control)
                max_jump_deg = max(max_jump_deg, target_jump)
                motion_deg.append(target_jump)
            else:
                target_name = adapter["controls"][semantic]
                target_world = target.matrix_world @ target.pose.bones[target_name].matrix
                target_delta_control = rotation_delta(target_world, control_rest[target_name])
            previous_source[semantic] = source_delta.copy()
            previous_target[semantic] = target_delta_control.copy()

        source_hips_position = sample[source_hips]["world"].translation
        target_hips_name = adapter["outputs"].get("hips", adapter["controls"]["hips"])
        target_hips_position = (
            target.matrix_world @ target.pose.bones[target_hips_name].matrix
        ).translation
        for semantic in sorted(endpoint_semantics & set(shared)):
            source_name = source_by_semantic[semantic]
            target_name = adapter["outputs"].get(semantic, adapter["controls"][semantic])
            source_relative = (
                sample[source_name]["world"].translation - source_hips_position
            ) / max(source_height, 1e-6)
            target_relative = (
                (target.matrix_world @ target.pose.bones[target_name].matrix).translation
                - target_hips_position
            ) / max(target_height, 1e-6)
            if semantic not in source_endpoint_origin:
                source_endpoint_origin[semantic] = source_relative.copy()
                target_endpoint_origin[semantic] = target_relative.copy()
            source_trajectory = source_relative - source_endpoint_origin[semantic]
            target_trajectory = target_relative - target_endpoint_origin[semantic]
            endpoint_error = (source_trajectory - target_trajectory).length
            endpoint_trajectory_errors.append(endpoint_error)
            endpoint_errors_by_semantic[semantic].append(endpoint_error)

            if semantic.endswith("foot"):
                contact_semantic = {
                    "leftfoot": "lefttoebase", "rightfoot": "righttoebase",
                }.get(semantic, semantic)
                if contact_semantic not in source_by_semantic or contact_semantic not in target_rest:
                    contact_semantic = semantic
                source_contact_name = source_by_semantic[contact_semantic]
                target_contact_name = adapter["outputs"].get(
                    contact_semantic, adapter["controls"][contact_semantic]
                )
                source_foot_world = (
                    sample[source_contact_name]["world"].translation / max(source_height, 1e-6)
                )
                target_foot_world = (
                    target.matrix_world @ target.pose.bones[target_contact_name].matrix
                ).translation / max(target_height, 1e-6)
            if semantic in previous_source_foot_world and semantic.endswith("foot"):
                source_step = (source_foot_world - previous_source_foot_world[semantic]).length
                target_step = (target_foot_world - previous_target_foot_world[semantic]).length
                if source_step < 0.0025:
                    planted_foot_target_slips.append(target_step)
                    planted_foot_slips_by_semantic[semantic].append(target_step)
            previous_source_endpoint[semantic] = source_relative.copy()
            previous_target_endpoint[semantic] = target_relative.copy()
            if semantic.endswith("foot"):
                previous_source_foot_world[semantic] = source_foot_world.copy()
                previous_target_foot_world[semantic] = target_foot_world.copy()

    angular_errors_sorted = sorted(angular_errors)
    p95_index = min(len(angular_errors_sorted) - 1, int(0.95 * len(angular_errors_sorted))) if angular_errors_sorted else 0
    endpoint_errors_sorted = sorted(endpoint_trajectory_errors)
    endpoint_p95_index = (
        min(len(endpoint_errors_sorted) - 1, int(0.95 * len(endpoint_errors_sorted)))
        if endpoint_errors_sorted else 0
    )
    foot_slips_sorted = sorted(planted_foot_target_slips)
    foot_slip_p95_index = (
        min(len(foot_slips_sorted) - 1, int(0.95 * len(foot_slips_sorted)))
        if foot_slips_sorted else 0
    )
    def distribution(values, empty_value):
        ordered = sorted(values)
        if not ordered:
            return {"count": 0, "mean": empty_value, "p95": empty_value, "max": empty_value}
        index = min(len(ordered) - 1, int(0.95 * len(ordered)))
        return {
            "count": len(ordered),
            "mean": sum(ordered) / len(ordered),
            "p95": ordered[index],
            "max": ordered[-1],
        }
    return {
        "adapter_type": adapter["type"],
        "mapped_bones": {adapter["controls"][key]: source_by_semantic[key] for key in shared},
        "mapping_count": len(shared),
        "root_scale": root_scale,
        "max_frame_rotation_jump_deg": max_jump_deg,
        "mean_frame_rotation_change_deg": sum(motion_deg) / len(motion_deg) if motion_deg else 0.0,
        "mean_source_target_angular_error_deg": sum(angular_errors) / len(angular_errors) if angular_errors else 180.0,
        "p95_source_target_angular_error_deg": angular_errors_sorted[p95_index] if angular_errors_sorted else 180.0,
        "mean_endpoint_trajectory_error_normalized": (
            sum(endpoint_trajectory_errors) / len(endpoint_trajectory_errors)
            if endpoint_trajectory_errors else 1.0
        ),
        "p95_endpoint_trajectory_error_normalized": (
            endpoint_errors_sorted[endpoint_p95_index] if endpoint_errors_sorted else 1.0
        ),
        "planted_foot_samples": len(planted_foot_target_slips),
        "p95_planted_foot_slip_per_frame_normalized": (
            foot_slips_sorted[foot_slip_p95_index] if foot_slips_sorted else 0.0
        ),
        "max_planted_foot_slip_per_frame_normalized": (
            max(planted_foot_target_slips) if planted_foot_target_slips else 0.0
        ),
        "angular_error_by_semantic_deg": {
            semantic: distribution(values, 180.0)
            for semantic, values in sorted(angular_errors_by_semantic.items())
        },
        "endpoint_trajectory_error_by_semantic_normalized": {
            semantic: distribution(values, 1.0)
            for semantic, values in sorted(endpoint_errors_by_semantic.items())
        },
        "planted_foot_slip_by_semantic_normalized": {
            semantic: distribution(values, 0.0)
            for semantic, values in sorted(planted_foot_slips_by_semantic.items())
        },
        "collision_correction_by_semantic_normalized": {
            semantic: distribution(values, 0.0)
            for semantic, values in sorted(collision_corrections_by_semantic.items())
        },
        "split_deform_policy": "drive_primary_segment_only",
        "forced_fk_constraint_count": len(forced_fk_constraints),
        "forced_fk_constraints": forced_fk_constraints,
        "ik_end_controls": adapter["ik_end_controls"],
        "ik_pole_controls": adapter["ik_pole_controls"],
    }


def evaluated_bounds(meshes, frames, framing="fullbody"):
    points = []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for frame in frames:
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        for obj in meshes:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            try:
                stride = max(1, len(mesh.vertices) // 20000)
                points.extend(evaluated.matrix_world @ mesh.vertices[i].co for i in range(0, len(mesh.vertices), stride))
            finally:
                evaluated.to_mesh_clear()
    if not points:
        raise RuntimeError("no evaluated mesh vertices")
    full_low_z = min(p.z for p in points)
    full_high_z = max(p.z for p in points)
    fraction = {"fullbody": 0.0, "halfbody": 0.40, "closeup_hair": 0.67}.get(framing, 0.0)
    cutoff = full_low_z + (full_high_z - full_low_z) * fraction
    framed_points = [point for point in points if point.z >= cutoff]
    low = Vector((min(p.x for p in framed_points), min(p.y for p in framed_points), min(p.z for p in framed_points)))
    high = Vector((max(p.x for p in framed_points), max(p.y for p in framed_points), max(p.z for p in framed_points)))
    return low, high


def _vertex_indices_for_groups(obj, predicates, min_weight=0.55):
    matching = {
        group.index for group in obj.vertex_groups
        if any(predicate(group.name.lower()) for predicate in predicates)
    }
    if not matching:
        return []
    indices = []
    for vertex in obj.data.vertices:
        if any(item.group in matching and item.weight >= min_weight for item in vertex.groups):
            indices.append(vertex.index)
    return indices


def mesh_collision_qc(meshes, frames):
    """Measure severe hand/body and hand/hand proximity on the actual deformed skin."""
    body = next((obj for obj in meshes if "body" in obj.name.lower()), None)
    if body is None:
        return {"status": "not_available", "reason": "no visible body mesh"}

    left_hand = _vertex_indices_for_groups(body, [
        lambda name: "def-hand.l" in name or name.endswith("hand_l"),
    ], min_weight=0.70)
    right_hand = _vertex_indices_for_groups(body, [
        lambda name: "def-hand.r" in name or name.endswith("hand_r"),
    ], min_weight=0.70)
    torso_and_legs = _vertex_indices_for_groups(body, [
        lambda name: any(token in name for token in (
            "def-spine", "def-thigh", "def-pelvis", "def-breast",
            "spine_", "thigh_", "pelvis",
        )),
    ], min_weight=0.60)
    if not left_hand or not right_hand or not torso_and_legs:
        return {
            "status": "not_available",
            "reason": "required weighted vertex regions not found",
            "region_vertex_counts": {
                "left_hand": len(left_hand),
                "right_hand": len(right_hand),
                "torso_and_legs": len(torso_and_legs),
            },
        }

    depsgraph = bpy.context.evaluated_depsgraph_get()
    frame_results = []
    for frame in frames:
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        evaluated = body.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            if len(mesh.vertices) != len(body.data.vertices):
                return {
                    "status": "not_available",
                    "reason": "evaluated body topology differs from source topology",
                    "source_vertices": len(body.data.vertices),
                    "evaluated_vertices": len(mesh.vertices),
                }
            world = evaluated.matrix_world
            all_points = [world @ vertex.co for vertex in mesh.vertices]
            low_z = min(point.z for point in all_points)
            high_z = max(point.z for point in all_points)
            height = max(high_z - low_z, 1e-6)
            close_threshold = height * 0.004

            def nearest_metrics(query_indices, obstacle_indices):
                tree = KDTree(len(obstacle_indices))
                for tree_index, vertex_index in enumerate(obstacle_indices):
                    tree.insert(all_points[vertex_index], tree_index)
                tree.balance()
                distances = [tree.find(all_points[index])[2] / height for index in query_indices]
                close = [distance for distance in distances if distance <= close_threshold / height]
                return {
                    "minimum_distance_normalized": min(distances) if distances else 1.0,
                    "close_vertex_count": len(close),
                    "close_vertex_fraction": len(close) / max(1, len(distances)),
                }

            frame_results.append({
                "frame": frame,
                "left_hand_to_body": nearest_metrics(left_hand, torso_and_legs),
                "right_hand_to_body": nearest_metrics(right_hand, torso_and_legs),
                "left_to_right_hand": nearest_metrics(left_hand, right_hand),
            })
        finally:
            evaluated.to_mesh_clear()

    pairs = ("left_hand_to_body", "right_hand_to_body", "left_to_right_hand")
    summary = {}
    for pair in pairs:
        values = [item[pair] for item in frame_results]
        summary[pair] = {
            "minimum_distance_normalized": min(v["minimum_distance_normalized"] for v in values),
            "max_close_vertex_count": max(v["close_vertex_count"] for v in values),
            "max_close_vertex_fraction": max(v["close_vertex_fraction"] for v in values),
        }
    severe_pairs = [
        pair for pair, values in summary.items()
        if values["minimum_distance_normalized"] < 0.0015
        and values["max_close_vertex_fraction"] > 0.03
    ]
    return {
        "status": "failed" if severe_pairs else "passed",
        "body_mesh": body.name,
        "close_distance_threshold_normalized": 0.004,
        "region_vertex_counts": {
            "left_hand": len(left_hand),
            "right_hand": len(right_hand),
            "torso_and_legs": len(torso_and_legs),
        },
        "summary": summary,
        "severe_pairs": severe_pairs,
        "frames": frame_results,
    }


def look_at(obj, point):
    obj.rotation_euler = (point - obj.location).to_track_quat("-Z", "Y").to_euler()


def smooth01(value):
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def material_audit(meshes):
    records = []
    seen = set()
    def inspect(tree, path, stack):
        if tree in stack:
            return []
        result = []
        for node in tree.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                result.append({"node": path + "/" + node.name, "image": node.image.name,
                               "size": list(node.image.size), "colorspace": node.image.colorspace_settings.name,
                               "destinations": [link.to_node.name + ":" + link.to_socket.name
                                                for output in node.outputs for link in output.links]})
            if node.type == "BSDF_PRINCIPLED":
                sockets = {}
                for name in ("Roughness", "Metallic", "Subsurface Weight", "Normal", "Alpha"):
                    socket = node.inputs.get(name)
                    if socket is not None:
                        sockets[name] = {"linked": socket.is_linked,
                                         "value": float(socket.default_value) if isinstance(socket.default_value, (int, float)) else list(socket.default_value)}
                result.append({"node": path + "/" + node.name, "principled": sockets})
            if node.type == "GROUP" and node.node_tree:
                result.extend(inspect(node.node_tree, path + "/" + node.name, stack | {tree}))
        return result
    for mesh in meshes:
        for slot in mesh.material_slots:
            mat = slot.material
            if mat and mat.use_nodes and mat not in seen:
                seen.add(mat)
                records.append({"material": mat.name, "nodes": inspect(mat.node_tree, mat.name, set())})
    return records


def setup_render(meshes, frames, output, resolution, render_all=False, framing="fullbody", camera_angle=0.0, samples=16, engine="AUTO"):
    selected = set(meshes)
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH":
            # Never unhide alternate outfits or meshes from disabled collections.
            if obj not in selected:
                obj.hide_render = True

    # Apply geometry policy before bounds evaluation. Preview uses level 1;
    # optimized 4K production keeps level 2 for silhouette and hair quality.
    subdivision_cap = (1 if engine == "WORKBENCH" else
                       int(bpy.context.scene.get("rm_subdivision_cap", 2))
                       if engine == "CYCLES" else None)
    subdivision_changes = []
    if subdivision_cap is not None:
        for obj in meshes:
            for modifier in obj.modifiers:
                if modifier.type != "SUBSURF":
                    continue
                before = {"levels": int(modifier.levels), "render_levels": int(modifier.render_levels)}
                modifier.levels = min(int(modifier.levels), subdivision_cap)
                modifier.render_levels = min(int(modifier.render_levels), subdivision_cap)
                after = {"levels": int(modifier.levels), "render_levels": int(modifier.render_levels)}
                if before != after:
                    subdivision_changes.append({
                        "object": obj.name, "modifier": modifier.name,
                        "before": before, "after": after,
                    })

    bounds_frames = list(range(1, int(bpy.context.scene.get("rm_bounds_frame_end", max(frames))) + 1))
    all_bounds = {frame: evaluated_bounds(meshes, [frame], framing) for frame in bounds_frames}
    frame_bounds = [all_bounds[frame] for frame in frames]
    low = Vector(tuple(min(bounds[0][axis] for bounds in all_bounds.values()) for axis in range(3)))
    high = Vector(tuple(max(bounds[1][axis] for bounds in all_bounds.values()) for axis in range(3)))
    raw_centers = [(bounds[0] + bounds[1]) * 0.5 for bounds in frame_bounds]
    # Procedural actions recover to their starting place. Keep the camera on the
    # initial subject center so swinging limbs or hair cannot drag the whole body
    # across the frame. Background-camera response is applied separately below.
    first_bounds = all_bounds[bounds_frames[0]]
    center = (first_bounds[0] + first_bounds[1]) * 0.5
    subject_tracking_gain = max(
        0.0, min(0.85, float(bpy.context.scene.get("rm_subject_tracking_gain", 0.0)))
    )
    centers = [
        center + (raw_center - center) * subject_tracking_gain
        for raw_center in raw_centers
    ]
    scene = bpy.context.scene
    source_lights = []
    for obj in scene.objects:
        if obj.type == "LIGHT":
            source_lights.append(obj.name)
            obj.hide_render = True
    scene.render.use_compositing = False
    scene.render.use_sequencer = False
    scene.render.use_simplify = False
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.cycles.max_bounces = 12
    scene.cycles.diffuse_bounces = 4
    scene.cycles.glossy_bounces = 4
    scene.cycles.transmission_bounces = 12
    scene.cycles.transparent_max_bounces = 32
    scene.cycles.sample_clamp_direct = 0.0
    scene.cycles.sample_clamp_indirect = 10.0
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.denoising_input_passes = "RGB_ALBEDO_NORMAL"
    scene.cycles.denoising_prefilter = "ACCURATE"
    for marker in list(scene.timeline_markers):
        if marker.camera is not None:
            scene.timeline_markers.remove(marker)
    scene.render.engine = (
        "BLENDER_WORKBENCH" if engine == "WORKBENCH"
        else "BLENDER_EEVEE" if engine == "EEVEE"
        else "CYCLES" if engine == "CYCLES"
        else "BLENDER_EEVEE" if not render_all
        else "CYCLES"
    )
    scene.render.resolution_x = resolution
    scene.render.resolution_y = round(resolution * 9 / 16)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.media_type = "IMAGE"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "16"
    scene.render.film_transparent = True
    scene.render.use_motion_blur = False
    # Preserve sub-pixel coverage while avoiding the broad 1.5 px halo that is
    # especially visible around alpha-card hair at 4K.
    scene.render.filter_size = float(scene.get("rm_pixel_filter_width", 1.0))
    scene.render.fps = 30
    scene.render.fps_base = 1.0
    scene.frame_start = min(frames)
    scene.frame_end = max(frames)

    if scene.render.engine == "BLENDER_WORKBENCH":
        # The headless textured workbench path is the temporal preview channel.
        # It keeps texture visibility and silhouette/hair geometry while avoiding
        # the unusually slow off-screen Eevee backend on this Blender build.
        scene.display.shading.light = "STUDIO"
        scene.display.shading.color_type = "TEXTURE"
        scene.display.shading.show_shadows = True
        scene.display.shading.show_cavity = True
        scene.display.shading.cavity_type = "BOTH"
        scene.display.render_aa = "16"

    camera_data = bpy.data.cameras.new("Retarget_QC_Camera")
    camera = bpy.data.objects.new("Retarget_QC_Camera", camera_data)
    scene.collection.objects.link(camera)
    camera_data.type = "ORTHO"
    aspect = scene.render.resolution_x / scene.render.resolution_y
    # In a landscape render Blender's orthographic scale spans the camera width;
    # the visible vertical span is ortho_scale / aspect. Convert vertical bounds
    # into width units before fitting the camera.
    margin = {"fullbody": 1.12, "halfbody": 1.10, "closeup_hair": 1.08}.get(framing, 1.12)
    frame_extents = [bounds[1] - bounds[0] for bounds in frame_bounds]
    camera_data.ortho_scale = max(max(extent.x, extent.z * aspect) for extent in frame_extents) * margin
    scene_height_fraction = float(scene.get("rm_scene_human_height_fraction", 0.0))
    scene_anchor_x = float(scene.get("rm_scene_anchor_x", 0.5))
    scene_anchor_y = float(scene.get("rm_scene_anchor_y", 0.9))
    if scene_height_fraction > 0.0:
        # The VLM estimate is a scene-scale constraint, not an invitation to make
        # the subject tiny.  This range preserves useful matting detail while
        # preventing the giant-person failure on distant architecture.
        fullbody_max = float(scene.get("rm_scene_human_height_fraction_max", 0.68))
        limits = {
            "fullbody": (0.38, fullbody_max),
            "halfbody": (0.48, 0.78),
            "closeup_hair": (0.58, 0.88),
        }.get(framing, (0.38, 0.68))
        scene_height_fraction = max(limits[0], min(limits[1], scene_height_fraction))
        full_height = max(extent.z for extent in frame_extents)
        camera_data.ortho_scale = max(
            camera_data.ortho_scale,
            full_height * aspect / scene_height_fraction,
        )
    base_ortho_scale = camera_data.ortho_scale
    # Fit camera-relative travel. A partially tracking camera should account
    # only for the residual on-screen displacement, not the full world path.
    relative_lows = []
    relative_highs = []
    for bounds in all_bounds.values():
        raw_center = (bounds[0] + bounds[1]) * 0.5
        tracked_delta = (raw_center - center) * subject_tracking_gain
        relative_lows.append(bounds[0] - tracked_delta)
        relative_highs.append(bounds[1] - tracked_delta)
    relative_low = Vector(tuple(min(value[axis] for value in relative_lows) for axis in range(3)))
    relative_high = Vector(tuple(max(value[axis] for value in relative_highs) for axis in range(3)))
    union_extent = relative_high - relative_low
    resolved_fit = None
    if framing == "fullbody":
        import importlib.util
        spec = importlib.util.spec_from_file_location("framing_math", Path(__file__).with_name("framing_math.py"))
        fitting = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fitting)
        angle_rad = math.radians(camera_angle)
        projected_width = abs(math.cos(angle_rad)) * union_extent.x + abs(math.sin(angle_rad)) * union_extent.y
        resolved_fit = fitting.fullbody_fit(
            relative_low.z, relative_high.z, projected_width, aspect,
            scene_height_fraction or 0.78, scene_anchor_y,
        )
        camera_data.ortho_scale = resolved_fit["ortho_scale"]
        base_ortho_scale = camera_data.ortho_scale
    distance = max(4.0, union_extent.length * 2.0)
    angle = math.radians(camera_angle)
    camera_offset = Vector((math.sin(angle) * distance, -math.cos(angle) * distance, 0.0))
    composition_center = center.copy()
    if scene_height_fraction > 0.0 and framing != "fullbody":
        visible_vertical_span = base_ortho_scale / aspect
        subject_center_y = (
            scene_anchor_y - scene_height_fraction * 0.5
            if framing == "fullbody" else scene_anchor_y
        )
        composition_center.z += (subject_center_y - 0.5) * visible_vertical_span
    if framing == "fullbody":
        composition_center.z = resolved_fit["center_z"]
    camera.location = composition_center + camera_offset
    look_at(camera, composition_center)
    scene.camera = camera

    if scene.render.engine == "CYCLES":
        sample_cap = max(1, int(scene.get("rm_cycles_sample_cap", 12)))
        effective_samples = min(int(samples), sample_cap)
        scene.cycles.samples = effective_samples
        scene.cycles.use_adaptive_sampling = True
        scene.cycles.adaptive_min_samples = min(16, effective_samples)
        scene.cycles.adaptive_threshold = 0.01
        scene.cycles.use_denoising = bool(scene.get("rm_cycles_denoising", True))
        force_cpu = bool(scene.get("rm_force_cycles_cpu", False))
        scene.cycles.device = "CPU" if force_cpu else "GPU"
        scene.render.use_persistent_data = render_all
        if force_cpu:
            gpu_type = "CPU"
            enabled = ["CPU debug validation"]
        else:
            preferences = bpy.context.preferences.addons["cycles"].preferences
            preferences.compute_device_type = "OPTIX"
            preferences.get_devices()
            optix_devices = [device for device in preferences.devices if device.type == "OPTIX"]
            if not optix_devices:
                preferences.compute_device_type = "CUDA"
                preferences.get_devices()
                gpu_type = "CUDA"
            else:
                gpu_type = "OPTIX"
            enabled = []
            for device in preferences.devices:
                device.use = device.type == gpu_type
                if device.use:
                    enabled.append(device.name)
            if not enabled:
                raise RuntimeError("Cycles GPU requested but no CUDA/OptiX device is available")

    world = bpy.data.worlds.new("RenderMatte_QualityV2_World")
    scene.world = world
    world.use_nodes = True
    background_node = next((node for node in world.node_tree.nodes if node.type == "BACKGROUND"), None)
    if background_node is None:
        background_node = world.node_tree.nodes.new("ShaderNodeBackground")
        output_node = next((node for node in world.node_tree.nodes if node.type == "OUTPUT_WORLD"), None)
        if output_node is None:
            output_node = world.node_tree.nodes.new("ShaderNodeOutputWorld")
        world.node_tree.links.new(background_node.outputs["Background"], output_node.inputs["Surface"])
    background_node.inputs["Color"].default_value = (0.045, 0.055, 0.07, 1)
    background_node.inputs["Strength"].default_value = 0.55

    light_direction = str(scene.get("rm_scene_lighting_direction", "unknown"))
    light_temperature = str(scene.get("rm_scene_lighting_temperature", "neutral"))
    key_offsets = {
        "front": (-2.5, -4.5, 3.5), "back": (0.0, 4.5, 3.5),
        "left": (-4.5, -2.0, 3.0), "right": (4.5, -2.0, 3.0),
        "top": (0.0, -0.5, 5.5), "diffuse": (-2.0, -3.0, 4.0),
        "unknown": (-3.0, -4.0, 4.0),
    }
    color_by_temperature = {
        "warm": (1.0, 0.78, 0.60), "cool": (0.68, 0.82, 1.0),
        "neutral": (1.0, 0.96, 0.90), "mixed": (0.90, 0.92, 1.0),
        "unknown": (1.0, 0.96, 0.90),
    }
    key_offset = key_offsets.get(light_direction, key_offsets["unknown"])
    light_specs = (
        ("Key", 1400 if light_direction == "back" else 900 if light_direction == "diffuse" else 1100,
         (center.x + key_offset[0], center.y + key_offset[1], center.z + key_offset[2]), 4.0),
        ("Fill", 450 if light_direction == "back" else 400 if light_direction == "diffuse" else 300,
         (center.x - key_offset[0] * 0.7, center.y - 1.0, center.z + 2.0), 3.5),
    )
    for name, energy, position, size in light_specs:
        data = bpy.data.lights.new(name, "AREA")
        data.energy = energy
        data.shape = "DISK"
        data.size = size
        base_color = color_by_temperature.get(light_temperature, color_by_temperature["neutral"])
        if name == "Fill" and light_temperature == "mixed":
            base_color = color_by_temperature["warm"]
        data.color = base_color
        light = bpy.data.objects.new(name, data)
        scene.collection.objects.link(light)
        light.location = position
        look_at(light, center)

    output.mkdir(parents=True, exist_ok=True)
    pan_x = float(scene.get("rm_bg_pan_x", 0.0))
    pan_y = float(scene.get("rm_bg_pan_y", 0.0))
    zoom = float(scene.get("rm_bg_zoom", 0.0))
    follow_gain = float(scene.get("rm_camera_follow_gain", 0.8))
    base_shift_x = 0.5 - scene_anchor_x if scene_height_fraction > 0.0 else 0.0
    frame_span = max(1, max(frames) - min(frames))
    for frame, tracked_center in zip(frames, centers):
        raw_progress = (frame - min(frames)) / frame_span
        progress = smooth01(raw_progress) - 0.5
        camera_data.shift_x = base_shift_x - pan_x * progress * 0.08 * follow_gain
        camera_data.shift_y = -pan_y * progress * 0.08 * follow_gain
        camera_data.ortho_scale = base_ortho_scale * max(
            0.92, min(1.08, 1.0 - zoom * progress * 0.08 * follow_gain)
        )
        scene.frame_set(frame)
        tracked_composition_center = tracked_center + (composition_center - center)
        camera.location = tracked_composition_center + camera_offset
        look_at(camera, tracked_composition_center)
        scene.camera = camera
        filename = f"{frame:05d}.png" if render_all else f"frame_{frame:04d}.png"
        scene.render.filepath = str(output / filename)
        bpy.ops.render.render(write_still=True)
    report = {"bbox_low": list(low), "bbox_high": list(high), "ortho_scale": base_ortho_scale,
              "quality_profile": "quality_v3_framing_lighting_comparison",
              "framing_fit": resolved_fit,
              "composition_center": list(composition_center),
              "bounds_frame_count": len(bounds_frames),
              "light_specs": list(light_specs),
              "material_audit": material_audit(meshes),
              "source_lights_disabled": source_lights,
              "source_compositor_disabled": True,
              "source_world_replaced": True,
              "simplify": scene.render.use_simplify,
              "resolution": [scene.render.resolution_x, scene.render.resolution_y],
              "color_management": {"view_transform": "AgX", "look": "None", "exposure": 0.0},
              "alpha_policy": "native_render_alpha_no_artistic_compositor",
              "framing": framing,
              "framing_vertical_fraction": {"fullbody": 0.0, "halfbody": 0.40, "closeup_hair": 0.67}.get(framing, 0.0),
              "camera_follow": (
                  "partial_subject_root_tracking_plus_eased_background_motion"
                  if subject_tracking_gain > 0.0
                  else "fixed_initial_subject_center_plus_eased_background_motion"
              ),
              "subject_tracking_gain": subject_tracking_gain,
              "camera_terminal_velocity": "zero_by_smoothstep",
              "motion_blur": False,
              "pixel_filter_width": scene.render.filter_size,
              "subdivision_cap": subdivision_cap,
              "subdivision_changes": subdivision_changes,
              "background_camera_match": {"pan_x": pan_x, "pan_y": pan_y, "zoom": zoom,
                                          "follow_gain": follow_gain},
              "scene_placement": {
                  "enabled": scene_height_fraction > 0.0,
                  "anchor": [scene_anchor_x, scene_anchor_y],
                  "human_height_fraction": scene_height_fraction,
              },
              "scene_lighting_match": {
                  "direction": light_direction,
                  "temperature": light_temperature,
              }}
    if scene.render.engine == "CYCLES":
        report.update({
            "cycles_device_type": gpu_type, "cycles_devices": enabled,
            "cycles_adaptive_sampling": True,
            "cycles_min_samples": min(16, effective_samples),
            "cycles_max_samples": effective_samples,
            "cycles_adaptive_threshold": 0.01,
            "denoising_passes": "RGB_ALBEDO_NORMAL",
            "transparent_max_bounces": scene.cycles.transparent_max_bounces,
            "cycles_denoising": scene.cycles.use_denoising,
            "cycles_sample_cap": sample_cap,
        })
    return report


def main():
    args = parse_args()
    job = None
    if args.job:
        job = json.loads(Path(args.job).read_text(encoding="utf-8"))
        args.motion = job["motion_path"]
        args.output = str(Path(job["output_dir"]) / "foreground")
        args.start = float(job["source_start_frame"])
        args.frames = int(job["frame_count"])
        args.resolution = int(job.get("resolution_x", 3840))
        args.render_all = True
        args.framing = job.get("framing", "fullbody")
        args.camera_angle = float(job.get("camera_angle_deg", 0.0))
        args.samples = int(job.get("samples", 32))
        if args.resolution != 3840:
            raise RuntimeError(f"dataset jobs must render at 3840x2160, got width={args.resolution}")
    if not args.motion or not args.output:
        raise RuntimeError("--motion/--output or --job is required")
    output = Path(args.output)
    initial_armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    target = target_armature(initial_armatures)
    meshes = character_meshes(target)
    repaired_textures = repair_character_textures(meshes)
    before = {obj.name for obj in bpy.data.objects}
    bpy.ops.import_scene.fbx(filepath=args.motion, automatic_bone_orientation=False)
    imported = {obj.name for obj in bpy.context.scene.objects if obj.name not in before}
    source = source_armature(imported)
    action = source.animation_data.action if source.animation_data else None
    if not action:
        raise RuntimeError("motion armature has no action")

    available_end = float(action.frame_range[1])
    source_frames = [args.start + i for i in range(min(args.frames, int(available_end - args.start + 1)))]
    output_frames = list(range(1, len(source_frames) + 1))
    source_by_clean = semantic_bone_map(source)
    adapter = make_adapter(target)
    shared = CORE_BONES & source_by_clean.keys() & adapter["controls"].keys()
    mapped_source_names = [source_by_clean[key] for key in shared]
    samples = capture_motion(source, source_frames, mapped_source_names)
    source.hide_render = True
    report = retarget(target, source, output_frames, samples)
    inspection_frames = sorted(set([1, max(1, len(output_frames) // 4), max(1, len(output_frames) // 2), max(1, 3 * len(output_frames) // 4), len(output_frames)]))
    report["mesh_collision_qc"] = mesh_collision_qc(meshes, inspection_frames)
    render_frames = output_frames if args.render_all else inspection_frames
    report.update(setup_render(
        meshes, render_frames, output, args.resolution, args.render_all,
        args.framing, args.camera_angle, args.samples, args.engine,
    ))
    report.update({
        "target": target.name,
        "source": source.name,
        "motion": args.motion,
        "source_frames": [source_frames[0], source_frames[-1]],
        "output_frames": [output_frames[0], output_frames[-1]],
        "inspection_frames": inspection_frames,
        "framing": args.framing,
        "camera_angle_deg": args.camera_angle,
        "retarget_policy": "canonical_world_delta_full_fk_v8",
        "repaired_textures": repaired_textures,
        "visible_character_mesh_count": len(meshes),
        "visible_character_meshes": sorted(obj.name for obj in meshes),
        "output_resolution": [args.resolution, round(args.resolution * 9 / 16)],
    })
    report_path = output.parent / "retarget_qc.json" if job else output / "qc_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["max_frame_rotation_jump_deg"] > 60.0:
        raise RuntimeError(f"retarget QC failed: rotation jump {report['max_frame_rotation_jump_deg']:.2f} deg/frame")
    if report["mean_frame_rotation_change_deg"] < 0.15:
        raise RuntimeError(f"retarget QC failed: motion too static {report['mean_frame_rotation_change_deg']:.4f} deg/frame")
    if report["mean_source_target_angular_error_deg"] > 5.0:
        raise RuntimeError(
            f"retarget QC failed: mean source-target angular error "
            f"{report['mean_source_target_angular_error_deg']:.2f} deg"
        )
    if report["p95_source_target_angular_error_deg"] > 12.0:
        raise RuntimeError(
            f"retarget QC failed: p95 source-target angular error "
            f"{report['p95_source_target_angular_error_deg']:.2f} deg"
        )
    # Absolute hand paths differ legitimately with shoulder/arm proportions.
    # Preserve the source joint angles and gate only root-linked head/foot paths.
    failed_endpoints = {
        semantic: values["p95"]
        for semantic, values in report["endpoint_trajectory_error_by_semantic_normalized"].items()
        if semantic in {"head", "leftfoot", "rightfoot"} and values["p95"] > 0.08
    }
    if failed_endpoints:
        raise RuntimeError(f"retarget QC failed: endpoint trajectory p95 {failed_endpoints}")
    if report["p95_planted_foot_slip_per_frame_normalized"] > 0.0025:
        raise RuntimeError(
            "retarget QC failed: planted-foot slip p95 "
            f"{report['p95_planted_foot_slip_per_frame_normalized']:.6f}"
        )
    if report["max_planted_foot_slip_per_frame_normalized"] > 0.0030:
        raise RuntimeError(
            "retarget QC failed: planted-foot slip max "
            f"{report['max_planted_foot_slip_per_frame_normalized']:.6f}"
        )
    if report["mesh_collision_qc"]["status"] != "passed":
        raise RuntimeError(
            "retarget QC failed: mesh collision check "
            f"{report['mesh_collision_qc']}"
        )


if __name__ == "__main__":
    main()
