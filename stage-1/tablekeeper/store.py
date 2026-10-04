import json
import sqlite3
import threading
from contextlib import contextmanager
from .validation import fail, Problem

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
                for column,value in zip(columns,row):
                    expected=int if column in ('position','counter') else str
                    if type(value) is not expected: fail()
                staged.db.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in row)+')',row)
        from .snapshot_validation import validate_snapshot
        validate_snapshot(staged.db)
        clear(db)
        for table in TABLES:
            for row in staged.db.execute('SELECT * FROM '+table):
                db.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in row)+')',tuple(row))
    except (Problem,KeyError,IndexError,AttributeError,TypeError,ValueError,OverflowError,sqlite3.Error): fail()
    finally: staged.db.close()
