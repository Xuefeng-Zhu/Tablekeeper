"""Own Docker HTTP checks. Exports/tokens live only in orchestrator memory.
Usage: python tests/container_checks.py IMAGE
"""
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime,timezone

IMAGE=sys.argv[1]
PREFIX='tk-own-'+uuid.uuid4().hex[:10]
containers=[]
started=datetime.now(timezone.utc).isoformat()

def docker(*args,input=None):
    return subprocess.run(['docker',*args],input=input,text=True,capture_output=True,check=True).stdout

def run(name):
    docker('run','-d','--name',name,'--network','none','--cpus','2','--memory','2g','-e','PORT=8097',IMAGE)
    containers.append(name)
    return name

COMMON='''
import json,sys,time,urllib.request,urllib.error
from concurrent.futures import ThreadPoolExecutor
base='http://127.0.0.1:8097'
def call(path,body=None,token=None,key=None,method=None):
    headers={'Content-Type':'application/json'}
    if token: headers['Authorization']='Bearer '+token
    if key: headers['Idempotency-Key']=key
    start=time.monotonic()
    request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
    try: response=urllib.request.urlopen(request,timeout=10)
    except urllib.error.HTTPError as error: response=error
    raw=response.read()
    return response.status,json.loads(raw) if raw else None,time.monotonic()-start
start=time.monotonic()
while True:
    try:
        if call('/health')[0]==200: break
    except OSError: pass
    assert time.monotonic()-start<60
    time.sleep(.05)
healthy=time.monotonic()-start
'''
SOURCE=COMMON+'''
fixture={'users':[{'id':'u','email':'a@example.com','password':'password1','display_name':'Ada'}], 'restaurants':[{'id':'r','name':'R','timezone':'UTC','slot_minutes':30,'reservation_duration_minutes':90,'cancellation_cutoff_minutes':0,'opening_hours':[{'weekday':d,'opens':'00:00','closes':'23:30'} for d in ['mon','tue','wed','thu','fri','sat','sun']], 'tables':[{'id':'a','label':'A','capacity':4},{'id':'b','label':'B','capacity':4}]}],'reservations':[]}
status,_,reset_seconds=call('/_test/reset',fixture);assert status==204
login={'email':'a@example.com','password':'password1'}
status,account,_=call('/auth/login',login);assert status==200
token=account['token']
body={'restaurant_id':'r','table_id':'a','starts_at_local':'2030-11-01T18:00','party_size':2}
with ThreadPoolExecutor(max_workers=50) as pool:
    receipts=list(pool.map(lambda _:call('/reservations',body,token,'shared'),range(50)))
assert sum(r[0]==201 for r in receipts)==1
assert sum(r[0]==200 for r in receipts)==49
assert all(r[1]==receipts[0][1] for r in receipts)
assert max(r[2] for r in receipts)<5
with ThreadPoolExecutor(max_workers=50) as pool:
    logins=list(pool.map(lambda _:call('/auth/login',login),range(50)))
assert all(r[0]==200 and r[2]<5 for r in logins)
original=receipts[0][1]
assert call('/reservations/'+original['reference']+'/cancel',{},token)[0]==200
assert call('/reservations',body,token,'shared')[1]==original
snapshot=call('/_test/export')[1]
print(json.dumps({'snapshot':snapshot,'token':token,'body':body,'original':original,'health_seconds':healthy,'reset_seconds':reset_seconds,'create_max_seconds':max(r[2] for r in receipts),'login_max_seconds':max(r[2] for r in logins)}))
'''
DESTINATION=COMMON+'''
package=json.load(sys.stdin)
status,sentinel,_=call('/auth/signup',{'email':'sentinel@example.com','password':'password1','display_name':'Sentinel'});assert status==201
status,_,import_seconds=call('/_test/import',package['snapshot']);assert status==204
assert call('/reservations',token=sentinel['token'])[0]==401
assert call('/auth/login',{'email':'a@example.com','password':'password1'})[0]==200
assert call('/reservations',package['body'],package['token'],'shared')[1]==package['original']
assert call('/reservations',token=package['token'])[1]['reservations'][0]['status']=='cancelled'
assert call('/_test/import',package['snapshot'])[0]==204
assert call('/_test/export')[1]==package['snapshot']
bad={'track':'tablekeeper','format_version':1,'state':{}}
assert call('/_test/import',bad)[0]==422
assert call('/_test/export')[1]==package['snapshot']
assert call('/_test/reset',{'users':[],'restaurants':[],'reservations':[]})[0]==204
assert call('/reservations',token=package['token'])[0]==401
print(json.dumps({'destination_health_seconds':healthy,'import_seconds':import_seconds,'source_destroyed_before_import':True,'portable_original_receipts_sessions_passwords':True,'replacement_and_repeat_import':True}))
'''
try:
    source=run(PREFIX+'-source')
    package=json.loads(docker('exec',source,'python','-c',SOURCE))
    resources=json.loads(docker('inspect','--format','{{json .HostConfig}}',source))
    assert resources['Memory']==2147483648 and resources['NanoCpus']==2000000000 and resources['NetworkMode']=='none'
    docker('rm','-f',source);containers.remove(source)
    destination=run(PREFIX+'-destination')
    results=json.loads(docker('exec','-i',destination,'python','-c',DESTINATION,input=json.dumps(package)))
    results.update({k:package[k] for k in ('health_seconds','reset_seconds','create_max_seconds','login_max_seconds')})
    results.update(status='PASS',started_at=started,finished_at=datetime.now(timezone.utc).isoformat(),cpus=2,memory_bytes=2147483648,network='none',concurrent_requests=50,image=IMAGE)
    print(json.dumps(results,indent=2))
finally:
    for name in containers: docker('rm','-f',name)
