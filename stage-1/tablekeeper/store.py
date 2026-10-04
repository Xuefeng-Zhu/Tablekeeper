import json
import sqlite3
import threading
from contextlib import contextmanager
from .validation import fail

SCHEMA='''
CREATE TABLE users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,data TEXT NOT NULL);
CREATE TABLE sessions(digest TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id));
CREATE TABLE restaurants(id TEXT PRIMARY KEY,position INTEGER UNIQUE,data TEXT NOT NULL,counter INTEGER NOT NULL DEFAULT 0);
CREATE TABLE dining_tables(id TEXT,restaurant_id TEXT NOT NULL REFERENCES restaurants(id),position INTEGER,data TEXT NOT NULL,PRIMARY KEY(restaurant_id,id));
CREATE TABLE reservations(id TEXT PRIMARY KEY,reference TEXT UNIQUE NOT NULL,user_id TEXT NOT NULL REFERENCES users(id),restaurant_id TEXT NOT NULL REFERENCES restaurants(id),data TEXT NOT NULL);
CREATE TABLE allocations(reservation_id TEXT REFERENCES reservations(id),restaurant_id TEXT,table_id TEXT,PRIMARY KEY(reservation_id,table_id),FOREIGN KEY(restaurant_id,table_id) REFERENCES dining_tables(restaurant_id,id));
CREATE TABLE receipts(user_id TEXT REFERENCES users(id),method TEXT,path TEXT,key TEXT,body TEXT,response TEXT,PRIMARY KEY(user_id,method,path,key));
'''
TABLES=['users','sessions','restaurants','dining_tables','reservations','allocations','receipts']

def dumps(value): return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)

class Store:
    def __init__(self):
        self.lock=threading.RLock()
        self.db=sqlite3.connect(':memory:',check_same_thread=False,isolation_level=None)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(SCHEMA)
    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

def clear(db):
    for table in reversed(TABLES): db.execute('DELETE FROM '+table)

def export_state(db):
    return {'track':'tablekeeper','format_version':1,'state':{'schema_version':1,'entities':{
        t:{'columns':[c[1] for c in db.execute('PRAGMA table_info('+t+')')],
           'rows':[list(r) for r in db.execute('SELECT * FROM '+t)]} for t in TABLES}}}

def import_state(db,body):
    # Stage in a separate database first; never execute SQL supplied by an export.
    if body.get('track')!='tablekeeper' or type(body.get('format_version')) is not int or body['format_version']!=1: fail()
    state=body.get('state')
    if not isinstance(state,dict) or type(state.get('schema_version')) is not int or state.get('schema_version')!=1 or not isinstance(state.get('entities'),dict): fail()
    staged=Store()
    try:
        entities=state['entities']
        if set(entities)!=set(TABLES): fail()
        for table in TABLES:
            entity=entities[table]
            columns=[c[1] for c in staged.db.execute('PRAGMA table_info('+table+')')]
            if not isinstance(entity,dict) or entity.get('columns')!=columns or not isinstance(entity.get('rows'),list): fail()
            for row in entity['rows']:
                if not isinstance(row,list) or len(row)!=len(columns): fail()
                staged.db.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in row)+')',row)
        validate_snapshot(staged.db)
        clear(db)
        for table in TABLES:
            for row in staged.db.execute('SELECT * FROM '+table):
                db.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in row)+')',tuple(row))
    except (KeyError,IndexError,TypeError,ValueError,OverflowError,sqlite3.Error): fail()
    finally: staged.db.close()

def validate_snapshot(db):
    # Validate portable credentials, entity snapshots and foreign key relationships.
    import re
    for ident,email,raw in db.execute('SELECT * FROM users'):
        u=json.loads(raw); p=u['password_hash']
        if u['id']!=ident or u['email']!=email or not isinstance(u['display_name'],str): fail()
        if p.get('algorithm')!='scrypt' or (p['n'],p['r'],p['p'])!=(16384,8,1): fail()
        if not re.fullmatch('[0-9a-f]{32}',p['salt']) or not re.fullmatch('[0-9a-f]{64}',p['digest']): fail()
    for digest,_ in db.execute('SELECT * FROM sessions'):
        if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest): fail()
    for table in ['users','restaurants','dining_tables','reservations']:
        for ident,raw in db.execute('SELECT id,data FROM '+table):
            if not isinstance(ident,str) or not 1<=len(ident)<=64 or not isinstance(json.loads(raw),dict): fail()
    from datetime import datetime
    reservations=[]
    for ident,reference,user,restaurant,raw in db.execute('SELECT * FROM reservations'):
        r=json.loads(raw)
        if r['reservation_id']!=ident or r['reference']!=reference or r['user_id']!=user or r['restaurant_id']!=restaurant: fail()
        if r['status'] not in ('confirmed','cancelled') or not re.fullmatch('[A-Z0-9]{6,12}',reference): fail()
        if datetime.fromisoformat(r['starts_at'])>=datetime.fromisoformat(r['ends_at']): fail()
        actual=[x[0] for x in db.execute('SELECT table_id FROM allocations WHERE reservation_id=?',(ident,))]
        if actual!=r['table_ids']: fail()
        reservations.append(r)
    from .reservations import overlaps
    for i,a in enumerate(reservations):
        for b in reservations[i+1:]:
            if overlaps(a,b): fail()
    for _,_,_,key,body,response in db.execute('SELECT * FROM receipts'):
        if not isinstance(key,str) or not 1<=len(key)<=255 or not isinstance(json.loads(body),dict) or not isinstance(json.loads(response),dict): fail()
