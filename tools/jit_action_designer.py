import gzip
import hashlib
import json
import math
import os
import sqlite3
from pathlib import Path


VIDEO_ROOT = Path(os.environ["VIDEO_ROOT"])
OBS_ROOT = Path(os.environ["MOTION_OBSERVATION_ROOT"])
OBS_DB = OBS_ROOT / "motion_observations.sqlite"
OBSERVATION_VERSION = "full-source-observables-v2"
PRESET_CATALOG = Path(os.environ.get("EXPANDED_PRESET_CATALOG", OBS_ROOT / "expanded_motion_preset_catalog_v4_expanded.json.gz"))
DESIGN_STATUS = "expanded_safe_preset_v1_assigned_jit"
EXPECTED_CATALOG_VERSION = os.environ.get(
    "EXPANDED_PRESET_CATALOG_VERSION", "expanded-safe-presets-v4-expanded"
)
_OBSERVATION_CATALOG = None
_PRESET_CATALOG_CACHE = None


def stable_int(value):
    return int(hashlib.sha1(value.encode("utf-8")).hexdigest()[:16], 16)


def _raw_vector(data):
    metrics = data["metrics"]
    ranges = metrics.get("joint_axis_ranges", {})

    def extent(names):
        return sum(sum(ranges.get(name, (0.0, 0.0, 0.0))) for name in names)

    contacts = metrics.get("contact_fraction", {})
    transitions = metrics.get("contact_transitions", {})
    root = metrics.get("root_displacement", (0.0, 0.0, 0.0))
    return (
        math.log1p(data["frame_count"]),
        math.log1p(metrics.get("motion_energy", 0.0)),
        metrics.get("root_path_length", 0.0),
        math.sqrt(sum(value * value for value in root)),
        metrics.get("body_yaw_range_deg", 0.0) / 180.0,
        metrics.get("body_yaw_path_deg", 0.0) / 360.0,
        contacts.get("left", 0.0), contacts.get("right", 0.0),
        transitions.get("left", 0), transitions.get("right", 0),
        extent(("lefthand", "righthand", "leftforearm", "rightforearm")),
        extent(("leftfoot", "rightfoot", "leftleg", "rightleg")),
        sum(metrics.get("knee_angle_ranges", {}).values()) / 180.0,
        sum(metrics.get("elbow_angle_ranges", {}).values()) / 180.0,
    )


def observation_catalog():
    global _OBSERVATION_CATALOG
    if _OBSERVATION_CATALOG is not None:
        return _OBSERVATION_CATALOG
    db = sqlite3.connect(OBS_DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT segment_id,motion_path,source_start,frame_count,feature_path,"
        "trajectory_fingerprint FROM observations WHERE status='done' ORDER BY segment_id"
    ).fetchall()
    db.close()
    catalog = []
    for row in rows:
        feature_path = Path(row["feature_path"])
        if not feature_path.exists():
            continue
        with gzip.open(feature_path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("schema") != 2 or data.get("observation_version") != OBSERVATION_VERSION:
            continue
        item = dict(row)
        item["feature_path"] = str(feature_path)
        item["source_frames"] = data["source_frames"]
        item["source_fps"] = float(data["scene_fps"])
        item["phase_events"] = data["phase_events"]
        item["metrics"] = data["metrics"]
        item["raw_vector"] = _raw_vector(data)
        catalog.append(item)
    if len(catalog) < 1908:
        raise RuntimeError(f"only {len(catalog)} independent motion observations are available")
    dimensions = len(catalog[0]["raw_vector"])
    centers = []
    scales = []
    for axis in range(dimensions):
        values = sorted(item["raw_vector"][axis] for item in catalog)
        centers.append(values[len(values) // 2])
        scales.append(max(values[(3 * len(values)) // 4] - values[len(values) // 4], 1e-5))
    for item in catalog:
        item["vector"] = tuple(
            max(-3.0, min(3.0, (value - center) / scale))
            for value, center, scale in zip(item["raw_vector"], centers, scales)
        )
    _OBSERVATION_CATALOG = catalog
    return _OBSERVATION_CATALOG


def expanded_preset_catalog():
    global _PRESET_CATALOG_CACHE
    if _PRESET_CATALOG_CACHE is not None:
        return _PRESET_CATALOG_CACHE
    with gzip.open(PRESET_CATALOG, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("catalog_version") != EXPECTED_CATALOG_VERSION:
        raise RuntimeError(f"unexpected expanded preset catalog: {payload.get('catalog_version')}")
    catalog = payload.get("presets", [])
    if len(catalog) < 1908:
        raise RuntimeError(f"only {len(catalog)} independent expanded presets are available")
    if len({item["source_trajectory_fingerprint"] for item in catalog}) != len(catalog):
        raise RuntimeError("expanded preset catalog repeats a source trajectory")
    if len({item["blueprint_signature"] for item in catalog}) != len(catalog):
        raise RuntimeError("expanded preset catalog repeats a channel blueprint")
    if len({item["trajectory_fingerprint"] for item in catalog}) != len(catalog):
        raise RuntimeError("expanded preset catalog repeats a sampled trajectory")
    _PRESET_CATALOG_CACHE = catalog
    return _PRESET_CATALOG_CACHE


def _used_observations(db):
    used = set()
    for (payload,) in db.execute("SELECT action_params_json FROM action_designs"):
        try:
            segment_id = json.loads(payload).get("observation_segment_id")
        except (TypeError, json.JSONDecodeError):
            segment_id = None
        if segment_id:
            used.add(segment_id)
    return used


def _subject_vectors(db, subject_id, by_segment):
    result = []
    rows = db.execute(
        "SELECT action_params_json FROM action_designs WHERE subject_id=? ORDER BY subject_action_index",
        (subject_id,),
    ).fetchall()
    for (payload,) in rows:
        try:
            segment_id = json.loads(payload).get("observation_segment_id")
        except (TypeError, json.JSONDecodeError):
            continue
        if segment_id in by_segment:
            result.append(by_segment[segment_id]["vector"])
    return result


def _distance(first, second):
    return sum((a - b) ** 2 for a, b in zip(first, second))


def _used_presets(db):
    used = set()
    for (payload,) in db.execute("SELECT action_params_json FROM action_designs"):
        try:
            preset_id = json.loads(payload).get("expanded_preset_id")
        except (TypeError, json.JSONDecodeError):
            preset_id = None
        if preset_id:
            used.add(preset_id)
    return used


def _subject_preset_descriptors(db, subject_id):
    result = []
    rows = db.execute(
        "SELECT action_params_json FROM action_designs WHERE subject_id=? ORDER BY subject_action_index",
        (subject_id,),
    ).fetchall()
    for (payload,) in rows:
        try:
            descriptor = json.loads(payload).get("expanded_blueprint", {}).get("trajectory_descriptor")
        except (TypeError, json.JSONDecodeError):
            descriptor = None
        if descriptor:
            result.append(descriptor)
    return result


def choose_expanded_preset(db, job):
    catalog = expanded_preset_catalog()
    used = _used_presets(db)
    candidates = [item for item in catalog if item["preset_id"] not in used]
    if not candidates:
        raise RuntimeError("independent expanded preset reserve is exhausted")
    previous = _subject_preset_descriptors(db, job["subject_id"])
    if not previous:
        candidates.sort(key=lambda item: stable_int(f"{job['subject_id']}:{item['preset_id']}"))
        return candidates[len(candidates) // 3]
    return max(
        candidates,
        key=lambda item: (
            min(_distance(item["trajectory_descriptor"], vector) for vector in previous),
            -stable_int(f"{job['subject_id']}:{job['id']}:{item['preset_id']}"),
        ),
    )


def design_if_needed(db, job):
    current = json.loads(job["action_params_json"])
    if current.get("design_status") == DESIGN_STATUS:
        return job
    preset = choose_expanded_preset(db, job)
    design_seq = db.execute("SELECT COALESCE(MAX(design_seq), -1) + 1 FROM action_designs").fetchone()[0]
    params = {
        "design_status": DESIGN_STATUS,
        "design_seq": design_seq,
        "subject_action_index": current["subject_action_index"],
        "expanded_preset_catalog_version": preset["catalog_version"],
        "expanded_preset_id": preset["preset_id"],
        "expanded_blueprint": preset,
        "trajectory_fingerprint": preset["trajectory_fingerprint"],
        "blueprint_signature": preset["blueprint_signature"],
        "source_motion_path": preset["source_motion_path"],
        "source_frames": preset["source_frames"],
        "source_frame_count": int(preset["source_frames"][1] - preset["source_frames"][0] + 1),
        "source_fps": preset["source_fps"],
        "output_frame_count": int(job["frame_count"]),
        "motion_solver": "target_native_safe_preset_ik_v1",
        "motion_reference_policy": "all_library_actions_inspire_bounded_independent_presets",
        "source_bone_rotations_used": False,
        "timeline_policy": "shared_prepare_action_recover_with_continuous_channel_phases",
        "preset_independence_policy": "unique_source_blueprint_and_sampled_trajectory",
        "hair_gain": 1.0,
        "camera_follow_gain": 0.84,
    }
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))
    action_id = hashlib.sha1(
        f"expanded-preset-v1:{job['subject_id']}:{job['id']}:{preset['preset_id']}".encode()
    ).hexdigest()[:24]
    db.execute(
        "UPDATE jobs SET recipe='expanded_preset',action_design_id=?,action_params_json=?,"
        "motion_segment_id=?,motion_path=?,source_start_frame=? WHERE id=?",
        (action_id, params_json, preset["source_segment_id"], preset["source_motion_path"],
         int(preset["source_frames"][0]), job["id"]),
    )
    db.execute(
        "INSERT INTO action_designs(job_id,design_seq,subject_id,subject_action_index,"
        "action_design_id,inspiration_signature,blueprint_signature,action_params_json) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (job["id"], design_seq, job["subject_id"], params["subject_action_index"], action_id,
         preset["source_segment_id"], preset["blueprint_signature"], params_json),
    )
    db.execute(
        "INSERT INTO events(job_id,event,detail) VALUES(?, 'expanded_safe_preset_assigned_jit', ?)",
        (job["id"], json.dumps({
            "design_seq": design_seq,
            "expanded_preset_id": preset["preset_id"],
            "blueprint_signature": preset["blueprint_signature"],
            "trajectory_fingerprint": preset["trajectory_fingerprint"],
            "motion_path": preset["source_motion_path"],
        }, ensure_ascii=False)),
    )
    row = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
    columns = [item[0] for item in db.execute("SELECT * FROM jobs LIMIT 0").description]
    return dict(zip(columns, row))
