"""Explicitly activate registered humans; historical QC labels are informational."""
import argparse,csv,json,os,sqlite3,sys
from pathlib import Path
def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--subject-id');g.add_argument('--all',action='store_true');a=p.parse_args();root=Path(os.environ['DATA_ROOT']);sys.path.insert(0,str(root/'tools'))
 from frozen_pairing import load_roots,resolve_background_ref
 humans=list(csv.DictReader((root/'tools/asset_preflight_classified.tsv').open(),delimiter='\t'))
 match=[x for x in humans if (a.all or x['subject_id']==a.subject_id) and x['asset_kind']=='human']
 if not match or (not a.all and len(match)!=1):raise RuntimeError('A registered human is required')
 selected={x['subject_id'] for x in match}
 roots=load_roots(root/'reports/pairing_reference/background_roots.json');pairs=sqlite3.connect(root/'reports/pairing_reference/render_pairing_reference.sqlite');db=sqlite3.connect(root/'jobs.sqlite');activated=blocked=0
 for ident,sample,status,subject in db.execute('select id,sample_id,status,subject_id from jobs').fetchall():
  if subject not in selected:continue
  if status!='waiting_release_review':continue
  row=pairs.execute('select background_ref from pairings where sample_id=?',(sample,)).fetchone()
  if not row or not resolve_background_ref(row[0],roots).is_file():
   db.execute("update jobs set status='waiting_missing_background' where id=?",(ident,));blocked+=1
  else:db.execute("update jobs set status='pending' where id=?",(ident,));activated+=1
 db.commit();db.close();pairs.close();print(json.dumps({'activated':activated,'blocked_missing_background':blocked}))
if __name__=='__main__':main()
