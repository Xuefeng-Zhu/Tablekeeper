"""Serialized transactions with indexed immutable receipts and separate histories."""
import copy
import sqlite3
import threading
from . import codec as json
from contextlib import contextmanager


def empty():
    return dict(schema_version=1, users=[], sessions=[], restaurants=[],
                reservations=[], receipts=[], restaurant_revisions={})


class Transaction(dict):
    """Detached mutable core; callers explicitly mark a logical core change."""
    def __init__(self, store, core, writable):
        super().__init__(core)
        self.store = store
        self.writable = writable
        self.dirty = False

    def mark_dirty(self):
        if not self.writable:
            raise RuntimeError('Read-only transaction')
        self.dirty = True

    def receipt(self, user, method, path, key):
        row = self.store.db.execute(
            'SELECT request, response FROM receipts WHERE user_id=? AND method=? AND path=? AND key=?',
            (user, method, path, key)).fetchone()
        return None if row is None else dict(request=row[0], response=row[1])

    def insert_receipt(self, receipt):
        if not self.writable:
            raise RuntimeError('Read-only transaction')
        self.store.insert_receipt(receipt)

    def append_history(self, reservation_id, event):
        if not self.writable:
            raise RuntimeError('Read-only transaction')
        self.store.db.execute('INSERT INTO histories VALUES (?,?,?)',
                              (reservation_id, event['sequence'], json.dumps(event)))


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.generation = 0
        self.db = sqlite3.connect(':memory:', check_same_thread=False, isolation_level=None)
        self.db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL)')
        self.db.execute('''CREATE TABLE receipts (
            ordinal INTEGER PRIMARY KEY, user_id TEXT NOT NULL, method TEXT NOT NULL,
            path TEXT NOT NULL, key TEXT NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL,
            UNIQUE(user_id, method, path, key))''')
        self.db.execute('''CREATE TABLE histories (
            reservation_id TEXT NOT NULL, sequence INTEGER NOT NULL, event TEXT NOT NULL,
            PRIMARY KEY(reservation_id, sequence))''')
        initial = empty()
        del initial['receipts']
        self.db.execute('INSERT INTO state VALUES (1,?)', (json.dumps(initial),))

    def insert_receipt(self, receipt):
        self.db.execute('''INSERT INTO receipts(user_id,method,path,key,request,response)
                           VALUES (?,?,?,?,?,?)''',
                        tuple(receipt[k] for k in ['user_id','method','path','key','request','response']))

    @contextmanager
    def transaction(self, write=False):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            try:
                core = json.loads(self.db.execute('SELECT document FROM state WHERE id=1').fetchone()[0])
                state = Transaction(self, core, write)
                yield state
                if state.dirty:
                    self.db.execute('UPDATE state SET document=? WHERE id=1',
                                    (json.dumps(state, separators=(',', ':')),))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def health(self):
        with self.lock:
            self.db.execute('SELECT 1 FROM state WHERE id=1').fetchone()

    def export(self):
        with self.transaction() as state:
            snapshot = dict(state)
            snapshot['receipts'] = [dict(zip(
                ['user_id','method','path','key','request','response'], row))
                for row in self.db.execute('''SELECT user_id,method,path,key,request,response
                                               FROM receipts ORDER BY ordinal''')]
            for booking in snapshot['reservations']:
                booking['_history'] = [json.loads(row[0]) for row in self.db.execute(
                    'SELECT event FROM histories WHERE reservation_id=? ORDER BY sequence',
                    (booking['reservation_id'],))]
            return snapshot

    def replace(self, candidate):
        # Build detached replacement and serialized documents before entering the lock.
        core = copy.deepcopy(candidate)
        receipts = core.pop('receipts')
        histories = [(b['reservation_id'], h['sequence'], json.dumps(h))
                     for b in core['reservations'] for h in b.pop('_history')]
        document = json.dumps(core, separators=(',', ':'))
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('UPDATE state SET document=? WHERE id=1', (document,))
                self.db.execute('DELETE FROM receipts')
                self.db.execute('DELETE FROM histories')
                for receipt in receipts:
                    self.insert_receipt(receipt)
                self.db.executemany('INSERT INTO histories VALUES (?,?,?)', histories)
                self.db.execute('COMMIT')
                self.generation += 1
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
