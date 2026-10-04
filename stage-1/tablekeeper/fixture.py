"""Fixture validation and transactional replacement. Never retains passwords."""
import re
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .validation import fail, identifier, string
from .auth import password_hash
from .store import clear, dumps


def object_(value):
    if not isinstance(value, dict): fail('malformed_request', 400)
    return value


def array(body, key):
    if key not in body: fail()
    if not isinstance(body[key], list): fail('malformed_request', 400)
    return body[key]


def integer(body, key, minimum):
    if key not in body: fail()
    value = body[key]
    if type(value) is not int: fail('malformed_request', 400)
    if value < minimum: fail()
    return value


def unique(values):
    if len(values) != len(set(values)): fail()


def restaurant_config(value):
    body = object_(value)
    result = {k: string(body, k) for k in ('name', 'timezone')}
    result['id'] = identifier(body, 'id')
    try: ZoneInfo(result['timezone'])
    except (ZoneInfoNotFoundError, ValueError): fail()
    for key in ('slot_minutes', 'reservation_duration_minutes'):
        result[key] = integer(body, key, 1)
    result['cancellation_cutoff_minutes'] = integer(body, 'cancellation_cutoff_minutes', 0)
    result['opening_hours'] = []
    for raw in array(body, 'opening_hours'):
        hour = object_(raw)
        weekday = string(hour, 'weekday')
        if weekday not in ('mon','tue','wed','thu','fri','sat','sun'): fail()
        entry = {'weekday':weekday}
        for key in ('opens','closes'):
            text = string(hour,key)
            if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', text): fail()
            entry[key] = text
        if entry['closes'] <= entry['opens']: fail()
        result['opening_hours'].append(entry)
    unique([h['weekday'] for h in result['opening_hours']])
    result['tables'] = []
    for raw in array(body, 'tables'):
        table = object_(raw)
        result['tables'].append({'id':identifier(table,'id'),'label':string(table,'label'),'capacity':integer(table,'capacity',1)})
    unique([t['id'] for t in result['tables']])
    return result


def replace_fixture(db, fixture):
    from .reservations import create
    users = []
    for raw in array(fixture, 'users'):
        body = object_(raw)
        email = string(body, 'email')
        if not re.fullmatch(r'[^@\s]+@[^@\s]+', email): fail()
        users.append({'id':identifier(body,'id'),'email':email,'display_name':string(body,'display_name'),'password_hash':password_hash(string(body,'password'))})
    unique([u['id'] for u in users]); unique([u['email'] for u in users])
    restaurants = [restaurant_config(r) for r in array(fixture,'restaurants')]
    unique([r['id'] for r in restaurants])
    seeds = array(fixture,'reservations')
    for raw in seeds:
        body = object_(raw)
        identifier(body,'id'); identifier(body,'user_id')
        reference = string(body,'reference')
        if not re.fullmatch('[A-Z0-9]{6,12}',reference): fail()
    unique([r['id'] for r in seeds]); unique([r['reference'] for r in seeds])
    try:
        clear(db)
        for user in users:
            db.execute('INSERT INTO users VALUES(?,?,?)',(user['id'],user['email'],dumps(user)))
        for i,r in enumerate(restaurants):
            db.execute('INSERT INTO restaurants(id,position,data) VALUES(?,?,?)',(r['id'],i,dumps(r)))
            for j,t in enumerate(r['tables']): db.execute('INSERT INTO dining_tables VALUES(?,?,?,?)',(t['id'],r['id'],j,dumps(t)))
        for seed in seeds: create(db,seed,seed['user_id'],seed)
    except sqlite3.IntegrityError: fail()
