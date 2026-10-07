"""One bounded rebuild on data-only upstream change. Never rebase/replay old data."""
import datetime as dt,json,os,pathlib,subprocess,sys,tempfile
OWNED={'saves','renewals','tiers','day','metricsMeta'}
def git(root,*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
def validate(before,after):
 def sheet(d):return {k:({s:v for s,v in val.items() if s not in OWNED} if k=='board' else val) for k,val in d.items() if k!='meta'}
 if {k:v for k,v in before.get('meta',{}).items() if k not in ('syncedAt','source')}!={k:v for k,v in after.get('meta',{}).items() if k not in ('syncedAt','source')}:raise RuntimeError('Other top-level meta changed')
 if sheet(before)!=sheet(after):raise RuntimeError('Sheet-owned fields changed')
 if after.get('meta',{}).get('source')!='fieldroutes':raise RuntimeError('Invalid source')
 for b in (after['board']['tiers'],after['board']['day']['tiers']):
  if b['status'] not in ('ok','unavailable','tie-review') or any('hours' in r for r in b['rows']):raise RuntimeError('Private hours/tier validation failed')
def next_time(data):
 value=os.environ.get('NEXT_UPDATE_AT','').strip();meta=data['board']['metricsMeta']
 if value:
  t=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
  if t.tzinfo is None:raise RuntimeError('Next update timezone missing')
  meta['nextUpdateAt']=t.isoformat()
 else:meta.pop('nextUpdateAt',None)
def rebuild(root):
 # Revalidate original encrypted snapshot against current clock before any FR reads.
 sys.path.insert(0,str(root))
 from hours_crypto import decrypt
 from hours_input import validate as hours_validate
 before=json.loads((root/'data.json').read_text());roster=[a['name'] for a in before['agents']]
 raw=decrypt(os.environ['HOURS_SNAPSHOT'],os.environ['HOURS_PRIVATE_KEY']);hours_validate(raw,roster)
 subprocess.run([sys.executable,'fr_pull.py'],cwd=root,check=True)
 after=json.loads((root/'data.json').read_text());next_time(after);validate(before,after)
 (root/'data.json').write_text(json.dumps(after,separators=(',',':')))
def publish(root,builder=rebuild,push=True):
 root=pathlib.Path(root).resolve();base=git(root,'rev-parse','HEAD')
 before=json.loads(git(root,'show','HEAD:data.json'));after=json.loads((root/'data.json').read_text());validate(before,after)
 git(root,'fetch','origin','main');remote=git(root,'rev-parse','origin/main');work=root;temporary=None
 try:
  if remote!=base:
   # A Claude data-only update is the sole recoverable conflict. Never execute changed code.
   subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',base,remote],check=True)
   changed=set(git(root,'diff','--name-only',base,remote).splitlines())
   if changed-{'data.json'}:raise RuntimeError('Remote changed outside data.json; stop for review')
   temporary=tempfile.TemporaryDirectory(prefix='fr-rebuild-');work=pathlib.Path(temporary.name)/'work'
   git(root,'worktree','add','--detach',str(work),remote);base=remote
   builder(work)
   validate(json.loads(git(work,'show','HEAD:data.json')),json.loads((work/'data.json').read_text()))
   print('Rebuilt once on latest data-only base',flush=True)
  git(work,'fetch','origin','main')
  if git(work,'rev-parse','origin/main')!=base:raise RuntimeError('Remote changed again; refusing publish')
  git(work,'add','--','data.json')
  if not git(work,'diff','--cached','--name-only'):return {'published':False,'rebuilt':temporary is not None,'base':base}
  staged=git(work,'diff','--cached','--name-only')
  if staged!='data.json':raise RuntimeError('Unexpected staged files')
  git(work,'-c','user.name=github-actions[bot]','-c','user.email=41898282+github-actions[bot]@users.noreply.github.com','commit','-m','Refresh FieldRoutes aggregate metrics')
  commit=git(work,'rev-parse','HEAD')
  if push:git(work,'push','origin','HEAD:main')  # ordinary non-force push rejects final race
  return {'published':bool(push),'rebuilt':temporary is not None,'base':base,'commit':commit}
 finally:
  if temporary:
   subprocess.run(['git','-C',str(root),'worktree','remove','--force',str(work)],check=True,stdout=subprocess.DEVNULL);temporary.cleanup()
if __name__=='__main__':
 try:print(json.dumps(publish(pathlib.Path.cwd())))
 except Exception:print('Guarded publish stopped safely; no stale replay or further retry',file=sys.stderr);raise
