"""Bind an idle existing workspace to the canonical asset inventory; no rendering."""
import argparse,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--assets',type=Path,required=True);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--video-root',type=Path,required=True);a=p.parse_args()
code=Path(__file__).resolve().parents[1];sys.path.insert(0,str(code/'tools'))
from asset_source import bind_workspace
if not (a.workspace/'tools').is_dir():raise RuntimeError('Existing prepared workspace required')
if (a.workspace/'runtime/asset_source.json').exists():raise RuntimeError('Already bound; preserve the binding and use a new workspace for another source')
print(bind_workspace(a.assets,code,a.workspace,a.video_root))
