#!/usr/bin/env python3
"""Portable, pre-approved foreground/background pairing lookup."""

import json
import os
import re
import sqlite3
from pathlib import Path


PAIRING_REFERENCE_VERSION = "frozen-pairing-v1-6-3-3"
ROOT_TOKEN = re.compile(r"^\$\{([A-Z0-9_]+)\}(.*)$")


def desired_framing(action_params_json):
    params = json.loads(action_params_json or "{}")
    index = int(params.get("subject_action_index", 0))
    if not 0 <= index < 12:
        raise ValueError(f"subject_action_index outside [0, 11]: {index}")
    if index < 6:
        return "fullbody"
    if index < 9:
        return "halfbody"
    return "closeup_hair"


def _expand_root(value):
    match = ROOT_TOKEN.match(value)
    if not match:
        return Path(value).expanduser()
    name, suffix = match.groups()
    if name not in os.environ:
        raise RuntimeError(f"background root requires environment variable {name}")
    return Path(os.environ[name] + suffix).expanduser()


def load_roots(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {alias: _expand_root(value) for alias, value in data["roots"].items()}


def resolve_background_ref(background_ref, roots):
    alias, separator, relative = background_ref.partition("://")
    if not separator or alias not in roots:
        raise RuntimeError(f"unknown portable background reference: {background_ref}")
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise RuntimeError(f"unsafe relative background path: {background_ref}")
    return roots[alias] / relative_path


def assign_frozen_pairing(job_db, job, manifest_path, roots_path):
    manifest = sqlite3.connect(manifest_path, timeout=60)
    manifest.row_factory = sqlite3.Row
    try:
        row = manifest.execute(
            "SELECT * FROM pairings WHERE sample_id=?", (job["sample_id"],)
        ).fetchone()
    finally:
        manifest.close()
    if row is None:
        raise RuntimeError(f"no frozen background pairing for {job['sample_id']}")
    if row["manifest_version"] != PAIRING_REFERENCE_VERSION:
        raise RuntimeError(
            f"unsupported pairing manifest version for {job['sample_id']}: "
            f"{row['manifest_version']}"
        )
    if row["approval_status"] != "approved":
        raise RuntimeError(
            f"frozen background pairing is not approved for {job['sample_id']}: "
            f"{row['approval_status']}"
        )
    roots = load_roots(roots_path)
    background_path = resolve_background_ref(row["background_ref"], roots)
    if not background_path.is_file():
        raise RuntimeError(f"resolved background does not exist: {background_path}")
    framing = desired_framing(job["action_params_json"])
    if framing != row["framing"]:
        raise RuntimeError(
            f"frozen framing mismatch for {job['sample_id']}: {framing} != {row['framing']}"
        )
    params = json.loads(job["action_params_json"] or "{}")
    params["scene_pairing"] = {
        "version": PAIRING_REFERENCE_VERSION,
        "background_ref": row["background_ref"],
        "background_start_sec": row["background_start_sec"],
        "compatibility_score": row["compatibility_score"],
        "precheck_method": row["precheck_method"],
        "placement": json.loads(row["placement_json"]),
    }
    params_json = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    job_db.execute(
        "UPDATE jobs SET background_path=?,background_start_sec=?,framing=?,"
        "background_motion_hint=?,action_params_json=? WHERE id=?",
        (
            str(background_path), row["background_start_sec"], framing,
            "frozen_offline_prechecked", params_json, job["id"],
        ),
    )
    return {
        **job,
        "background_path": str(background_path),
        "background_start_sec": row["background_start_sec"],
        "framing": framing,
        "background_motion_hint": "frozen_offline_prechecked",
        "action_params_json": params_json,
    }
