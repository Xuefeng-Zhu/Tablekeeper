"""Schema4 snapshots: validate historical agreements independently of current policy."""
import copy
import re
from decimal import Decimal
from stage2_state import credential, password_matches, email
import stage2_state as legacy
from domain import find_restaurant, plan as base_plan, members, instant, check_occupancy
from agreements import policy0, terms, policy, view
from ledger import bootstrap, changes
from values import fail, field, identifier, integer, plain, validate_tree, canonical, APIError


def empty():
    return {**legacy.empty(),'schema_version':4,'policies':{},'histories':{},'series':[],'restaurant_revisions':{},'plans':[],'closures':[]}


def migrate(old, managers=None):
    result=copy.deepcopy(old);result['schema_version']=4
    result['restaurants']=[{**r,'manager_user_ids':(managers or {}).get(r['id'],[])} for r in result['restaurants']]
    result['policies']={r['id']:[] for r in result['restaurants']}
    result['restaurant_revisions']={r['id']:0 for r in result['restaurants']}
    result['series']=[];result['histories']={};result['plans']=[];result['closures']=[]
    for record in result['reservations']:
        record['revision']=1;record['accepted_terms']=policy0(find_restaurant(result,record['restaurant_id']))
        result['histories'][record['reference']]=bootstrap(record)
    return result


def reset(body):
    old=legacy.reset(body);managers={};uids={u['id'] for u in old['users']}
    for r in body.get('restaurants',[]):
        ids=field(r,'manager_user_ids',list) if 'manager_user_ids' in r else []
        ids=[identifier({'id':uid},'id') for uid in ids]
        if len(set(ids))!=len(ids) or any(uid not in uids for uid in ids):fail()
        managers[r['id']]=ids
    return migrate(old,managers)


def positive(value, minimum=1):
    if type(value) is not int or value<minimum:fail()
    return value


def validate_terms(state, rid, accepted):
    if not isinstance(accepted,dict) or set(accepted)!=set(policy0(find_restaurant(state,rid))):fail()
    version=positive(accepted['policy_version'],0)
    origin=policy0(find_restaurant(state,rid)) if version==0 else next((terms(p) for p in state['policies'][rid] if p['policy_version']==version),None)
    if origin is None or canonical(origin)!=canonical(accepted):fail()


def validate_record(state, record, owner=None):
    if not isinstance(record,dict):fail()
    identifier(record,'reservation_id')
    ref=field(record,'reference')
    if not re.fullmatch('[A-Z0-9]{6,12}',ref) or record['status'] not in ('confirmed','cancelled'):fail()
    rid=field(record,'restaurant_id');restaurant=find_restaurant(state,rid)
    native='accepted_terms' in record or 'revision' in record
    if native:
        positive(record['revision']);validate_terms(state,rid,record['accepted_terms'])
        accepted=record['accepted_terms']
        version=accepted['policy_version']
        if version:
            candidates=[p for p in state['policies'][rid][:version] if p['effective_from']<=record['starts_at_local'][:10]]
            if not candidates or max(candidates,key=lambda p:(p['effective_from'],p['policy_version']))['policy_version']!=version:fail()
    else:accepted=policy0(restaurant)
    body={k:v for k,v in record.items() if k!='table_id'} if 'table_ids' in record else record
    target=base_plan(view(state,rid,accepted),body)
    for k in ('party_size','starts_at_local','restaurant_id'):
        if record[k]!=target[k]:fail()
    if 'table_ids' in record:
        if record['table_ids']!=target['table_ids'] or ('table_id' in record)!=('table_id' in target) or record.get('table_id')!=target.get('table_id'):fail()
    if any(instant(record[k])!=instant(target[k]) for k in ('starts_at','ends_at')):fail()
    instant(record['created_at'])
    if owner is not None and record.get('user_id',owner)!=owner:fail()
    return native


def identity(state, record, owner):
    validate_record(state,record,owner)
    actual=next((r for r in state['reservations'] if r['reference']==record['reference']),None)
    if actual is None or any(actual[k]!=record[k] for k in ('reservation_id','restaurant_id','created_at')) or actual['user_id']!=owner:fail()
    return actual


def validate_history(state, record):
    entries=state['histories'][record['reference']]
    if not isinstance(entries,list) or not entries:fail()
    previous=None;current=None;terminal=False;revision=0
    for index,entry in enumerate(entries):
        if not isinstance(entry,dict) or type(entry['seq']) is not int or entry['seq']!=index+1 or terminal:fail()
        stamp=instant(entry['at'])
        if previous is not None and stamp<previous:fail()
        previous=stamp;validate_terms(state,record['restaurant_id'],entry['accepted_terms'])
        positive(entry['revision'])
        event=entry['event'];delta=entry['changes']
        if not isinstance(delta,list):fail()
        if index==0:
            if event!='created' or entry['revision']!=1 or len(delta)!=3:fail()
            current={k:record[k] for k in ('reservation_id','reference','user_id','restaurant_id','created_at')}
            current.update({'status':'confirmed','revision':1,'accepted_terms':entry['accepted_terms']})
        elif event in ('changed','reassigned'):
            if entry['revision']!=revision+1 or not delta:fail()
        elif event=='cancelled':
            if entry['accepted_terms']!=current['accepted_terms']:fail()
            if delta or entry['revision'] not in (revision+1,revision):fail()
            # Equal revision is only the explicitly documented cancelled-legacy bootstrap.
            if entry['revision']==revision and not (index==1 and revision==1 and entry['at']==record['created_at']):fail()
            terminal=True
        else:fail()
        old=copy.deepcopy(current) if index else None
        for change in delta:
            name=change['field']
            if name not in ('table_id','table_ids','starts_at_local','party_size'):fail()
            before=None if old is None else (members(old) if name=='table_ids' else old.get(name))
            if change['from']!=before:fail()
            if name in ('table_id','table_ids'):
                current.pop('table_id',None);current.pop('table_ids',None)
                current[name]=change['to']
            else:current[name]=change['to']
        current.update({'revision':entry['revision'],'accepted_terms':entry['accepted_terms'],'status':'cancelled' if event=='cancelled' else 'confirmed'})
        planning={k:v for k,v in current.items() if k!='table_id'} if 'table_ids' in current else current
        target=base_plan(view(state,record['restaurant_id'],entry['accepted_terms']),planning)
        current={**current,**target}
        validate_record(state,current)
        if event=='reassigned':
            if delta!=[{'field':'table_ids','from':members(old),'to':members(current)}] or members(old)==members(current) or entry['accepted_terms']!=old['accepted_terms'] or any(current[k]!=old[k] for k in ('party_size','starts_at_local','starts_at','ends_at')):fail()
            from snapshot4 import history_plan
            history_plan(state,record,entry,old,current)
        elif event!='cancelled' and delta!=changes(old,current):fail()
        revision=entry['revision']
    if any(current.get(k)!=record[k] for k in ('table_ids','party_size','starts_at_local','revision','accepted_terms','status')):fail()
    if any(instant(current[k])!=instant(record[k]) for k in ('starts_at','ends_at')):fail()


def validate_series(state, series, historical=False, owner=None):
    identifier(series,'series_id');positive(series['revision'])
    weeks=positive(series['interval_weeks'])
    if weeks>4:fail()
    occurrences=field(series,'occurrences',list)
    if not 2<=len(occurrences)<=12:fail()
    refs=[]
    for index,o in enumerate(occurrences):
        if type(o['index']) is not int or o['index']!=index or type(o['exception']) is not bool or o['reference'] in refs:fail()
        refs.append(o['reference'])
        actual=next((r for r in state['reservations'] if r['reference']==o['reference']),None)
        if actual is None or actual['user_id']!=(owner or series['user_id']):fail()
        if historical:
            embedded=o['reservation']
            if embedded['reference']!=o['reference'] or embedded['status']!='confirmed' or o['exception']:fail()
            identity(state,embedded,owner)
        elif actual['restaurant_id']!=series['restaurant_id']:fail()
    if not historical:
        baselines=series['baseline_revisions'];log=series['mutation_log']
        if not isinstance(baselines,dict) or set(baselines)!=set(refs) or not isinstance(log,list) or series['revision']!=len(log)+1:fail()
        current={ref:positive(rev) for ref,rev in baselines.items()};exceptions=set()
        for index,operation in enumerate(log):
            if type(operation['revision']) is not int or operation['revision']!=index+2:fail()
            changes=field(operation,'changes',list)
            if not changes or len({c['reference'] for c in changes})!=len(changes):fail()
            for change in changes:
                ref=change['reference']
                if ref not in current or type(change['revision']) is not int or change['revision']!=current[ref]+1 or change['event'] not in ('changed','cancelled','reassigned'):fail()
                entry=next((e for e in state['histories'][ref] if e['revision']==change['revision'] and e['event']==change['event']),None)
                if entry is None:fail()
                current[ref]=change['revision']
                kind=operation.get('kind')
                if kind not in ('diner','repair','series_amend'):fail()
                if (kind=='repair')!=(change['event']=='reassigned'):fail()
                if kind=='repair' and operation.get('plan_id')!=entry.get('plan_id'):fail()
                if kind=='series_amend' and change['event']!='changed':fail()
                if kind=='diner' and change['event']=='changed':exceptions.add(ref)
        actuals={r['reference']:r for r in state['reservations']}
        if any(current[ref]!=actuals[ref]['revision'] for ref in refs) or any(o['exception']!=(o['reference'] in exceptions) for o in occurrences):fail()
    return refs


def request_tree(tree):
    tag=tree[0]
    if tag=='null':return None
    if tag in ('boolean','string'):return tree[1]
    if tag=='number':return Decimal(('-' if tree[1] else '')+tree[2]+'e'+tree[3])
    if tag=='array':return [request_tree(t) for t in tree[1]]
    return {k:request_tree(t) for k,t in tree[1]}


def request_record(body, record, required=False, check_expected=False):
    if required and any(k not in body for k in ('restaurant_id','party_size','starts_at_local')):fail()
    if 'table_ids' not in record:
        body={k:v for k,v in body.items() if k!='table_ids'}
    if 'table_id' in body and 'table_ids' in body:fail()
    if required and 'table_id' not in body and 'table_ids' not in body:fail()
    if 'table_id' in body and members(record)!=[body['table_id']]:fail()
    if 'table_ids' in body:
        ids=field(body,'table_ids',list)
        if len(ids)!=len(set(ids)) or set(ids)!=set(members(record)):fail()
    for key in (('restaurant_id','starts_at_local','party_size') if required else ('starts_at_local','party_size')):
        if key in body and canonical(body[key])!=canonical(record[key]):fail()
    if check_expected and 'expected_revision' in body and 'revision' in record:
        value=integer(body['expected_revision'])
        if value not in (record['revision'],record['revision']-1):fail()


def imported(body):
    try:
        if body.get('track')!='tablekeeper' or type(body.get('format_version')) is bool or body.get('format_version')!=1:fail()
        snapshot=plain(body['state'])
        if not isinstance(snapshot,dict) or type(snapshot.get('schema_version')) is not int:fail()
        if snapshot['schema_version'] in (1,2):return migrate(legacy.imported(body))
        if snapshot['schema_version']==3:
            import stage3_state
            return migrate3(stage3_state.imported(body))
        if snapshot['schema_version']!=4:fail()
        state=copy.deepcopy(snapshot)
        for k in ('policies','histories','restaurant_revisions'):
            if not isinstance(state[k],dict):fail()
        if not isinstance(state['series'],list):fail()
        # Reuse exact legacy credential/session/configuration validation, without current bookings/receipts.
        stripped={**state,'schema_version':2,'reservations':[],'receipts':[],
                  'restaurants':[{k:v for k,v in r.items() if k!='manager_user_ids'} for r in state['restaurants']]}
        legacy.imported({'track':'tablekeeper','format_version':1,'state':stripped})
        rids={r['id'] for r in state['restaurants']};uids={u['id'] for u in state['users']}
        if set(state['policies'])!=rids or set(state['restaurant_revisions'])!=rids:fail()
        for r in state['restaurants']:
            managers=field(r,'manager_user_ids',list)
            if any(not isinstance(uid,str) or uid not in uids for uid in managers) or len(managers)!=len(set(managers)):fail()
            positive(state['restaurant_revisions'][r['id']],0)
            versions=state['policies'][r['id']]
            if not isinstance(versions,list):fail()
            for i,p in enumerate(versions):
                if type(p['policy_version']) is not int or p['policy_version']!=i+1 or {**policy(p,r),'policy_version':i+1}!=p:fail()
        if not isinstance(state['reservations'],list) or not isinstance(state['receipts'],list):fail()
        legacy.unique(state['reservations'],'reservation_id');refs=legacy.unique(state['reservations'],'reference')
        if set(state['histories'])!=refs:fail()
        for record in state['reservations']:
            if 'table_ids' not in record or not validate_record(state,record) or record['user_id'] not in uids:fail()
            validate_history(state,record)
        check_occupancy(state['reservations'],[],state['closures'])
        from snapshot4 import validate_plans
        validate_plans(state)
        seen=set();series_ids=set();membership=set();series_receipts=set();policy_receipts=set()
        for series in state['series']:
            if series['series_id'] in series_ids or series['user_id'] not in uids:fail()
            series_ids.add(series['series_id']);refs=validate_series(state,series)
            if membership&set(refs):fail()
            membership.update(refs)
        for receipt in state['receipts']:
            uid=receipt['user_id'];path=receipt['path'];key=field(receipt,'key')
            if uid not in uids or receipt['method']!='POST' or not 1<=len(key)<=255:fail()
            namespace=(uid,path,key)
            if namespace in seen:fail()
            seen.add(namespace);validate_tree(receipt['body'])
            if receipt['body'][0]!='object':fail()
            original=request_tree(receipt['body'])
            response=receipt['response']
            if path=='/reservations':
                identity(state,response,uid)
                request_record(original,response,True)
                if response['status']!='confirmed':fail()
            elif path=='/reservation-moves':
                records=field(response,'reservations',list)
                if not 1<=len(records)<=8 or len({r['reference'] for r in records})!=len(records):fail()
                moves=field(original,'moves',list)
                if len(moves)!=len(records) or [m['reference'] for m in moves]!=[r['reference'] for r in records]:fail()
                for move,r in zip(moves,records):
                    request_record(move,r,check_expected=True)
                    identity(state,r,uid)
                    if r['status']!='confirmed':fail()
            elif path=='/series':
                validate_series(state,response,True,uid)
                if original['anchor_reference']!=response['occurrences'][0]['reference'] or integer(original['count'])!=len(response['occurrences']) or integer(original['interval_weeks'])!=response['interval_weeks']:fail()
                series_receipts.add(response['series_id'])
                actual=next((s for s in state['series'] if s['series_id']==response['series_id']),None)
                if actual is None or actual['user_id']!=uid or [o['reference'] for o in actual['occurrences']]!=[o['reference'] for o in response['occurrences']] or response['revision']!=1 or response['interval_weeks']!=actual['interval_weeks'] or any(o['reservation']['revision']!=actual['baseline_revisions'][o['reference']] for o in response['occurrences']):fail()
            elif '/replans' in path or (path.startswith('/series/') and path.endswith('/amend')):
                from snapshot4 import validate_receipt
                validate_receipt(state,receipt,original)
            elif path.startswith('/restaurants/') and path.endswith('/policies'):
                rid=path.split('/')[2]
                if rid not in rids or response not in state['policies'][rid] or policy(original,find_restaurant(state,rid))!={k:v for k,v in response.items() if k!='policy_version'}:fail()
                policy_receipts.add((rid,response['policy_version']))
            else:fail()
        if series_receipts!=series_ids or policy_receipts!={(rid,p['policy_version']) for rid in rids for p in state['policies'][rid]}:fail()
        validate_schedules(state)
        return state
    except (APIError,KeyError,TypeError,ValueError,OverflowError,RecursionError,AttributeError):fail()


def validate_schedules(state):
    for series in state['series']:
        receipts=[r['response'] for r in state['receipts'] if r['path']=='/series' and r['response'].get('series_id')==series['series_id']]
        if len(receipts)!=1:fail()
        response=receipts[0];dates=[o['reservation']['starts_at_local'][:10] for o in response['occurrences']]
        if series['scheduled_dates']!=dates:fail()
        from datetime import timedelta
        from domain import local
        anchor=local(dates[0]+'T00:00')
        if dates!=[(anchor+timedelta(days=i*series['interval_weeks']*7)).date().isoformat() for i in range(len(dates))]:fail()


def migrate3(state):
    result=copy.deepcopy(state);result.update({'schema_version':4,'plans':[],'closures':[]})
    for series in result['series']:
        for operation in series['mutation_log']:operation['kind']='diner'
        receipt=next(r['response'] for r in result['receipts'] if r['path']=='/series' and r['response']['series_id']==series['series_id'])
        series['scheduled_dates']=[o['reservation']['starts_at_local'][:10] for o in receipt['occurrences']]
    validate_schedules(result)
    return result
