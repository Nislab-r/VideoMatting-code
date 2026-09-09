"""Exercise worker prepass/final ordering with isolated Blender/FFmpeg substitutes."""
import contextlib,importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
class TaskWorkerTests(unittest.TestCase):
 def exercise(self,stale):
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'DATA_ROOT':d,'VIDEO_ROOT':d,'MOTION_OBSERVATION_ROOT':d,'BLENDER_BIN':sys.executable,'SCENE_COMPOSITE_VLM_QC':'0'}):
   root=Path(d);(root/'logs').mkdir();asset=root/'asset';asset.write_text('input')
   spec=importlib.util.spec_from_file_location('isolated_task_worker',ROOT/'tools/procedural_render_worker.py');w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
   params={'design_status':'expanded_safe_preset_v1_assigned_jit','source_bone_rotations_used':False,'motion_solver':'target_native_safe_preset_ik_v1','expanded_blueprint':{'catalog_version':'test'}}
   job=dict(subject_id='human',motion_segment_id='motion',action_design_id='design',frame_count=3,asset_path=str(asset),motion_path=str(asset),background_path=str(asset),background_start_sec=0,action_params_json=json.dumps(params),split='train',sample_id='sample',framing='fullbody')
   calls=[]
   def blender(command,log):
    calls.append(command)
    if '--prepass-only' not in command:raise RuntimeError('FINAL_REACHED')
    config=json.loads((root/'output/train/sample/metadata.json').read_text())
    report={k:config[k] for k in ['subject_id','motion_segment_id','action_design_id','frame_count','task_preflight_attempt','task_preflight_input_fingerprint']}
    report.update(status='passed_prepass',task_preflight_version='task_preflight_v1',source_animation_imported=False,rendered_frame_subset=[1,2,3],geometry_motion_qc={'status':'passed'},limb_deformation_qc={'status':'passed','frame_count':3},collision_gate={'status':'passed'},authored_trajectory_qc={'status':'passed','amplitude_normalized':.1,'terminal_policy':'preserve_source_motion_without_forced_stop'})
    if stale:report['task_preflight_attempt']='previous-attempt'
    Path(config['prepass_report_path']).write_text(json.dumps(report))
   with contextlib.ExitStack() as stack:
    for name,value in [('verify_job_sources',{}),('verify_human_job',None),('load_setting',{'output_directory':'output','id':'test'}),('job_settings',{}),('ffprobe_duration',10),('background_camera_motion',{}),('background_statistics',{}),('verify_pngs',None),('validate_garment_report',None)]:stack.enter_context(patch.object(w,name,return_value=value))
    stack.enter_context(patch.object(w.subprocess,'run',return_value=SimpleNamespace(stdout=b'')))
    stack.enter_context(patch.object(w,'run_blender',side_effect=blender))
    with self.assertRaisesRegex(RuntimeError,'fresh report identity mismatch' if stale else 'FINAL_REACHED'):w.process(job,'test')
   record=json.loads((root/'output/train/sample/render_record.json').read_text());self.assertEqual(record['status'],'failed');self.assertEqual(record['sample_id'],'sample');self.assertTrue(record['asset']['sha256']);self.assertIn('FINAL_REACHED' if not stale else 'identity mismatch',record['error'])
   self.assertEqual(len(calls),1 if stale else 2)
   audit=json.loads((root/'output/train/sample/task_preflight.json').read_text());self.assertEqual(audit['status'],'running' if stale else 'passed')
 def test_stale_prepass_never_reaches_final(self):self.exercise(True)
 def test_verified_prepass_persisted_before_final(self):self.exercise(False)
if __name__=='__main__':unittest.main()
