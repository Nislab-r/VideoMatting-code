"""Fresh per-attempt production preflight contract (stdlib, no Blender dependency)."""
import hashlib
import json
from pathlib import Path

VERSION = "task_preflight_v1"

def reject_bypasses(job):
    if job.get("debug_skip_geometry_qc"):
        raise RuntimeError("PREPASS: geometry checks cannot be skipped")
    requested = job.get("prepass_render_frames")
    if requested is not None and requested != list(range(1, int(job["frame_count"]) + 1)):
        raise RuntimeError("PREPASS: all authored frames are mandatory")

def input_fingerprint(job):
    # Stat guards detect ordinary changes; these are not asset content hashes.
    source = {}
    for key in ("asset_path", "motion_path", "background_path"):
        p = Path(job[key]); s = p.stat()
        source[key] = [str(p.resolve()), s.st_size, s.st_mtime_ns]
    frozen = {k:v for k,v in job.items() if k not in {
        "task_preflight_input_fingerprint", "scene_composite_vlm_qc"}}
    return hashlib.sha256(json.dumps([frozen, source], sort_keys=True).encode()).hexdigest()

def validate_task_report(report, job, final=False):
    reject_bypasses(job)
    expected = "rendered_rgba" if final else "passed_prepass"
    if report.get("status") != expected:
        raise RuntimeError("PREPASS: report status mismatch")
    for key in ("subject_id", "motion_segment_id", "action_design_id", "frame_count",
                "task_preflight_attempt", "task_preflight_input_fingerprint"):
        if job.get(key) is None or report.get(key) != job[key]:
            raise RuntimeError("PREPASS: fresh report identity mismatch: " + key)
    if report.get("task_preflight_version") != VERSION:
        raise RuntimeError("PREPASS: incompatible contract")
    if report.get("source_animation_imported") is not False:
        raise RuntimeError("PREPASS: source animation imported")
    if report.get("rendered_frame_subset") != list(range(1, int(job["frame_count"]) + 1)):
        raise RuntimeError("PREPASS: incomplete rendered frame coverage")
    for key in ("geometry_motion_qc", "limb_deformation_qc", "collision_gate"):
        if (report.get(key) or {}).get("status") != "passed":
            raise RuntimeError("PREPASS: missing or failed " + key)
    if (report.get("limb_deformation_qc") or {}).get("frame_count") != int(job["frame_count"]):
        raise RuntimeError("PREPASS: incomplete limb coverage")
