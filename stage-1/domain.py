"""Shared booking planner: local grids, absolute intervals, final-set occupancy."""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from values import fail, field, identifier, APIError
UTC = timezone.utc
WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']


def now():
    return datetime.now(UTC)


def instant(text):
    try:
        value = datetime.fromisoformat(text)
        if value.tzinfo is None:
            fail()
        return value.astimezone(UTC)
    except (ValueError, TypeError):
        fail()


def local(text):
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}', text):
        fail()
    try:
        return datetime.strptime(text, '%Y-%m-%dT%H:%M')
    except ValueError:
        fail()


def resolve(value, zone):
    candidates = []
    for fold in (0, 1):
        utc = value.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) == value:
            candidates.append(utc)
    if not candidates:
        fail(422, 'invalid_local_time')
    return min(candidates)


def boundary(value, zone):
    # Spec does not define opening/closing in a gap: advance to the first valid minute.
    for _ in range(1441):
        try:
            return resolve(value, zone)
        except APIError as error:
            if error.code != 'invalid_local_time':
                raise
            value += timedelta(minutes=1)
    fail()


def hours(restaurant, value):
    day = WEEKDAYS[value.weekday()]
    for entry in restaurant['opening_hours']:
        if entry['weekday'] == day:
            prefix = value.strftime('%Y-%m-%dT')
            return local(prefix + entry['opens']), local(prefix + entry['closes'])
    return None


def find_restaurant(state, rid):
    for r in state['restaurants']:
        if r['id'] == rid:
            return r
    fail(404, 'not_found')


def plan(state, body, existing=None):
    rid = existing['restaurant_id'] if existing else identifier(body, 'restaurant_id')
    restaurant = find_restaurant(state, rid)
    merged = {**existing, **{k: v for k, v in body.items() if k in ('table_id', 'party_size', 'starts_at_local')}} if existing else body
    tid = identifier(merged, 'table_id')
    table = next((t for t in restaurant['tables'] if t['id'] == tid), None)
    if table is None:
        fail(404, 'not_found')
    if 'party_size' not in merged:
        fail()
    raw_party = merged['party_size']
    from decimal import Decimal
    if isinstance(raw_party, bool) or not isinstance(raw_party, (int, Decimal)) or raw_party < 1 or (isinstance(raw_party, Decimal) and raw_party != raw_party.to_integral_value()):
        fail()
    if raw_party > table['capacity']:
        fail(422, 'party_exceeds_capacity')
    party = int(raw_party)
    text = field(merged, 'starts_at_local')
    naive = local(text)
    zone = ZoneInfo(restaurant['timezone'])
    start = resolve(naive, zone)
    try:
        end = start + timedelta(minutes=restaurant['reservation_duration_minutes'])
    except OverflowError:
        fail(422, 'outside_opening_hours')
    window = hours(restaurant, naive)
    if window is None:
        fail(422, 'outside_opening_hours')
    opening, closing = window
    if naive < opening or naive >= closing or start < boundary(opening, zone) or end > boundary(closing, zone):
        fail(422, 'outside_opening_hours')
    if int((naive - opening).total_seconds() // 60) % restaurant['slot_minutes']:
        fail(422, 'not_on_slot_grid')
    if existing and all(existing[k] == v for k, v in [('table_id', tid), ('party_size', party), ('starts_at_local', text)]):
        return {k: existing[k] for k in ('restaurant_id', 'table_id', 'party_size', 'starts_at_local', 'starts_at', 'ends_at')}
    return {'restaurant_id': rid, 'table_id': tid, 'party_size': party,
            'starts_at_local': text, 'starts_at': start.astimezone(zone).isoformat(),
            'ends_at': end.astimezone(zone).isoformat()}


def overlaps(a, b):
    return (a['status'] == b['status'] == 'confirmed' and
            a['restaurant_id'] == b['restaurant_id'] and a['table_id'] == b['table_id'] and
            instant(a['starts_at']) < instant(b['ends_at']) and instant(b['starts_at']) < instant(a['ends_at']))


def check_occupancy(changed, others):
    for index, record in enumerate(changed):
        if any(overlaps(record, other) for other in others + changed[:index]):
            fail(409, 'table_unavailable')


def visible(state, reference, uid):
    for record in state['reservations']:
        if record['reference'] == reference and record['user_id'] == uid:
            return record
    fail(404, 'not_found')


def amendable(state, record, clock):
    if record['status'] == 'cancelled':
        fail(409, 'reservation_cancelled')
    restaurant = find_restaurant(state, record['restaurant_id'])
    if clock >= instant(record['starts_at']) - timedelta(minutes=restaurant['cancellation_cutoff_minutes']):
        fail(409, 'cutoff_passed')


def public(record):
    return {k: v for k, v in record.items() if k != 'user_id'}


def availability(state, query):
    if any(not query.get(k) for k in ('restaurant_id', 'date', 'party_size')):
        fail()
    rid, date, party = (query[k][0] for k in ('restaurant_id', 'date', 'party_size'))
    identifier({'restaurant_id': rid}, 'restaurant_id')
    if not re.fullmatch(r'[0-9]+', party) or len(party) > 12 or int(party) < 1:
        fail()
    naive = local(date + 'T00:00')
    restaurant = find_restaurant(state, rid)
    slots = []
    window = hours(restaurant, naive)
    if window:
        cursor, close = window
        while cursor < close:
            text = cursor.strftime('%Y-%m-%dT%H:%M')
            try:
                # A synthetic capacity-valid table lets shared planning decide legal instants.
                table = restaurant['tables'][0] if restaurant['tables'] else None
                if table is None:
                    zone = ZoneInfo(restaurant['timezone'])
                    start = resolve(cursor, zone)
                    legal = start + timedelta(minutes=restaurant['reservation_duration_minutes']) <= boundary(close, zone)
                    candidate = {'starts_at': start.astimezone(zone).isoformat()}
                else:
                    candidate = plan(state, {'restaurant_id': rid, 'table_id': table['id'], 'party_size': 1, 'starts_at_local': text})
                    legal = True
                if legal:
                    available = []
                    for t in restaurant['tables']:
                        if t['capacity'] >= int(party):
                            record = {**candidate, 'status': 'confirmed', 'table_id': t['id'], 'restaurant_id': rid}
                            if not any(overlaps(record, other) for other in state['reservations']):
                                available.append(t['id'])
                    slots.append({'starts_at_local': text, 'starts_at': candidate['starts_at'], 'available_table_ids': available})
            except APIError as error:
                if error.code not in ('invalid_local_time', 'outside_opening_hours'):
                    raise
            cursor += timedelta(minutes=restaurant['slot_minutes'])
    return {'restaurant_id': rid, 'date': date, 'timezone': restaurant['timezone'], 'slots': slots}
