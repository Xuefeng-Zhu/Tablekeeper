"""Successful request identities and immutable response snapshots."""
import json
from .validation import canonical, fail
from .store import dumps


def lookup(db, user, method, path, key, body):
    if not key: fail('missing_idempotency_key',400)
    if len(key)>255: fail()
    scope=(user,method,path,key)
    row=db.execute('SELECT body,response FROM receipts WHERE user_id=? AND method=? AND path=? AND key=?',scope).fetchone()
    if row:
        if canonical(json.loads(row[0]))!=canonical(body): fail('idempotency_key_reuse',409)
        return scope,json.loads(row[1])
    return scope,None


def save(db,scope,body,result):
    db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)',(*scope,dumps(body),dumps(result)))
