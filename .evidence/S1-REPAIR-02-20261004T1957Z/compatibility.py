"""Import an actual rejected-version export into the new version, in memory."""
import datetime,json,pathlib,subprocess,uuid
root=pathlib.Path('/Users/frank/mygit/Tablekeeper/result-run-4')
helper=root/'.evidence/S1-REVIEW-20261004T194647Z/independent.py'
prefix='tk-numeric-compat-'+uuid.uuid4().hex[:8]
containers=[]
started=datetime.datetime.now(datetime.timezone.utc).isoformat()
def command(*args,input=None):
 return subprocess.run(['docker',*args],input=input,text=True,capture_output=True,check=True).stdout
HEALTH="import time,urllib.request\nfor _ in range(100):\n try:\n  assert urllib.request.urlopen('http://127.0.0.1:8099/health',timeout=1).status==200;break\n except OSError:time.sleep(.1)\nelse:raise RuntimeError('not healthy')"
def start(suffix,image):
 name=prefix+suffix
 command('run','-d','--name',name,'--network','none','--cpus','2','--memory','2g','-e','PORT=8099',image);containers.append(name)
 command('exec',name,'python','-c',HEALTH)
 command('cp',str(helper),name+':/tmp/independent.py')
 return name
PRELUDE="import json,sys\nns={};exec(open('/tmp/independent.py').read().split('for f in [receipt_identity')[0],ns)\n"
SOURCE=PRELUDE+'''
t=ns['reset']();body=ns['body']();body['ignored']={'n':10000000000000000000000000001,'decimal':1.25,'flag':True}
s,created=ns['req']('POST','/reservations',body,t,'legacy-create');assert s==201
moves={'moves':[{'reference':created['reference']}],'ignored':body['ignored']}
s,moved=ns['req']('POST','/reservation-moves',moves,t,'legacy-moves');assert s==201
assert ns['req']('POST','/reservations/'+created['reference']+'/cancel',{},t)[0]==200
snapshot=ns['req']('GET','/_test/export')[1]
print(json.dumps({'snapshot':snapshot,'token':t,'body':body,'moves':moves,'created':created,'moved':moved}))
'''
TARGET=PRELUDE+'''
p=json.load(sys.stdin);r=ns['req'];assert r('POST','/_test/import',p['snapshot'])[0]==204
assert r('GET','/_test/export')[1]==p['snapshot']
for path,body,key,response in [('/reservations',p['body'],'legacy-create',p['created']),('/reservation-moves',p['moves'],'legacy-moves',p['moved'])]:
 assert r('POST',path,body,p['token'],key)==(200,response)
 changed=json.loads(json.dumps(body));changed['ignored']['n']+=1
 assert r('POST',path,changed,p['token'],key)[0]==409
assert r('POST','/auth/login',{'email':'qa@example.com','password':'synthetic-password'})[0]==200
print(json.dumps({'status':'PASS','legacy_export_unchanged':True,'source_destroyed':True,'endpoints':2,'original_cancelled_receipts_replayed':True,'distinct_large_integer_rejected':True,'hashed_password_login':True}))
'''
try:
 source=start('-source','tablekeeper-backend-numeric-before')
 package=command('exec',source,'python','-c',SOURCE)
 command('rm','-f',source);containers.remove(source)
 target=start('-target','tablekeeper-backend-numeric-after')
 results=json.loads(command('exec','-i',target,'python','-c',TARGET,input=package))
 results.update(source_revision='c8558d8f5f4003221d92d297db5bcabf6cf3fa30',target_revision='51284ad3202f927d41107b052f721b401c0a6d51',started_at=started,finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),layer='Docker HTTP, network none, 2CPU/2GiB')
 print(json.dumps(results,indent=2))
finally:
 for name in containers:command('rm','-f',name)
