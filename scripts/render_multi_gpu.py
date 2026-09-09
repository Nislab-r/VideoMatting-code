"""One local supervisor, explicit physical GPUs, configurable worker slots per GPU."""
import argparse,fcntl,json,os,signal,subprocess,sys,time
from pathlib import Path
from gpu_inventory import select_idle
from render_control import check

def layout(gpus,slots):
 if len(slots)==1:slots=slots*len(gpus)
 if len(slots)!=len(gpus) or any(s<1 for s in slots):raise ValueError('Use one positive slot count for all GPUs, or one per GPU')
 return [(gpu,slot) for gpu,count in zip(gpus,slots) for slot in range(count)]

def main():
 p=argparse.ArgumentParser();p.add_argument('--gpus',nargs='+',required=True);p.add_argument('--slots',nargs='+',type=int,default=[1]);p.add_argument('--start',action='store_true');a=p.parse_args();root=Path(os.environ['DATA_ROOT']).resolve()
 report=check(root);worker=root/'tools/procedural_render_worker.py'
 if 'MULTI_GPU_QUEUE_VERSION = 1' not in worker.read_text():raise RuntimeError('Workspace worker is the old serial version; do not overwrite active tools. Prepare a new workspace or perform a reviewed migration first.')
 if report['running']:raise RuntimeError('Inspect running/stale jobs before starting a new supervisor')
 if not report['pending']:raise RuntimeError('No pending jobs. No blocked jobs were enabled.')
 if not report['disk_ready']:raise RuntimeError('Insufficient free disk for configured threshold')
 selected=select_idle(a.gpus);workers=layout(selected,a.slots)
 (root/'runtime').mkdir(exist_ok=True);(root/'logs').mkdir(exist_ok=True)
 if a.start:
  args=[sys.executable,str(Path(__file__).resolve()),'--gpus',*a.gpus,'--slots',*[str(v) for v in a.slots]]
  with (root/'logs/multi_gpu_supervisor.log').open('a') as log:proc=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  time.sleep(.5)
  if proc.poll() not in [None,0]:raise RuntimeError('Supervisor failed; inspect multi_gpu_supervisor.log')
  print(json.dumps({'supervisor_pid':proc.pid,'requested_workers':len(workers)}));return
 children=[]
 def stop(signum,frame):
  for proc,_ in children:
   if proc.poll() is None:os.killpg(proc.pid,signal.SIGTERM)
  raise SystemExit(128+signum)
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 with (root/'runtime/render_control.lock').open('a') as lock:
  try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise RuntimeError('Another managed supervisor is active')
  try:
   for gpu,slot in workers:
    name=f"gpu-{gpu['index']}-slot-{slot}";env=dict(os.environ,CUDA_VISIBLE_DEVICES=gpu['uuid'],VIDEOMATTING_GPU_UUID=gpu['uuid'],VIDEOMATTING_GPU_SLOT=str(slot),BLENDER_THREADS=os.environ.get('BLENDER_THREADS',str(max(1,(os.cpu_count() or 1)//len(workers)))))
    log=(root/'logs'/f'{name}.log').open('a')
    proc=subprocess.Popen([sys.executable,str(worker),'--worker-id',name],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);children.append((proc,log))
   (root/'runtime/multi_gpu_launch.json').write_text(json.dumps({'workers':[{'pid':p.pid,'gpu_uuid':g['uuid'],'slot':s} for (p,_),(g,s) in zip(children,workers)]},indent=2))
   codes=[p.wait() for p,_ in children]
   if any(codes):raise RuntimeError('One or more workers exited unsuccessfully: '+str(codes))
  finally:
   for proc,log in children:
    if proc.poll() is None:os.killpg(proc.pid,signal.SIGTERM);proc.wait()
    log.close()

if __name__=='__main__':main()
