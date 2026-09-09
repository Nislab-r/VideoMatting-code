import json,os,sqlite3
from pathlib import Path
db=sqlite3.connect(f"file:{Path(os.environ['DATA_ROOT'])/'jobs.sqlite'}?mode=ro",uri=True)
print(json.dumps({'subjects':db.execute('select count(distinct subject_id) from jobs').fetchone()[0],'statuses':dict(db.execute('select status,count(*) from jobs group by status'))},indent=2))
