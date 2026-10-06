"""History snapshots and deduplicated per-transaction agreement counters."""
import copy
from domain import members, public, instant


def changes(old, record):
    before=members(old) if old else None;after=members(record)
    result=[]
    if before!=after:
        pair=len(after)>1 or (before is not None and len(before)>1)
        result.append({'field':'table_ids' if pair else 'table_id','from':before if pair else (before[0] if before else None),'to':after if pair else after[0]})
    for name in ('starts_at_local','party_size'):
        a=old[name] if old else None;b=record[name]
        if a!=b:result.append({'field':name,'from':a,'to':b})
    return result


def append(history, record, event, clock, old=None):
    at=clock.isoformat() if hasattr(clock,'isoformat') else clock
    if history and instant(at)<instant(history[-1]['at']):at=history[-1]['at']
    entry={'seq':len(history)+1,'at':at,'event':event,'changes':[] if event=='cancelled' else changes(old,record),
           'revision':record['revision'],'accepted_terms':copy.deepcopy(record['accepted_terms'])}
    return history+[entry]


def bootstrap(record):
    history=append([],record,'created',record['created_at'])
    if record['status']=='cancelled':history=append(history,record,'cancelled',record['created_at'])
    return history


def series_response(state, series):
    records={r['reference']:r for r in state['reservations']}
    return {k:copy.deepcopy(series[k]) for k in ('series_id','revision','interval_weeks')} | {'occurrences':[
        {**o,'reservation':public(records[o['reference']])} for o in series['occurrences']]}


def deltas(state, changed, clock, adoption=None):
    old_by={r['reference']:r for r in state['reservations']}
    histories=dict(state['histories']);touched=set();real=set();cancelled=set()
    for record in changed:
        old=old_by.get(record['reference'])
        if old==record:continue
        ref=record['reference'];touched.add(record['restaurant_id'])
        event='created' if old is None else ('cancelled' if record['status']=='cancelled' else 'changed')
        histories[ref]=append(histories.get(ref,[]),record,event,clock,old)
        (cancelled if event=='cancelled' else real).add(ref)
    series=[]
    for item in state['series']:
        refs={o['reference'] for o in item['occurrences']}
        if refs & (real|cancelled):
            log={'revision':item['revision']+1,'changes':[{'reference':r['reference'],'revision':r['revision'],'event':'cancelled' if r['reference'] in cancelled else 'changed'} for r in changed if r['reference'] in refs and r['reference'] in real|cancelled]}
            item={**item,'revision':item['revision']+1,'mutation_log':item['mutation_log']+[log],'occurrences':[{**o,'exception':o['exception'] or o['reference'] in real} for o in item['occurrences']]}
        series.append(item)
    if adoption:
        series.append(adoption);touched.add(adoption['restaurant_id'])
    counters={rid:value+(rid in touched) for rid,value in state['restaurant_revisions'].items()}
    return {'histories':histories,'series':series,'restaurant_revisions':counters}
