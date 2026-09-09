import importlib.util,json,sqlite3,tempfile,unittest,shutil,os,sys,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('render_control',ROOT/'scripts/render_control.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class ResumeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);r=self.root
  (r/'tools/production_quality').mkdir(parents=True);(r/'reports/pairing_reference').mkdir(parents=True)
  shutil.copy2(ROOT/'tools/frozen_pairing.py',r/'tools/frozen_pairing.py')
  (r/'person.blend').touch();(r/'background.mp4').touch()
  (r/'tools/production_quality/setting.json').write_text(json.dumps({'output_directory':'output'}))
  (r/'tools/asset_preflight_classified.tsv').write_text('subject_id\tasset_kind\tstatus\nperson\thuman\tsupported_v6\n')
  (r/'reports/pairing_reference/background_roots.json').write_text(json.dumps({'roots':{'test':str(r)}}))
  with sqlite3.connect(r/'reports/pairing_reference/render_pairing_reference.sqlite') as db:db.execute('create table pairings(sample_id text, background_ref text)');db.executemany('insert into pairings values(?,?)',[(f's{i}','test://background.mp4') for i in [1,2]])
  with sqlite3.connect(r/'jobs.sqlite') as db:
   db.execute('create table jobs(id integer,subject_id text,asset_path text,sample_id text,split text,status text,worker_id text,pid integer,started_at text,finished_at text,error text,attempts integer)')
   db.executemany('insert into jobs values(?,?,?,?,?,?,NULL,NULL,NULL,NULL,?,?)',[(1,'person',str(r/'person.blend'),'s1','train','paused_motion_redesign','old failure',2),(2,'person',str(r/'person.blend'),'s2','train','done',None,1)])
   db.execute('create table events(job_id integer,event text,detail text)')
 def tearDown(self):self.temp.cleanup()
 def test_retry_preserves_done_attempts_and_backup(self):
  result=m.retry(self.root,[1],'verified repair report')
  with sqlite3.connect(self.root/'jobs.sqlite') as db:
   self.assertEqual(db.execute('select status,attempts from jobs order by id').fetchall(),[('pending',2),('done',1)])
  with sqlite3.connect(Path(result['backup'])/'jobs.sqlite') as db:self.assertEqual(db.execute('select status from jobs where id=1').fetchone()[0],'paused_motion_redesign')
 def test_invalid_selection_rolls_back_all(self):
  with self.assertRaises(RuntimeError):m.retry(self.root,[1,2],'verified repair')
  with sqlite3.connect(self.root/'jobs.sqlite') as db:self.assertEqual(db.execute('select status from jobs where id=1').fetchone()[0],'paused_motion_redesign')
 def test_background_launcher_with_isolated_stub(self):
  if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):self.skipTest('External FFmpeg required for launcher check')
  with sqlite3.connect(self.root/'jobs.sqlite') as db:db.execute("update jobs set status='pending' where id=1")
  (self.root/'tools/procedural_render_worker.py').write_text("TASK_PREFLIGHT_VERSION = 1\nimport os,sqlite3\nfrom pathlib import Path\nwith sqlite3.connect(Path(os.environ['DATA_ROOT'])/'jobs.sqlite') as db:db.execute(\"update jobs set status='done' where id=1\")\n")
  shutil.copy2(ROOT/'tools/production_quality/task_preflight.py',self.root/'tools/production_quality/task_preflight.py')
  shutil.copy2(ROOT/'tools/render_record.py',self.root/'tools/render_record.py')
  (self.root/'runtime').mkdir(exist_ok=True)
  (self.root/'runtime/asset_source.json').write_text('{}')
  (self.root/'runtime/asset_source_files.jsonl').write_text('')
  env=dict(os.environ,DATA_ROOT=str(self.root),VIDEO_ROOT=str(self.root),BLENDER_BIN=sys.executable,RENDER_MIN_FREE_GB='0')
  result=subprocess.run([sys.executable,str(ROOT/'scripts/render_control.py'),'start'],env=env,capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)
  for _ in range(60):
   with sqlite3.connect(self.root/'jobs.sqlite') as db:status=db.execute('select status from jobs where id=1').fetchone()[0]
   if status=='done':break
   time.sleep(.05)
  self.assertEqual(status,'done')
 def test_activate_all_ignores_historical_labels_and_preserves_done(self):
  (self.root/'tools/asset_preflight_classified.tsv').write_text('subject_id\tasset_kind\tstatus\nperson\thuman\tunsupported\n')
  with sqlite3.connect(self.root/'jobs.sqlite') as db:db.execute("update jobs set status='waiting_release_review' where id=1")
  result=subprocess.run([sys.executable,str(ROOT/'scripts/activate_subject.py'),'--all'],env=dict(os.environ,DATA_ROOT=str(self.root)),capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)
  with sqlite3.connect(self.root/'jobs.sqlite') as db:self.assertEqual(db.execute('select status from jobs order by id').fetchall(),[('pending',),('done',)])
 def test_historical_label_does_not_block_registered_human(self):
  (self.root/'tools/asset_preflight_classified.tsv').write_text('subject_id\tasset_kind\tstatus\nperson\thuman\tprovisional_direct\n')
  result=m.retry(self.root,[1],'retry under default asset availability policy')
  self.assertEqual(result['requeued'],1)
if __name__=='__main__':unittest.main()
