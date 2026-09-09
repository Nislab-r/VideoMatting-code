"""Render the shipped one-frame example and verify alpha plus background composite."""
import json,os,struct,subprocess,sys,time
from pathlib import Path
root=Path(os.environ['DATA_ROOT']);path=root/'examples/standard_smoke.json';job=json.loads(path.read_text());out=root/'smoke'
if out.exists():raise RuntimeError('Use a fresh demo workspace; previous smoke output is preserved')
root.mkdir(parents=True,exist_ok=True);sys.path.insert(0,str(root/'tools'))
from production_quality import background_statistics
bg=job['action_params']['scene_pairing']['background_path'];start=job['action_params']['scene_pairing']['background_start_sec']
from asset_source import verify_job_sources
job['background_path']=bg
job['dataset_source_refs']=verify_job_sources(job,root)
r=subprocess.run(['ffmpeg','-v','error','-ss',str(start),'-i',bg,'-vf','scale=192:108','-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],capture_output=True,check=True)
job['background_light_statistics']=background_statistics(r.stdout);path.write_text(json.dumps(job,ensure_ascii=False,indent=2));started=time.time()
threads=[]
if os.environ.get('BLENDER_THREADS'):
 if not os.environ['BLENDER_THREADS'].isdigit() or int(os.environ['BLENDER_THREADS'])<1:raise ValueError('BLENDER_THREADS must be positive')
 threads=['-t',os.environ['BLENDER_THREADS']]
with (root/'demo.log').open('w') as log:
 subprocess.run([os.environ['BLENDER_BIN'],'--factory-startup',*threads,'-b',job['asset_path'],'--disable-autoexec','--python-exit-code','1','--python',str(root/'tools/procedural_reference_production.py'),'--','--job',str(path)],stdout=log,stderr=subprocess.STDOUT,check=True)
rgba=out/'rgba/00001.png';header=rgba.read_bytes()[:33];assert struct.unpack('>IIBB',header[16:26])==(1920,1080,16,6)
report=json.loads((out/'report.json').read_text());assert report['garment_qc']['status']=='passed'
subprocess.run(['ffmpeg','-v','error','-i',str(rgba),'-vf','alphaextract,format=gray16le','-frames:v','1',str(out/'alpha.png')],check=True)
subprocess.run(['ffmpeg','-v','error','-ss',str(start),'-i',bg,'-i',str(rgba),'-filter_complex','[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080[b];[b][1:v]overlay=0:0:alpha=straight[out]','-map','[out]','-frames:v','1',str(out/'composite.png')],check=True)
for p,expected in [(out/'alpha.png',(1920,1080,16,0))]:assert struct.unpack('>IIBB',p.read_bytes()[16:26])==expected
(out/'demo_validation.json').write_text(json.dumps({'status':'passed','rendered_frames':1,'authored_frames':job['frame_count'],'garment_status':report['garment_qc']['status'],'elapsed_seconds':time.time()-started,'full_queue_started':False},indent=2));print(str(out/'demo_validation.json'))
