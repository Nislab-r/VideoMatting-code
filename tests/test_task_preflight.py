import copy, importlib.util, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('task_preflight',ROOT/'tools/production_quality/task_preflight.py');gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
class TaskPreflightTests(unittest.TestCase):
 def setUp(self):
  self.job=dict(subject_id='human',motion_segment_id='m',action_design_id='a',frame_count=3,task_preflight_attempt='fresh',task_preflight_input_fingerprint='hash')
  self.report=dict(self.job,status='passed_prepass',task_preflight_version=gate.VERSION,source_animation_imported=False,rendered_frame_subset=[1,2,3],geometry_motion_qc={'status':'passed'},limb_deformation_qc={'status':'passed','frame_count':3},collision_gate={'status':'passed'})
 def test_current_report(self):gate.validate_task_report(self.report,self.job)
 def test_stale_or_wrong_task(self):
  for key in self.job:
   r=copy.deepcopy(self.report);r[key]='stale'
   with self.assertRaises(RuntimeError):gate.validate_task_report(r,self.job)
 def test_missing_checks_and_frames(self):
  for key in ('geometry_motion_qc','limb_deformation_qc','collision_gate','rendered_frame_subset','task_preflight_version'):
   r=copy.deepcopy(self.report);r.pop(key)
   with self.assertRaises(RuntimeError):gate.validate_task_report(r,self.job)
 def test_bypasses(self):
  for field,value in [('debug_skip_geometry_qc',True),('prepass_render_frames',[1,3]),('prepass_render_frames',[])]:
   with self.assertRaises(RuntimeError):gate.reject_bypasses(dict(self.job,**{field:value}))
 def test_input_changes(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'input';p.write_text('original');j=dict(self.job,asset_path=str(p),motion_path=str(p),background_path=str(p));h=gate.input_fingerprint(j);j['task_preflight_input_fingerprint']=h
   self.assertEqual(h,gate.input_fingerprint(j));p.write_text('changed size');self.assertNotEqual(h,gate.input_fingerprint(j))
 def test_worker_gate_before_final(self):
  s=(ROOT/'tools/procedural_render_worker.py').read_text();self.assertLess(s.index('validate_task_report(prepass_report, config)'),s.index('validate_task_report(final_report, config, final=True)'));self.assertIn('inputs changed during preflight',s)
if __name__=='__main__':unittest.main()
