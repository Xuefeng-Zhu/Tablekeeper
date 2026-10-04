import datetime,json,urllib.request,urllib.error
ns={};exec(open('/tmp/independent.py').read().split('for f in [receipt_identity')[0],ns)
results=[]
for path in ('/reservations','/reservation-moves'):
 for original,equivalent,changed in [
  ('10000000000000000000000000001','10000000000000000000000000001.000','10000000000000000000000000000'),
  ('0.1234567890123456789012345678','1234567890123456789012345678e-28','0.1234567890123456789012345679'),
  ('1','1e0','true'),('-0','0.00','false'),('1e999','10e998','2e999')]:
  token=ns['reset']()
  body=ns['body']() if path=='/reservations' else {'moves':[{'reference':ns['create'](token,'seed')['reference']}]}
  body['ignored']={'nested':['__NUMBER__']}
  def send(number):
   raw=json.dumps(body).replace('"__NUMBER__"',number).encode()
   request=urllib.request.Request('http://127.0.0.1:8099'+path,data=raw,headers={'Content-Type':'application/json','Authorization':'Bearer '+token,'Idempotency-Key':'numeric-http'},method='POST')
   try:r=urllib.request.urlopen(request,timeout=5)
   except urllib.error.HTTPError as error:r=error
   return r.status,json.loads(r.read())
  first=send(original);assert first[0]==201
  reference=(first[1] if path=='/reservations' else first[1]['reservations'][0])['reference']
  assert ns['req']('POST','/reservations/'+reference+'/cancel',{},token)[0]==200
  assert send(equivalent)==(200,first[1])
  before=ns['req']('GET','/_test/export')[1]
  rejected=send(changed);assert (rejected[0],rejected[1]['error']['code'])==(409,'idempotency_key_reuse')
  assert ns['req']('GET','/_test/export')[1]==before
  assert ns['req']('POST','/_test/import',before)[0]==204
  assert send(equivalent)==(200,first[1])
  rejected=send(changed);assert (rejected[0],rejected[1]['error']['code'])==(409,'idempotency_key_reuse')
  results.append({'endpoint':path,'original':original,'equivalent':equivalent,'different':changed,'outcome':'PASS'})
print(json.dumps({'revision':'51284ad3202f927d41107b052f721b401c0a6d51','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'layer':'real Docker HTTP, network none, 2CPU/2GiB','results':results,'status':'PASS'},indent=2))
