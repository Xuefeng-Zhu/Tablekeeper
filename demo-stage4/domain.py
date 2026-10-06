"""Shared booking planner: local grids, absolute intervals, final-set occupancy."""
import re
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from values import fail, field, identifier, APIError
from temporal import instant, resolve, to_local, microseconds, MINUTE_US
UTC = timezone.utc
WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']


def now():
    return datetime.now(UTC)


def local(text):
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}', text):
        fail()
    try:
        return datetime.strptime(text, '%Y-%m-%dT%H:%M')
    except ValueError:
        fail()


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
            prefix = value.date().isoformat() + 'T'
            return local(prefix + entry['opens']), local(prefix + entry['closes'])
    return None


def find_restaurant(state, rid):
    for r in state['restaurants']:
        if r['id'] == rid:
            return r
    fail(404, 'not_found')


def members(record):
    return record.get('table_ids', [record.get('table_id')])


def selection(restaurant, body):
    if 'table_id' in body and 'table_ids' in body:
        fail()
    if 'table_ids' in body:
        ids = field(body, 'table_ids', list)
        ids = [identifier({'id': value}, 'id') for value in ids]
    else:
        ids = [identifier(body, 'table_id')]
    if not ids or len(ids) != len(set(ids)):
        fail()
    if len(ids) > 2:
        fail(422, 'combination_not_allowed')
    tables = {t['id']: t for t in restaurant['tables']}
    if any(t not in tables for t in ids):
        fail(404, 'not_found')
    if len(ids) == 2:
        ids = next((pair for pair in restaurant['combinable'] if set(pair) == set(ids)), None)
        if ids is None:
            fail(422, 'combination_not_allowed')
    return list(ids), sum(tables[t]['capacity'] for t in ids)


def changed_record(old, target):
    # A supplied pair replaces the old singleton representation completely.
    return {**{k: v for k, v in old.items() if k not in ('table_id', 'table_ids')}, **target}


def plan(state, body, existing=None):
    rid = existing['restaurant_id'] if existing else identifier(body, 'restaurant_id')
    restaurant = find_restaurant(state, rid)
    merged = {**existing, **{k: v for k, v in body.items() if k in ('party_size', 'starts_at_local')}} if existing else dict(body)
    if existing:
        merged.pop('table_id', None)
        merged.pop('table_ids', None)
        if 'table_id' in body or 'table_ids' in body:
            merged.update({k: body[k] for k in ('table_id', 'table_ids') if k in body})
        else:
            merged['table_ids'] = members(existing)
    ids, capacity = selection(restaurant, merged)
    if 'party_size' not in merged:
        fail()
    raw_party = merged['party_size']
    if isinstance(raw_party, bool) or not isinstance(raw_party, (int, Decimal)) or raw_party < 1 or (isinstance(raw_party, Decimal) and raw_party != raw_party.to_integral_value()):
        fail()
    if raw_party > capacity:
        fail(422, 'party_exceeds_capacity')
    party = int(raw_party)
    text = field(merged, 'starts_at_local')
    naive = local(text)
    zone = ZoneInfo(restaurant['timezone'])
    start = resolve(naive, zone)
    end = start + restaurant['reservation_duration_minutes'] * MINUTE_US
    window = hours(restaurant, naive)
    if window is None:
        fail(422, 'outside_opening_hours')
    opening, closing = window
    if naive < opening or naive >= closing or start < boundary(opening, zone) or end > boundary(closing, zone):
        fail(422, 'outside_opening_hours')
    if int((naive - opening).total_seconds() // 60) % restaurant['slot_minutes']:
        fail(422, 'not_on_slot_grid')
    seating = {'table_ids': ids}
    if len(ids) == 1:
        seating['table_id'] = ids[0]
    if existing and members(existing) == ids and existing['party_size'] == party and existing['starts_at_local'] == text:
        return {**{k: existing[k] for k in ('restaurant_id', 'party_size', 'starts_at_local', 'starts_at', 'ends_at')}, **seating}
    return {'restaurant_id': rid, **seating, 'party_size': party,
            'starts_at_local': text, 'starts_at': to_local(start, zone).isoformat(),
            'ends_at': to_local(end, zone).isoformat()}


def overlaps(a, b):
    return (a['status'] == b['status'] == 'confirmed' and
            a['restaurant_id'] == b['restaurant_id'] and bool(set(members(a)) & set(members(b))) and
            instant(a['starts_at']) < instant(b['ends_at']) and instant(b['starts_at']) < instant(a['ends_at']))


def closure_overlap(record, closure):
    return (record['status']=='confirmed' and record['restaurant_id']==closure['restaurant_id'] and closure['table_id'] in members(record) and instant(record['starts_at'])<instant(closure['to']) and instant(closure['from'])<instant(record['ends_at']))


def check_occupancy(changed, others, closures=()):
    for index, record in enumerate(changed):
        if any(overlaps(record, other) for other in others + changed[:index]) or any(closure_overlap(record,c) for c in closures):
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
    if microseconds(clock) >= instant(record['starts_at']) - restaurant['cancellation_cutoff_minutes'] * MINUTE_US:
        fail(409, 'cutoff_passed')


def public(record):
    return {k: v for k, v in record.items() if k != 'user_id'}


def availability(state, query):
    if any(not query.get(k) for k in ('restaurant_id', 'date', 'party_size')):
        fail()
    rid, date, party = (query[k][0] for k in ('restaurant_id', 'date', 'party_size'))
    identifier({'restaurant_id': rid}, 'restaurant_id')
    if not re.fullmatch(r'[0-9]+', party):
        fail()
    # Decimal preserves arbitrary digit lengths without Python's int-string limit.
    # Construction and comparison are exact and independent of Decimal precision.
    party = Decimal(party)
    if party < 1:
        fail()
    naive = local(date + 'T00:00')
    restaurant = find_restaurant(state, rid)
    slots = []
    window = hours(restaurant, naive)
    if window:
        cursor, close = window
        while cursor < close:
            text = cursor.isoformat(timespec='minutes')
            try:
                # A synthetic capacity-valid table lets shared planning decide legal instants.
                table = restaurant['tables'][0] if restaurant['tables'] else None
                if table is None:
                    zone = ZoneInfo(restaurant['timezone'])
                    start = resolve(cursor, zone)
                    legal = start + restaurant['reservation_duration_minutes'] * MINUTE_US <= boundary(close, zone)
                    candidate = {'starts_at': to_local(start, zone).isoformat()}
                else:
                    candidate = plan(state, {'restaurant_id': rid, 'table_id': table['id'], 'party_size': 1, 'starts_at_local': text})
                    legal = True
                if legal:
                    available, options = [], []
                    selections = [[t['id']] for t in restaurant['tables']] + restaurant['combinable']
                    capacities = {t['id']: t['capacity'] for t in restaurant['tables']}
                    for ids in selections:
                        capacity = sum(capacities[t] for t in ids)
                        if capacity >= party:
                            record = {**candidate, 'status': 'confirmed', 'table_ids': ids, 'restaurant_id': rid}
                            if not any(overlaps(record, other) for other in state['reservations']) and not any(closure_overlap(record,c) for c in state.get('closures',[])):
                                if len(ids) == 1:
                                    available.append(ids[0])
                                options.append({'table_ids': list(ids), 'capacity': capacity})
                    slots.append({'starts_at_local': text, 'starts_at': candidate['starts_at'], 'available_table_ids': available, 'available_options': options})

            except APIError as error:
                if error.code not in ('invalid_local_time', 'outside_opening_hours'):
                    raise
            step = restaurant['slot_minutes']
            remaining_minutes = int((close - cursor).total_seconds() // 60)
            if step >= remaining_minutes:
                break
            cursor += timedelta(minutes=step)
    return {'restaurant_id': rid, 'date': date, 'timezone': restaurant['timezone'], 'slots': slots}
