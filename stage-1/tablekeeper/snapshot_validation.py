"""Validate logical state independently of the destination before replacement."""
import json
from .exact_json import loads
import re
from datetime import datetime
from .validation import fail, Problem, canonical
from .fixture import restaurant_config


def require(condition):
    if not condition: fail()


def data(raw):
    require(isinstance(raw,str))
    value=loads(raw)
    require(isinstance(value,dict))
    return value


def timestamp(value):
    require(isinstance(value,str) and bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})',value)))
    result=datetime.fromisoformat(value)
    require(result.utcoffset() is not None)
    return result


def validate_snapshot(db):
    from .reservations import overlaps, proposal, public
    users={}
    for ident,email,raw in db.execute('SELECT * FROM users'):
        u=data(raw); p=u['password_hash']
        require(u['id']==ident and u['email']==email and isinstance(u['display_name'],str))
        require(isinstance(email,str) and bool(re.fullmatch(r'[^@\s]+@[^@\s]+',email)))
        require(isinstance(p,dict) and p.get('algorithm')=='scrypt')
        require(all(type(p[k]) is int for k in ('n','r','p')) and (p['n'],p['r'],p['p'])==(16384,8,1))
        require(isinstance(p['salt'],str) and bool(re.fullmatch('[0-9a-f]{32}',p['salt'])))
        require(isinstance(p['digest'],str) and bool(re.fullmatch('[0-9a-f]{64}',p['digest'])))
        require('password' not in u)
        users[ident]=u
    for digest,user in db.execute('SELECT * FROM sessions'):
        require(isinstance(digest,str) and bool(re.fullmatch('[0-9a-f]{64}',digest)) and user in users)
    for table in ['users','restaurants','dining_tables','reservations']:
        for ident,raw in db.execute('SELECT id,data FROM '+table):
            require(isinstance(ident,str) and 1<=len(ident)<=64); data(raw)
    restaurants={}
    positions=[]
    for ident,position,raw,counter in db.execute('SELECT * FROM restaurants'):
        config=data(raw)
        require(config['id']==ident and type(counter) is int and counter>=0)
        require(type(position) is int);positions.append(position)
        require(canonical(restaurant_config(config))==canonical(config))
        tables=list(db.execute('SELECT id,position,data FROM dining_tables WHERE restaurant_id=? ORDER BY position',(ident,)))
        require(len(tables)==len(config['tables']))
        for i,(tid,pos,table_raw) in enumerate(tables):
            require(pos==i and tid==config['tables'][i]['id'] and canonical(data(table_raw))==canonical(config['tables'][i]))
        restaurants[ident]=config
    require(sorted(positions)==list(range(len(positions))))

    def validate_booking(r):
        require(r['restaurant_id'] in restaurants)
        require(isinstance(r['reference'],str) and bool(re.fullmatch('[A-Z0-9]{6,12}',r['reference'])))
        require(r['status'] in ('confirmed','cancelled'))
        for k in ('reservation_id','restaurant_id','table_id'):
            require(isinstance(r[k],str) and 1<=len(r[k])<=64)
        timestamp(r['created_at']); timestamp(r['starts_at']); timestamp(r['ends_at'])
        proposed=proposal(db,r)
        for k in ('table_id','party_size','starts_at_local','starts_at','ends_at'):
            require(canonical(proposed[k])==canonical(r[k]))

    reservations={}
    for ident,reference,user,restaurant,raw in db.execute('SELECT * FROM reservations'):
        r=data(raw);validate_booking(r)
        require(r['reservation_id']==ident and r['reference']==reference and r['user_id']==user and r['restaurant_id']==restaurant)
        require(r['table_ids']==[r['table_id']] and type(r['revision']) is int and r['revision']>=1)
        allocations=list(db.execute('SELECT restaurant_id,table_id FROM allocations WHERE reservation_id=?',(ident,)))
        require(allocations==[(restaurant,r['table_id'])])
        terms=r['accepted_terms'];config=restaurants[restaurant]
        require(isinstance(terms,dict) and type(terms.get('policy_version')) is int and terms['policy_version']==0)
        for key in ('slot_minutes','reservation_duration_minutes','cancellation_cutoff_minutes','opening_hours'):
            require(canonical(terms[key])==canonical(config[key]))
        if 'timezone' in terms: require(terms['timezone']==config['timezone'])
        if 'tables' in terms: require(canonical(terms['tables'])==canonical(config['tables']))
        history=r['history'];require(isinstance(history,list) and len(history)==r['revision'])
        previous=None
        for i,event in enumerate(history):
            require(isinstance(event,dict) and type(event['seq']) is int and event['seq']==i+1 and type(event['revision']) is int and event['revision']==i+1)
            at=timestamp(event['at']);require(previous is None or at>=previous);previous=at
            require(event['event']==('created' if i==0 else 'cancelled' if event['snapshot']['status']=='cancelled' else 'amended'))
            snap=event['snapshot'];validate_booking(snap)
            for key in ('reservation_id','reference','restaurant_id','created_at'): require(snap[key]==r[key])
            require(canonical(event['accepted_terms'])==canonical(terms))
        require(canonical(history[-1]['snapshot'])==canonical(public(r)))
        reservations[ident]=r
    rows=list(reservations.values())
    for i,a in enumerate(rows):
        for b in rows[i+1:]: require(not overlaps(a,b))
    for user,method,path,key,raw,response in db.execute('SELECT * FROM receipts'):
        require(user in users and method=='POST' and path in ('/reservations','/reservation-moves'))
        require(isinstance(key,str) and 1<=len(key)<=255)
        body=data(raw); result=data(response)
        values=[result] if path=='/reservations' else result['reservations']
        require(isinstance(values,list) and 1<=len(values)<=8)
        for value in values:
            validate_booking(value)
            current=reservations[value['reservation_id']]
            require(current['user_id']==user)
            require(any(canonical(event['snapshot'])==canonical(value) for event in current['history']))
        if path=='/reservations':
            proposed=proposal(db,body)
            for k in ('restaurant_id','table_id','party_size','starts_at_local'): require(canonical(result[k])==canonical(proposed[k]))
        else:
            moves=body['moves'];require(isinstance(moves,list) and len(moves)==len(values))
            require(len({v['reference'] for v in values})==len(values))
            require(len({v['restaurant_id'] for v in values})==1)
            for move,value in zip(moves,values):
                require(isinstance(move,dict) and move['reference']==value['reference'])
                for key in ('table_id','party_size','starts_at_local'):
                    if key in move: require(canonical(move[key])==canonical(value[key]))
