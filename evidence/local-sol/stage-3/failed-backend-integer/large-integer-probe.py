import json,os,subprocess,socket,time,urllib.request,urllib.error,datetime
from decimal import Decimal
start=datetime.datetime.now(datetime.timezone.utc).isoformat()
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
p=subprocess.Popen(['python','server.py'],env={**os.environ,'PORT':str(port)},stdout=subprocess.DEVNULL)
url='http://127.0.0.1:'+str(port)
try:
    for _ in range(100):
        try:urllib.request.urlopen(url+'/health').close();break
        except OSError:time.sleep(.02)
    raw='{"restaurants":[{"id":"r","name":"R","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":120,"opening_hours":[{"weekday":"mon","opens":"18:00","closes":"23:00"}],"tables":[{"id":"A","label":"A","capacity":'+('1'+'0'*5000)+'}]}]}'
    r=urllib.request.urlopen(urllib.request.Request(url+'/_test/reset',data=raw.encode(),headers={'Content-Type':'application/json'},method='POST'));assert r.status==204;r.close()
    try:r=urllib.request.urlopen(url+'/availability?restaurant_id=r&date=2030-01-07&party_size=1')
    except urllib.error.HTTPError as e:r=e
    with r:status=r.status;body=json.loads(r.read(),parse_int=Decimal)
    print(json.dumps({'work_item':'BUILD-S3','candidate':'abcf6df2cdc86f3295460b47044aed7ee9fc5f00','start_utc':start,'end_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'capacity_decimal_digits':5001,'expected_status':200,'observed_status':status,'result':'PASS' if status==200 else 'FAIL','error':body.get('error')}))
finally:p.terminate();p.wait()
