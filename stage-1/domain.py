"""Atomic, process-local reservation domain. All published roots are immutable by convention."""
import copy
import hashlib
import hmac
import json
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

UTC = timezone.utc
DAYS = 'mon tue wed thu fri sat sun'.split()

class Error(Exception):
    def __init__(self, status=422, code='validation_failed'):
        self.status, self.code = status, code

def fail(status=422, code='validation_failed'):
    raise Error(status, code)

def parse(raw):
    try:
        value = json.loads(raw, parse_float=Decimal, parse_constant=lambda _: fail(400, 'malformed_request'))
    except (ValueError, UnicodeError, RecursionError):
        fail(400, 'malformed_request')
    if not isinstance(value, dict): fail(400, 'malformed_request')
    return value

def equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool): return type(a) is type(b) and a == b
    if isinstance(a, (int, Decimal)) and isinstance(b, (int, Decimal)): return a == b
    if type(a) is not type(b): return False
    if isinstance(a, dict): return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list): return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return a == b

def string(obj, key, ident=False):
    if key not in obj: fail()
    v = obj[key]
    if not isinstance(v, str): fail(400, 'malformed_request')
    if ident and (not v or len(v) > 64): fail()
    return v

def integer(v, minimum=1, maximum=None):
    if isinstance(v, bool) or not isinstance(v, (int, Decimal)) or not (-10**100 < v < 10**100) or v != int(v): fail()
    if v < minimum or (maximum is not None and v > maximum): fail()
    return int(v)

def local_value(v):
    if not isinstance(v, str): fail(400, 'malformed_request')
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}', v): fail()
    try: return datetime.strptime(v, '%Y-%m-%dT%H:%M')
    except ValueError: fail()

def resolve(local, zone):
    aware = local.replace(tzinfo=zone, fold=0)
    instant = aware.astimezone(UTC)
    if instant.astimezone(zone).replace(tzinfo=None) != local: fail(422, 'invalid_local_time')
    return instant

def instant(v):
    return datetime.fromisoformat(v).astimezone(UTC)

def password(value, record=None):
    salt = bytes.fromhex(record['salt']) if record else secrets.token_bytes(16)
    digest = hashlib.scrypt(value.encode(), salt=salt, n=16384, r=8, p=1, dklen=32, maxmem=64*1024*1024).hex()
    if record: return hmac.compare_digest(digest, record['digest'])
    return {'algorithm': 'scrypt', 'n': 16384, 'r': 8, 'p': 1, 'salt': salt.hex(), 'digest': digest}

def empty():
    return {'schema_version': 1, 'users': {}, 'tokens': {}, 'restaurants': {}, 'reservations': {}, 'receipts': []}

class Service:
    def __init__(self, clock=None):
        self.lock = threading.RLock()
        self.hash_slots = threading.Semaphore(2)
        self.state = empty()
        self.clock = clock or (lambda: datetime.now(UTC))
        for z in ['Europe/Berlin', 'America/New_York']: ZoneInfo(z)

    def publish(self, **changes):
        self.state = dict(self.state, **changes)

    def authenticate(self, header):
        if not isinstance(header, str) or not re.fullmatch(r'Bearer [^\s]+', header): fail(401, 'unauthenticated')
        uid = self.state['tokens'].get(header[7:])
        if uid is None: fail(401, 'unauthenticated')
        return uid

    def auth(self, path, body):
        email, pw = string(body, 'email'), string(body, 'password')
        if path == '/auth/signup':
            name = string(body, 'display_name')
            if not re.fullmatch(r'[^\s@]+@[^\s@]+', email) or len(pw) < 8: fail()
            with self.hash_slots: hashed = password(pw)
            with self.lock:
                if any(u['email'] == email for u in self.state['users'].values()): fail(409, 'email_taken')
                uid = secrets.token_hex(16)
                user = {'id': uid, 'email': email, 'display_name': name, 'password_hash': hashed}
                token = secrets.token_urlsafe(32)
                self.publish(users=dict(self.state['users'], **{uid: user}), tokens=dict(self.state['tokens'], **{token: uid}))
                return 201, {'user_id': uid, 'display_name': name, 'token': token}
        while True:
            with self.lock:
                user = next((u for u in self.state['users'].values() if u['email'] == email), None)
            if user is None: fail(401, 'unauthenticated')
            with self.hash_slots: valid = password(pw, user['password_hash'])
            with self.lock:
                if self.state['users'].get(user['id']) is not user: continue
                if not valid: fail(401, 'unauthenticated')
                token = secrets.token_urlsafe(32)
                self.publish(tokens=dict(self.state['tokens'], **{token: user['id']}))
                return 200, {'user_id': user['id'], 'display_name': user['display_name'], 'token': token}

    def restaurant(self, rid, state=None):
        r = (state or self.state)['restaurants'].get(rid)
        if r is None: fail(404, 'not_found')
        return r

    def proposal(self, b, state=None):
        rid, tid = string(b, 'restaurant_id', True), string(b, 'table_id', True)
        local = local_value(b.get('starts_at_local')) if 'starts_at_local' in b else fail()
        if 'party_size' not in b: fail()
        party = integer(b['party_size'])
        r = self.restaurant(rid, state)
        table = next((t for t in r['tables'] if t['id'] == tid), None)
        if table is None: fail(404, 'not_found')
        zone = ZoneInfo(r['timezone'])
        start = resolve(local, zone)
        try: end = start + timedelta(minutes=r['reservation_duration_minutes'])
        except OverflowError: fail()
        windows = [h for h in r['opening_hours'] if h['weekday'] == DAYS[local.weekday()]]
        suitable = []
        for h in windows:
            op = datetime.combine(local.date(), datetime.strptime(h['opens'], '%H:%M').time())
            cl = datetime.combine(local.date(), datetime.strptime(h['closes'], '%H:%M').time())
            while True:
                try: close = resolve(cl, zone); break
                except Error: cl += timedelta(minutes=1)
            if op <= local < cl and end <= close: suitable.append(op)
        if not suitable: fail(422, 'outside_opening_hours')
        if not any(int((local-op).total_seconds()/60) % r['slot_minutes'] == 0 for op in suitable): fail(422, 'not_on_slot_grid')
        if party > table['capacity']: fail(422, 'party_exceeds_capacity')
        return {'restaurant_id': rid, 'table_id': tid, 'party_size': party, 'starts_at_local': b['starts_at_local'], 'starts_at': start.astimezone(zone).isoformat(), 'ends_at': end.astimezone(zone).isoformat()}

    @staticmethod
    def overlaps(a, b):
        return a['restaurant_id'] == b['restaurant_id'] and a['table_id'] == b['table_id'] and instant(a['starts_at']) < instant(b['ends_at']) and instant(b['starts_at']) < instant(a['ends_at'])

    def occupancy(self, proposals, excluded=(), state=None):
        existing = [r for k,r in (state or self.state)['reservations'].items() if k not in excluded and r['status'] == 'confirmed']
        for p in proposals:
            if any(self.overlaps(p, r) for r in existing): fail(409, 'table_unavailable')
            existing.append(p)

    def owned(self, reference, uid):
        r = next((r for r in self.state['reservations'].values() if r['reference'] == reference and r['user_id'] == uid), None)
        if r is None: fail(404, 'not_found')
        return r

    @staticmethod
    def response(r):
        return {k:v for k,v in r.items() if k != 'user_id'}

    def cutoff(self, r, now):
        minutes = self.restaurant(r['restaurant_id'])['cancellation_cutoff_minutes']
        if now >= instant(r['starts_at']) - timedelta(minutes=minutes): fail(409, 'cutoff_passed')

    def amended(self, r, item, now):
        if r['status'] == 'cancelled': fail(409, 'reservation_cancelled')
        self.cutoff(r, now)
        merged = dict(r)
        merged.update({k:item[k] for k in ['table_id','starts_at_local','party_size'] if k in item})
        return dict(r, **self.proposal(merged))

    def receipt(self, uid, path, key, body):
        if not key: fail(400, 'missing_idempotency_key')
        if len(key) > 255: fail()
        for r in self.state['receipts']:
            if (r['user_id'], r['method'], r['path'], r['key']) == (uid, 'POST', path, key):
                if not equal(parse(r['request_json']), body): fail(409, 'idempotency_key_reuse')
                return copy.deepcopy(r['response'])
        return None

    def availability(self, q):
        for k in ['restaurant_id','date','party_size']:
            if k not in q: fail()
        rid = string(q, 'restaurant_id', True)
        if not re.fullmatch(r'[0-9]+', q['party_size']): fail()
        party = integer(int(q['party_size']))
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', q['date']): fail()
        try: day = datetime.strptime(q['date'], '%Y-%m-%d')
        except ValueError: fail()
        r = self.restaurant(rid)
        slots = []
        for h in r['opening_hours']:
            if h['weekday'] != DAYS[day.weekday()]: continue
            cursor = datetime.combine(day.date(), datetime.strptime(h['opens'], '%H:%M').time())
            close = datetime.combine(day.date(), datetime.strptime(h['closes'], '%H:%M').time())
            while cursor < close:
                label = cursor.isoformat(timespec='minutes')
                try:
                    # Time validity is independent of capacity, including empty table sets.
                    virtual = copy.deepcopy(r)
                    virtual['tables'] = [{'id':'availability', 'capacity':party}]
                    p = self.proposal({'restaurant_id':rid,'table_id':'availability','party_size':party,'starts_at_local':label}, {'restaurants':{rid:virtual}})
                    ids = []
                    for t in r['tables']:
                        candidate = dict(p, table_id=t['id'])
                        if t['capacity'] >= party and not any(x['status']=='confirmed' and self.overlaps(candidate,x) for x in self.state['reservations'].values()): ids.append(t['id'])
                    slots.append({'starts_at_local':label,'starts_at':p['starts_at'],'available_table_ids':ids})
                except Error as e:
                    if e.code not in ['invalid_local_time','outside_opening_hours']: raise
                cursor += timedelta(minutes=r['slot_minutes'])
        return {'restaurant_id':rid,'date':q['date'],'timezone':r['timezone'],'slots':slots}

    def transact(self, method, path, body, raw, headers, query):
        with self.lock:
            if method == 'GET' and path == '/health': return 200, {'status':'ok'}
            if method == 'GET' and path == '/_test/export': return 200, {'track':'tablekeeper','format_version':1,'state':copy.deepcopy(self.state)}
            if method == 'GET' and path == '/restaurants': return 200, {'restaurants':[{k:r[k] for k in ['id','name','timezone']} for r in self.state['restaurants'].values()]}
            if method == 'GET' and path.startswith('/restaurants/'): return 200, copy.deepcopy(self.restaurant(path.split('/')[-1]))
            if method == 'GET' and path == '/availability': return 200, self.availability(query)
            uid = self.authenticate(headers.get('Authorization'))
            now = self.clock()
            if method == 'POST' and path in ['/reservations','/reservation-moves']:
                key = headers.get('Idempotency-Key')
                replay = self.receipt(uid, path, key, body)
                if replay is not None: return 200, replay
                if path == '/reservations':
                    proposal = self.proposal(body)
                    self.occupancy([proposal])
                    reference = secrets.token_hex(5).upper()
                    while any(r['reference'] == reference for r in self.state['reservations'].values()): reference = secrets.token_hex(5).upper()
                    r = dict(proposal, reservation_id=secrets.token_hex(16), reference=reference, user_id=uid, status='confirmed', created_at=now.isoformat())
                    proposals = [r]
                    response = self.response(r)
                else:
                    moves = body.get('moves')
                    if not isinstance(moves,list) or not 1 <= len(moves) <= 8: fail()
                    refs = []
                    for m in moves:
                        if not isinstance(m,dict) or not isinstance(m.get('reference'),str) or len(m['reference'])>64 or m['reference'] in refs: fail()
                        refs.append(m['reference'])
                    proposals = []
                    for m in moves:
                        old = self.owned(m['reference'],uid)
                        if proposals and old['restaurant_id'] != proposals[0]['restaurant_id']: fail()
                        proposals.append(self.amended(old,m,now))
                    self.occupancy(proposals, [r['reservation_id'] for r in proposals])
                    response = {'reservations':[self.response(r) for r in proposals]}
                records = dict(self.state['reservations'])
                records.update({r['reservation_id']:r for r in proposals})
                receipt = {'user_id':uid,'method':method,'path':path,'key':key,'request_json':raw,'response':copy.deepcopy(response)}
                self.publish(reservations=records, receipts=self.state['receipts']+[receipt])
                return 201, response
            if method == 'GET' and path == '/reservations':
                records = sorted((r for r in self.state['reservations'].values() if r['user_id']==uid), key=lambda r:instant(r['starts_at']), reverse=True)
                return 200, {'reservations':[self.response(r) for r in records]}
            match = re.fullmatch(r'/reservations/([^/]+)(/cancel)?',path)
            if match:
                r = self.owned(match[1],uid)
                if method == 'GET' and not match[2]: return 200, self.response(r)
                if method == 'POST' and match[2]:
                    if r['status']=='cancelled': return 200, self.response(r)
                    self.cutoff(r,now)
                    changed = dict(r,status='cancelled')
                elif method == 'PATCH' and not match[2]:
                    changed = self.amended(r,body,now)
                    self.occupancy([changed],[r['reservation_id']])
                else: fail(404,'not_found')
                self.publish(reservations=dict(self.state['reservations'], **{r['reservation_id']:changed}))
                return 200, self.response(changed)
            fail(404,'not_found')
