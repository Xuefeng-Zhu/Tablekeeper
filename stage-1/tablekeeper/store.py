"""One connection and one transaction boundary for all application state."""
import sqlite3, threading, json
from contextlib import contextmanager

def empty():
    return dict(schema_version=1,users=[],sessions=[],restaurants=[],reservations=[],receipts=[],restaurant_revisions={})
class Store:
    def __init__(self):
        self.lock=threading.RLock(); self.generation=0
        self.db=sqlite3.connect(':memory:',check_same_thread=False,isolation_level=None)
        self.db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL)')
        self.db.execute('INSERT INTO state VALUES (1,?)',(json.dumps(empty()),))
    @contextmanager
    def transaction(self,write=False):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            try:
                s=json.loads(self.db.execute('SELECT document FROM state WHERE id=1').fetchone()[0])
                yield s
                if write: self.db.execute('UPDATE state SET document=? WHERE id=1',(json.dumps(s,separators=(',',':')),))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK'); raise
    def replace(self,candidate):
        with self.transaction(True) as s:
            s.clear(); s.update(candidate); self.generation+=1
