"""Validate historical response DTOs and immutable links, never current booking terms."""
import re
from .core import fail,text,integer,wall,instant

TIMESTAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?[+-][0-9]{2}:[0-9]{2}')


def timestamp(value):
    if not isinstance(value,str) or not TIMESTAMP.fullmatch(value): fail()
    return instant(value)


def response_booking(response, user, bookings, restaurants):
    if not isinstance(response,dict): fail()
    ref=text(response,'reference')
    if not re.fullmatch('[A-Z0-9]{6,12}',ref) or ref not in bookings: fail()
    current=bookings[ref]
    # Only immutable values link an old successful response to its current record.
    if current['_user_id']!=user: fail()
    for field in ['reservation_id','restaurant_id']:
        if text(response,field,64)!=current[field]: fail()
    if text(response,'created_at')!=current['created_at']: fail()
    timestamp(response['created_at'])
    table=text(response,'table_id',64)
    if not any(t['id']==table for t in restaurants[current['restaurant_id']]['tables']): fail()
    integer(response,'party_size')
    wall(text(response,'starts_at_local'))
    start=timestamp(text(response,'starts_at')); end=timestamp(text(response,'ends_at'))
    if end<=start or text(response,'status')!='confirmed': fail()
    return current


def validate_receipt(request,response,receipt,bookings,restaurants):
    user=receipt['user_id']
    if receipt['path']=='/reservations':
        current=response_booking(response,user,bookings,restaurants)
        if text(request,'restaurant_id',64)!=current['restaurant_id']: fail()
        # Structural validation only. Do not rerun booking validation against now.
        text(request,'table_id',64);wall(text(request,'starts_at_local'));integer(request,'party_size')
        return
    moves=request.get('moves'); responses=response.get('reservations')
    if not isinstance(moves,list) or not 1<=len(moves)<=8 or not isinstance(responses,list) or len(moves)!=len(responses): fail()
    seen=set(); restaurant_id=None
    for move,historical in zip(moves,responses):
        if not isinstance(move,dict): fail()
        ref=text(move,'reference')
        if ref in seen: fail()
        seen.add(ref)
        current=response_booking(historical,user,bookings,restaurants)
        if historical['reference']!=ref: fail()
        if restaurant_id is not None and current['restaurant_id']!=restaurant_id: fail()
        restaurant_id=current['restaurant_id']
        if 'table_id' in move: text(move,'table_id',64)
        if 'starts_at_local' in move: wall(text(move,'starts_at_local'))
        if 'party_size' in move: integer(move,'party_size')
