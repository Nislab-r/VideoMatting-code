"""Replay one recorded job to a fresh directory, without editing queue or old output."""
import argparse,json,os,sys,uuid
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--record',type=Path,required=True);p.add_argument('--output-root',type=Path,required=True);p.add_argument('--gpu',required=True);p.add_argument('--change-note',default='',help='Required when replaying with changed code/settings');a=p.parse_args()
 if a.output_root.exists():raise RuntimeError('Choose a NEW output root; original results must be retained')
 root=Path(os.environ['DATA_ROOT']).resolve();sys.path.insert(0,str(root/'tools'))
 from render_record import sha256
 record=json.loads(a.record.read_text());job=record['replay_job']
 if not all(job.get(k) for k in ['asset_path','motion_path','background_path','action_params_json']):raise RuntimeError('Record lacks a complete assigned job; cannot replay')
 for key in ['asset','motion','background']:
  source=record.get(key) or {}
  if not source.get('sha256') or not Path(source['path']).is_file() or sha256(source['path'])!=source['sha256']:raise RuntimeError('Source missing/changed: '+key+'; preserve recorded inputs')
 current={str(p.relative_to(root/'tools')):sha256(p) for p in sorted((root/'tools').rglob('*.py')) if '__pycache__' not in p.parts}
 changed=current!=record['code_files_sha256'] or sha256(root/'tools/production_quality/setting.json')!=(record.get('setting_file') or {}).get('sha256')
 if changed and not a.change_note.strip():raise RuntimeError('Code/setting changed; use --change-note to record the verified fix')
 from gpu_inventory import select_idle
 gpu=select_idle([a.gpu])[0]
 os.environ.update(CUDA_VISIBLE_DEVICES=gpu['uuid'],VIDEOMATTING_GPU_UUID=gpu['uuid'],VIDEOMATTING_GPU_SLOT='0')
 import procedural_render_worker as worker
 job['rerender_parent_record']={'path':str(a.record.resolve()),'sha256':sha256(a.record)};job['rerender_change_note']=a.change_note
 worker.process(job,'rerender-'+uuid.uuid4().hex[:8],output_root=a.output_root.resolve(),work_root=a.output_root.resolve()/'.work')
 print(str(a.output_root.resolve()/job['split']/job['sample_id']/'render_record.json'))
if __name__=='__main__':main()
