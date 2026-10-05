"""Validation, wall clock resolution, and absolute occupancy predicates."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import re

UTC = timezone.utc
DAYS = ['mon','tue','wed','thu','fri','sat','sun']

class Error(Exception):
    def __init__(self, status=422, code='validation_failed'):
        self.status, self.code = status, code
        super().__init__(code)


def require(condition, status=422, code='validation_failed'):
    if not condition:
        raise Error(status, code)


def field(body, name, kind=str):
    require(name in body)
    value = body[name]
    require(isinstance(value, kind), 400, 'malformed_request')
    return value


def ident(value):
    require(isinstance(value,str),400,'malformed_request')
    require(0 < len(value) <= 64)
    return value


def integer(value, minimum=1):
    require(not isinstance(value,bool) and isinstance(value,(int,Decimal)))
    require(not isinstance(value,Decimal) or value.is_finite())
    require(value >= minimum and value == int(value))
    return int(value)


def local(value):
    require(isinstance(value,str),400,'malformed_request')
    require(re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}', value))
    try:
        return datetime.strptime(value,'%Y-%m-%dT%H:%M')
    except ValueError:
        raise Error()


def resolve(wall, zone):
    valid = []
    for fold in (0,1):
        utc = wall.replace(tzinfo=zone,fold=fold).astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) == wall:
            valid.append(utc)
    if not valid:
        raise Error(422,'invalid_local_time')
    return min(valid)


def opening(restaurant, wall):
    return next((h for h in restaurant['opening_hours'] if h['weekday']==DAYS[wall.weekday()]),None)


def boundaries(restaurant, wall):
    hours = opening(restaurant,wall)
    require(hours is not None,422,'outside_opening_hours')
    start = datetime.combine(wall.date(),datetime.strptime(hours['opens'],'%H:%M').time())
    close = datetime.combine(wall.date(),datetime.strptime(hours['closes'],'%H:%M').time())
    zone = ZoneInfo(restaurant['timezone'])
    while True:
        try:
            end = resolve(close,zone)
            break
        except Error:
            close += timedelta(minutes=1)
    return start, datetime.combine(wall.date(),datetime.strptime(hours['closes'],'%H:%M').time()), end


def interval(restaurant, value):
    wall = local(value)
    zone = ZoneInfo(restaurant['timezone'])
    start = resolve(wall,zone)
    opens, closes, closing = boundaries(restaurant,wall)
    require(opens <= wall < closes,422,'outside_opening_hours')
    require((wall-opens).total_seconds() % (restaurant['slot_minutes']*60) == 0,422,'not_on_slot_grid')
    end = start + timedelta(minutes=restaurant['reservation_duration_minutes'])
    require(end <= closing,422,'outside_opening_hours')
    return start.astimezone(zone).isoformat(),end.astimezone(zone).isoformat()


def instant(value):
    return datetime.fromisoformat(value).astimezone(UTC)


def overlaps(a,b):
    return (a['restaurant_id']==b['restaurant_id'] and a['table_id']==b['table_id'] and
            instant(a['starts_at']) < instant(b['ends_at']) and instant(b['starts_at']) < instant(a['ends_at']))
