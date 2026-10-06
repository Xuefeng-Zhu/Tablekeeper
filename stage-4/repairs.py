"""Exact seating search and serialized atomic operator/agreement transactions."""
import copy
import re
import secrets
from domain import members, instant, overlaps, closure_overlap, check_occupancy, public, changed_record
from agreements import plan, amendable
from ledger import deltas, series_response
from values import fail, integer, APIError


def interval(body):
    values=[]
    for name in ('from','to'):
        text=body.get(name)
        if not isinstance(text,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})',text):fail()
        values.append(instant(text))
    if values[0]>=values[1]:fail()
    return values


def solve(state, restaurant, closure):
    """DFS exact tuple minimization; component minima are optimistic lower bounds."""
    a,b=interval(closure);rid=restaurant['id']
    considered=sorted((r for r in state['reservations'] if r['status']=='confirmed' and r['restaurant_id']==rid and instant(r['starts_at'])<b and a<instant(r['ends_at'])),key=lambda r:r['reference'])
    if len(restaurant['tables'])>6 or len(restaurant['combinable'])>4 or len(considered)>6:fail(422,'planning_limit')
    refs={r['reference'] for r in considered}
    fixed=[r for r in state['reservations'] if r['reference'] not in refs]
    options=[[t['id']] for t in restaurant['tables']]+restaurant['combinable']
    bits={t['id']:1<<i for i,t in enumerate(restaurant['tables'])}
    masks=[sum(bits[t] for t in ids) for ids in options]
    candidates=[]
    for record in considered:
        choices=[]
        for rank,ids in enumerate(options):
            capacity=sum(record['accepted_terms']['capacities'][t] for t in ids)
            seating={**record,'table_ids':ids}
            if capacity<record['party_size'] or any(overlaps(seating,r) for r in fixed) or any(closure_overlap(seating,c) for c in state.get('closures',[])+[closure]):continue
            choices.append((int(set(ids)!=set(members(record))),capacity-record['party_size'],rank,masks[rank]))
        if not choices:fail(409,'no_feasible_plan')
        candidates.append(sorted(choices))
    n=len(considered);conflict=[[instant(x['starts_at'])<instant(y['ends_at']) and instant(y['starts_at'])<instant(x['ends_at']) for y in considered] for x in considered]
    lower_changed=[0]*(n+1);lower_unused=[0]*(n+1);lower_ranks=[()]*(n+1)
    for i in range(n-1,-1,-1):
        lower_changed[i]=lower_changed[i+1]+min(c[0] for c in candidates[i])
        lower_unused[i]=lower_unused[i+1]+min(c[1] for c in candidates[i])
        lower_ranks[i]=(min(c[2] for c in candidates[i]),)+lower_ranks[i+1]
    best=None;winner=None
    def visit(index,changed,unused,ranks,used):
        nonlocal best,winner
        optimistic=(changed+lower_changed[index],unused+lower_unused[index],ranks+lower_ranks[index])
        if best is not None and optimistic>=best:return
        if index==n:
            best=(changed,unused,ranks);winner=ranks;return
        for delta,surplus,rank,mask in candidates[index]:
            if any(conflict[index][j] and mask&used[j] for j in range(index)):continue
            visit(index+1,changed+delta,unused+surplus,ranks+(rank,),used+(mask,))
    visit(0,0,0,(),())
    if winner is None:fail(409,'no_feasible_plan')
    assignments=[{'reference':r['reference'],'table_ids':list(options[rank]),'changed':set(options[rank])!=set(members(r))} for r,rank in zip(considered,winner)]
    return considered,assignments,best[:2]


def preview(state, restaurant, body):
    table=body.get('table_id')
    if not isinstance(table,str):fail()
    if table not in {t['id'] for t in restaurant['tables']}:fail(404,'not_found')
    interval(body)
    closure={'restaurant_id':restaurant['id'],'table_id':table,'from':body['from'],'to':body['to']}
    considered,assignments,cost=solve(state,restaurant,closure)
    pid='plan_'+secrets.token_hex(16);base=state['restaurant_revisions'][restaurant['id']]
    response={'plan_id':pid,'restaurant_revision':base,'closure':{k:closure[k] for k in ('table_id','from','to')},'assignments':assignments,'moved_count':cost[0],'unused_seats':cost[1]}
    stored={'plan_id':pid,'restaurant_id':restaurant['id'],'base_revision':base,'applied':False,'response':copy.deepcopy(response),
            'snapshot_reservations':copy.deepcopy(state['reservations']),'snapshot_closures':copy.deepcopy(state['closures'])}
    return {**state,'plans':state['plans']+[stored]},response


def apply_plan(state, restaurant, pid, clock):
    stored=next((p for p in state['plans'] if p['plan_id']==pid and p['restaurant_id']==restaurant['id']),None)
    if stored is None:fail(404,'not_found')
    if stored['applied']:fail(409,'plan_already_applied')
    if stored['base_revision']!=state['restaurant_revisions'][restaurant['id']]:fail(409,'stale_plan')
    old_by={r['reference']:r for r in state['reservations']};changed=[];records=[]
    for assignment in stored['response']['assignments']:
        old=old_by[assignment['reference']];record=old
        if assignment['changed']:
            record={k:v for k,v in old.items() if k!='table_id'}
            record.update({'table_ids':assignment['table_ids'],'revision':old['revision']+1})
            if len(record['table_ids'])==1:record['table_id']=record['table_ids'][0]
            changed.append(record)
        records.append(record)
    closure={**stored['response']['closure'],'restaurant_id':restaurant['id'],'plan_id':pid}
    replacements={r['reference']:r for r in changed}
    final=[replacements.get(r['reference'],r) for r in state['reservations']]
    closures=state['closures']+[closure];check_occupancy(final,[],closures)
    candidate={**state,'reservations':final,'closures':closures,**deltas(state,changed,clock,kind='repair',plan_id=pid)}
    candidate['restaurant_revisions']={**state['restaurant_revisions'],restaurant['id']:stored['base_revision']+1}
    candidate['plans']=[{**p,'applied':True,'applied_revision':stored['base_revision']+1} if p['plan_id']==pid else p for p in state['plans']]
    response={'plan_id':pid,'restaurant_revision':stored['base_revision']+1,'reservations':[public(r) for r in records]}
    return candidate,response


def amend_series(state, sid, uid, body, clock):
    series=next((s for s in state['series'] if s['series_id']==sid and s['user_id']==uid),None)
    if series is None:fail(404,'not_found')
    try:
        revision=integer(body['expected_revision']);first=integer(body['from_index'],minimum=0);time=body['local_time']
        if first>=len(series['occurrences']) or not isinstance(time,str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',time):fail()
    except (KeyError,TypeError,APIError):fail()
    if revision!=series['revision']:fail(409,'stale_revision')
    old_by={r['reference']:r for r in state['reservations']};changed=[]
    for o in series['occurrences'][first:]:
        old=old_by[o['reference']]
        if o['exception'] or old['status']=='cancelled':continue
        text=series['scheduled_dates'][o['index']]+'T'+time
        if text==old['starts_at_local']:continue
        amendable(state,old,clock)
        changed.append(changed_record(old,plan(state,{'starts_at_local':text},old)))
    refs={r['reference'] for r in changed};others=[r for r in state['reservations'] if r['reference'] not in refs]
    check_occupancy(changed,others,state['closures'])
    replacements={r['reference']:r for r in changed}
    candidate={**state,'reservations':[replacements.get(r['reference'],r) for r in state['reservations']],**deltas(state,changed,clock,kind='series_amend')}
    updated=next(s for s in candidate['series'] if s['series_id']==sid)
    return candidate,series_response(candidate,updated)
