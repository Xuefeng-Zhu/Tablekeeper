import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from .validation import fail
UTC = timezone.utc
DAYS = ['mon','tue','wed','thu','fri','sat','sun']

def local_parse(value):
    if not isinstance(value,str): fail('malformed_request',400)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}',value): fail()
    try: return datetime.strptime(value,'%Y-%m-%dT%H:%M')
    except ValueError: fail()

def resolve(wall, zone, boundary=False):
    tz=ZoneInfo(zone)
    candidates=[]
    for fold in (0,1):
        instant=wall.replace(tzinfo=tz,fold=fold).astimezone(UTC)
        if instant.astimezone(tz).replace(tzinfo=None)==wall: candidates.append(instant)
    if candidates: return min(candidates)
    if boundary:
        for minute in range(1,181):
            try: return resolve(wall+timedelta(minutes=minute),zone)
            except Exception: pass
    fail('invalid_local_time')

def interval(restaurant, value):
    wall=local_parse(value)
    start=resolve(wall,restaurant['timezone'])
    hours=next((h for h in restaurant['opening_hours'] if h['weekday']==DAYS[wall.weekday()]),None)
    if hours is None: fail('outside_opening_hours')
    opening=local_parse(wall.strftime('%Y-%m-%d')+'T'+hours['opens'])
    closing=local_parse(wall.strftime('%Y-%m-%d')+'T'+hours['closes'])
    try: end=start+timedelta(minutes=restaurant['reservation_duration_minutes'])
    except OverflowError: fail('outside_opening_hours')
    if wall<opening or wall>=closing or end>resolve(closing,restaurant['timezone'],True): fail('outside_opening_hours')
    if int((wall-opening).total_seconds()/60)%restaurant['slot_minutes']: fail('not_on_slot_grid')
    return start,end

def formatted(instant,zone):
    return instant.astimezone(ZoneInfo(zone)).isoformat()

def now(): return datetime.now(UTC)
