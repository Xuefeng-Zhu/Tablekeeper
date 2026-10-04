import json
import secrets
import re
from datetime import datetime,timedelta
from .validation import fail,identifier,party,string
from .time_rules import interval,formatted,now
from .store import dumps

PUBLIC=['reservation_id','reference','restaurant_id','table_id','party_size','status','starts_at_local','starts_at','ends_at','created_at']
def public(r): return {k:r[k] for k in PUBLIC}
def restaurant(db,ident):
    row=db.execute('SELECT data FROM restaurants WHERE id=?',(ident,)).fetchone()
    if row is None: fail('not_found',404)
    return json.loads(row[0])
def all_reservations(db): return [json.loads(x[0]) for x in db.execute('SELECT data FROM reservations')]
def owned(db,reference,user):
    row=db.execute('SELECT data FROM reservations WHERE reference=? AND user_id=?',(reference,user)).fetchone()
    if row is None: fail('not_found',404)
    return json.loads(row[0])
def overlaps(a,b):
    return (a.get('restaurant_id')==b.get('restaurant_id') and a['status']==b['status']=='confirmed' and bool(set(a['table_ids'])&set(b['table_ids'])) and
            datetime.fromisoformat(a['starts_at'])<datetime.fromisoformat(b['ends_at']) and datetime.fromisoformat(b['starts_at'])<datetime.fromisoformat(a['ends_at']))
def check_occupancy(db,proposals):
    ids={r['reservation_id'] for r in proposals}
    others=[r for r in all_reservations(db) if r['reservation_id'] not in ids]
    for i,r in enumerate(proposals):
        if any(overlaps(r,o) for o in others+proposals[:i]): fail('table_unavailable',409)
def proposal(db,body,base=None):
    rid=base['restaurant_id'] if base else identifier(body,'restaurant_id')
    r=restaurant(db,rid)
    merged={**(base or {}),**{k:v for k,v in body.items() if k in ('table_id','party_size','starts_at_local')}}
    tid=identifier(merged,'table_id')
    if 'party_size' not in merged: fail()
    size=party(merged['party_size'])
    local=string(merged,'starts_at_local')
    table=next((t for t in r['tables'] if t['id']==tid),None)
    if table is None: fail('not_found',404)
    start,end=interval(r,local)
    if size>table['capacity']: fail('party_exceeds_capacity')
    result={**(base or {}),'restaurant_id':rid,'table_id':tid,'table_ids':[tid],'party_size':size,'starts_at_local':local,'starts_at':formatted(start,r['timezone']),'ends_at':formatted(end,r['timezone']),'status':'confirmed'}
    return result

def editable(r,instant):
    if r['status']=='cancelled': fail('reservation_cancelled',409)
    if (datetime.fromisoformat(r['starts_at'])-instant).total_seconds()<=r['accepted_terms']['cancellation_cutoff_minutes']*60: fail('cutoff_passed',409)

def persist(db,r):
    db.execute('INSERT INTO reservations VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(r['reservation_id'],r['reference'],r['user_id'],r['restaurant_id'],dumps(r)))
    db.execute('DELETE FROM allocations WHERE reservation_id=?',(r['reservation_id'],))
    for tid in r['table_ids']: db.execute('INSERT INTO allocations VALUES(?,?,?)',(r['reservation_id'],r['restaurant_id'],tid))

def history(db,r,event,at):
    previous=r['history'][-1]['snapshot'] if r['history'] else None
    changed=[key for key in ('table_id','starts_at_local','party_size','status') if previous is None or previous[key]!=r[key]]
    if r['history']: at=max(at,r['history'][-1]['at'])
    r['history'].append({'changed_fields':changed,'seq':len(r['history'])+1,'event':event,'at':at,'revision':r['revision'],'snapshot':public(r),'accepted_terms':r['accepted_terms']})
    db.execute('UPDATE restaurants SET counter=counter+1 WHERE id=?',(r['restaurant_id'],))

def create(db,body,user,seed=None):
    r=proposal(db,body)
    reference=(seed or {}).get('reference')
    if seed is not None:
        identifier(seed,'id')
        if not isinstance(reference,str) or not re.fullmatch('[A-Z0-9]{6,12}',reference): fail()
    while reference is None:
        candidate=''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(10))
        if not db.execute('SELECT 1 FROM reservations WHERE reference=?',(candidate,)).fetchone(): reference=candidate
    stamp=now().isoformat()
    config=restaurant(db,r['restaurant_id'])
    r.update(reservation_id=(seed or {}).get('id',secrets.token_hex(16)),reference=reference,user_id=user,created_at=stamp,revision=1,history=[],accepted_terms={'policy_version':0,**{k:config[k] for k in ['slot_minutes','reservation_duration_minutes','cancellation_cutoff_minutes','opening_hours','timezone','tables']}})
    check_occupancy(db,[r]); history(db,r,'created',stamp); persist(db,r)
    return public(r)

def amend(db,r,body,instant):
    editable(r,instant)
    candidate=proposal(db,body,r)
    if all(candidate[k]==r[k] for k in PUBLIC): return r
    candidate['revision']=r['revision']+1
    candidate['history']=list(r['history'])
    return candidate

def save_changes(db,old,new,instant,event='amended'):
    if old==new: return
    history(db,new,event,instant.isoformat());persist(db,new)

def moves(db,body,user):
    items=body.get('moves')
    if not isinstance(items,list) or not 1<=len(items)<=8 or any(not isinstance(i,dict) or not isinstance(i.get('reference'),str) for i in items): fail()
    refs=[i['reference'] for i in items]
    if len(set(refs))!=len(refs): fail()
    instant=now()
    old=[]; new=[]
    # Section 11 orders all non-occupancy errors by input item. A later
    # missing reference must not mask an earlier cutoff or invalid change.
    for item in items:
        current=owned(db,item['reference'],user)
        editable(current,instant)
        if old and current['restaurant_id']!=old[0]['restaurant_id']: fail()
        proposed=amend(db,current,item,instant)
        old.append(current)
        new.append(proposed)
    # No live rows were changed while constructing these proposals. Only now
    # compare final occupancy, so swaps and unchanged listed items work together.
    check_occupancy(db,new)
    counter=db.execute('SELECT counter FROM restaurants WHERE id=?',(old[0]['restaurant_id'],)).fetchone()[0]
    changed=any(a!=b for a,b in zip(old,new))
    for a,b in zip(old,new): save_changes(db,a,b,instant)
    if changed: db.execute('UPDATE restaurants SET counter=? WHERE id=?',(counter+1,old[0]['restaurant_id']))
    return {'reservations':[public(r) for r in new]}
