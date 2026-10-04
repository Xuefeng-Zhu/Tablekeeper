import datetime,hashlib,json,pathlib,subprocess,sys,time
root=pathlib.Path('/Users/frank/mygit/Tablekeeper/result-run-4')
qa=root/'.evidence/S1-QA-20261004T192516Z'
out=root/'.evidence/S1-REPAIR-01-20261004T1936Z'/sys.argv[1]
out.mkdir()
name='tablekeeper-backend-repair-'+sys.argv[1]
record={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'actual_revision':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'commands':[],'source_checksums':{p:hashlib.sha256((qa/p).read_bytes()).hexdigest() for p in ('independent.py','precedence.py')}}
def run(args,log):
 r=subprocess.run(args,text=True,capture_output=True)
 (out/(log+'.stdout')).write_text(r.stdout);(out/(log+'.stderr')).write_text(r.stderr)
 record['commands'].append({'args':args,'exit':r.returncode,'log':log})
 return r
try:
 r=run(['docker','build','-t',name,str(root/'stage-1')],'build');assert r.returncode==0
 r=run(['docker','run','-d','--name',name,'--network','none','--cpus','2','--memory','2g','-e','PORT=8099',name],'start');assert r.returncode==0
 health="import time,urllib.request\nfor _ in range(100):\n try:\n  assert urllib.request.urlopen('http://127.0.0.1:8099/health',timeout=1).status==200;break\n except OSError:time.sleep(.1)\nelse:raise RuntimeError('not healthy')"
 assert run(['docker','exec',name,'python','-c',health],'health').returncode==0
 for file in ('independent.py','precedence.py'):
  assert run(['docker','cp',str(qa/file),name+':/tmp/'+file],'copy-'+file).returncode==0
 r=run(['docker','exec',name,'python','/tmp/precedence.py'],'precedence')
 record['reproduction_exit']=r.returncode
 # The unchanged QA script's JSON embeds its original rejected revision.
 # Actual tested revision above is authoritative for this owner-support run.
 if sys.argv[1]=='after':
  r=run(['docker','exec',name,'python','/tmp/independent.py'],'independent')
  record['independent_exit']=r.returncode
finally:
 run(['docker','rm','-f',name],'cleanup')
 record['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
 (out/'execution.json').write_text(json.dumps(record,indent=2))
print(json.dumps(record))
raise SystemExit(record.get('reproduction_exit',1) or record.get('independent_exit',0))
