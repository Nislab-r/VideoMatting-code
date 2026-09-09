"""Rebuild a review index from per-sample records; never opens the queue for writing."""
import argparse,json,os
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--output-root',required=True,type=Path);p.add_argument('--index',required=True,type=Path);a=p.parse_args()
 paths=sorted(a.output_root.glob('*/*/render_record.json'));rows=[]
 for path in paths:
  r=json.loads(path.read_text());r['record_path']=str(path.resolve());r['done_exists']=Path(r['outputs']['done']).is_file();r['video_exists']=Path(r['outputs']['video']).is_file();rows.append(r)
 a.index.parent.mkdir(parents=True,exist_ok=True);tmp=a.index.with_name(a.index.name+'.tmp');tmp.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows));tmp.replace(a.index)
 legacy=[str(p.parent) for p in a.output_root.glob('*/*/DONE') if not (p.parent/'render_record.json').exists()]
 print(json.dumps({'indexed':len(rows),'legacy_done_without_record':legacy,'index':str(a.index)},ensure_ascii=False))
if __name__=='__main__':main()
