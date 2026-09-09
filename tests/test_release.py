import importlib.util,json,os,sqlite3,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class ReleaseTests(unittest.TestCase):
 def test_human_gate(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'tools').mkdir();asset=root/'person.blend';asset.touch()
   registry=root/'tools/asset_preflight_classified.tsv'
   registry.write_text('asset_kind\tsubject_id\tasset_path\nhuman\tperson\t'+str(asset)+'\n')
   old=os.environ.get('DATA_ROOT');os.environ['DATA_ROOT']=str(root)
   try:
    m=module('human_registry_test',ROOT/'tools/human_registry.py');m.verify_human_job({'subject_id':'person','asset_path':str(asset)})
    for job in [{'subject_id':'unregistered','asset_path':str(asset)},{'subject_id':'person','asset_path':str(root/'different.blend')}]:
     with self.assertRaises(RuntimeError):m.verify_human_job(job)
    registry.write_text('asset_kind\tsubject_id\tasset_path\nanimal\tperson\t'+str(asset)+'\n')
    with self.assertRaises(RuntimeError):m.verify_human_job({'subject_id':'person','asset_path':str(asset)})
   finally:
    if old is None:os.environ.pop('DATA_ROOT',None)
    else:os.environ['DATA_ROOT']=old
 def test_database_relocation(self):
  m=module('prepare_test',ROOT/'scripts/prepare_workspace.py')
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory)/'metadata.sqlite';db=sqlite3.connect(p);db.execute('create table observations(feature_path text, nested text)');db.execute('insert into observations values(?,?)',('${VIDEO_ROOT}/features/a.gz','{"path":"${DATA_ROOT}/result"}'));db.commit();db.close()
   m.relocate_db(p,Path('/portable/video'),Path('/portable/work'))
   with sqlite3.connect(p) as db:row=db.execute('select * from observations').fetchone()
   self.assertEqual(row,('/portable/video/features/a.gz','{"path":"/portable/work/result"}'))
 def test_policy_is_2k_and_guard_enabled(self):
  s=json.loads((ROOT/'tools/production_quality/setting.json').read_text());self.assertEqual(s['resolution'],[1920,1080]);self.assertEqual(s['sampling'],{'samples':64,'min_samples':16,'threshold':.01,'denoising':True});self.assertTrue(s['garment_guard']['enabled']);self.assertTrue(s['garment_guard']['all_frames'])
 def test_no_private_host_paths(self):
  for p in ROOT.rglob('*.py'):
   if p==Path(__file__):continue
   text=p.read_text()
   for forbidden in ['/nvmedata/','/Users/','student@']:self.assertNotIn(forbidden,text,str(p))

if __name__=='__main__':unittest.main()
