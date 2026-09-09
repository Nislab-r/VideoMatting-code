"""Prepare one isolated example using only extracted release assets; no queue."""
import sys
import argparse,csv,io,json,shutil
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--assets',type=Path,required=True);p.add_argument('--video-root',type=Path,required=True);p.add_argument('--workspace',type=Path,required=True);a=p.parse_args()
assets=a.assets.resolve();video=a.video_root.resolve();work=a.workspace.resolve()
code=Path(__file__).resolve().parents[1];sys.path.insert(0,str(code/'tools'))
from asset_source import validate_release,bind_workspace
validate_release(assets,code)
if work.exists():raise RuntimeError('Use a fresh demo workspace')
job=json.loads((assets/'metadata/examples/standard_smoke.json').read_text())
text=json.dumps(job,ensure_ascii=False).replace('${DATA_ROOT}',str(work)).replace('${VIDEO_ROOT}',str(video));job=json.loads(text)
for path in [job['asset_path'],job['motion_path'],job['action_params']['scene_pairing']['background_path']]:
 if not Path(path).is_file():raise RuntimeError('Missing demo input '+path)
reader=csv.DictReader(io.StringIO((assets/'metadata/asset_preflight_classified.tsv').read_text()),delimiter='\t');fields=reader.fieldnames;rows=[r for r in reader if r['subject_id']==job['subject_id']]
if len(rows)!=1 or rows[0]['asset_kind']!='human':raise RuntimeError('Demo human registry mismatch')
work.mkdir(parents=True);shutil.copytree(Path(__file__).resolve().parents[1]/'tools',work/'tools',ignore=shutil.ignore_patterns('__pycache__'))
with (work/'tools/asset_preflight_classified.tsv').open('w') as f:
 writer=csv.DictWriter(f,fieldnames=fields,delimiter='\t');writer.writeheader();writer.writerow({k:v.replace('${VIDEO_ROOT}',str(video)) for k,v in rows[0].items()})
bind_workspace(assets,code,work,video)
(work/'examples').mkdir();(work/'examples/standard_smoke.json').write_text(json.dumps(job,ensure_ascii=False,indent=2))
print(json.dumps({'workspace':str(work),'profile':'demo','subjects':1,'queue_created':False}))
