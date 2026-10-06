"""Portable state construction and structural/invariant validation."""
import hashlib
import hmac
import re
import secrets
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from values import fail, field, identifier, integer, plain, validate_tree, APIError
from domain import plan, instant, now, check_occupancy
HASH_SLOTS = threading.BoundedSemaphore(2)


def empty():
    return {'schema_version': 1, 'users': [], 'sessions': [], 'restaurants': [], 'reservations': [], 'receipts': []}


def credential(password):
    salt = secrets.token_hex(16)
    with HASH_SLOTS:
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32).hex()
    return {'algorithm': 'scrypt', 'n': 16384, 'r': 8, 'p': 1, 'salt': salt, 'hash': digest}


def password_matches(password, stored):
    with HASH_SLOTS:
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(stored['salt']), n=stored['n'], r=stored['r'], p=stored['p'], dklen=32).hex()
    return hmac.compare_digest(digest, stored['hash'])


def email(body):
    value = field(body, 'email')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+', value):
        fail()
    return value


def restaurants(raw):
    if not isinstance(raw, list):
        fail(400, 'malformed_request')
    result, ids = [], set()
    for r in raw:
        if not isinstance(r, dict):
            fail(400, 'malformed_request')
        rid = identifier(r, 'id')
        if rid in ids:
            fail()
        ids.add(rid)
        zone = field(r, 'timezone')
        try:
            ZoneInfo(zone)
        except (ValueError, ZoneInfoNotFoundError):
            fail()
        item = {'id': rid, 'name': field(r, 'name'), 'timezone': zone}
        for key in ('slot_minutes', 'reservation_duration_minutes', 'cancellation_cutoff_minutes'):
            if key not in r:
                fail()
            item[key] = integer(r[key], minimum=0 if key == 'cancellation_cutoff_minutes' else 1)
        item['opening_hours'], days = [], set()
        for entry in field(r, 'opening_hours', list):
            if not isinstance(entry, dict):
                fail(400, 'malformed_request')
            day = field(entry, 'weekday')
            opening, closing = field(entry, 'opens'), field(entry, 'closes')
            if day not in ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] or day in days:
                fail()
            if any(not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', t) for t in (opening, closing)) or closing <= opening:
                fail()
            days.add(day)
            item['opening_hours'].append({'weekday': day, 'opens': opening, 'closes': closing})
        item['tables'], tids = [], set()
        for table in field(r, 'tables', list):
            if not isinstance(table, dict):
                fail(400, 'malformed_request')
            tid = identifier(table, 'id')
            if tid in tids:
                fail()
            tids.add(tid)
            if 'capacity' not in table:
                fail()
            item['tables'].append({'id': tid, 'label': field(table, 'label'), 'capacity': integer(table['capacity'])})
        result.append(item)
    return result


def reset(body):
    state = empty()
    state['restaurants'] = restaurants(body.get('restaurants', []))
    users = body.get('users', [])
    if not isinstance(users, list):
        fail(400, 'malformed_request')
    ids, emails = set(), set()
    for user in users:
        if not isinstance(user, dict):
            fail(400, 'malformed_request')
        uid, address = identifier(user, 'id'), email(user)
        if uid in ids or address in emails:
            fail()
        ids.add(uid); emails.add(address)
        password = field(user, 'password')
        state['users'].append({'id': uid, 'email': address, 'display_name': field(user, 'display_name'), 'password': credential(password)})
    bookings = body.get('reservations', [])
    if not isinstance(bookings, list):
        fail(400, 'malformed_request')
    refs, bids = set(), set()
    for booking in bookings:
        if not isinstance(booking, dict):
            fail(400, 'malformed_request')
        bid, uid = identifier(booking, 'id'), identifier(booking, 'user_id')
        ref = field(booking, 'reference')
        if bid in bids or ref in refs or uid not in ids or not re.fullmatch('[A-Z0-9]{6,12}', ref):
            fail()
        record = {**plan(state, booking), 'reservation_id': bid, 'user_id': uid, 'reference': ref,
                  'status': 'confirmed', 'created_at': now().isoformat()}
        check_occupancy([record], state['reservations'])
        state['reservations'].append(record)
        refs.add(ref); bids.add(bid)
    return state


def unique(items, key):
    values = [item[key] for item in items]
    if len(values) != len(set(values)):
        fail()
    return set(values)


def validate_public_record(record, state, require_owner=True):
    if not isinstance(record, dict):
        fail()
    identifier(record, 'reservation_id')
    if not re.fullmatch('[A-Z0-9]{6,12}', field(record, 'reference')) or record['status'] not in ('confirmed', 'cancelled'):
        fail()
    target = plan(state, record)
    if any(record[k] != target[k] for k in ('restaurant_id', 'table_id', 'party_size', 'starts_at_local')):
        fail()
    if instant(record['starts_at']) != instant(target['starts_at']) or instant(record['ends_at']) != instant(target['ends_at']):
        fail()
    instant(record['created_at'])
    if require_owner and record['user_id'] not in {u['id'] for u in state['users']}:
        fail()


def imported(body):
    try:
        if body.get('track') != 'tablekeeper' or isinstance(body.get('format_version'), bool) or body.get('format_version') != 1:
            fail()
        state = plain(body['state'])
        if not isinstance(state, dict) or state.get('schema_version') != 1 or type(state.get('schema_version')) is not int:
            fail()
        for key in ('users', 'sessions', 'restaurants', 'reservations', 'receipts'):
            if not isinstance(state[key], list) or any(not isinstance(v, dict) for v in state[key]):
                fail()
        if restaurants(state['restaurants']) != state['restaurants']:
            fail()
        ids = unique(state['users'], 'id')
        unique(state['users'], 'email')
        for user in state['users']:
            identifier(user, 'id'); email(user); field(user, 'display_name')
            c = user['password']
            if not isinstance(c, dict) or c.get('algorithm') != 'scrypt' or any(type(c.get(k)) is not int or c[k] != v for k, v in [('n',16384),('r',8),('p',1)]):
                fail()
            if not re.fullmatch('[0-9a-f]{32}', c['salt']) or not re.fullmatch('[0-9a-f]{64}', c['hash']):
                fail()
        unique(state['sessions'], 'token')
        for session in state['sessions']:
            if not field(session, 'token') or session['user_id'] not in ids:
                fail()
        unique(state['reservations'], 'reservation_id'); unique(state['reservations'], 'reference')
        for booking in state['reservations']:
            validate_public_record(booking, state)
        check_occupancy(state['reservations'], [])
        receipt_keys = set()
        for receipt in state['receipts']:
            if receipt['user_id'] not in ids or receipt['method'] != 'POST' or receipt['path'] not in ('/reservations', '/reservation-moves'):
                fail()
            key = field(receipt, 'key')
            if not 1 <= len(key) <= 255:
                fail()
            namespace = (receipt['user_id'], receipt['method'], receipt['path'], key)
            if namespace in receipt_keys:
                fail()
            receipt_keys.add(namespace)
            validate_tree(receipt['body'])
            if receipt['body'][0] != 'object':
                fail()
            response = receipt['response']
            records = [response] if receipt['path'] == '/reservations' else response['reservations']
            if not isinstance(records, list) or not 1 <= len(records) <= 8:
                fail()
            for record in records:
                validate_public_record(record, state, False)
                actual = next((b for b in state['reservations'] if b['reference'] == record['reference']), None)
                if actual is None or actual['reservation_id'] != record['reservation_id'] or actual['user_id'] != receipt['user_id'] or actual['created_at'] != record['created_at'] or actual['restaurant_id'] != record['restaurant_id'] or record['status'] != 'confirmed':
                    fail()
        return state
    except (APIError, KeyError, TypeError, ValueError, OverflowError, RecursionError):
        fail()
