"""Exact instants without datetime's narrower UTC calendar limit.

Public/local dates stay in years 0001..9999. An offset can place their UTC
instant just outside that range, so compare integer microseconds from ordinal
one instead of materializing UTC datetimes. ZoneInfo conversion normally uses
the real UTC date. At the two UTC edges, move by a Gregorian 400-year cycle:
IANA's future POSIX rules repeat on that cycle, and its pre-transition initial
offset applies at the ancient edge. Restore the local year after conversion.
No shifted date is stored, exposed or used for duration/ordering comparisons.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import re
from values import fail

UTC = timezone.utc
DAY_US = 86400 * 1000000
MINUTE_US = 60 * 1000000
CYCLE_US = 146097 * DAY_US
MAX_US = datetime.max.toordinal() * DAY_US
EPOCH = datetime(1, 1, 1)


def microseconds(value):
    offset = value.utcoffset()
    if offset is None:
        fail()
    local_us = ((value.toordinal() - 1) * DAY_US +
                (value.hour * 3600 + value.minute * 60 + value.second) * 1000000 + value.microsecond)
    offset_us = (offset.days * 86400 + offset.seconds) * 1000000 + offset.microseconds
    return local_us - offset_us


def instant(text):
    try:
        normalized=text[:-1]+'Z' if text.endswith('z') else text
        ticks=microseconds(datetime.fromisoformat(normalized))
        # Closures may supply arbitrary RFC3339 fractional precision. Booking
        # timestamps remain integer microseconds; rational comparisons preserve
        # submicrosecond closure boundaries without storing rounded endpoints.
        match=re.search(r'T\d{2}:\d{2}:\d{2}\.([0-9]+)',normalized,re.IGNORECASE)
        if match and len(match[1])>6:
            ticks+=Fraction(Decimal('0.'+match[1][6:]))
        return ticks
    except (ValueError, TypeError):
        fail()


def to_local(ticks, zone):
    shifted, year_adjustment = ticks, 0
    if ticks < 0:
        shifted += CYCLE_US
        year_adjustment = -400
    elif ticks >= MAX_US:
        shifted -= CYCLE_US
        year_adjustment = 400
    utc = (EPOCH + timedelta(microseconds=shifted)).replace(tzinfo=UTC)
    result = utc.astimezone(zone)
    if year_adjustment:
        result = result.replace(year=result.year + year_adjustment)
    return result


def resolve(value, zone):
    candidates = []
    for fold in (0, 1):
        ticks = microseconds(value.replace(tzinfo=zone, fold=fold))
        if to_local(ticks, zone).replace(tzinfo=None) == value:
            candidates.append(ticks)
    if not candidates:
        fail(422, 'invalid_local_time')
    return min(candidates)
