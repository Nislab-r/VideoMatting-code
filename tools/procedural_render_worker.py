import argparse
import uuid
import fcntl
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import time
import traceback
import tempfile
import re
from pathlib import Path

from jit_action_designer import design_if_needed
from frozen_pairing import assign_frozen_pairing
from production_quality import job_settings, load_setting, background_statistics
from production_quality.garments import validate_report as validate_garment_report
from production_quality.task_preflight import reject_bypasses, input_fingerprint, validate_task_report, VERSION
from asset_source import verify_job_sources
from render_record import write_record
from human_registry import verify_human_job


ROOT = Path(os.environ["DATA_ROOT"])
DB_PATH = ROOT / "jobs.sqlite"
BLENDER = Path(os.environ["BLENDER_BIN"])
GENERATOR = ROOT / "tools/procedural_reference_production.py"
SCENE_QC = ROOT / "tools/qc_scene_composite_vlm.py"
SCENE_VLM_PYTHON = Path(os.environ.get(
    "SCENE_VLM_PYTHON", str(ROOT / "runtime/optional-vlm/bin/python")
))
SCENE_VLM_MODEL = Path(os.environ.get(
    "SCENE_VLM_MODEL", str(ROOT / "models/Qwen2.5-VL-7B-Instruct")
))
PAIRING_REFERENCE_DIR = Path(os.environ.get(
    "PAIRING_REFERENCE_DIR", ROOT / "reports/pairing_reference"
))
PAIRING_MANIFEST = PAIRING_REFERENCE_DIR / "render_pairing_reference.sqlite"
BACKGROUND_ROOTS = Path(os.environ.get(
    "BACKGROUND_ROOTS_CONFIG", PAIRING_REFERENCE_DIR / "background_roots.json"
))
FPS = "30"
COMPOSITE_SUFFIX = ".jpg"
COMPOSITE_JPEG_QSCALE = "3"
# Multi-GPU contract: the supervisor assigns one physical GPU and slot per worker.
GPU_SLOT = os.environ.get("VIDEOMATTING_GPU_SLOT", "0")
GPU_ID = os.environ.get("VIDEOMATTING_GPU_UUID", "legacy-single")
if not re.fullmatch(r"[A-Za-z0-9_-]+", GPU_ID) or not GPU_SLOT.isdigit():
    raise ValueError("Invalid GPU slot identity")
GPU_RENDER_LOCK = Path(tempfile.gettempdir()) / f"videomatting-gpu-locks-{os.getuid()}" / f"{GPU_ID}-{GPU_SLOT}.lock"
MULTI_GPU_QUEUE_VERSION = 1
TASK_PREFLIGHT_VERSION = 1
EGL_LIB = Path(os.environ.get(
    "EGL_LIB", "/usr/lib/x86_64-linux-gnu/libEGL.so.1"
))

if EGL_LIB.exists():
    egl_dir = EGL_LIB.parent if EGL_LIB.is_file() else EGL_LIB
    os.environ["LD_LIBRARY_PATH"] = str(egl_dir) + ":" + os.environ.get("LD_LIBRARY_PATH", "")
    os.environ.setdefault("EGL_PLATFORM", "surfaceless")
    os.environ.setdefault(
        "__EGL_VENDOR_LIBRARY_FILENAMES",
        "/usr/share/glvnd/egl_vendor.d/10_nvidia.json",
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-id", required=True)
    return parser.parse_args()


def connection():
    db = sqlite3.connect(DB_PATH, timeout=60)
    db.execute("PRAGMA busy_timeout=60000")
    # A fresh database can be opened by several worker slots simultaneously.
    # Switching DELETE -> WAL races with other first connections; retry that
    # transition, while ordinary WAL connections need no mode-changing write.
    for attempt in range(20):
        try:
            if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                db.execute("PRAGMA journal_mode=WAL")
            return db
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower() or attempt == 19:
                db.close()
                raise
            time.sleep(0.05 * (attempt + 1))


def recover_stale_running():
    db = connection()
    try:
        rows = db.execute("SELECT id, pid FROM jobs WHERE status='running'").fetchall()
        stale = [job_id for job_id, pid in rows if not pid or not Path(f"/proc/{pid}").exists()]
        if stale:
            db.executemany(
                "UPDATE jobs SET status='pending', worker_id=NULL, pid=NULL, started_at=NULL, "
                "error='recovered stale worker' WHERE id=?",
                [(job_id,) for job_id in stale],
            )
            db.commit()
    finally:
        db.close()


def claim(worker_id):
    db = connection()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""
            SELECT * FROM jobs
            WHERE status='pending'
              AND NOT EXISTS (
                  SELECT 1 FROM jobs AS active
                  WHERE active.status='running' AND active.subject_id=jobs.subject_id
              )
            ORDER BY CASE split WHEN 'train' THEN 0 ELSE 1 END, subject_id, id LIMIT 1
        """).fetchone()
        if row is None:
            db.rollback()
            return None
        columns = [item[0] for item in db.execute("SELECT * FROM jobs LIMIT 0").description]
        job = dict(zip(columns, row))
        job = design_if_needed(db, job)
        if os.environ.get("SCENE_PAIRING_REQUIRED", "1") == "1":
            if not PAIRING_MANIFEST.is_file() or not BACKGROUND_ROOTS.is_file():
                raise RuntimeError("frozen background pairing manifest is missing")
            job = assign_frozen_pairing(db, job, PAIRING_MANIFEST, BACKGROUND_ROOTS)
        db.execute(
            "UPDATE jobs SET status='running', worker_id=?, pid=?, attempts=attempts+1, started_at=CURRENT_TIMESTAMP, error=NULL WHERE id=?",
            (worker_id, os.getpid(), job["id"]),
        )
        db.execute("INSERT INTO events(job_id,event,detail) VALUES(?, 'claimed', ?)", (job["id"], worker_id))
        db.commit()
        return job
    finally:
        db.close()


def queue_has_active_work():
    db = connection()
    try:
        return bool(db.execute(
            "SELECT 1 FROM jobs WHERE status IN ('pending','running') LIMIT 1"
        ).fetchone())
    finally:
        db.close()


def set_status(job_id, status, detail=None):
    db = connection()
    try:
        db.execute(
            "UPDATE jobs SET status=?, finished_at=CURRENT_TIMESTAMP, error=? WHERE id=?",
            (status, detail, job_id),
        )
        db.execute("INSERT INTO events(job_id,event,detail) VALUES(?,?,?)", (job_id, status, detail))
        db.commit()
    finally:
        db.close()


def run(command, log_handle):
    log_handle.write("COMMAND " + json.dumps([str(item) for item in command], ensure_ascii=False) + "\n")
    log_handle.flush()
    subprocess.run([str(item) for item in command], check=True, stdout=log_handle, stderr=subprocess.STDOUT)


def run_blender(command, log_handle):
    threads = os.environ.get("BLENDER_THREADS")
    if threads:
        if not threads.isdigit() or int(threads) < 1:
            raise ValueError("BLENDER_THREADS must be a positive integer")
        command = [command[0], "-t", threads, *command[1:]]
    GPU_RENDER_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with GPU_RENDER_LOCK.open("a", encoding="ascii") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        run(command, log_handle)


def ffprobe_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True, text=True, capture_output=True,
    )
    return float(result.stdout.strip())


def background_camera_motion(path, start_sec, duration):
    frame_count = 5
    rate = max(0.5, (frame_count - 1) / max(duration, 0.1))
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", f"{start_sec:.6f}", "-t", f"{duration:.6f}",
        "-i", str(path), "-vf", f"fps={rate:.8f},scale=64:36,format=gray",
        "-frames:v", str(frame_count), "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ], check=True, capture_output=True)
    size = 64 * 36
    frames = [result.stdout[index:index + size] for index in range(0, len(result.stdout), size)]
    frames = [frame for frame in frames if len(frame) == size]
    if len(frames) < 2:
        return {"class": "unknown", "pan_x_normalized": 0.0, "pan_y_normalized": 0.0,
                "zoom_normalized": 0.0, "activity": 0.0}

    shifts = []
    for first, second in zip(frames, frames[1:]):
        best = None
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                total = count = 0
                for y in range(6, 30, 2):
                    yy = y + dy
                    if not 0 <= yy < 36:
                        continue
                    for x in range(8, 56, 2):
                        xx = x + dx
                        if 0 <= xx < 64:
                            total += abs(first[y * 64 + x] - second[yy * 64 + xx])
                            count += 1
                score = total / max(1, count)
                if best is None or score < best[0]:
                    best = (score, dx, dy)
        shifts.append(best)
    mean_dx = sum(item[1] for item in shifts) / len(shifts)
    mean_dy = sum(item[2] for item in shifts) / len(shifts)
    activity = sum(item[0] for item in shifts) / len(shifts) / 255.0
    magnitude = (mean_dx * mean_dx + mean_dy * mean_dy) ** 0.5
    motion_class = "static" if magnitude < 0.35 else "slow_pan" if magnitude < 1.5 else "fast_pan"
    return {
        "class": motion_class,
        "pan_x_normalized": max(-1.0, min(1.0, mean_dx / 4.0)),
        "pan_y_normalized": max(-1.0, min(1.0, mean_dy / 4.0)),
        "zoom_normalized": 0.0,
        "activity": activity,
        "measured_frame_pairs": len(shifts),
    }


def verify_pngs(folder, expected, *, size=None, depth=None, color_type=None):
    files = sorted(folder.glob("*.png"))
    if len(files) != expected:
        raise RuntimeError(f"{folder.name} frame count {len(files)} != {expected}")
    if [p.stem for p in files] != [f'{n:05d}' for n in range(1, expected + 1)]:
        raise RuntimeError(f'Non-contiguous PNG sequence: {folder}')
    if size is not None:
        for path in files:
            with path.open('rb') as stream:
                header = stream.read(26)
            if header[:8] != b'\x89PNG\r\n\x1a\n' or len(header) != 26:
                raise RuntimeError(f'Invalid PNG: {path}')
            w, h, bits, kind = struct.unpack('>IIBB', header[16:26])
            if (w, h) != tuple(size) or bits != depth or kind != color_type:
                raise RuntimeError(f'Unexpected PNG dimensions/depth/type: {path}')


def _process(job, worker_id, *, output_root=None, work_root=None):
    verify_human_job(job)
    reject_bypasses(job)
    if job.get("render_frames") is not None and job["render_frames"] != list(range(1, int(job["frame_count"]) + 1)):
        raise RuntimeError("PREPASS: production jobs require complete final frames")
    setting = load_setting()
    output_root = Path(output_root) if output_root else ROOT / setting['output_directory']
    work_root = Path(work_root) if work_root else ROOT / '.work' / setting['id']
    sample = output_root / job["split"] / job["sample_id"]
    done = sample / "DONE"
    if done.exists():
        return
    work = work_root / job["sample_id"]
    rgba = work / "rgba"
    prepass = work / "prepass"
    if work.exists():
        shutil.rmtree(work)
    for folder in (rgba, prepass, sample / "composite", sample / "mask", sample / "video"):
        folder.mkdir(parents=True, exist_ok=True)

    source_refs = verify_job_sources(job, ROOT)
    action_params = json.loads(job["action_params_json"])
    design_status = action_params.get("design_status")
    if design_status not in {
        "expanded_safe_preset_v1_assigned_jit",
        "subject_action_blueprint_v4_assigned_jit",
    }:
        raise RuntimeError("PREPASS: action lacks a character-adaptive design assignment")
    if action_params.get("source_bone_rotations_used") is not False:
        raise RuntimeError("PREPASS: source bone rotations must never be applied")
    if design_status == "expanded_safe_preset_v1_assigned_jit":
        solver = action_params.get('motion_solver')
        catalog = (action_params.get('expanded_blueprint') or {}).get('catalog_version')
        supported_solver = solver == 'target_native_safe_preset_ik_v1' or (
            solver == 'target_native_safe_preset_ik_v2'
            and catalog == 'expanded-safe-presets-v4-expanded'
        )
        if (
            not supported_solver
            or not action_params.get("expanded_blueprint")
        ):
            raise RuntimeError("PREPASS: invalid expanded-preset motion contract")
    elif (
        action_params.get("motion_solver") != "subject_specific_landmark_ik_v4"
        or not action_params.get("observation_path")
        or not action_params.get("trajectory_fingerprint")
    ):
        raise RuntimeError("PREPASS: invalid subject-action blueprint v4 contract")
    frame_count = int(job["frame_count"])
    duration = frame_count / 30.0
    background_duration = ffprobe_duration(job["background_path"])
    start_sec = min(float(job["background_start_sec"]), max(0.0, background_duration - duration - 0.1))
    camera_motion = background_camera_motion(job["background_path"], start_sec, duration)
    reference = subprocess.run([
        'ffmpeg', '-v', 'error', '-nostdin', '-ss', str(start_sec), '-i', job['background_path'],
        '-vf', 'scale=192:108:force_original_aspect_ratio=increase,crop=192:108',
        '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1',
    ], check=True, capture_output=True)
    config = {
        **job,
        **job_settings(),
        "dataset_source_refs": source_refs,
        'background_light_statistics': background_statistics(reference.stdout),
        "action_params": action_params,
        "rgba_work_dir": str(rgba),
        "prepass_dir": str(prepass),
        "prepass_report_path": str(work / "prepass_report.json"),
        "render_report_path": str(sample / "render_report.json"),
        "background_start_sec_actual": start_sec,
        "background_camera_motion": camera_motion,
        "worker_id": worker_id,
    }
    config["task_preflight_attempt"] = uuid.uuid4().hex
    config["task_preflight_input_fingerprint"] = input_fingerprint(config)
    task_audit = sample / "task_preflight.json"
    if task_audit.exists():
        history = sample / "preflight_history"
        history.mkdir(exist_ok=True)
        shutil.copy2(task_audit, history / (str(time.time_ns()) + ".json"))
    task_audit.write_text(json.dumps({"status": "running", "version": VERSION, "attempt": config["task_preflight_attempt"]}))
    job_json = sample / "metadata.json"
    job_json.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    write_record(config, sample, ROOT, "preflight")
    log_path = ROOT / "logs" / f"{job['sample_id']}.log"
    with log_path.open("a", encoding="utf-8") as log:
        try:
            run_blender([BLENDER, "-b", job["asset_path"], "--python-exit-code", "1", "--python", GENERATOR, "--", "--job", job_json,
                         "--prepass-only"], log)
        except subprocess.CalledProcessError as error:
            garment_path = Path(config['prepass_report_path']).with_suffix('.garment.json')
            if garment_path.exists():
                garment = json.loads(garment_path.read_text())
                if garment.get('status') != 'passed':
                    raise RuntimeError(f"PREPASS: GARMENT_QC: needs review; report={garment_path}; failures={garment.get('failures', garment.get('error'))}") from error
            raise RuntimeError(f"PREPASS: Blender process failed: {error}") from error
        verify_pngs(prepass, frame_count)
        prepass_report = json.loads((work / "prepass_report.json").read_text(encoding="utf-8"))
        validate_task_report(prepass_report, config)
        try:
            validate_garment_report(prepass_report.get('garment_qc'), frame_count)
        except RuntimeError as error:
            raise RuntimeError(f'PREPASS: {error}') from error
        if prepass_report.get("source_animation_imported") is not False:
            raise RuntimeError("PREPASS: source animation was applied to target")
        if prepass_report.get("action_design_id") != job["action_design_id"]:
            raise RuntimeError("PREPASS: action design identity mismatch")
        trajectory = prepass_report.get("authored_trajectory_qc") or {}
        if trajectory.get("status") != "passed":
            raise RuntimeError(f"PREPASS: authored trajectory QC missing or failed: {trajectory}")
        if float(trajectory.get("amplitude_normalized", 0.0)) < 0.075:
            raise RuntimeError(f"PREPASS: action amplitude is not visibly sufficient: {trajectory}")
        terminal_policy = trajectory.get("terminal_policy")
        if terminal_policy is not None:
            if terminal_policy != "preserve_source_motion_without_forced_stop":
                raise RuntimeError(f"PREPASS: invalid terminal motion policy: {trajectory}")
        else:
            # Expanded presets use a cubic settle followed by a short stable
            # hold. Validate the measured result instead of requiring the
            # legacy observation-motion policy field.
            if int(trajectory.get("terminal_hold_frames", 0)) < 3:
                raise RuntimeError(f"PREPASS: terminal hold is too short: {trajectory}")
            terminal_limits = {
                "terminal_hold_delta_normalized": 0.004,
                "terminal_hold_velocity_normalized": 0.003,
                "terminal_root_drift_normalized": 0.002,
                "terminal_foot_drift_normalized": 0.002,
            }
            failures = {
                key: float(trajectory.get(key, float("inf")))
                for key, limit in terminal_limits.items()
                if float(trajectory.get(key, float("inf"))) > limit
            }
            if failures:
                raise RuntimeError(
                    f"PREPASS: terminal settle is not stable: {failures}; trajectory={trajectory}"
                )
        visible = [name.lower() for name in prepass_report.get("visible_character_meshes", [])]
        has_hair = any(any(token in name for token in ("hair", "ponytail", "braid", "bang")) for name in visible)
        hair_qc_status = prepass_report.get("hair_motion_qc_status", "unknown")
        if hair_qc_status == "needs_hair_adapter":
            raise RuntimeError("PREPASS: long hair has no safe dynamic representation")
        if (
            has_hair and prepass_report.get("hair_dynamic_expected")
            and float(prepass_report.get("hair_secondary_peak_deg", 0.0)) < 1.5
        ):
            raise RuntimeError("PREPASS: dynamic hair chain lacks sufficient inertial response")
        if any("outline" in name or name.startswith("shadow") for name in visible):
            raise RuntimeError("PREPASS: outline helper mesh is still visible")
        render_framing = prepass_report.get("framing")
        if job["framing"] == "halfbody" and (
            render_framing != "halfbody"
            or float(prepass_report.get("framing_vertical_fraction", 0.0)) < 0.35
        ):
            raise RuntimeError("PREPASS: halfbody framing does not preserve the upper body")
        # The exact pair was approved offline. Render workers do not spend GPU
        # time trying candidate backgrounds or invoking a VLM per sample.
        if os.environ.get("SCENE_COMPOSITE_VLM_QC", "0") == "1":
            if not SCENE_VLM_PYTHON.exists() or not SCENE_VLM_MODEL.exists():
                raise RuntimeError("PREPASS: scene VLM environment or model is missing")
            scene_qc_path = work / "scene_composite_qc.json"
            try:
                run([
                    SCENE_VLM_PYTHON, SCENE_QC,
                    "--foreground-dir", prepass,
                    "--background", job["background_path"],
                    "--background-start-sec", f"{start_sec:.6f}",
                    "--frame-count", str(frame_count),
                    "--framing", job["framing"],
                    "--model", SCENE_VLM_MODEL,
                    "--gpu", os.environ.get("SCENE_VLM_GPU", "0"),
                    "--output", scene_qc_path,
                ], log)
            except subprocess.CalledProcessError as error:
                detail = scene_qc_path.read_text(encoding="utf-8") if scene_qc_path.exists() else repr(error)
                raise RuntimeError(f"PREPASS: scene composite VLM QC failed: {detail}") from error
            config["scene_composite_vlm_qc"] = json.loads(
                scene_qc_path.read_text(encoding="utf-8")
            )
        if input_fingerprint(config) != config["task_preflight_input_fingerprint"]:
            raise RuntimeError("PREPASS: inputs changed during preflight")
        task_audit.write_text(json.dumps({
            "status": "passed", "version": VERSION,
            "attempt": config["task_preflight_attempt"],
            "input_fingerprint": config["task_preflight_input_fingerprint"],
            "source_validation": "path_size_mtime; not content hash",
            "report": prepass_report,
        }, ensure_ascii=False, indent=2))
        try:
            run_blender([BLENDER, "-b", job["asset_path"], "--python-exit-code", "1", "--python", GENERATOR, "--", "--job", job_json], log)
        except subprocess.CalledProcessError as error:
            garment_path = Path(config['render_report_path']).with_suffix('.garment.json')
            if garment_path.exists():
                garment = json.loads(garment_path.read_text())
                if garment.get('status') != 'passed':
                    raise RuntimeError(f"GARMENT_QC: final render needs review; report={garment_path}; failures={garment.get('failures', garment.get('error'))}") from error
            raise
        verify_pngs(rgba, frame_count, size=setting['resolution'], depth=16, color_type=6)
        final_report = json.loads(Path(config['render_report_path']).read_text())
        validate_task_report(final_report, config, final=True)
        validate_garment_report(final_report.get('garment_qc'), frame_count)
        if final_report['resolution'] != setting['resolution']:
            raise RuntimeError('Final render resolution does not match selected setting')
        for key, wanted in [('cycles_max_samples', setting['sampling']['samples']),
                            ('cycles_min_samples', setting['sampling']['min_samples']),
                            ('cycles_adaptive_threshold', setting['sampling']['threshold']),
                            ('cycles_denoising', setting['sampling']['denoising'])]:
            if final_report[key] != wanted:
                raise RuntimeError(f'Final render setting mismatch: {key}')

        mask_pattern = sample / "mask/%05d.png"
        run([
            "ffmpeg", "-y", "-framerate", FPS, "-start_number", "1", "-i", rgba / "%05d.png",
            "-vf", "alphaextract,format=gray16le", "-frames:v", str(frame_count),
            "-compression_level", "4", mask_pattern,
        ], log)

        # RGB composites dominate storage because natural backgrounds have
        # very high spatial entropy. High-quality 4:4:4 JPEG keeps the input
        # camera-like while the supervision alpha remains lossless 16-bit PNG.
        composite_pattern = sample / f"composite/%05d{COMPOSITE_SUFFIX}"
        filter_graph = (
            f"[0:v]fps=30,scale={config['resolution_x']}:{config['resolution_y']}:force_original_aspect_ratio=increase,"
            f"crop={config['resolution_x']}:{config['resolution_y']}[bg];"
            "[bg][1:v]overlay=0:0:shortest=1:alpha=straight,format=rgb24[out]"
        )
        run([
            "ffmpeg", "-y", "-ss", f"{start_sec:.6f}", "-i", job["background_path"],
            "-framerate", FPS, "-start_number", "1", "-i", rgba / "%05d.png",
            "-filter_complex", filter_graph, "-map", "[out]", "-frames:v", str(frame_count),
            "-q:v", COMPOSITE_JPEG_QSCALE, "-pix_fmt", "yuvj444p", composite_pattern,
        ], log)
        verify_pngs(sample / "mask", frame_count, size=setting['resolution'], depth=16, color_type=0)
        composite_files = sorted((sample / "composite").glob(f"*{COMPOSITE_SUFFIX}"))
        if len(composite_files) != frame_count:
            raise RuntimeError(
                f"composite frame count {len(composite_files)} != {frame_count}"
            )

        video_path = sample / "video/preview_960x540.mp4"
        run([
            "ffmpeg", "-y", "-framerate", FPS, "-start_number", "1", "-i", composite_pattern,
            "-vf", "scale=960:540:flags=lanczos", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", video_path,
        ], log)
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,nb_frames,avg_frame_rate", "-of", "json", video_path,
        ], check=True, text=True, capture_output=True)
        stream = json.loads(probe.stdout)["streams"][0]
        if int(stream["width"]) != 960 or int(stream["height"]) != 540 or int(stream["nb_frames"]) != frame_count:
            raise RuntimeError(f"invalid final video: {stream}")
        config["final_video_probe"] = stream
        config["storage_policy"] = {
            "composite": "1920x1080 JPEG, yuvj444p, ffmpeg qscale 3",
            "mask": "lossless 16-bit grayscale PNG",
            "preview": "960x540 H.264 CRF 24",
            "foreground_rgba_retained": False,
            "background_copy_retained": False,
        }
        config["prepass_qc"] = {
            "status": "passed", "visible_hair": has_hair,
            "garment_qc": prepass_report['garment_qc'],
            "hair_secondary_peak_deg": prepass_report.get("hair_secondary_peak_deg"),
            "neutralized_stylized_edges": prepass_report.get("neutralized_stylized_edges", []),
        }
        job_json.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(work)
    done.write_text("done\n", encoding="ascii")



def process(job, worker_id, *, output_root=None, work_root=None):
    setting = load_setting()
    root = Path(output_root) if output_root else ROOT / setting['output_directory']
    sample = root / job['split'] / job['sample_id']
    if (sample / 'DONE').exists():
        return
    previous = sample / 'render_record.json'
    if previous.exists():
        history = sample / 'record_history'
        history.mkdir(exist_ok=True)
        shutil.copy2(previous, history / (str(time.time_ns()) + '.json'))
    attempt_started_ns = time.time_ns()
    def actual_job():
        path = sample / 'metadata.json'
        return json.loads(path.read_text()) if path.exists() and path.stat().st_mtime_ns >= attempt_started_ns else dict(job, production_setting=setting)
    try:
        _process(job, worker_id, output_root=output_root, work_root=work_root)
    except Exception:
        detail = traceback.format_exc()[-12000:]
        try:
            write_record(actual_job(), sample, ROOT, 'failed', detail)
        except Exception:
            # Keep the render failure visible even if the filesystem is full.
            traceback.print_exc()
        raise
    write_record(actual_job(), sample, ROOT, 'completed')


def main():
    args = parse_args()
    recover_stale_running()
    while True:
        free_gb = shutil.disk_usage(ROOT).free / (1024 ** 3)
        if free_gb < float(os.environ.get("RENDER_MIN_FREE_GB", "180")):
            time.sleep(300)
            continue
        job = claim(args.worker_id)
        if job is None:
            if queue_has_active_work():
                time.sleep(20)
                continue
            break
        try:
            process(job, args.worker_id)
        except Exception:
            detail = traceback.format_exc()[-12000:]
            if "GARMENT_QC:" in detail:
                db = connection()
                try:
                    db.execute("UPDATE jobs SET status='paused_subject_qc', error=? WHERE subject_id=? AND status='pending'",
                               (detail, job['subject_id']))
                    db.commit()
                finally:
                    db.close()
                set_status(job['id'], 'failed_prepass', detail)
            elif "PREPASS:" in detail:
                db = connection()
                try:
                    db.execute("UPDATE jobs SET status='paused_subject_qc', error=? WHERE subject_id=? AND status='pending'",
                               (detail, job["subject_id"]))
                    db.commit()
                finally:
                    db.close()
                set_status(job["id"], "failed_prepass", detail)
            else:
                set_status(job["id"], "failed", detail)
        else:
            set_status(job["id"], "done", None)


if __name__ == "__main__":
    main()
