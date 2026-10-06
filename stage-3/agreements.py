"""Dated policy views and amendments against immutable accepted agreements."""
import copy
from decimal import Decimal
from domain import (plan as base_plan, availability as base_availability, find_restaurant,
                    members, selection, local, overlaps, instant)
from temporal import microseconds, MINUTE_US
from values import fail, field, integer, identifier, APIError
RULES = ('slot_minutes','reservation_duration_minutes','cancellation_cutoff_minutes','opening_hours','capacities')


def policy0(restaurant):
    return {'policy_version':0, **{k:copy.deepcopy(restaurant[k]) for k in RULES if k!='capacities'},
            'capacities':{t['id']:t['capacity'] for t in restaurant['tables']}}


def terms(policy):
    return {k:copy.deepcopy(policy[k]) for k in ('policy_version',)+RULES}


def selected(state, rid, date):
    eligible=[p for p in state['policies'][rid] if p['effective_from']<=date]
    return terms(max(eligible,key=lambda p:(p['effective_from'],p['policy_version']))) if eligible else policy0(find_restaurant(state,rid))


def view(state, rid, accepted):
    original=find_restaurant(state,rid)
    restaurant={**original,**{k:accepted[k] for k in RULES if k!='capacities'},
                'tables':[{**t,'capacity':accepted['capacities'][t['id']]} for t in original['tables']]}
    return {**state,'restaurants':[restaurant if r['id']==rid else r for r in state['restaurants']]}


def policy(body, restaurant):
    """All invalid publication fields, including ordinary types, are explicitly422."""
    try:
        date=field(body,'effective_from');local(date+'T00:00')
        numeric={}
        for name,minimum,maximum in [('slot_minutes',1,1440),('reservation_duration_minutes',1,1440),('cancellation_cutoff_minutes',0,10080)]:
            value=integer(body[name],minimum=minimum)
            if value>maximum:fail()
            numeric[name]=value
        capacities=field(body,'capacities',dict)
        if set(capacities)!={t['id'] for t in restaurant['tables']}:fail()
        capacities={k:integer(v) for k,v in capacities.items()}
        if any(v>100 for v in capacities.values()):fail()
        from stage2_state import restaurants
        raw={**restaurant,**numeric,'opening_hours':field(body,'opening_hours',list)}
        checked=restaurants([raw])[0]
        return {'effective_from':date,**numeric,'opening_hours':checked['opening_hours'],'capacities':capacities}
    except (APIError,KeyError,TypeError,ValueError,OverflowError):
        fail()


def expected(body, record):
    if 'expected_revision' not in body:return
    try:value=integer(body['expected_revision'])
    except APIError:fail()
    if value!=record['revision']:fail(409,'stale_revision')


def amendable(state, record, clock):
    if record['status']=='cancelled':fail(409,'reservation_cancelled')
    cutoff=record['accepted_terms']['cancellation_cutoff_minutes']
    if microseconds(clock)>=instant(record['starts_at'])-cutoff*MINUTE_US:fail(409,'cutoff_passed')


def plan(state, body, existing=None):
    rid=existing['restaurant_id'] if existing else identifier(body,'restaurant_id')
    restaurant=find_restaurant(state,rid)
    if existing:
        if 'table_id' in body or 'table_ids' in body:
            ids,_=selection(restaurant,body)
        else:ids=members(existing)
        raw_party=body.get('party_size',existing['party_size'])
        party=integer(raw_party,party=True)
        text=field(body,'starts_at_local') if 'starts_at_local' in body else existing['starts_at_local']
        local(text)
        if ids==members(existing) and party==existing['party_size'] and text==existing['starts_at_local']:
            return existing
        merged={'restaurant_id':rid,'table_ids':ids,'party_size':party,'starts_at_local':text}
    else:merged=body;text=field(body,'starts_at_local');local(text)
    accepted=selected(state,rid,text[:10])
    target=base_plan(view(state,rid,accepted),merged)
    return {**target,'revision':existing['revision']+1 if existing else 1,'accepted_terms':accepted}


def availability(state, query):
    if 'explain' in query and query['explain']!=['true']:fail()
    if not query.get('restaurant_id') or not query.get('date'):
        return base_availability(state,query)
    rid,date=query['restaurant_id'][0],query['date'][0]
    local(date+'T00:00')
    accepted=selected(state,rid,date)
    current=view(state,rid,accepted)
    response=base_availability(current,query)
    if 'explain' in query:
        restaurant=find_restaurant(current,rid)
        party=Decimal(query['party_size'][0])
        for slot in response['slots']:
            end=instant(slot['starts_at'])+accepted['reservation_duration_minutes']*MINUTE_US
            slot['explain']=[]
            for table in restaurant['tables']:
                capacity=party<=table['capacity']
                no_overlap=not any(r['status']=='confirmed' and r['restaurant_id']==rid and table['id'] in members(r) and instant(r['starts_at'])<end and instant(slot['starts_at'])<instant(r['ends_at']) for r in state['reservations'])
                slot['explain'].append({'table_id':table['id'],'policy_version':accepted['policy_version'],'available':capacity and no_overlap,
                                       'rules':[{'rule':'capacity','holds':capacity},{'rule':'no_overlap','holds':no_overlap}]})
    return response
