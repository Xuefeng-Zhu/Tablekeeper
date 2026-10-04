"""Validation, lossless request comparison, portable password and civil-time rules."""
import re, secrets, hashlib, hmac, threading
from . import codec as json
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
UTC = timezone.utc
DAYS = ['mon','tue','wed','thu','fri','sat','sun']
HASH_LIMIT = threading.Semaphore(2)
class Error(Exception):
    def __init__(self, status=422, code='validation_failed'):
        self.status, self.code = status, code

def fail(status=422, code='validation_failed'): raise Error(status, code)
def parse(raw):
    try:
        v=json.loads(raw, parse_int=Decimal, parse_float=Decimal, parse_constant=lambda _: fail(400,'malformed_request'))
    except (ValueError, UnicodeError): fail(400,'malformed_request')
    if not isinstance(v,dict): fail(400,'malformed_request')
    return v

def equal(a,b):
    if isinstance(a,Decimal) and isinstance(b,Decimal): return a==b
    if type(a) is not type(b): return False
    if isinstance(a,dict): return a.keys()==b.keys() and all(equal(a[k],b[k]) for k in a)
    if isinstance(a,list): return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
    return a==b

def text(o,k,maximum=None):
    if k not in o: fail()
    v=o[k]
    if not isinstance(v,str): fail(400,'malformed_request')
    if maximum is not None and (not v or len(v)>maximum): fail()
    return v

def integer(o,k,minimum=1):
    if k not in o: fail()
    v=o[k]
    if isinstance(v,bool) or not isinstance(v,(int,Decimal)):
        if k=='party_size': fail()
        fail(400,'malformed_request')
    if isinstance(v,Decimal) and not v.is_finite(): fail()
    if v!=int(v) or v<minimum: fail()
    return int(v)

def query_count(value):
    """Exact positive ASCII decimal count, independent of int string limits."""
    if not re.fullmatch('[0-9]+',value): fail()
    count=Decimal(value)
    if count<1: fail()
    return count

def array(o,k):
    if k not in o: fail()
    if not isinstance(o[k],list): fail(400,'malformed_request')
    return o[k]

def wall(s):
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}',s): fail()
    try: return datetime.fromisoformat(s)
    except ValueError: fail()

def date(s):
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',s): fail()
    try: return datetime.fromisoformat(s)
    except ValueError: fail()

def clock(s):
    if not re.fullmatch(r'[0-9]{2}:[0-9]{2}',s): fail()
    h,m=map(int,s.split(':'))
    if h>23 or m>59: fail()
    return h*60+m

def resolve(w,z,boundary=False):
    candidates=[]
    for fold in (0,1):
        try:
            u=w.replace(tzinfo=z,fold=fold).astimezone(UTC)
            if u.astimezone(z).replace(tzinfo=None)==w: candidates.append(u)
        except (OverflowError,ValueError): continue
    if candidates: return min(candidates)
    if boundary:
        for n in range(1,1441):
            try: return resolve(w+timedelta(minutes=n),z)
            except Error: pass
    fail(422,'invalid_local_time')

def stamp(u,z=UTC):
    v=u.astimezone(z)
    if v.utcoffset().total_seconds()%60: v=u.astimezone(UTC)
    return v.isoformat(timespec='seconds')

def instant(s): return datetime.fromisoformat(s).astimezone(UTC)
def uid(): return secrets.token_hex(16)
def password(p,record=None):
    salt=bytes.fromhex(record['salt']) if record else secrets.token_bytes(16)
    with HASH_LIMIT: digest=hashlib.scrypt(p.encode(),salt=salt,n=16384,r=8,p=1,dklen=64,maxmem=67108864).hex()
    if record: return hmac.compare_digest(digest,record['digest'])
    return dict(version=1,algorithm='scrypt',n=16384,r=8,p=1,dklen=64,maxmem=67108864,salt=salt.hex(),digest=digest)

def credentials(b,signup=False):
    email=text(b,'email'); p=text(b,'password')
    if not re.fullmatch(r'[^@\s]+@[^@\s]+',email): fail()
    if signup and len(p)<8: fail()
    return email,p

def restaurant(b):
    if not isinstance(b,dict): fail()
    r={k:text(b,k,64 if k=='id' else None) for k in ['id','name','timezone']}
    try: ZoneInfo(r['timezone'])
    except (ValueError,ZoneInfoNotFoundError): fail()
    for k in ['slot_minutes','reservation_duration_minutes','cancellation_cutoff_minutes']: r[k]=integer(b,k,0 if k.startswith('cancellation') else 1)
    r['opening_hours']=[]; seen=set()
    for h in array(b,'opening_hours'):
        if not isinstance(h,dict): fail()
        d=text(h,'weekday'); op=text(h,'opens'); cl=text(h,'closes')
        if d not in DAYS or d in seen or clock(cl)<=clock(op): fail()
        seen.add(d); r['opening_hours'].append(dict(weekday=d,opens=op,closes=cl))
    r['tables']=[]; seen=set()
    for t in array(b,'tables'):
        if not isinstance(t,dict): fail()
        tid=text(t,'id',64)
        if tid in seen: fail()
        seen.add(tid); r['tables'].append(dict(id=tid,label=text(t,'label'),capacity=integer(t,'capacity')))
    return r

def timing(r,local):
    w=wall(local); z=ZoneInfo(r['timezone']); start=resolve(w,z)
    h=next((h for h in r['opening_hours'] if h['weekday']==DAYS[w.weekday()]),None)
    if h is None: fail(422,'outside_opening_hours')
    m=w.hour*60+w.minute; op=clock(h['opens']); cl=clock(h['closes'])
    if not op<=m<cl: fail(422,'outside_opening_hours')
    if (m-op)%r['slot_minutes']: fail(422,'not_on_slot_grid')
    try:
        closing=resolve(w.replace(hour=cl//60,minute=cl%60),z,True)
        # Compare elapsed UTC capacity before adding duration at calendar edges.
        # Whole-minute division is exact even for historical second-based offsets.
        remaining_minutes=(closing-start)//timedelta(minutes=1)
        if r['reservation_duration_minutes']>remaining_minutes: fail(422,'outside_opening_hours')
        end=start+timedelta(minutes=r['reservation_duration_minutes'])
        return stamp(start,z),stamp(end,z)
    except (OverflowError,ValueError): fail()

def proposal(r,b):
    tid=text(b,'table_id',64); local=text(b,'starts_at_local'); party=integer(b,'party_size')
    t=next((t for t in r['tables'] if t['id']==tid),None)
    if t is None: fail(404,'not_found')
    start,end=timing(r,local)
    if party>t['capacity']: fail(422,'party_exceeds_capacity')
    return dict(restaurant_id=r['id'],table_id=tid,starts_at_local=local,party_size=party,starts_at=start,ends_at=end)

def overlaps(a,b):
    return a['restaurant_id']==b['restaurant_id'] and a['table_id']==b['table_id'] and instant(a['starts_at'])<instant(b['ends_at']) and instant(b['starts_at'])<instant(a['ends_at'])

def view(b): return {k:v for k,v in b.items() if not k.startswith('_')}
