"""Off-lock state preparation; callers publish only fully validated roots."""
import copy
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from domain import Error, fail, empty, string, integer, password, parse, instant

def restaurants(items):
    if not isinstance(items,list): fail()
    out = {}
    for source in items:
        if not isinstance(source,dict): fail()
        r = {k:string(source,k,k=='id') for k in ['id','name','timezone']}
        if r['id'] in out: fail()
        try: ZoneInfo(r['timezone'])
        except (ValueError, KeyError): fail()
        for k in ['slot_minutes','reservation_duration_minutes','cancellation_cutoff_minutes']:
            if k not in source: fail()
            r[k] = integer(source[k],0 if k=='cancellation_cutoff_minutes' else 1,5256000)
        r['tables'], r['opening_hours'] = [], []
        if not isinstance(source.get('tables'),list) or not isinstance(source.get('opening_hours'),list): fail()
        for t in source['tables']:
            if not isinstance(t,dict): fail()
            table = {'id':string(t,'id',True),'label':string(t,'label'),'capacity':integer(t.get('capacity'))}
            if any(x['id']==table['id'] for x in r['tables']): fail()
            r['tables'].append(table)
        for h in source['opening_hours']:
            if not isinstance(h,dict): fail()
            hours = {k:string(h,k) for k in ['weekday','opens','closes']}
            if hours['weekday'] not in ['mon','tue','wed','thu','fri','sat','sun']: fail()
            for k in ['opens','closes']:
                if not re.fullmatch(r'[0-9]{2}:[0-9]{2}',hours[k]): fail()
                try: datetime.strptime(hours[k],'%H:%M')
                except ValueError: fail()
            if hours['opens'] >= hours['closes']: fail()
            r['opening_hours'].append(hours)
        out[r['id']] = r
    return out

def fixture(service, body):
    state = empty()
    state['restaurants'] = restaurants(body.get('restaurants'))
    if not isinstance(body.get('users'),list) or not isinstance(body.get('reservations',[]),list): fail()
    emails = set()
    for u in body['users']:
        if not isinstance(u,dict): fail()
        uid,email,pw,name = string(u,'id',True),string(u,'email'),string(u,'password'),string(u,'display_name')
        if uid in state['users'] or email in emails: fail()
        emails.add(email)
        with service.hash_slots: hashed = password(pw)
        state['users'][uid] = {'id':uid,'email':email,'display_name':name,'password_hash':hashed}
    refs = set()
    for source in body.get('reservations',[]):
        if not isinstance(source,dict): fail()
        rid,ref,uid = string(source,'id',True),string(source,'reference'),string(source,'user_id',True)
        if rid in state['reservations'] or ref in refs or not re.fullmatch(r'[A-Z0-9]{6,12}',ref) or uid not in state['users']: fail()
        proposal = service.proposal(source,state)
        service.occupancy([proposal],state=state)
        state['reservations'][rid] = dict(proposal,reservation_id=rid,reference=ref,user_id=uid,status='confirmed',created_at=service.clock().isoformat())
        refs.add(ref)
    return state

def imported(service, body):
    try:
        if body.get('track')!='tablekeeper' or type(body.get('format_version')) is not int or body['format_version']!=1: fail()
        s = body['state']
        if not isinstance(s,dict) or type(s.get('schema_version')) is not int or s['schema_version']!=1: fail()
        if any(not isinstance(s.get(k),dict) for k in ['users','tokens','restaurants','reservations']) or not isinstance(s.get('receipts'),list): fail()
        result = empty()
        result['restaurants'] = restaurants(list(s['restaurants'].values()))
        if result['restaurants'].keys()!=s['restaurants'].keys(): fail()
        emails = set()
        for uid,u in s['users'].items():
            if string(u,'id',True)!=uid or string(u,'email') in emails: fail()
            string(u,'display_name')
            emails.add(u['email'])
            p = u['password_hash']
            if not isinstance(p,dict) or p.get('algorithm')!='scrypt' or [p.get(k) for k in ['n','r','p']] != [16384,8,1]: fail()
            if not re.fullmatch('[0-9a-f]{32}',p['salt']) or not re.fullmatch('[0-9a-f]{64}',p['digest']): fail()
            result['users'][uid]=copy.deepcopy(u)
        for token,uid in s['tokens'].items():
            if not isinstance(token,str) or not token or uid not in result['users']: fail()
            result['tokens'][token]=uid
        refs=set()
        for rid,r in s['reservations'].items():
            if string(r,'reservation_id',True)!=rid or r['user_id'] not in result['users'] or r['status'] not in ['confirmed','cancelled']: fail()
            if not re.fullmatch('[A-Z0-9]{6,12}',r['reference']) or r['reference'] in refs: fail()
            p = service.proposal(r,result)
            if any(p[k]!=r[k] for k in p): fail()
            for k in ['starts_at','ends_at','created_at']:
                if datetime.fromisoformat(r[k]).tzinfo is None: fail()
            if r['status']=='confirmed': service.occupancy([r],state=result)
            result['reservations'][rid]=copy.deepcopy(r)
            refs.add(r['reference'])
        receipt_keys=set()
        for receipt in s['receipts']:
            if receipt['user_id'] not in result['users'] or receipt['method']!='POST' or receipt['path'] not in ['/reservations','/reservation-moves']: fail()
            key=receipt['key']
            if not isinstance(key,str) or not 1<=len(key)<=255: fail()
            identity=(receipt['user_id'],receipt['method'],receipt['path'],key)
            if identity in receipt_keys: fail()
            receipt_keys.add(identity)
            parse(receipt['request_json'])
            response=receipt['response']
            records=response.get('reservations') if receipt['path']=='/reservation-moves' else [response]
            if not isinstance(records,list) or not records: fail()
            for r in records:
                if not isinstance(r,dict) or not all(k in r for k in ['reservation_id','reference','restaurant_id','table_id','party_size','status','starts_at_local','starts_at','ends_at','created_at']): fail()
                if r['reservation_id'] not in result['reservations'] or r['reference'] != result['reservations'][r['reservation_id']]['reference']: fail()
                service.proposal(r,result)
            result['receipts'].append(copy.deepcopy(receipt))
        return result
    except (Error,KeyError,TypeError,ValueError,OverflowError,AttributeError): fail()
