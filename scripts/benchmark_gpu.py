"""Measure isolated shipped-demo concurrency; never touches the production queue."""
import argparse,json,os,subprocess,sys,time,signal
from pathlib import Path
from gpu_inventory import inventory,select_idle

def main():
 p=argparse.ArgumentParser();p.add_argument('--gpu',required=True);p.add_argument('--assets',type=Path,required=True);p.add_argument('--video-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--concurrency',nargs='+',type=int,default=[1,2,4]);p.add_argument('--repeats',type=int,default=2);a=p.parse_args()
 if a.output.exists():raise RuntimeError('Use a new benchmark output directory')
 if any(n<1 for n in a.concurrency) or a.repeats<1:raise ValueError('Positive concurrency and repeats required')
 gpu=select_idle([a.gpu])[0];a.output.mkdir(parents=True);here=Path(__file__).resolve().parent;results=[]
 # A separate warmup prevents initial kernel/cache startup from being compared to warm runs.
 plans=[(1,0,True)]+[(n,r,False) for n in a.concurrency for r in range(1,a.repeats+1)]
 for n,repeat,warmup in plans:
  if results:time.sleep(2)
  select_idle([gpu['uuid']]);children=[];peak=0;util=[];started=time.time()
  try:
   for slot in range(n):
    work=a.output/f'n{n}-r{repeat}-s{slot}';subprocess.run([sys.executable,str(here/'prepare_demo.py'),'--assets',str(a.assets.resolve()),'--video-root',str(a.video_root.resolve()),'--workspace',str(work.resolve())],check=True,capture_output=True)
    env=dict(os.environ,DATA_ROOT=str(work.resolve()),VIDEO_ROOT=str(a.video_root.resolve()),CUDA_VISIBLE_DEVICES=gpu['uuid'],BLENDER_THREADS=os.environ.get('BLENDER_THREADS',str(max(1,(os.cpu_count() or 1)//n))))
    log=(work/'benchmark.log').open('w');proc=subprocess.Popen([sys.executable,str(here/'run_demo.py')],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);children.append((proc,log,work))
   while any(proc.poll() is None for proc,_,_ in children):
    device=next(d for d in inventory()['devices'] if d['uuid']==gpu['uuid']);peak=max(peak,device['memory_used_mib']);util.append(device['gpu_utilization_percent']);time.sleep(1)
   elapsed=time.time()-started;success=all(p.returncode==0 and (w/'smoke/demo_validation.json').is_file() for p,_,w in children)
   results.append({'concurrency':n,'repeat':repeat,'warmup':warmup,'success':success,'wall_seconds':elapsed,'completed_demos_per_hour':n*3600/elapsed if success else 0,'peak_board_memory_mib':peak,'mean_gpu_utilization_percent':sum(util)/max(1,len(util)),'memory_total_mib':gpu['memory_total_mib']})
   (a.output/'results.json').write_text(json.dumps({'gpu':gpu,'workload':'one shipped subject, full 120-frame guard, one final 2K frame plus composition','not_full_library_capacity':True,'results':results},indent=2))
   if not success:raise RuntimeError('Benchmark failed; inspect case logs. Do not increase concurrency.')
  finally:
   for proc,log,_ in children:
    if proc.poll() is None:os.killpg(proc.pid,signal.SIGTERM);proc.wait()
    log.close()
 print(str(a.output/'results.json'))

if __name__=='__main__':main()
