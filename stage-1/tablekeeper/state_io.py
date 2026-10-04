"""Detached, versioned portable snapshot validation. Never replay historical writes."""
from .core import *
from .receipt_validation import validate_receipt

def validate_import(envelope,service):
    try:
        if envelope.get('track')!='tablekeeper' or isinstance(envelope.get('format_version'),bool) or envelope.get('format_version')!=1: fail()
        state=envelope['state']
        if not isinstance(state,dict) or isinstance(state.get('schema_version'),bool) or state.get('schema_version')!=1: fail()
        # This schema emits only integral JSON numbers outside opaque receipt strings.
        def normalized(x):
            if isinstance(x,Decimal):
                if x!=int(x): fail()
                return int(x)
            if isinstance(x,list): return [normalized(v) for v in x]
            if isinstance(x,dict): return {k:normalized(v) for k,v in x.items()}
            return x
        s=normalized(state)
        required={'schema_version','users','sessions','restaurants','reservations','receipts','restaurant_revisions'}
        if set(s)!=required or any(not isinstance(s[k],list) for k in required-{'schema_version','restaurant_revisions'}): fail()
        users=set(); emails=set()
        for u in s['users']:
            i=text(u,'id',64); email=text(u,'email'); text(u,'display_name')
            if i in users or email in emails or not re.fullmatch(r'[^@\s]+@[^@\s]+',email): fail()
            users.add(i); emails.add(email); p=u['password']
            expected=dict(version=1,algorithm='scrypt',n=16384,r=8,p=1,dklen=64,maxmem=67108864)
            if set(p)!=set(expected)|{'salt','digest'} or any(type(p[k]) is not type(v) or p[k]!=v for k,v in expected.items()): fail()
            if not re.fullmatch('[0-9a-f]{32}',p['salt']) or not re.fullmatch('[0-9a-f]{128}',p['digest']): fail()
        tokens=set()
        for session in s['sessions']:
            token=text(session,'token'); user=text(session,'user_id',64)
            if user not in users or not token or token in tokens or re.search(r'\s',token): fail()
            tokens.add(token)
        restaurants={}
        for r in s['restaurants']:
            canonical=restaurant(r)
            if canonical!=r or r['id'] in restaurants: fail()
            restaurants[r['id']]=r
        revisions=s['restaurant_revisions']
        if not isinstance(revisions,dict) or revisions.keys()!=restaurants.keys() or any(type(v) is not int or v<0 for v in revisions.values()): fail()
        refs=set(); ids=set(); bookings=[]
        for b in s['reservations']:
            bid=text(b,'reservation_id',64); ref=text(b,'reference'); rid=text(b,'restaurant_id',64)
            if bid in ids or ref in refs or not re.fullmatch('[A-Z0-9]{6,12}',ref) or rid not in restaurants or b['_user_id'] not in users: fail()
            ids.add(bid); refs.add(ref)
            if b['status'] not in ['confirmed','cancelled']: fail()
            r=restaurants[rid]; expected=proposal(r,b)
            if any(b[k]!=v for k,v in expected.items()) or b['_terms']!=service.terms(r): fail()
            for k in ['starts_at','ends_at','created_at']:
                if not isinstance(b[k],str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?[+-][0-9]{2}:[0-9]{2}',b[k]): fail()
                instant(b[k])
            history=b['_history']
            if type(b['_revision']) is not int or b['_revision']<1 or not isinstance(history,list) or len(history)!=b['_revision']: fail()
            for i,h in enumerate(history):
                if h['sequence']!=i+1 or h['kind'] not in ['created','amended','cancelled'] or not isinstance(h['state'],dict): fail()
                instant(h['at'])
                if h['state'].get('reference')!=ref or h['state'].get('reservation_id')!=bid: fail()
            if history[-1]['state']!=view(b): fail()
            if b['status']=='confirmed' and any(x['status']=='confirmed' and overlaps(x,b) for x in bookings): fail()
            bookings.append(b)
        keys=set()
        for receipt in s['receipts']:
            user=text(receipt,'user_id',64); method=text(receipt,'method'); path=text(receipt,'path'); key=text(receipt,'key')
            if user not in users or method!='POST' or path not in ['/reservations','/reservation-moves'] or not 1<=len(key)<=255: fail()
            address=(user,method,path,key)
            if address in keys: fail()
            keys.add(address)
            request=parse(text(receipt,'request')); response=parse(text(receipt,'response'))
            validate_receipt(request,response,receipt,{b['reference']:b for b in bookings},restaurants)
        return s
    except (Error,KeyError,TypeError,ValueError,OverflowError,AttributeError): fail()
