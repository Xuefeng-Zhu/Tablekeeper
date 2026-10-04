import json,datetime
ns={};exec(open('/tmp/independent.py').read().split('for f in [receipt_identity')[0],ns)
results=[]
for date in ['0001-01-01','0999-01-07','1000-01-06','2020-01-06']:
 t=ns['reset'](date=date);status,response=ns['req']('POST','/reservations',ns['body'](start=date+'T18:00'),t,'date')
 av=ns['req']('GET','/availability?restaurant_id=r&date='+date+'&party_size=2')
 results.append({'date':date,'create_expected':201,'create_actual':status,'create_error':response.get('error'),'availability_expected':200,'availability_actual':av[0],'availability_error':av[1].get('error'),'outcome':'PASS' if status==201 and av[0]==200 else 'FAIL'})
print(json.dumps({'work_item':'S1-REVIEW','revision':'7edd47cccf839e8ec175c1e7e7252f40d80cf191','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'results':results},indent=2))
raise SystemExit(any(r['outcome']=='FAIL' for r in results))
