"""Download a fixed HF dataset revision; use after the dataset upload is complete."""
import argparse,json,sys
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--assets',required=True,type=Path);p.add_argument('--revision',default='main');a=p.parse_args();code=Path(__file__).resolve().parents[1];sys.path.insert(0,str(code/'tools'))
 from asset_source import REPO_ID,validate_release
 from huggingface_hub import HfApi,snapshot_download
 receipt=a.assets/'.videomatting_download.json'
 if receipt.exists():
  prior=json.loads(receipt.read_text())
  if prior['repo_id']!=REPO_ID or prior['requested_revision']!=a.revision:raise RuntimeError('Use a new directory for a different repository/revision')
  revision=prior['resolved_revision']
 else:
  if a.assets.exists() and any(a.assets.iterdir()):raise RuntimeError('Choose a new download directory; existing assets are not overwritten')
  revision=HfApi().dataset_info(REPO_ID,revision=a.revision).sha
  a.assets.mkdir(parents=True,exist_ok=True)
  receipt.write_text(json.dumps({'repo_id':REPO_ID,'requested_revision':a.revision,'resolved_revision':revision,'status':'downloading'},indent=2))
 snapshot_download(repo_id=REPO_ID,repo_type='dataset',revision=revision,local_dir=str(a.assets))
 validate_release(a.assets,code)
 expected=json.loads((code/'config/asset_source.json').read_text())
 for r in map(json.loads,(a.assets/'manifests/archives.jsonl').read_text().splitlines()):
  path=a.assets/r['path']
  if not path.is_file() or path.stat().st_size!=r['bytes']:raise RuntimeError('Upload/download incomplete: '+r['path'])
 data=json.loads(receipt.read_text());data['status']='downloaded_inventory_checked';receipt.write_text(json.dumps(data,indent=2));print(json.dumps(data))
if __name__=='__main__':main()
