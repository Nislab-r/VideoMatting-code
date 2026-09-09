import importlib.util,json,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('record_test',ROOT/'tools/render_record.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class RecordTests(unittest.TestCase):
 def test_exact_inputs_and_unique_video_path(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'tools/production_quality').mkdir(parents=True);(root/'tools/production_quality/setting.json').write_text('{}');asset=root/'a';asset.write_text('input')
   job={'sample_id':'s1','split':'train','asset_path':str(asset),'motion_path':str(asset),'background_path':str(asset),'background_start_sec_actual':2.5,'action_params_json':'{"preset_id":"p1"}'}
   r=m.write_record(job,root/'out/train/s1',root,'completed')
   self.assertEqual(r['asset']['sha256'],m.sha256(asset));self.assertEqual(r['background_start_seconds_actual'],2.5);self.assertEqual(r['action_parameters']['preset_id'],'p1');self.assertEqual(r['video_unique_name'],'train/s1/video/preview_960x540.mp4');self.assertEqual(r['replay_job'],job)
 def test_hash_updates_when_input_changes(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x';p.write_text('a');h=m.sha256(p);p.write_text('changed');self.assertNotEqual(h,m.sha256(p))
 def test_missing_input_is_explicit(self):
  with tempfile.TemporaryDirectory() as d:self.assertEqual(m.source(Path(d)/'missing')['exists'],False)
if __name__=='__main__':unittest.main()
