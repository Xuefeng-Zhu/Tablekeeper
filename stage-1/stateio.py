"""Portable state validation; callers commit only a wholly validated replacement."""
import copy
import re
from decimal import Decimal
from datetime import datetime
from zoneinfo import ZoneInfo
import codec
from rules import require, field, ident, integer, instant, Error, DAYS


def validate_restaurants(raw):
    result=[]; ids=set(); table_ids=set()
    for item in raw:
        require(isinstance(item,dict))
        r={k:field(item,k) for k in ('id','name','timezone')}; ident(r['id'])
        require(r['id'] not in ids); ids.add(r['id']); ZoneInfo(r['timezone'])
        for key in ('slot_minutes','reservation_duration_minutes','cancellation_cutoff_minutes'):
            require(key in item); r[key]=integer(item[key],0 if key=='cancellation_cutoff_minutes' else 1)
        r['opening_hours']=[]; days=set()
        for h in field(item,'opening_hours',list):
            require(isinstance(h,dict)); day=field(h,'weekday')
            require(day in DAYS and day not in days); days.add(day)
            opens=field(h,'opens'); closes=field(h,'closes')
            for value in (opens,closes):
                require(re.fullmatch('[0-9]{2}:[0-9]{2}',value)); datetime.strptime(value,'%H:%M')
            require(opens<closes); r['opening_hours'].append(dict(weekday=day,opens=opens,closes=closes))
        r['tables']=[]; table_ids=set()
        for t in field(item,'tables',list):
            require(isinstance(t,dict)); tid=ident(field(t,'id')); require(tid not in table_ids); table_ids.add(tid)
            require('capacity' in t)
            r['tables'].append(dict(id=tid,label=field(t,'label'),capacity=integer(t['capacity'])))
        result.append(r)
    return result


def validate_import(envelope,service):
    try:
        require(envelope['track']=='tablekeeper' and type(envelope['format_version']) is not bool and envelope['format_version']==1)
        s=copy.deepcopy(envelope['state']); require(isinstance(s,dict))
        require(type(s['schema_version']) is not bool and s['schema_version']==1)
        require(set(s)=={'schema_version','users','restaurants','reservations','tokens','receipts','restaurant_revisions'})
        s['schema_version']=1
        require(isinstance(s['restaurants'],list)); s['restaurants']=validate_restaurants(s['restaurants'])
        uids=set(); emails=set()
        require(isinstance(s['users'],list))
        for u in s['users']:
            ident(u['id']); require(u['id'] not in uids and u['email'] not in emails)
            uids.add(u['id']); emails.add(u['email']); require(isinstance(u['email'],str) and isinstance(u['display_name'],str))
            p=u['password_hash']; require(set(p)=={'algorithm','salt','hash','n','r','p'})
            require(p['algorithm']=='scrypt' and p['n']==16384 and p['r']==8 and p['p']==1)
            require(len(bytes.fromhex(p['salt']))==16 and len(bytes.fromhex(p['hash']))==64)
            for key in ('n','r','p'): p[key]=integer(p[key])
            require(set(u)=={'id','email','display_name','password_hash'})
        require(isinstance(s['tokens'],dict) and all(isinstance(t,str) and t and uid in uids for t,uid in s['tokens'].items()))
        require(set(s['restaurant_revisions'])=={r['id'] for r in s['restaurants']})
        s['restaurant_revisions']={k:integer(v,0) for k,v in s['restaurant_revisions'].items()}
        refs=set(); ids=set(); require(isinstance(s['reservations'],list))
        for r in s['reservations']:
            ident(r['reservation_id']); require(r['reservation_id'] not in ids); ids.add(r['reservation_id'])
            require(isinstance(r['reference'],str) and re.fullmatch('[A-Z0-9]{6,12}',r['reference']) and r['reference'] not in refs); refs.add(r['reference'])
            require(r['user_id'] in uids and r['status'] in ('confirmed','cancelled'))
            proposed=service.proposed(s,r)
            require(all(codec.equal(proposed[k],r[k]) for k in proposed))
            r['party_size']=integer(r['party_size']); r['revision']=integer(r['revision'])
            for key in ('starts_at','ends_at','created_at'):
                require(isinstance(r[key],str) and datetime.fromisoformat(r[key]).utcoffset() is not None)
            from domain import terms
            require(codec.equal(r['accepted_terms'],terms(service.restaurant(s,r['restaurant_id']))))
            require(isinstance(r['facts'],list) and len(r['facts'])==r['revision'])
            previous=None
            for index,f in enumerate(r['facts'],1):
                require(f['seq']==index and f['revision']==index and f['type'] in ('created','changed','cancelled'))
                require(f['provenance'] in ('fixture_seed','diner_operation') and isinstance(f['changes'],dict))
                require(codec.equal(f['accepted_terms'],r['accepted_terms']))
                timestamp=instant(f['at']); require(previous is None or timestamp>=previous); previous=timestamp
                require((index==1)==(f['type']=='created'))
            require((r['facts'][-1]['type']=='cancelled' or r['facts'][0].get('initial_status')=='cancelled') == (r['status']=='cancelled'))
        confirmed=[r for r in s['reservations'] if r['status']=='confirmed']
        service.free(dict(s,reservations=[]),confirmed)
        scopes=set(); require(isinstance(s['receipts'],list))
        for r in s['receipts']:
            require(r['user_id'] in uids and r['method']=='POST' and r['path'] in ('/reservations','/reservation-moves'))
            require(isinstance(r['key'],str) and 1<=len(r['key'])<=255)
            scope=(r['user_id'],r['method'],r['path'],r['key']); require(scope not in scopes); scopes.add(scope)
            require(isinstance(r['request_json'],str) and isinstance(r['response_json'],str))
            request=codec.loads(r['request_json']); response=codec.loads(r['response_json'])
            require(isinstance(request,dict) and isinstance(response,dict))
            records=response.get('reservations',[]) if r['path']=='/reservation-moves' else [response]
            require(isinstance(records,list) and 1<=len(records)<=8)
            for record in records:
                require(record['reference'] in refs)
                live=next(v for v in s['reservations'] if v['reference']==record['reference'])
                require(record['reservation_id']==live['reservation_id'] and live['user_id']==r['user_id'])
                from domain import PUBLIC
                require(set(record)==set(PUBLIC))
        def domain_numbers(value):
            if isinstance(value, Decimal):
                return integer(value,0)
            if isinstance(value, list):
                return [domain_numbers(v) for v in value]
            if isinstance(value, dict):
                return {k:domain_numbers(v) for k,v in value.items()}
            return value
        s=domain_numbers(s)
        codec.dumps(s)
        return s
    except (Error,ValueError,TypeError,KeyError,OverflowError,AttributeError):
        raise Error(422,'validation_failed')
