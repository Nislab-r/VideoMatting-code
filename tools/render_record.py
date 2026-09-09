"""Per-video provenance; each worker writes only its own sample directory."""
import hashlib,json,os,platform,time
from pathlib import Path
_CACHE={}
def sha256(path):
 p=Path(path);st=p.stat();key=(str(p.resolve()),st.st_size,st.st_mtime_ns)
 if key not in _CACHE:
  h=hashlib.sha256()
  with p.open('rb') as f:
   for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
  _CACHE[key]=h.hexdigest()
 return _CACHE[key]
def source(path):
 if not path:return None
 p=Path(path);result={'path':str(p),'exists':p.is_file()}
 if result['exists']:
  s=p.stat();result.update(bytes=s.st_size,mtime_ns=s.st_mtime_ns,sha256=sha256(p))
 return result

def write_record(job,sample,root,status,error=None):
 sample=Path(sample);root=Path(root);sample.mkdir(parents=True,exist_ok=True)
 action=job.get('action_params') or json.loads(job.get('action_params_json') or '{}')
 code={str(p.relative_to(root/'tools')):sha256(p) for p in sorted((root/'tools').rglob('*.py')) if '__pycache__' not in p.parts}
 outputs={'video':str(sample/'video/preview_960x540.mp4'),'composite_frames':str(sample/'composite/%05d.jpg'),'alpha_frames':str(sample/'mask/%05d.png'),'metadata':str(sample/'metadata.json'),'render_report':str(sample/'render_report.json'),'task_preflight':str(sample/'task_preflight.json'),'done':str(sample/'DONE')}
 report_path=sample/'render_report.json'
 runtime_report=json.loads(report_path.read_text()) if status=='completed' and report_path.exists() else {}
 binding=root/'runtime/asset_release_binding.json'
 source_binding=root/'runtime/asset_source.json'
 record={'asset_source_binding':json.loads(source_binding.read_text()) if source_binding.exists() else None,'dataset_source_refs':job.get('dataset_source_refs'),'asset_release':json.loads(binding.read_text()) if binding.exists() else None,'runtime_action_parameters':runtime_report.get('action_params'),'visible_character_meshes':runtime_report.get('visible_character_meshes'),'hair_variant_visibility':runtime_report.get('hair_variant_visibility'),'schema_version':1,'status':status,'updated_unix':time.time(),'job_id':job.get('id'),'sample_id':job.get('sample_id'),'split':job.get('split'),'subject_id':job.get('subject_id'),'attempt_id':job.get('task_preflight_attempt'),'asset':source(job.get('asset_path')),'background':source(job.get('background_path')),'background_start_seconds_requested':job.get('background_start_sec'),'background_start_seconds_actual':job.get('background_start_sec_actual'),'motion':source(job.get('motion_path')),'motion_segment_id':job.get('motion_segment_id'),'action_design_id':job.get('action_design_id'),'action_parameters':action,'motion_source_start_frame':job.get('source_start_frame',action.get('source_frames',[None])[0]),'frame_count':job.get('frame_count'),'fps':30,'framing':job.get('framing'),'camera_angle_deg':job.get('camera_angle_deg'),'setting':job.get('production_setting'),'setting_file':source(root/'tools/production_quality/setting.json'),'code_files_sha256':code,'outputs':outputs,'video_name':(sample/'video/preview_960x540.mp4').name,'video_unique_name':str(Path(str(job.get('split')))/str(job.get('sample_id'))/'video/preview_960x540.mp4'),'error':error,'environment':{'python':platform.python_version(),'blender_bin':os.environ.get('BLENDER_BIN'),'gpu_uuid':os.environ.get('VIDEOMATTING_GPU_UUID'),'cuda_visible_devices':os.environ.get('CUDA_VISIBLE_DEVICES')},'replay_job':job,'limitations':'Three primary files are hashed. Preserve complete asset dependency packages and runtime; bit-identical GPU output is not guaranteed.'}
 tmp=sample/'render_record.json.tmp';tmp.write_text(json.dumps(record,ensure_ascii=False,indent=2));tmp.replace(sample/'render_record.json');return record
