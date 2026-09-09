"""Reject jobs outside the prepared, human-only source registry."""
import csv
import os
from pathlib import Path


def verify_human_job(job):
    path=Path(os.environ['DATA_ROOT'])/'tools/asset_preflight_classified.tsv'
    with path.open() as stream:
        rows=list(csv.DictReader(stream,delimiter='\t'))
    if not rows or any(row['asset_kind']!='human' for row in rows):
        raise RuntimeError('VideoMatting requires a human-only asset registry')
    matches=[row for row in rows if row['subject_id']==job.get('subject_id')]
    if len(matches)!=1:
        raise RuntimeError('Subject is not uniquely registered as human')
    expected=Path(matches[0]['asset_path']).resolve()
    supplied=Path(job.get('asset_path','')).resolve()
    if supplied!=expected or not supplied.is_file():
        raise RuntimeError('Human asset path differs from the prepared registry or is missing')
