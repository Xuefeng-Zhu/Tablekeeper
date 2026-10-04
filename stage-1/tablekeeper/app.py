import json
import secrets
import sqlite3
from datetime import datetime,timedelta
from flask import Flask,request,Response
from werkzeug.exceptions import HTTPException
from . import auth, receipts, reservations as bookings
from .store import Store,clear,dumps,export_state,import_state
from .validation import Problem,fail,parse,string,identifier,query_party,canonical
from .time_rules import DAYS,local_parse,interval,formatted,now

def create_app():
    app=Flask(__name__)
    store=Store();app.config['STORE']=store
    def response(body,status=200): return Response(dumps(body),status=status,content_type='application/json; charset=utf-8')
    @app.errorhandler(Problem)
    def problem(error): return response({'error':{'code':error.code,'message':error.code.replace('_',' ')}},error.status)
    @app.errorhandler(HTTPException)
    def http_error(error): return response({'error':{'code':'not_found' if error.code==404 else 'malformed_request','message':error.name}},error.code)
    @app.errorhandler(Exception)
    def unexpected(error):
        app.logger.error('Unhandled request error: %s',type(error).__name__)
        return response({'error':{'code':'internal_error','message':'Internal service error'}},500)
    @app.route('/health')
    def health():
        with store.transaction() as db: db.execute('SELECT 1')
        return response({'status':'ok'})
    @app.route('/_test/export')
    def export():
        with store.transaction() as db: result=export_state(db)
        return response(result)
    @app.route('/_test/import',methods=['POST'])
    def import_():
        body=parse(request.get_data())
        with store.transaction() as db: import_state(db,body)
        return Response(status=204)
    @app.route('/_test/reset',methods=['POST'])
    def reset():
        fixture=parse(request.get_data())
        with store.transaction() as db:
            from .fixture import replace_fixture
            replace_fixture(db,fixture)
        return Response(status=204)
    @app.route('/auth/<action>',methods=['POST'])
    def account(action):
        if action not in ('login','signup'): fail('not_found',404)
        body=parse(request.get_data());email,password=auth.credentials(body,action=='signup')
        with store.transaction() as db:
            row=db.execute('SELECT data FROM users WHERE email=?',(email,)).fetchone()
            if action=='signup':
                name=string(body,'display_name')
                if row: fail('email_taken',409)
                user={'id':secrets.token_hex(16),'email':email,'display_name':name,'password_hash':auth.password_hash(password)}
                db.execute('INSERT INTO users VALUES(?,?,?)',(user['id'],email,dumps(user)))
            else:
                if not row: fail('unauthenticated',401)
                user=json.loads(row[0])
                if not auth.matches(password,user['password_hash']): fail('unauthenticated',401)
            token=secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES(?,?)',(auth.token_digest(token),user['id']))
            result={'user_id':user['id'],'display_name':user['display_name'],'token':token}
        return response(result,201 if action=='signup' else 200)
    @app.route('/restaurants')
    def restaurants():
        with store.transaction() as db:
            result=[{k:r[k] for k in ('id','name','timezone')} for r in [json.loads(x[0]) for x in db.execute('SELECT data FROM restaurants ORDER BY position')]]
        return response({'restaurants':result})
    @app.route('/restaurants/<rid>')
    def detail(rid):
        with store.transaction() as db: result=bookings.restaurant(db,rid)
        return response(result)
    @app.route('/availability')
    def availability():
        args=request.args
        rid=identifier(args,'restaurant_id');size=query_party(args.get('party_size'));date=string(args,'date')
        try:
            if len(date)!=10: fail()
            day=local_parse(date+'T00:00')
        except ValueError: fail()
        with store.transaction() as db:
            r=bookings.restaurant(db,rid);slots=[]
            hours=next((h for h in r['opening_hours'] if h['weekday']==DAYS[day.weekday()]),None)
            occupied=bookings.all_reservations(db)
            if hours:
                wall=local_parse(date+'T'+hours['opens']);close=local_parse(date+'T'+hours['closes'])
                while wall<close:
                    value=wall.strftime('%Y-%m-%dT%H:%M')
                    try: start,end=interval(r,value)
                    except Problem as error:
                        if error.code not in ('invalid_local_time','outside_opening_hours'): raise
                    else:
                        available=[]
                        for t in r['tables']:
                            candidate={'restaurant_id':rid,'status':'confirmed','table_ids':[t['id']],'starts_at':start.isoformat(),'ends_at':end.isoformat()}
                            if t['capacity']>=size and not any(bookings.overlaps(candidate,o) for o in occupied): available.append(t['id'])
                        slots.append({'starts_at_local':value,'starts_at':formatted(start,r['timezone']),'available_table_ids':available})
                    wall+=timedelta(minutes=min(r['slot_minutes'],1440))
            result={'restaurant_id':rid,'date':date,'timezone':r['timezone'],'slots':slots}
        return response(result)
    @app.route('/reservations',methods=['GET','POST'])
    @app.route('/reservation-moves',methods=['POST'])
    def collection():
        body=parse(request.get_data()) if request.method=='POST' else None
        with store.transaction() as db:
            user=auth.authenticate(db,request.headers.get('Authorization'))
            if request.method=='GET':
                rows=[r for r in bookings.all_reservations(db) if r['user_id']==user]
                rows.sort(key=lambda r:datetime.fromisoformat(r['starts_at']),reverse=True)
                result={'reservations':[bookings.public(r) for r in rows]};status=200
            else:
                scope,result=receipts.lookup(db,user,request.method,request.path,request.headers.get('Idempotency-Key'),body);status=200
                if result is None:
                    result=bookings.moves(db,body,user) if request.path=='/reservation-moves' else bookings.create(db,body,user)
                    receipts.save(db,scope,body,result);status=201
        return response(result,status)
    @app.route('/reservations/<reference>',methods=['GET','PATCH'])
    @app.route('/reservations/<reference>/cancel',methods=['POST'])
    def reservation(reference):
        body=parse(request.get_data()) if request.method=='PATCH' else None
        with store.transaction() as db:
            user=auth.authenticate(db,request.headers.get('Authorization'));r=bookings.owned(db,reference,user);instant=now()
            if request.method=='PATCH':
                new=bookings.amend(db,r,body,instant);bookings.check_occupancy(db,[new]);bookings.save_changes(db,r,new,instant);r=new
            elif request.method=='POST' and r['status']!='cancelled':
                bookings.editable(r,instant);new={**r,'status':'cancelled','revision':r['revision']+1,'history':list(r['history'])};bookings.save_changes(db,r,new,instant,'cancelled');r=new
            result=bookings.public(r)
        return response(result)
    return app
