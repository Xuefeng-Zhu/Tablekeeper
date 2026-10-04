import hashlib
import hmac
import secrets
import re
from .validation import string, fail

def password_hash(password):
    salt=secrets.token_hex(16)
    digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32).hex()
    return {'algorithm':'scrypt','n':16384,'r':8,'p':1,'salt':salt,'digest':digest}

def matches(password,record):
    digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(record['salt']),n=record['n'],r=record['r'],p=record['p'],dklen=32).hex()
    return hmac.compare_digest(digest,record['digest'])

def token_digest(token): return hashlib.sha256(token.encode()).hexdigest()

def credentials(body,signup=False):
    email=string(body,'email'); password=string(body,'password')
    if not re.fullmatch(r'[^@\s]+@[^@\s]+',email): fail()
    if signup and len(password)<8: fail()
    return email,password

def authenticate(db,header):
    if not header or not re.fullmatch(r'Bearer [^\s]+',header): fail('unauthenticated',401)
    row=db.execute('SELECT user_id FROM sessions WHERE digest=?',(token_digest(header[7:]),)).fetchone()
    if row is None: fail('unauthenticated',401)
    return row[0]
