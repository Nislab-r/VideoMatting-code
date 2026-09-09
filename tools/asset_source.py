"""Bind local extraction and runtime inputs to the declared HF dataset inventory."""
import json,shutil,os
from pathlib import Path
from render_record import sha256
REPO_ID='Renz-7/VideoMatting-Assets'
REPO_URL='https://huggingface.co/datasets/'+REPO_ID

def validate_release(assets,code):
 assets=Path(assets);expected=json.loads((Path(code)/'config/asset_source.json').read_text())
 for rel,digest in expected['manifest_sha256'].items():
  if not (assets/rel).is_file() or sha256(assets/rel)!=digest:raise RuntimeError('Asset release is incomplete or differs from the expected HF inventory: '+rel)
 receipt=assets/'.videomatting_download.json'
 downloaded=json.loads(receipt.read_text()) if receipt.exists() else {}
 if downloaded and downloaded.get('repo_id')!=REPO_ID:raise RuntimeError('Wrong dataset download receipt')
 return {**expected,'resolved_revision':downloaded.get('resolved_revision'),'origin':'hf_download' if downloaded else 'local_release','revision_note':'Null means local complete release; no HF commit has been verified.'}

def bind_workspace(assets,code,work,video):
 binding=validate_release(assets,code);binding['video_root']=str(Path(video).resolve());runtime=Path(work)/'runtime';runtime.mkdir(exist_ok=True)
 shutil.copy2(Path(assets)/'manifests/files.jsonl',runtime/'asset_source_files.jsonl')
 (runtime/'asset_source.json').write_text(json.dumps(binding,ensure_ascii=False,indent=2))
 return binding

_CACHE={}
def verify_job_sources(job,root):
 root=Path(root);binding=root/'runtime/asset_source.json';manifest=root/'runtime/asset_source_files.jsonl'
 if not binding.is_file() or not manifest.is_file():raise RuntimeError('ASSET_SOURCE: prepare or bind this workspace to the declared HF dataset first')
 b=json.loads(binding.read_text());expected=b['manifest_sha256']['manifests/files.jsonl']
 if b.get('repo_id')!=REPO_ID or sha256(manifest)!=expected:raise RuntimeError('ASSET_SOURCE: invalid dataset binding')
 if expected not in _CACHE:_CACHE[expected]={r['path']:r for r in map(json.loads,manifest.read_text().splitlines())}
 inventory=_CACHE[expected];video=Path(b['video_root']).resolve();refs={}
 for key in ['asset_path','motion_path','background_path']:
  p=Path(job[key]).resolve()
  try:rel=p.relative_to(video).as_posix()
  except ValueError:raise RuntimeError('ASSET_SOURCE: input outside bound VIDEO_ROOT: '+key)
  entry=inventory.get(rel)
  if entry is None or not p.is_file() or sha256(p)!=entry['sha256']:raise RuntimeError('ASSET_SOURCE: missing/changed/unlisted input: '+key)
  refs[key]={'repo_id':REPO_ID,'repo_url':REPO_URL,'revision':b.get('resolved_revision'),'origin':b.get('origin'),'file_manifest_sha256':expected,'archive_path':entry['archive'],'path_in_video_root':rel,'sha256':entry['sha256']}
 return refs
