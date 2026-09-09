import concurrent.futures,importlib.util,os,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'tools'))
import gpu_inventory,render_multi_gpu
class MultiGpuTests(unittest.TestCase):
 def test_layout_per_card_counts(self):
  g=[{'uuid':'a'},{'uuid':'b'}]
  self.assertEqual([(x['uuid'],s) for x,s in render_multi_gpu.layout(g,[2,1])],[('a',0),('a',1),('b',0)])
  with self.assertRaises(ValueError):render_multi_gpu.layout(g,[1,2,3])
 def test_busy_gpu_refused(self):
  with patch.object(gpu_inventory,'inventory',return_value={'devices':[{'index':0,'uuid':'a','gpu_utilization_percent':99}],'compute_processes':[{'uuid':'a','pid':1}]}):
   with self.assertRaises(RuntimeError):gpu_inventory.select_idle(['0'])
 def test_atomic_claims_distinct_people(self):
  with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'DATA_ROOT':temp,'VIDEO_ROOT':temp,'MOTION_OBSERVATION_ROOT':temp,'BLENDER_BIN':sys.executable,'SCENE_PAIRING_REQUIRED':'0'}):
   path=Path(temp)/'jobs.sqlite'
   with sqlite3.connect(path) as db:
    db.execute('create table jobs(id integer primary key,subject_id text,sample_id text,split text,status text,worker_id text,pid integer,attempts integer,started_at text,error text)')
    db.executemany("insert into jobs values(?,?,?,'train','pending',NULL,NULL,0,NULL,NULL)",[(1,'a','a1'),(2,'a','a2'),(3,'b','b1'),(4,'c','c1')]);db.execute('create table events(job_id integer,event text,detail text)')
   spec=importlib.util.spec_from_file_location('worker_concurrency_test',ROOT/'tools/procedural_render_worker.py');w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
   with patch.object(w,'design_if_needed',side_effect=lambda db,job:job):
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:jobs=list(pool.map(w.claim,['g0s0','g0s1','g1s0']))
    self.assertEqual({j['subject_id'] for j in jobs},{'a','b','c'});self.assertEqual(len({j['id'] for j in jobs}),3)
    self.assertIsNone(w.claim('extra'))
   with sqlite3.connect(path) as db:self.assertEqual(db.execute("select count(*) from jobs where status='running'").fetchone()[0],3);self.assertEqual(db.execute('select status from jobs where id=2').fetchone()[0],'pending')
if __name__=='__main__':unittest.main()
