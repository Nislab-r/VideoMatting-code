"""Read NVIDIA devices and choose only explicitly requested, currently idle GPUs."""
import csv,io,json,subprocess

def inventory():
 result=subprocess.run(['nvidia-smi','--query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu','--format=csv,noheader,nounits'],check=True,capture_output=True,text=True)
 devices=[]
 for row in csv.reader(io.StringIO(result.stdout)):
  index,uuid,name,total,used,util=[v.strip() for v in row]
  devices.append({'index':int(index),'uuid':uuid,'name':name,'memory_total_mib':int(total),'memory_used_mib':int(used),'gpu_utilization_percent':int(util)})
 result=subprocess.run(['nvidia-smi','--query-compute-apps=gpu_uuid,pid,used_gpu_memory','--format=csv,noheader,nounits'],check=True,capture_output=True,text=True)
 apps=[]
 for row in csv.reader(io.StringIO(result.stdout)):
  if not row:continue
  uuid,pid,memory=[v.strip() for v in row];apps.append({'uuid':uuid,'pid':int(pid),'memory_mib':int(memory) if memory.isdigit() else None})
 return {'devices':devices,'compute_processes':apps}

def select_idle(requested):
 data=inventory();selected=[]
 for key in requested:
  matches=[d for d in data['devices'] if key in [str(d['index']),d['uuid']]]
  if len(matches)!=1:raise RuntimeError('Unknown GPU '+key)
  d=matches[0]
  if d['uuid'] in {x['uuid'] for x in selected}:raise RuntimeError('Duplicate GPU selection')
  if any(a['uuid']==d['uuid'] for a in data['compute_processes']) or d['gpu_utilization_percent']>10:raise RuntimeError('GPU occupied; do not benchmark or launch on it: '+str(d['index']))
  selected.append(d)
 return selected

if __name__=='__main__':print(json.dumps(inventory(),indent=2))
