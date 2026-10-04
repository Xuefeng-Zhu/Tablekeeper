import copy, re, secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .core import *
from .store import Store,Transaction,empty
class Service:
    def __init__(self,now=None):
        self.store=Store(); self.now=now or (lambda: datetime.now(UTC))
    def auth(self,s,header):
        if not header or not re.fullmatch(r'Bearer [^\s]+',header): fail(401,'unauthenticated')
        session=next((x for x in s['sessions'] if x['token']==header[7:]),None)
        if session is None: fail(401,'unauthenticated')
        return session['user_id']
    def get_restaurant(self,s,rid):
        if not isinstance(rid,str): fail(400,'malformed_request')
        if len(rid)>64: fail()
        r=next((r for r in s['restaurants'] if r['id']==rid),None)
        if r is None: fail(404,'not_found')
        return r
    def booking(self,s,ref,user):
        b=next((b for b in s['reservations'] if b['reference']==ref and b['_user_id']==user),None)
        if b is None: fail(404,'not_found')
        return b
    def available(self,s,p,excluded=()):
        if any(b['reference'] not in excluded and b['status']=='confirmed' and overlaps(b,p) for b in s['reservations']): fail(409,'table_unavailable')
    def cutoff(self,b,now):
        try: passed=now>=instant(b['starts_at'])-timedelta(minutes=b['_terms']['cancellation_cutoff_minutes'])
        except OverflowError: passed=True
        if passed: fail(409,'cutoff_passed')
    def terms(self,r): return {k:copy.deepcopy(r[k]) for k in ['slot_minutes','reservation_duration_minutes','cancellation_cutoff_minutes','tables']}
    def changed(self,s,b,p,r,kind):
        if all(b[k]==v for k,v in p.items()): return False
        b.update(p); b['_terms']=self.terms(r); b['_revision']+=1
        self.record_history(s,b,kind,stamp(self.now()))
        return True
    def bump(self,s,rid):
        s['restaurant_revisions'][rid]+=1
        s.mark_dirty()
    def record_history(self,s,b,kind,at):
        event=dict(sequence=b['_revision'],kind=kind,at=at,state=view(b))
        if isinstance(s,Transaction): s.append_history(b['reservation_id'],event)
        else: b.setdefault('_history',[]).append(event)
    def new_booking(self,s,r,b,user,seed=False):
        p=proposal(r,b); self.available(s,p)
        ref=text(b,'reference') if seed else ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(10))
        while not seed and any(x['reference']==ref for x in s['reservations']): ref=''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(10))
        if not re.fullmatch('[A-Z0-9]{6,12}',ref): fail()
        bid=text(b,'id',64) if seed else uid()
        if any(x['reference']==ref or x['reservation_id']==bid for x in s['reservations']): fail()
        result=dict(reservation_id=bid,reference=ref,**p,status='confirmed',created_at=stamp(self.now()),_user_id=user,_terms=self.terms(r),_revision=1)
        self.record_history(s,result,'created',result['created_at'])
        s['reservations'].append(result); return result
    def reset(self,b):
        candidate=empty(); ids=set(); emails=set()
        for u in array(b,'users'):
            if not isinstance(u,dict): fail()
            i=text(u,'id',64); email,p=credentials(u,True); name=text(u,'display_name')
            if i in ids or email in emails: fail()
            ids.add(i); emails.add(email); candidate['users'].append(dict(id=i,email=email,display_name=name,password=password(p)))
        ids=set()
        for value in array(b,'restaurants'):
            r=restaurant(value)
            if r['id'] in ids: fail()
            ids.add(r['id']); candidate['restaurants'].append(r); candidate['restaurant_revisions'][r['id']]=0
        for value in array(b,'reservations'):
            if not isinstance(value,dict): fail()
            user=text(value,'user_id',64)
            if not any(u['id']==user for u in candidate['users']): fail()
            self.new_booking(candidate,self.get_restaurant(candidate,text(value,'restaurant_id',64)),value,user,True)
        self.store.replace(candidate)
    def login(self,b,signup):
        email,p=credentials(b,signup)
        if signup:
            name=text(b,'display_name'); hashed=password(p)
            with self.store.transaction(True) as s:
                if any(u['email']==email for u in s['users']): fail(409,'email_taken')
                u=dict(id=uid(),email=email,display_name=name,password=hashed); s['users'].append(u)
                return self.session(s,u)
        with self.store.transaction() as s:
            u=next((u for u in s['users'] if u['email']==email),None); generation=self.store.generation
        if u is None or not password(p,u['password']): fail(401,'unauthenticated')
        with self.store.transaction(True) as s:
            if generation!=self.store.generation or u not in s['users']: fail(401,'unauthenticated')
            return self.session(s,u)
    def session(self,s,u):
        token=secrets.token_urlsafe(32); s['sessions'].append(dict(token=token,user_id=u['id'])); s.mark_dirty()
        return dict(user_id=u['id'],display_name=u['display_name'],token=token)
    def amend(self,s,b,changes,now):
        if b['status']=='cancelled': fail(409,'reservation_cancelled')
        self.cutoff(b,now); r=self.get_restaurant(s,b['restaurant_id'])
        fields={k:changes.get(k,b[k]) for k in ['table_id','starts_at_local','party_size']}
        return r,proposal(r,fields)
    def route(self,method,path,q,b,raw,header,key):
        if method=='GET' and path=='/health':
            self.store.health(); return 200,{'status':'ok'}
        if method=='POST' and path=='/_test/reset': self.reset(b); return 204,None
        if method=='POST' and path=='/_test/import':
            from .state_io import validate_import
            self.store.replace(validate_import(b,self)); return 204,None
        if method=='GET' and path=='/_test/export':
            return 200,dict(track='tablekeeper',format_version=1,state=self.store.export())
        if method=='POST' and path in ['/auth/signup','/auth/login']: return (201 if path.endswith('signup') else 200),self.login(b,path.endswith('signup'))
        write=method in ['POST','PATCH']
        with self.store.transaction(write) as s:
            if method=='GET' and path=='/restaurants': return 200,{'restaurants':[{k:r[k] for k in ['id','name','timezone']} for r in s['restaurants']]}
            if method=='GET' and path.startswith('/restaurants/'): return 200,self.get_restaurant(s,path[len('/restaurants/'):])
            if method=='GET' and path=='/availability': return 200,self.availability(s,q)
            user=self.auth(s,header); now=self.now()
            receipt_path=method=='POST' and path in ['/reservations','/reservation-moves']
            if receipt_path:
                if key is None or key=='': fail(400,'missing_idempotency_key')
                if len(key)>255: fail()
                receipt=s.receipt(user,method,path,key)
                if receipt:
                    if not equal(parse(receipt['request']),b): fail(409,'idempotency_key_reuse')
                    return 200,json.loads(receipt['response'])
            if method=='GET' and path=='/reservations': return 200,{'reservations':[view(x) for x in sorted((x for x in s['reservations'] if x['_user_id']==user),key=lambda x:(instant(x['starts_at']),x['reservation_id']),reverse=True)]}
            if method=='POST' and path=='/reservations':
                r=self.get_restaurant(s,text(b,'restaurant_id',64)); result=view(self.new_booking(s,r,b,user)); self.bump(s,r['id']); status=201
            elif method=='POST' and path=='/reservation-moves':
                moves=b.get('moves')
                if not isinstance(moves,list) or not 1<=len(moves)<=8 or any(not isinstance(x,dict) or not isinstance(x.get('reference'),str) for x in moves): fail()
                refs=[x['reference'] for x in moves]
                if len(set(refs))!=len(refs): fail()
                proposals=[]; rid=None
                for move in moves:
                    old=self.booking(s,move['reference'],user)
                    if rid is not None and rid!=old['restaurant_id']: fail()
                    rid=old['restaurant_id']; r,p=self.amend(s,old,move,now); proposals.append((old,r,p))
                for i,(_,_,p) in enumerate(proposals):
                    self.available(s,p,refs)
                    if any(overlaps(p,other[2]) for other in proposals[:i]): fail(409,'table_unavailable')
                changes=[self.changed(s,old,p,r,'amended') for old,r,p in proposals]
                if any(changes): self.bump(s,rid)
                result={'reservations':[view(old) for old,_,_ in proposals]}; status=201
            elif path.startswith('/reservations/'):
                ref=path[len('/reservations/'):]; cancel=ref.endswith('/cancel')
                if cancel: ref=ref[:-7]
                old=self.booking(s,ref,user)
                if method=='GET' and not cancel: return 200,view(old)
                if method=='POST' and cancel:
                    if old['status']!='cancelled':
                        self.cutoff(old,now); old['status']='cancelled'; old['_revision']+=1
                        self.record_history(s,old,'cancelled',stamp(now)); self.bump(s,old['restaurant_id'])
                    return 200,view(old)
                if method=='PATCH' and not cancel:
                    r,p=self.amend(s,old,b,now); self.available(s,p,[ref])
                    if self.changed(s,old,p,r,'amended'): self.bump(s,r['id'])
                    return 200,view(old)
                fail(404,'not_found')
            else: fail(404,'not_found')
            if receipt_path: s.insert_receipt(dict(user_id=user,method=method,path=path,key=key,request=raw,response=json.dumps(result,separators=(',',':'))))
            return status,result
    def availability(self,s,q):
        if any(k not in q for k in ['restaurant_id','date','party_size']): fail()
        r=self.get_restaurant(s,q['restaurant_id']); day=date(q['date'])
        if not re.fullmatch('[0-9]+',q['party_size']) or int(q['party_size'])<1: fail()
        party=int(q['party_size']); slots=[]
        h=next((x for x in r['opening_hours'] if x['weekday']==DAYS[day.weekday()]),None)
        if h:
            for minute in range(clock(h['opens']),clock(h['closes']),r['slot_minutes']):
                local=q['date']+'T'+f'{minute//60:02}:{minute%60:02}'
                try: start,end=timing(r,local)
                except Error as e:
                    if e.code in ['invalid_local_time','outside_opening_hours']: continue
                    raise
                tables=[]
                for t in r['tables']:
                    p=dict(restaurant_id=r['id'],table_id=t['id'],starts_at=start,ends_at=end)
                    if t['capacity']>=party and not any(x['status']=='confirmed' and overlaps(x,p) for x in s['reservations']): tables.append(t['id'])
                slots.append(dict(starts_at_local=local,starts_at=start,available_table_ids=tables))
        return dict(restaurant_id=r['id'],date=q['date'],timezone=r['timezone'],slots=slots)
