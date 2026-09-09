"""Inspect, continue or explicitly retry an existing local render queue."""
import argparse,csv,datetime,fcntl,json,os,shutil,sqlite3,subprocess,sys,time
from pathlib import Path

RETRYABLE={'failed','failed_prepass','paused_subject_qc','paused_motion_redesign','waiting_adapter','waiting_missing_background'}

def connect(root):
 if not (root/'jobs.sqlite').is_file():raise RuntimeError('jobs.sqlite missing: prepare a workspace first')
 db=sqlite3.connect(root/'jobs.sqlite',timeout=60);db.row_factory=sqlite3.Row;return db

def inspect(root):
 with connect(root) as db:
  states=dict(db.execute('select status,count(*) from jobs group by status'))
  running=[dict(r) for r in db.execute("select id,worker_id,pid from jobs where status='running'")]
 return {'workspace':str(root),'statuses':states,'running':running,'free_disk_gib':round(shutil.disk_usage(root).free/1024**3,2),'minimum_free_gib':float(os.environ.get('RENDER_MIN_FREE_GB','180')),'pending':states.get('pending',0)}

def check(root):
 result=inspect(root)
 for key in ['VIDEO_ROOT','BLENDER_BIN']:
  if not os.environ.get(key):raise RuntimeError('Set '+key)
 for path in [Path(os.environ['BLENDER_BIN']),root/'tools/procedural_render_worker.py',root/'tools/production_quality/setting.json',root/'tools/asset_preflight_classified.tsv',root/'reports/pairing_reference/render_pairing_reference.sqlite',root/'reports/pairing_reference/background_roots.json']:
  if not path.is_file():raise RuntimeError('Missing '+str(path))
 for command in ['ffmpeg','ffprobe']:
  if not shutil.which(command):raise RuntimeError('Missing executable '+command)
 if 'TASK_PREFLIGHT_VERSION = 1' not in (root/'tools/procedural_render_worker.py').read_text() or not (root/'tools/production_quality/task_preflight.py').is_file():raise RuntimeError('Workspace lacks mandatory task preflight v1; migrate tools while stopped or prepare a fresh workspace. Never replace the existing queue.')
 if not (root/'tools/render_record.py').is_file():raise RuntimeError('Workspace lacks per-video records; update tools while stopped and preserve the queue')
 for name in ['asset_source.json','asset_source_files.jsonl']:
  if not (root/'runtime'/name).is_file():raise RuntimeError('Workspace is not bound to Renz-7/VideoMatting-Assets; prepare it or run bind_asset_source.py while idle')
 result['video_record_version']=1
 result['task_preflight_version']=1
 result['disk_ready']=result['free_disk_gib']>=result['minimum_free_gib']
 return result

def retry(root,ids,reason):
 if not ids or not reason.strip():raise RuntimeError('Explicit --job-ids and --resolved explanation required')
 with (root/'tools/asset_preflight_classified.tsv').open() as stream:
  registry={r['subject_id']:r for r in csv.DictReader(stream,delimiter='\t')}
 sys.path.insert(0,str(root/'tools'))
 from frozen_pairing import load_roots,resolve_background_ref
 roots=load_roots(root/'reports/pairing_reference/background_roots.json')
 with sqlite3.connect(root/'reports/pairing_reference/render_pairing_reference.sqlite') as p:
  pairs=dict(p.execute('select sample_id,background_ref from pairings'))
 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');backup=root/'reports/resume_history'/stamp;backup.mkdir(parents=True)
 with connect(root) as src,sqlite3.connect(backup/'jobs.sqlite') as dst:src.backup(dst)
 db=connect(root)
 try:
  db.execute('BEGIN IMMEDIATE')
  if db.execute("select 1 from jobs where status='running' limit 1").fetchone():raise RuntimeError('Wait for active jobs before retrying')
  changes=[]
  for ident in ids:
   row=db.execute('select * from jobs where id=?',(ident,)).fetchone()
   if row is None or row['status'] not in RETRYABLE:raise RuntimeError('Job absent or not retryable: '+str(ident))
   person=registry.get(row['subject_id'],{})
   if person.get('asset_kind')!='human':raise RuntimeError('Subject is not registered as human: '+str(ident))
   if not Path(row['asset_path']).is_file():raise RuntimeError('Missing human asset')
   ref=pairs.get(row['sample_id'])
   if not ref or not resolve_background_ref(ref,roots).is_file():raise RuntimeError('Missing frozen background')
   setting=json.loads((root/'tools/production_quality/setting.json').read_text())
   if (root/setting['output_directory']/row['split']/row['sample_id']/'DONE').exists():raise RuntimeError('DONE exists; use a new workspace to change completed output')
   changes.append({'job_id':ident,'previous_status':row['status']})
  for c in changes:
   db.execute("update jobs set status='pending',worker_id=NULL,pid=NULL,started_at=NULL,finished_at=NULL,error=NULL where id=?",(c['job_id'],))
   db.execute("insert into events(job_id,event,detail) values(?,'explicit_retry',?)",(c['job_id'],reason))
  db.commit()
 except Exception:db.rollback();raise
 finally:db.close()
 (backup/'retry.json').write_text(json.dumps({'reason':reason,'changes':changes},indent=2));return {'requeued':len(changes),'backup':str(backup)}

def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=['status','check','start','run','retry']);p.add_argument('--job-ids',type=int,nargs='+');p.add_argument('--resolved',default='');a=p.parse_args()
 root=Path(os.environ['DATA_ROOT']).resolve()
 if a.command=='status':print(json.dumps(inspect(root),indent=2));return
 if a.command=='retry':print(json.dumps(retry(root,a.job_ids,a.resolved),indent=2));return
 report=check(root)
 if a.command=='check':print(json.dumps(report,indent=2));return
 if any(r['pid'] and Path('/proc/'+str(r['pid'])).exists() for r in report['running']):raise RuntimeError('A live running job already exists; inspect it instead of starting another worker')
 if not report['disk_ready']:raise RuntimeError('Insufficient disk; worker would wait. See check output and capacity plan.')
 if not report['pending'] and not report['running']:raise RuntimeError('No pending/running work. Review blocked jobs; nothing was enabled automatically.')
 (root/'runtime').mkdir(exist_ok=True);(root/'logs').mkdir(exist_ok=True)
 if a.command=='start':
  logpath=root/'logs/render_control.log'
  with logpath.open('a') as log:
   proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'run'],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  time.sleep(.5)
  if proc.poll() is not None and proc.returncode:raise RuntimeError('Launcher exited; inspect '+str(logpath))
  print(json.dumps({'supervisor_pid':proc.pid,'log':str(logpath),'note':'Inspect status and log to track completion'}));return
 with (root/'runtime/render_control.lock').open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise RuntimeError('Another render_control supervisor holds this workspace lock')
  # Supports the existing workspace worker as well as the portable release worker.
  # The legacy runtime/bin/python must already point to Blender; never overwrite it.
  os.environ.setdefault('BLENDER_ROOT',str(root/'runtime'))
  worker=root/'tools/procedural_render_worker.py'
  if 'os.environ["BLENDER_ROOT"]' in worker.read_text() and not (Path(os.environ['BLENDER_ROOT'])/'bin/python').is_file():raise RuntimeError('Legacy BLENDER_ROOT/bin/python missing')
  raise SystemExit(subprocess.call([sys.executable,str(worker),'--worker-id','human-resume']))

if __name__=='__main__':main()
