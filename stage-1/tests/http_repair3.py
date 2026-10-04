"""Two-process HTTP regressions; no private snapshot content written to evidence."""
import argparse,copy,datetime,json,urllib.request,urllib.error
from decimal import Decimal

def fixture():
 return {'users':[{'id':'u','email':'u@x','display_name':'U','password':'synthetic-pass'}],'restaurants':[{'id':'r','name':'R','timezone':'UTC','slot_minutes':30,'reservation_duration_minutes':90,'cancellation_cutoff_minutes':0,'opening_hours':[{'weekday':d,'opens':'18:00','closes':'23:00'} for d in ['mon','tue','wed','thu','fri','sat','sun']],'tables':[{'id':'t','label':'T','capacity':4},{'id':'t2','label':'T2','capacity':4}]}],'reservations':[]}

def run(a,b):
 results=[]
 def req(base,method,path,body=None,token=None,key=None,raw=None):
  headers={'Content-Type':'application/json'}
  if token:headers['Authorization']='Bearer '+token
  if key:headers['Idempotency-Key']=key
  data=raw.encode() if raw is not None else json.dumps(body).encode() if body is not None else None
  try:r=urllib.request.urlopen(urllib.request.Request(base+path,data=data,headers=headers,method=method),timeout=10)
  except urllib.error.HTTPError as e:r=e
  with r:
   raw=r.read().decode();return r.status,json.loads(raw,parse_int=Decimal,parse_float=Decimal) if raw else None,raw
 def status(r,expected):assert r[0]==expected,(r[0],expected)
 f=fixture();status(req(a,'POST','/_test/reset',f),204);previous=req(a,'GET','/_test/export')[2]
 for field in ['slot_minutes','reservation_duration_minutes','cancellation_cutoff_minutes','capacity']:
  for value in ['30',True,None,[],{}]:
   bad=copy.deepcopy(f);target=bad['restaurants'][0]['tables'][0] if field=='capacity' else bad['restaurants'][0];target[field]=value
   r=req(a,'POST','/_test/reset',bad);status(r,400);assert r[1]['error']['code']=='malformed_request';assert req(a,'GET','/_test/export')[2]==previous
  for variant in ['missing','negative','fraction']:
   bad=copy.deepcopy(f);target=bad['restaurants'][0]['tables'][0] if field=='capacity' else bad['restaurants'][0]
   if variant=='missing':del target[field]
   else:target[field]=-1 if variant=='negative' else 1.5
   status(req(a,'POST','/_test/reset',bad),422);assert req(a,'GET','/_test/export')[2]==previous
 results.append({'group':'reset-types-values-and-atomicity','status':'PASS','cases':32})
 # Huge numbers travel as real JSON numbers; client preserves exact lexical export.
 digits='9'*5000
 for field in ['capacity','reservation_duration_minutes','slot_minutes','cancellation_cutoff_minutes']:
  for suffix in ['', '.0']:
   big=fixture();target=big['restaurants'][0]['tables'][0] if field=='capacity' else big['restaurants'][0];target[field]='SENTINEL'
   raw=json.dumps(big).replace('"SENTINEL"',digits+suffix);status(req(a,'POST','/_test/reset',raw=raw),204)
   restaurant=req(a,'GET','/restaurants/r')[1];value=restaurant['tables'][0]['capacity'] if field=='capacity' else restaurant[field];assert value==Decimal(digits)
   exported=req(a,'GET','/_test/export')[2];status(req(b,'POST','/_test/import',raw=exported),204);assert req(b,'GET','/_test/export')[2]==exported
   available=req(b,'GET','/availability?restaurant_id=r&date=2096-09-24&party_size=2');status(available,200)
   assert len(available[1]['slots'])==({'reservation_duration_minutes':0,'slot_minutes':1}.get(field,8))
   token=req(b,'POST','/auth/login',{'email':'u@x','password':'synthetic-pass'})[1]['token']
   body={'restaurant_id':'r','table_id':'t','starts_at_local':'2096-09-24T18:00','party_size':2}
   if field=='capacity':
    rawbody=json.dumps(dict(body,party_size='HUGE')).replace('"HUGE"',digits)
    created=req(b,'POST','/reservations',token=token,key='huge',raw=rawbody);status(created,201);assert created[1]['party_size']==Decimal(digits)
    status(req(b,'POST','/reservations',token=token,key='huge',raw=rawbody),200)
    exported=req(b,'GET','/_test/export')[2];status(req(a,'POST','/_test/import',raw=exported),204)
    replay=req(a,'POST','/reservations',token=token,key='huge',raw=rawbody);status(replay,200);assert replay[1]==created[1]
   elif field=='cancellation_cutoff_minutes':
    created=req(b,'POST','/reservations',body,token,'cutoff');status(created,201)
    r=req(b,'POST','/reservations/'+created[1]['reference']+'/cancel',{},token);status(r,409);assert r[1]['error']['code']=='cutoff_passed'
 results.append({'group':'5000-digit-four-fields-integral-decimals-browse-import-booking-receipts','status':'PASS','field_forms':8})
 # Small ordinary snapshots permit independent stdlib serialization of corruptions.
 status(req(a,'POST','/_test/reset',f),204)
 token=req(a,'POST','/auth/login',{'email':'u@x','password':'synthetic-pass'})[1]['token']
 body={'restaurant_id':'r','table_id':'t','starts_at_local':'2096-09-24T18:00','party_size':2}
 first=req(a,'POST','/reservations',body,token,'create')[1];second=req(a,'POST','/reservations',dict(body,table_id='t2'),token,'create2')[1]
 moves={'moves':[{'reference':first['reference'],'party_size':1},{'reference':second['reference']}]}
 batch=req(a,'POST','/reservation-moves',moves,token,'batch')[1]
 status(req(a,'PATCH','/reservations/'+first['reference'],{'party_size':3},token),200);status(req(a,'POST','/reservations/'+first['reference']+'/cancel',{},token),200)
 rawsnapshot=req(a,'GET','/_test/export')[2];snapshot=json.loads(rawsnapshot)
 status(req(b,'POST','/_test/import',raw=rawsnapshot),204)
 assert req(b,'POST','/reservations',body,token,'create')[1]==first
 assert req(b,'POST','/reservation-moves',moves,token,'batch')[1]==batch
 previous=req(b,'GET','/_test/export')[2]
 for index in [0,2]:
  for field,value in [('incomplete',None),('reference','ORPHAN01'),('reservation_id','missing'),('restaurant_id','missing'),('party_size',True),('starts_at','bad'),('status',None)]:
   bad=copy.deepcopy(snapshot);receipt=bad['state']['receipts'][index];response=json.loads(receipt['response']);booking=response if index==0 else response['reservations'][0]
   if field=='incomplete':
    replacement={'reference':booking['reference']}
    if index==0:response=replacement
    else:response['reservations'][0]=replacement
   else:booking[field]=value
   receipt['response']=json.dumps(response);r=req(b,'POST','/_test/import',bad);status(r,422);assert r[1]['error']['code']=='validation_failed';assert req(b,'GET','/_test/export')[2]==previous
 bad=copy.deepcopy(snapshot);receipt=bad['state']['receipts'][2];response=json.loads(receipt['response']);response['reservations'].reverse();receipt['response']=json.dumps(response);status(req(b,'POST','/_test/import',bad),422);assert req(b,'GET','/_test/export')[2]==previous
 results.append({'group':'historical-create-batch-receipt-integrity-atomicity-positive-replay','status':'PASS','corruptions':15})
 return results

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--base-url',required=True);p.add_argument('--destination-url',required=True);p.add_argument('--revision',required=True);p.add_argument('--out',required=True);args=p.parse_args();start=datetime.datetime.now(datetime.timezone.utc).isoformat()
 results=run(args.base_url,args.destination_url)
 with open(args.out,'x') as f:json.dump(dict(work_item='BUILD-S1-REPAIR3',owner='@frankzhu94/factory-backend',revision=args.revision,start=start,end=datetime.datetime.now(datetime.timezone.utc).isoformat(),layer='host HTTP',results=results),f,indent=2)
 print('3 HTTP regression groups PASS')
