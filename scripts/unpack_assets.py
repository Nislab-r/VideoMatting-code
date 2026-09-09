"""Verify and safely unpack VideoMatting source shards into a fresh directory."""
import sys
import argparse,hashlib,json,os,shutil,subprocess,tarfile
from pathlib import Path

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()

def main():
 p=argparse.ArgumentParser();p.add_argument('--assets',type=Path,required=True);p.add_argument('--video-root',type=Path,required=True);p.add_argument('--profile',choices=['full','demo'],default='full');a=p.parse_args()
 code_root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(code_root/'tools'))
 from asset_source import validate_release
 validate_release(a.assets,code_root)
 root=a.video_root.resolve();root.mkdir(parents=True,exist_ok=True)
 if any(root.iterdir()):raise RuntimeError('Use a new empty video-root; existing files will not be overwritten')
 rows=[json.loads(s) for s in (a.assets/'manifests/archives.jsonl').read_text().splitlines()]
 selected=None
 if a.profile=='demo':
  job=json.loads((a.assets/'metadata/examples/standard_smoke.json').read_text())
  needed={job['asset_path'].removeprefix('${VIDEO_ROOT}/'),job['motion_path'].removeprefix('${VIDEO_ROOT}/'),job['action_params']['scene_pairing']['background_path'].removeprefix('${VIDEO_ROOT}/')}
  files=[json.loads(s) for s in (a.assets/'manifests/files.jsonl').read_text().splitlines()]
  by_path={r['path']:r for r in files}
  if not needed<=by_path.keys():raise RuntimeError('Demo references files absent from asset manifest')
  subject_archive=by_path[job['asset_path'].removeprefix('${VIDEO_ROOT}/')]['archive']
  selected=needed|{r['path'] for r in files if r['archive']==subject_archive}
  archives={by_path[x]['archive'] for x in selected};rows=[r for r in rows if r['path'] in archives]
 if shutil.disk_usage(root).free<sum(x['uncompressed_bytes'] for x in rows)+5*1024**3:raise RuntimeError('Insufficient free space for extraction')
 for row in rows:
  archive=(a.assets/row['path']).resolve()
  if not archive.is_relative_to(a.assets.resolve()) or sha(archive)!=row['sha256']:raise RuntimeError('Archive path/hash mismatch')
  proc=subprocess.Popen(['zstd','-q','-d','-c',str(archive)],stdout=subprocess.PIPE)
  try:
   with tarfile.open(fileobj=proc.stdout,mode='r|') as tar:
    for member in tar:
     rel=Path(member.name);target=(root/rel).resolve()
     if rel.is_absolute() or '..' in rel.parts or not target.is_relative_to(root):raise RuntimeError('Unsafe archive member')
     if selected is not None and member.name.removeprefix('./') not in selected:continue
     if member.isdir():target.mkdir(parents=True,exist_ok=True)
     elif member.isfile():
      target.parent.mkdir(parents=True,exist_ok=True)
      with tar.extractfile(member) as source,target.open('xb') as output:shutil.copyfileobj(source,output,8*1024*1024)
     elif member.islnk():
      link=(root/member.linkname).resolve()
      if not link.is_relative_to(root) or not link.is_file():raise RuntimeError('Unsafe hard link')
      target.parent.mkdir(parents=True,exist_ok=True);os.link(link,target)
     else:raise RuntimeError('Symlinks and special archive entries are not accepted')
   if proc.wait():raise RuntimeError('Archive decompression failed')
  finally:
   if proc.poll() is None:proc.terminate();proc.wait()
  print(row['path'],flush=True)
 if selected is not None:
  for rel in selected:
   if sha(root/rel)!=by_path[rel]['sha256']:raise RuntimeError('Extracted demo file hash mismatch: '+rel)
 (root/'.videomatting_profile.json').write_text(json.dumps({'profile':a.profile,'archives_verified':len(rows)}))

if __name__=='__main__':main()
