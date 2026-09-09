import json,sys,tempfile,unittest,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import asset_source as a
class AssetSourceTests(unittest.TestCase):
 def setup(self,d):
  root=Path(d);assets=root/'assets';code=root/'code';work=root/'work';video=root/'video'
  for p in [assets/'manifests',code/'config',work,video]:p.mkdir(parents=True)
  f=video/'person.blend';f.write_text('expected');entry={'path':'person.blend','archive':'subjects/packages/p.tar.zst','sha256':a.sha256(f)}
  manifest=assets/'manifests/files.jsonl';manifest.write_text(json.dumps(entry)+'\n');policy={'repo_id':a.REPO_ID,'manifest_sha256':{'manifests/files.jsonl':a.sha256(manifest)}};(code/'config/asset_source.json').write_text(json.dumps(policy));a.bind_workspace(assets,code,work,video)
  return assets,code,work,video,{k:str(f) for k in ['asset_path','motion_path','background_path']}
 def test_bound_input_preserves_hf_archive_mapping(self):
  with tempfile.TemporaryDirectory() as d:
   _,_,w,_,j=self.setup(d);r=a.verify_job_sources(j,w);self.assertEqual(r['asset_path']['repo_id'],'Renz-7/VideoMatting-Assets');self.assertEqual(r['background_path']['archive_path'],'subjects/packages/p.tar.zst');self.assertIsNone(r['asset_path']['revision'])
 def test_changed_file_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   _,_,w,v,j=self.setup(d);(v/'person.blend').write_text('changed')
   with self.assertRaises(RuntimeError):a.verify_job_sources(j,w)
 def test_wrong_release_manifest_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   assets,code,_,_,_=self.setup(d);(assets/'manifests/files.jsonl').write_text('different')
   with self.assertRaises(RuntimeError):a.validate_release(assets,code)
if __name__=='__main__':unittest.main()
