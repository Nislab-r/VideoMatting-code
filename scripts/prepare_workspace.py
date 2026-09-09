"""Materialize portable metadata and code without starting rendering."""
import sys
import argparse,csv,gzip,json,shutil,sqlite3
from pathlib import Path

def expand(text,video,work):return text.replace('${VIDEO_ROOT}',str(video)).replace('${DATA_ROOT}',str(work))

def relocate_db(path,video,work):
 db=sqlite3.connect(path)
 for (table,) in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'").fetchall():
  q='"'+table.replace('"','""')+'"'
  for column in db.execute(f'pragma table_info({q})').fetchall():
   c='"'+column[1].replace('"','""')+'"'
   for token,value in [('${VIDEO_ROOT}',str(video)),('${DATA_ROOT}',str(work))]:
    db.execute(f'update {q} set {c}=replace({c},?,?) where typeof({c})=\'text\' and instr({c},?)>0',(token,value,token))
 db.commit();db.close()

def main():
 p=argparse.ArgumentParser();p.add_argument('--assets',type=Path,required=True);p.add_argument('--video-root',type=Path,required=True);p.add_argument('--workspace',type=Path,required=True);a=p.parse_args()
 code_root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(code_root/'tools'))
 from asset_source import validate_release
 validate_release(a.assets,code_root)
 assets=a.assets.resolve();video=a.video_root.resolve();work=a.workspace.resolve();code=Path(__file__).resolve().parents[1]
 if work.exists():raise RuntimeError('Use a fresh workspace; existing workspaces are not overwritten')
 rows=[json.loads(s) for s in (assets/'manifests/subjects.jsonl').read_text().splitlines()]
 if len(rows)!=159 or len({x['subject_id'] for x in rows})!=159 or any(x['asset_kind']!='human' for x in rows):raise RuntimeError('Expected exactly 159 unique human entries')
 missing=[x['asset_path'] for x in rows if not (video/x['asset_path']).is_file()]
 if missing:raise RuntimeError(f'Missing {len(missing)} human assets; unpack source archives first')
 with sqlite3.connect(f'file:{assets}/metadata/queue_template.sqlite?mode=ro',uri=True) as db:
  if db.execute('select count(distinct subject_id),count(*) from jobs').fetchone()!=(159,1908):raise RuntimeError('Invalid human queue cardinality')
  if db.execute("select count(*) from jobs where status!='waiting_release_review'").fetchone()[0]:raise RuntimeError('Release queue contains active or completed states')
 if (video/'motion_observation_v2').exists() or (video/'motion_inventory').exists():raise RuntimeError('Metadata destinations already exist; use a fresh video-root to protect existing data')
 work.mkdir(parents=True);shutil.copytree(code/'tools',work/'tools',ignore=shutil.ignore_patterns('__pycache__'))
 for d in ['logs','runtime','reports/pairing_reference']:(work/d).mkdir(parents=True,exist_ok=True)
 shutil.copy2(assets/'release/code_release.json',work/'runtime/asset_release_binding.json')
 from asset_source import bind_workspace
 bind_workspace(assets,code,work,video)
 (work/'tools/asset_preflight_classified.tsv').write_text(expand((assets/'metadata/asset_preflight_classified.tsv').read_text(),video,work))
 (video/'motion_inventory').mkdir(parents=True)
 (video/'motion_inventory/motion_frame_ranges.tsv').write_text(expand((assets/'metadata/motion_frame_ranges.tsv').read_text(),video,work))
 for source in (assets/'metadata/video').rglob('*'):
  if not source.is_file():continue
  dest=video/source.relative_to(assets/'metadata/video');dest.parent.mkdir(parents=True,exist_ok=True)
  if source.suffix=='.gz':dest.write_bytes(gzip.compress(expand(gzip.decompress(source.read_bytes()).decode(),video,work).encode(),mtime=0))
  elif source.suffix=='.sqlite':shutil.copy2(source,dest);relocate_db(dest,video,work)
  else:dest.write_text(expand(source.read_text(),video,work))
 shutil.copy2(assets/'metadata/queue_template.sqlite',work/'jobs.sqlite');relocate_db(work/'jobs.sqlite',video,work)
 for source in (assets/'metadata/pairing_reference').iterdir():
  dest=work/'reports/pairing_reference'/source.name;shutil.copy2(source,dest)
  if dest.suffix=='.sqlite':relocate_db(dest,video,work)
 examples=assets/'metadata/examples'
 if examples.exists():
  (work/'examples').mkdir()
  for source in examples.glob('*.json'):(work/'examples'/source.name).write_text(expand(source.read_text(),video,work))
 report={'subjects':159,'queue_jobs':1908,'queue_status':'waiting_release_review','missing_backgrounds':len((assets/'manifests/missing_backgrounds.jsonl').read_text().splitlines()),'rendering_started':False}
 (work/'setup_report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))

if __name__=='__main__':main()
