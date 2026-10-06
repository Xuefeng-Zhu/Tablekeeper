"""One authoritative state, immutable candidates published under a transaction lock."""
import json
import secrets
import threading
from domain import availability, plan, public, visible, amendable, check_occupancy, instant, now, changed_record
from values import fail, field, canonical
from state import empty, reset, imported, email, credential, password_matches


def encoded(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode()


class Service:
    def __init__(self):
        self.state = empty()
        self.lock = threading.RLock()

    def publish(self, candidate, status, response):
        payload = encoded(response) if response is not None else b''
        self.state = candidate
        return status, payload

    def auth(self, headers):
        header = headers.get('Authorization', '')
        if not header.startswith('Bearer ') or not header[7:] or ' ' in header[7:]:
            fail(401, 'unauthenticated')
        for session in self.state['sessions']:
            if session['token'] == header[7:]:
                return session['user_id']
        fail(401, 'unauthenticated')

    def authentication(self, path, body):
        address, password = email(body), field(body, 'password')
        if path == '/auth/signup':
            name = field(body, 'display_name')
            if len(password) < 8:
                fail()
            hashed = credential(password)
            with self.lock:
                if any(u['email'] == address for u in self.state['users']):
                    fail(409, 'email_taken')
                user = {'id': 'u_' + secrets.token_hex(16), 'email': address, 'display_name': name, 'password': hashed}
                return self.session(user, 201, new=True)
        with self.lock:
            user = next((u for u in self.state['users'] if u['email'] == address), None)
            observed = self.state
        if user is None or not password_matches(password, user['password']):
            fail(401, 'unauthenticated')
        with self.lock:
            # Replacement during hashing cannot revive an account/token.
            if self.state is not observed and user not in self.state['users']:
                fail(401, 'unauthenticated')
            return self.session(user, 200)

    def session(self, user, status, new=False):
        token = secrets.token_urlsafe(32)
        candidate = {**self.state, 'sessions': self.state['sessions'] + [{'token': token, 'user_id': user['id']}]}
        if new:
            candidate['users'] = self.state['users'] + [user]
        return self.publish(candidate, status, {'user_id': user['id'], 'display_name': user['display_name'], 'token': token})

    def request(self, method, path, query, body, headers):
        if method == 'POST' and path in ('/auth/signup', '/auth/login'):
            return self.authentication(path, body)
        if method == 'POST' and path in ('/_test/reset', '/_test/import'):
            replacement = reset(body) if path.endswith('reset') else imported(body)
            with self.lock:
                return self.publish(replacement, 204, None)
        with self.lock:
            state = self.state
            if method == 'GET' and path == '/health':
                return 200, encoded({'status': 'ok'})
            if method == 'GET' and path == '/_test/export':
                return 200, encoded({'track': 'tablekeeper', 'format_version': 1, 'state': state})
            if method == 'GET' and path == '/restaurants':
                return 200, encoded({'restaurants': [{k: r[k] for k in ('id','name','timezone')} for r in state['restaurants']]})
            if method == 'GET' and path.startswith('/restaurants/'):
                from domain import find_restaurant
                return 200, encoded(find_restaurant(state, path.split('/')[2]))
            if method == 'GET' and path == '/availability':
                return 200, encoded(availability(state, query))
            uid = self.auth(headers)
            clock = now()
            receipt = None
            if method == 'POST' and path in ('/reservations', '/reservation-moves'):
                key = headers.get('Idempotency-Key')
                if not key:
                    fail(400, 'missing_idempotency_key')
                if len(key) > 255:
                    fail()
                tree = canonical(body)
                for stored in state['receipts']:
                    if (stored['user_id'], stored['method'], stored['path'], stored['key']) == (uid, method, path, key):
                        if stored['body'] != tree:
                            fail(409, 'idempotency_key_reuse')
                        return 200, encoded(stored['response'])
                receipt = {'user_id': uid, 'method': method, 'path': path, 'key': key, 'body': tree}
            if method == 'GET' and path == '/reservations':
                records = sorted((r for r in state['reservations'] if r['user_id'] == uid), key=lambda r: instant(r['starts_at']), reverse=True)
                return 200, encoded({'reservations': [public(r) for r in records]})
            if method == 'POST' and path == '/reservations':
                target = plan(state, body)
                refs = {r['reference'] for r in state['reservations']}
                reference = secrets.token_hex(5).upper()
                while reference in refs:
                    reference = secrets.token_hex(5).upper()
                record = {**target, 'reservation_id': 'res_' + secrets.token_hex(16), 'reference': reference,
                          'user_id': uid, 'status': 'confirmed', 'created_at': clock.isoformat()}
                check_occupancy([record], state['reservations'])
                return self.commit([record], public(record), receipt, 201)
            if method == 'POST' and path == '/reservation-moves':
                moves = body.get('moves')
                if not isinstance(moves, list) or not 1 <= len(moves) <= 8:
                    fail()
                references = []
                for move in moves:
                    if not isinstance(move, dict) or not isinstance(move.get('reference'), str) or move['reference'] in references:
                        fail()
                    references.append(move['reference'])
                changed, restaurant = [], None
                for move in moves:
                    old = visible(state, move['reference'], uid)
                    if restaurant is not None and old['restaurant_id'] != restaurant:
                        fail()
                    restaurant = old['restaurant_id']
                    amendable(state, old, clock)
                    changed.append(changed_record(old, plan(state, move, old)))
                others = [r for r in state['reservations'] if r['reference'] not in references]
                check_occupancy(changed, others)
                return self.commit(changed, {'reservations': [public(r) for r in changed]}, receipt, 201)
            pieces = path.strip('/').split('/')
            if len(pieces) in (2, 3) and pieces[0] == 'reservations':
                old = visible(state, pieces[1], uid)
                if method == 'GET' and len(pieces) == 2:
                    return 200, encoded(public(old))
                if method == 'POST' and len(pieces) == 3 and pieces[2] == 'cancel':
                    if old['status'] == 'cancelled':
                        return 200, encoded(public(old))
                    amendable(state, old, clock)
                    record = {**old, 'status': 'cancelled'}
                    return self.commit([record], public(record), None, 200)
                if method == 'PATCH' and len(pieces) == 2:
                    amendable(state, old, clock)
                    record = changed_record(old, plan(state, body, old))
                    check_occupancy([record], [r for r in state['reservations'] if r['reference'] != old['reference']])
                    return self.commit([record], public(record), None, 200)
            fail(404, 'not_found')

    def commit(self, changed, response, receipt, status):
        replacements = {r['reference']: r for r in changed}
        candidate = {**self.state, 'reservations': [replacements.pop(r['reference'], r) for r in self.state['reservations']]}
        candidate['reservations'] += list(replacements.values())
        if receipt is not None:
            candidate['receipts'] = self.state['receipts'] + [{**receipt, 'response': response}]
        return self.publish(candidate, status, response)
