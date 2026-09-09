"""Run isolated CPU human audits; no production queue is opened or changed."""
import argparse,concurrent.futures,csv,hashlib,json,os,signal,subprocess,time
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--assets',type=Path,required=True);p.add_argument('--video-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--workers',type=int,default=4);p.add_argument('--threads',type=int,default=4);p.add_argument('--timeout',type=int,default=600);p.add_argument('--subject-ids',nargs='+');a=p.parse_args()
 if a.output.exists():raise RuntimeError('Use a new audit output directory')
 if min(a.workers,a.threads,a.timeout)<1:raise ValueError('Positive resource limits required')
 rows=[json.loads(s) for s in (a.assets/'manifests/subjects.jsonl').read_text().splitlines()]
 if len(rows)!=159 or len({r['subject_id'] for r in rows})!=159 or any(r['asset_kind']!='human' for r in rows):raise RuntimeError('Expected 159 unique human entries')
 if a.subject_ids:
  wanted=set(a.subject_ids);rows=[r for r in rows if r['subject_id'] in wanted]
  if len(rows)!=len(wanted):raise RuntimeError('Unknown subject ID')
 a.output.mkdir(parents=True);code=Path(__file__).resolve().parents[1];started=time.time();reports=[]
 def run(row):
  token=hashlib.sha256(row['subject_id'].encode()).hexdigest()[:16];out=a.output/token;out.mkdir();source=dict(row);source['asset_path']=str((a.video_root/row['asset_path']).resolve());(out/'subject.json').write_text(json.dumps(source,ensure_ascii=False))
  env=dict(os.environ,DATA_ROOT=str(code),VIDEO_ROOT=str(a.video_root.resolve()),MOTION_OBSERVATION_ROOT=str(a.video_root.resolve()/'motion_observation_v2'),CUDA_VISIBLE_DEVICES='');cmd=[os.environ['BLENDER_BIN'],'--background','--factory-startup','--disable-autoexec','-t',str(a.threads),'--python-exit-code','1','--python',str(code/'tools/subject_preflight_blender.py'),'--','--subject-json',str(out/'subject.json'),'--output',str(out),'--example',str(a.assets/'metadata/examples/standard_smoke.json')]
  with (out/'blender.log').open('w') as log:
   proc=subprocess.Popen(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
   try:ret=proc.wait(timeout=a.timeout);reason=None
   except subprocess.TimeoutExpired:
    os.killpg(proc.pid,signal.SIGTERM)
    try:proc.wait(timeout=10)
    except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
    ret=-1;reason='timeout'
  path=out/'report.json'
  try:report=json.loads(path.read_text())
  except (FileNotFoundError,json.JSONDecodeError):report={'version':'subject_preflight_v1','subject_id':row['subject_id'],'checks':{},'previews':[]}
  if ret or 'status' not in report:report.update(status='inspection_failed',process_exit_code=ret,failure_reason=reason or 'process_failed')
  report['asset_path']=row['asset_path'];report['report_directory']=token;report['historical_status']=row['status'];report['production_eligible']=False
  path.write_text(json.dumps(report,ensure_ascii=False,indent=2));return report
 with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
  futures=[pool.submit(run,row) for row in rows]
  for f in concurrent.futures.as_completed(futures):
   reports.append(f.result());(a.output/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in sorted(reports,key=lambda r:r['subject_id'])))
   (a.output/'progress.json').write_text(json.dumps({'completed':len(reports),'total':len(rows),'elapsed_seconds':time.time()-started,'complete':len(reports)==len(rows)}));print('AUDITED',len(reports),'/',len(rows),flush=True)
if __name__=='__main__':main()
