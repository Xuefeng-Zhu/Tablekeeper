"""Portable plan relationships; historical feasibility uses captured snapshots."""
from values import fail, field, identifier, canonical
from domain import find_restaurant, members, instant
from repairs import solve, interval


def recorded_state(state, source):
    """Reconstruct a captured revision from its already validated immutable ledger."""
    from agreements import view
    from domain import plan as base_plan
    actual=next(r for r in state['reservations'] if r['reference']==source['reference'])
    current={k:actual[k] for k in ('reservation_id','reference','user_id','restaurant_id','created_at')}
    for entry in state['histories'][source['reference']]:
        for change in entry['changes']:
            name=change['field']
            if name in ('table_id','table_ids'):
                current.pop('table_id',None);current.pop('table_ids',None)
            current[name]=change['to']
        current.update({'revision':entry['revision'],'accepted_terms':entry['accepted_terms'],'status':'cancelled' if entry['event']=='cancelled' else 'confirmed'})
        body={k:v for k,v in current.items() if k!='table_id'} if 'table_ids' in current else current
        current={**current,**base_plan(view(state,current['restaurant_id'],current['accepted_terms']),body)}
        if current['revision']==source['revision'] and current['status']==source['status']:
            if current!=source:fail()
            return
    fail()


def history_plan(state, record, entry, old, current):
    stored=next((p for p in state['plans'] if p['plan_id']==entry.get('plan_id')),None)
    if stored is None or not stored['applied'] or stored['restaurant_id']!=record['restaurant_id']:fail()
    assignment=next((a for a in stored['response']['assignments'] if a['reference']==record['reference']),None)
    source=next((r for r in stored['snapshot_reservations'] if r['reference']==record['reference']),None)
    if assignment is None or source is None or not assignment['changed'] or members(current)!=assignment['table_ids'] or old!=source:fail()


def validate_plans(state):
    from state import validate_record, positive
    plans=field(state,'plans',list);closures=field(state,'closures',list);seen=set()
    for p in plans:
        identifier(p,'plan_id');rid=p['restaurant_id'];restaurant=find_restaurant(state,rid)
        if p['plan_id'] in seen or type(p['applied']) is not bool:fail()
        seen.add(p['plan_id']);positive(p['base_revision'],0)
        if p['base_revision']>state['restaurant_revisions'][rid]:fail()
        response=p['response']
        if response['plan_id']!=p['plan_id'] or response['restaurant_revision']!=p['base_revision']:fail()
        closure={**response['closure'],'restaurant_id':rid};interval(closure)
        if closure['table_id'] not in {t['id'] for t in restaurant['tables']}:fail()
        records=field(p,'snapshot_reservations',list);past=field(p,'snapshot_closures',list)
        if len({r['reference'] for r in records})!=len(records):fail()
        for r in records:
            validate_record(state,r)
            actual=next((a for a in state['reservations'] if a['reference']==r['reference']),None)
            if actual is None or any(actual[k]!=r[k] for k in ('reservation_id','user_id','restaurant_id','created_at')):fail()
            recorded_state(state,r)
        for c in past:
            if c not in closures:fail()
        captured={**state,'reservations':records,'closures':past}
        considered,assignments,cost=solve(captured,restaurant,closure)
        if assignments!=response['assignments'] or type(response['moved_count']) is not int or type(response['unused_seats']) is not int or cost!=(response['moved_count'],response['unused_seats']):fail()
        linked=[c for c in closures if c['plan_id']==p['plan_id']]
        if p['applied']:
            if p['applied_revision']!=p['base_revision']+1 or p['applied_revision']>state['restaurant_revisions'][rid] or linked!=[{**closure,'plan_id':p['plan_id']}]:fail()
            for assignment in assignments:
                source=next(r for r in considered if r['reference']==assignment['reference'])
                events=[e for e in state['histories'][source['reference']] if e.get('plan_id')==p['plan_id']]
                if assignment['changed']:
                    if len(events)!=1 or events[0]['event']!='reassigned' or events[0]['revision']!=source['revision']+1:fail()
                elif events:fail()
        elif linked or 'applied_revision' in p:fail()
        if not p['applied'] and p['base_revision']==state['restaurant_revisions'][rid]:
            if [r for r in records if r['restaurant_id']==rid]!=[r for r in state['reservations'] if r['restaurant_id']==rid]:fail()
        expected_past=[c for c in closures if c['restaurant_id']==rid and next(x for x in plans if x['plan_id']==c['plan_id'])['applied_revision']<=p['base_revision']]
        if [c for c in past if c['restaurant_id']==rid]!=expected_past:fail()
    if len({c['plan_id'] for c in closures})!=len(closures):fail()
    for c in closures:
        if c['plan_id'] not in seen:fail()
    for p in plans:
        previews=[r for r in state['receipts'] if r['path']=='/restaurants/'+p['restaurant_id']+'/replans' and r['response'].get('plan_id')==p['plan_id']]
        applied=[r for r in state['receipts'] if r['path']=='/restaurants/'+p['restaurant_id']+'/replans/'+p['plan_id']+'/apply']
        if len(previews)!=1 or len(applied)!=int(p['applied']):fail()
        if previews[0]['response']!=p['response']:fail()


def validate_receipt(state, receipt, original):
    from state import identity, validate_series, positive
    path=receipt['path'];response=receipt['response'];uid=receipt['user_id']
    if path.startswith('/restaurants/') and '/replans' in path:
        pieces=path.strip('/').split('/');rid=pieces[1]
        if len(pieces) not in (3,5) or pieces[2]!='replans':fail()
        if uid not in find_restaurant(state,rid)['manager_user_ids']:fail()
        p=next((p for p in state['plans'] if p['plan_id']==response.get('plan_id') and p['restaurant_id']==rid),None)
        if p is None:fail()
        if len(pieces)==3:
            if response!=p['response'] or any(original.get(k)!=response['closure'][k] for k in ('table_id','from','to')):fail()
        else:
            if pieces[3]!=p['plan_id'] or pieces[4]!='apply' or not p['applied'] or response['restaurant_revision']!=p['applied_revision']:fail()
            records=field(response,'reservations',list);assignments=p['response']['assignments']
            if [r['reference'] for r in records]!=[a['reference'] for a in assignments]:fail()
            for r,a in zip(records,assignments):
                source=next(s for s in p['snapshot_reservations'] if s['reference']==r['reference'])
                identity(state,r,source['user_id'])
                if members(r)!=a['table_ids'] or r['revision']!=source['revision']+int(a['changed']) or any(r[k]!=source[k] for k in ('accepted_terms','starts_at','ends_at','party_size','starts_at_local','status')):fail()
    elif path.startswith('/series/') and path.endswith('/amend'):
        sid=path.split('/')[2];series=next((s for s in state['series'] if s['series_id']==sid and s['user_id']==uid),None)
        if series is None or response['series_id']!=sid or response['interval_weeks']!=series['interval_weeks']:fail()
        from values import integer
        revision=positive(response['revision']);expected=integer(original['expected_revision'])
        if revision not in (expected,expected+1) or revision>series['revision']:fail()
        if [o['reference'] for o in response['occurrences']]!=[o['reference'] for o in series['occurrences']]:fail()
        for index,o in enumerate(response['occurrences']):
            if o['index']!=index or type(o['exception']) is not bool or o['reservation']['reference']!=o['reference']:fail()
            identity(state,o['reservation'],uid)
        from_index=original['from_index'];time=original['local_time']
        from values import integer
        import re
        if not 0<=integer(from_index,minimum=0)<len(series['occurrences']) or not isinstance(time,str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',time):fail()
    else:fail()
