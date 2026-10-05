"""Serialized transactional service, authentication, and private factual ledger."""
import copy
import hashlib
import hmac
import re
import secrets
import threading
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import codec
from rules import Error, require, field, ident, integer, local, resolve, boundaries, interval, instant, overlaps, UTC

PUBLIC = ('reservation_id','reference','restaurant_id','table_id','party_size','status','starts_at_local','starts_at','ends_at','created_at')
CHANGES = ('starts_at_local','table_id','party_size')


def empty():
    return dict(schema_version=1, users=[], restaurants=[], reservations=[], tokens={}, receipts=[], restaurant_revisions={})


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
    return dict(algorithm='scrypt',n=16384,r=8,p=1,salt=salt,hash=digest)


def public(record):
    return {k:record[k] for k in PUBLIC}


def terms(restaurant):
    return dict(policy_version=0, slot_minutes=restaurant['slot_minutes'],
                reservation_duration_minutes=restaurant['reservation_duration_minutes'],
                cancellation_cutoff_minutes=restaurant['cancellation_cutoff_minutes'],
                opening_hours=copy.deepcopy(restaurant['opening_hours']),
                table_capacities=[{'table_id':t['id'],'capacity':t['capacity']} for t in restaurant['tables']])


def fact(record, event, now, before=None, provenance='diner_operation'):
    changes = {}
    for key in CHANGES:
        if before is None or before[key] != record[key]:
            changes[key] = {'before':None if before is None else before[key], 'after':record[key]}
    timestamp = now.isoformat()
    if record['facts']:
        timestamp = max(timestamp,record['facts'][-1]['at'])
    entry = dict(seq=len(record['facts'])+1, type=event, at=timestamp, changes=changes,
                 revision=record['revision'], accepted_terms=copy.deepcopy(record['accepted_terms']),provenance=provenance)
    if provenance=='fixture_seed':
        entry['initial_status']=record['status']
    record['facts'].append(entry)


class Service:
    def __init__(self, clock=None):
        self.state=empty()
        self.lock=threading.RLock()
        self.clock=clock or (lambda:datetime.now(UTC))

    def ledger(self, reference):
        """Private in-process QA adapter; contains no user credentials or sessions."""
        with self.lock:
            r=next(r for r in self.state['reservations'] if r['reference']==reference)
            return copy.deepcopy(r['facts'])

    def restaurant(self,s,rid):
        ident(rid)
        r=next((r for r in s['restaurants'] if r['id']==rid),None)
        require(r is not None,404,'not_found')
        return r

    def owner(self,s,ref,uid):
        r=next((r for r in s['reservations'] if r['reference']==ref and r['user_id']==uid),None)
        require(r is not None,404,'not_found')
        return r

    def proposed(self,s,body,old=None):
        rid=old['restaurant_id'] if old else field(body,'restaurant_id')
        restaurant=self.restaurant(s,rid)
        values={k:body[k] if k in body else (old[k] if old else field(body,k)) for k in CHANGES}
        ident(values['table_id'])
        table=next((t for t in restaurant['tables'] if t['id']==values['table_id']),None)
        require(table is not None,404,'not_found')
        party=integer(values['party_size'])
        starts,ends=interval(restaurant,values['starts_at_local'])
        require(party <= table['capacity'],422,'party_exceeds_capacity')
        return dict(restaurant_id=rid,table_id=table['id'],party_size=party,starts_at_local=values['starts_at_local'],starts_at=starts,ends_at=ends)

    def free(self,s,records,exclude=()):
        existing=[r for r in s['reservations'] if r['status']=='confirmed' and r['reference'] not in exclude]
        for record in records:
            require(not any(overlaps(record,r) for r in existing),409,'table_unavailable')
            existing.append(record)

    def cutoff(self,r,now):
        require(now < instant(r['starts_at'])-timedelta(minutes=r['accepted_terms']['cancellation_cutoff_minutes']),409,'cutoff_passed')

    def amend(self,s,r,body,now):
        require(r['status']!='cancelled',409,'reservation_cancelled')
        self.cutoff(r,now)
        proposed=self.proposed(s,body,r)
        changed=any(proposed[k]!=r[k] for k in CHANGES)
        candidate=copy.deepcopy(r)
        if changed:
            candidate.update(proposed)
            candidate['revision']+=1
            fact(candidate,'changed',now,r)
        return candidate,changed

    def seed(self,fixture,now):
        from stateio import validate_restaurants
        s=empty()
        restaurants=field(fixture,'restaurants',list)
        # JSON conversion narrows validated domain numbers only; receipt numbers stay text.
        s['restaurants']=validate_restaurants(restaurants)
        s['restaurant_revisions']={r['id']:0 for r in s['restaurants']}
        for raw in field(fixture,'users',list):
            require(isinstance(raw,dict))
            uid=ident(field(raw,'id')); email=field(raw,'email'); password=field(raw,'password'); name=field(raw,'display_name')
            require(not any(u['id']==uid or u['email']==email for u in s['users']))
            s['users'].append(dict(id=uid,email=email,display_name=name,password_hash=password_hash(password)))
        for raw in fixture.get('reservations',[]):
            require(isinstance(raw,dict))
            uid=ident(field(raw,'user_id'))
            require(any(u['id']==uid for u in s['users']))
            values=self.proposed(s,raw)
            reference=field(raw,'reference'); rid=ident(field(raw,'id'))
            require(re.fullmatch('[A-Z0-9]{6,12}',reference))
            require(not any(r['reference']==reference or r['reservation_id']==rid for r in s['reservations']))
            record=dict(values,reservation_id=rid,reference=reference,user_id=uid,status=raw.get('status','confirmed'),created_at=raw.get('created_at',now.isoformat()),revision=1,facts=[],accepted_terms=terms(self.restaurant(s,values['restaurant_id'])))
            require(record['status'] in ('confirmed','cancelled'))
            if record['status']=='confirmed': self.free(s,[record])
            fact(record,'created',now,provenance='fixture_seed')
            s['reservations'].append(record)
        return s

    def execute(self,method,path,query,body,headers):
        with self.lock:
            # All mutations stage a detached candidate, including sessions and controls.
            s=copy.deepcopy(self.state) if method!='GET' else self.state
            status,value,changed=self.dispatch(s,method,path,query,body,headers,self.clock())
            text=codec.dumps(value) if status!=204 else ''
            if changed: self.state=s
            return status,text

    def dispatch(self,s,method,path,q,b,h,now):
        if method=='GET' and path=='/health': return 200,{'status':'ok'},False
        if method=='POST' and path=='/_test/reset':
            new=self.seed(b,now); s.clear(); s.update(new)
            return 204,None,True
        if method=='GET' and path=='/_test/export': return 200,dict(track='tablekeeper',format_version=1,state=s),False
        if method=='POST' and path=='/_test/import':
            from stateio import validate_import
            new=validate_import(b,self); s.clear(); s.update(new)
            return 204,None,True
        if method=='POST' and path in ('/auth/signup','/auth/login'):
            email=field(b,'email'); password=field(b,'password')
            user=next((u for u in s['users'] if u['email']==email),None)
            if path.endswith('signup'):
                name=field(b,'display_name')
                require(re.fullmatch(r'[^\s@]+@[^\s@]+',email) and len(password)>=8)
                require(user is None,409,'email_taken')
                user=dict(id=uuid.uuid4().hex,email=email,display_name=name,password_hash=password_hash(password)); s['users'].append(user)
            else:
                require(user is not None,401,'unauthenticated')
                require(hmac.compare_digest(password_hash(password,user['password_hash']['salt'])['hash'],user['password_hash']['hash']),401,'unauthenticated')
            token=secrets.token_urlsafe(32); s['tokens'][token]=user['id']
            return (201 if path.endswith('signup') else 200),dict(user_id=user['id'],display_name=user['display_name'],token=token),True
        if method=='GET' and path=='/restaurants':
            return 200,{'restaurants':[{k:r[k] for k in ('id','name','timezone')} for r in s['restaurants']]},False
        if method=='GET' and path.startswith('/restaurants/'):
            return 200,self.restaurant(s,path.split('/')[2]),False
        if method=='GET' and path=='/availability':
            require(all(k in q and q[k] for k in ('restaurant_id','date','party_size')))
            require(re.fullmatch('[0-9]+',q['party_size']))
            party=integer(int(q['party_size']))
            wall=local(q['date']+'T00:00'); r=self.restaurant(s,q['restaurant_id']); slots=[]
            try: opens,closes,_=boundaries(r,wall)
            except Error: return 200,dict(restaurant_id=r['id'],date=q['date'],timezone=r['timezone'],slots=[]),False
            candidate=opens
            while candidate<closes:
                value=f'{candidate.year:04d}-{candidate.month:02d}-{candidate.day:02d}T{candidate.hour:02d}:{candidate.minute:02d}'
                try: starts,ends=interval(r,value)
                except Error: candidate+=timedelta(minutes=r['slot_minutes']); continue
                available=[]
                for t in r['tables']:
                    proposed=dict(restaurant_id=r['id'],table_id=t['id'],starts_at=starts,ends_at=ends)
                    if t['capacity']>=party and not any(x['status']=='confirmed' and overlaps(proposed,x) for x in s['reservations']): available.append(t['id'])
                slots.append(dict(starts_at_local=value,starts_at=starts,available_table_ids=available))
                candidate+=timedelta(minutes=r['slot_minutes'])
            return 200,dict(restaurant_id=r['id'],date=q['date'],timezone=r['timezone'],slots=slots),False
        auth=h.get('Authorization','')
        uid=s['tokens'].get(auth[7:]) if auth.startswith('Bearer ') else None
        require(uid is not None,401,'unauthenticated')
        keyed=method=='POST' and path in ('/reservations','/reservation-moves')
        key=h.get('Idempotency-Key','')
        if keyed:
            require(bool(key),400,'missing_idempotency_key'); require(len(key)<=255)
            receipt=next((r for r in s['receipts'] if (r['user_id'],r['method'],r['path'],r['key'])==(uid,method,path,key)),None)
            if receipt:
                require(codec.equal(codec.loads(receipt['request_json']),b),409,'idempotency_key_reuse')
                return 200,codec.loads(receipt['response_json']),False
        if method=='GET' and path=='/reservations':
            records=sorted((r for r in s['reservations'] if r['user_id']==uid),key=lambda r:instant(r['starts_at']),reverse=True)
            return 200,{'reservations':[public(r) for r in records]},False
        if method=='POST' and path=='/reservations':
            values=self.proposed(s,b)
            reference=secrets.token_hex(5).upper()
            while any(r['reference']==reference for r in s['reservations']): reference=secrets.token_hex(5).upper()
            record=dict(values,reservation_id=uuid.uuid4().hex,reference=reference,user_id=uid,status='confirmed',created_at=now.isoformat(),revision=1,facts=[],accepted_terms=terms(self.restaurant(s,values['restaurant_id'])))
            self.free(s,[record]); fact(record,'created',now); s['reservations'].append(record)
            s['restaurant_revisions'][record['restaurant_id']]+=1
            result=public(record)
        elif method=='POST' and path=='/reservation-moves':
            moves=b.get('moves')
            require(isinstance(moves,list) and 1<=len(moves)<=8)
            require(all(isinstance(m,dict) and isinstance(m.get('reference'),str) for m in moves))
            refs=[m['reference'] for m in moves]; require(len(set(refs))==len(refs))
            originals=[self.owner(s,ref,uid) for ref in refs]
            require(len({r['restaurant_id'] for r in originals})==1)
            candidates=[self.amend(s,r,m,now) for r,m in zip(originals,moves)]
            self.free(s,[r for r,_ in candidates],refs)
            replacements={r['reference']:r for r,_ in candidates}
            s['reservations']=[replacements.get(r['reference'],r) for r in s['reservations']]
            if any(changed for _,changed in candidates): s['restaurant_revisions'][originals[0]['restaurant_id']]+=1
            result={'reservations':[public(r) for r,_ in candidates]}
        elif path.startswith('/reservations/'):
            parts=path.strip('/').split('/'); r=self.owner(s,parts[1],uid)
            if method=='GET' and len(parts)==2: return 200,public(r),False
            if method=='POST' and len(parts)==3 and parts[2]=='cancel':
                if r['status']=='cancelled': return 200,public(r),False
                self.cutoff(r,now); r['status']='cancelled'; r['revision']+=1; fact(r,'cancelled',now,r)
                s['restaurant_revisions'][r['restaurant_id']]+=1
                return 200,public(r),True
            if method=='PATCH' and len(parts)==2:
                candidate,changed=self.amend(s,r,b,now); self.free(s,[candidate],[r['reference']])
                if changed:
                    s['reservations'][s['reservations'].index(r)]=candidate
                    s['restaurant_revisions'][r['restaurant_id']]+=1
                return 200,public(candidate),changed
            raise Error(404,'not_found')
        else: raise Error(404,'not_found')
        s['receipts'].append(dict(user_id=uid,method=method,path=path,key=key,request_json=codec.dumps(b),response_json=codec.dumps(result)))
        return 201,result,True
