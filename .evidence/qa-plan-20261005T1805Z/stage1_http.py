#!/usr/bin/env python3
"""Independent destructive HTTP checks; target must be a disposable test service.
No product imports, mock server, credentials in output or connected PASS without HTTP.
"""
import argparse
import concurrent.futures
import copy
import datetime as dt
import json
import pathlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DATE = '2099-01-05'
DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
FIXTURE = {
    'users': [
        {'id': 'u_ada', 'email': 'ada@example.test', 'password': 'synthetic-pass', 'display_name': 'Ada'},
        {'id': 'u_bea', 'email': 'bea@example.test', 'password': 'synthetic-pass', 'display_name': 'Bea'}],
    'restaurants': [{'id': 'r_f', 'name': 'Harbour', 'timezone': 'Europe/Berlin',
                     'slot_minutes': 30, 'reservation_duration_minutes': 90,
                     'cancellation_cutoff_minutes': 120,
                     'opening_hours': [{'weekday': d, 'opens': '18:00', 'closes': '23:00'} for d in DAYS],
                     'tables': [{'id': 't_z', 'label': 'Window', 'capacity': 2},
                                {'id': 't_a', 'label': 'Garden', 'capacity': 4},
                                {'id': 't_m', 'label': 'Hearth', 'capacity': 4}]}],
    'reservations': []}


def equal(a, b):
    """JSON-value equality, preserving boolean versus numeric distinction."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return a == b


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class HTTP:
    def __init__(self, url):
        self.url = url.rstrip('/')

    def call(self, method, path, body=None, token=None, key=None, raw=None):
        headers = {'Accept': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if key is not None:
            headers['Idempotency-Key'] = key
        if raw is not None or body is not None:
            headers['Content-Type'] = 'application/json; charset=utf-8'
        data = raw.encode() if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        before = time.monotonic()
        try:
            response = urllib.request.urlopen(request, timeout=10 if path.startswith('/_test/') else 5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            payload = response.read()
            elapsed = time.monotonic() - before
            status = response.status
            require(status < 500, 'request produced 5xx')
            require(elapsed <= (10 if path.startswith('/_test/') else 5), 'request exceeded time limit')
            value = json.loads(payload) if payload else None
            if status != 204:
                content_type = response.headers.get('Content-Type', '').lower().replace(' ', '')
                require('application/json' in content_type and 'charset=utf-8' in content_type,
                        'API content type lacks JSON UTF-8 contract')
            if status >= 400:
                require(isinstance(value, dict) and isinstance(value.get('error'), dict), 'invalid error envelope')
                require(isinstance(value['error'].get('message'), str) and bool(value['error']['message']), 'empty error message')
            return status, value


class Suite:
    def __init__(self, url):
        self.http = HTTP(url)
        self.token = None

    def call(self, method, path, body=None, key=None, raw=None, token='owner'):
        return self.http.call(method, path, body, self.token if token == 'owner' else token, key, raw)

    def expected(self, response, status, code=None):
        require(response[0] == status, f'expected HTTP {status}; observed {response[0]}')
        if code:
            require(response[1]['error']['code'] == code, f'expected error code {code}')
        return response[1]

    def reset(self, fixture=None):
        self.expected(self.http.call('POST', '/_test/reset', fixture or FIXTURE), 204)
        self.token = self.expected(self.http.call('POST', '/auth/login',
                    {'email': 'ada@example.test', 'password': 'synthetic-pass'}), 200)['token']

    def body(self, table='t_z', start='19:00', party=2):
        return {'restaurant_id': 'r_f', 'table_id': table, 'starts_at_local': DATE + 'T' + start, 'party_size': party}

    def create(self, body=None, key='create'):
        return self.expected(self.call('POST', '/reservations', body or self.body(), key), 201)

    def snapshot(self):
        return self.expected(self.http.call('GET', '/_test/export'), 200)

    def availability(self, party=2, date=DATE):
        return self.expected(self.http.call('GET', '/availability?' + urllib.parse.urlencode(
            {'restaurant_id': 'r_f', 'date': date, 'party_size': party})), 200)

    def test_public_slots_and_grid(self):
        self.expected(self.http.call('GET', '/health'), 200)
        require(equal(self.http.call('GET', '/health')[1], {'status': 'ok'}), 'health body differs')
        self.expected(self.http.call('GET', '/restaurants'), 200)
        detail = self.expected(self.http.call('GET', '/restaurants/r_f'), 200)
        require([t['id'] for t in detail['tables']] == ['t_z', 't_a', 't_m'], 'table fixture order lost')
        slots = self.availability()['slots']
        require([s['starts_at_local'] for s in slots] == [DATE+'T'+x for x in
                ['18:00','18:30','19:00','19:30','20:00','20:30','21:00','21:30']], 'slot starts differ')
        require(all(s['available_table_ids'] == ['t_z','t_a','t_m'] for s in slots), 'available table order differs')
        for start, code in [('17:30','outside_opening_hours'),('22:00','outside_opening_hours'),('19:01','not_on_slot_grid')]:
            self.expected(self.call('POST','/reservations',self.body(start=start),'invalid-'+start),422,code)
        self.create(self.body(start='21:30'), 'last')

    def test_validation_classes(self):
        for raw in ['{', '[]', 'null']:
            self.expected(self.call('POST','/reservations',key='invalid',raw=raw),400,'malformed_request')
        for party in [True, False, '2', 0, -1, 1.5]:
            self.expected(self.call('POST','/reservations',self.body(party=party),'invalid'),422,'validation_failed')
        for local in ['2099-02-29T19:00',DATE+'T19:00Z',DATE+'T19:00:00',DATE+'T19:00+01:00']:
            body=self.body();body['starts_at_local']=local
            self.expected(self.call('POST','/reservations',body,'invalid'),422,'validation_failed')
        body=self.body();body['starts_at_local']=3
        self.expected(self.call('POST','/reservations',body,'invalid'),400,'malformed_request')
        for party in ['1e9','4.0','+4','0','-1','٤']:
            path='/availability?restaurant_id=r_f&date='+DATE+'&party_size='+urllib.parse.quote(party)
            self.expected(self.http.call('GET',path),422,'validation_failed')
        self.create(self.body(party=2.0), 'invalid')

    def test_key_bounds_and_failed_reuse(self):
        self.expected(self.call('POST','/reservations',self.body()),400,'missing_idempotency_key')
        self.expected(self.call('POST','/reservations',self.body(),''),400,'missing_idempotency_key')
        self.expected(self.call('POST','/reservations',self.body(),'x'*256),422,'validation_failed')
        self.create(key='x')
        self.expected(self.call('POST','/reservations',self.body(),'retry'),409,'table_unavailable')
        self.create(self.body('t_a'), 'retry')
        self.create(self.body('t_m'), 'x'*255)

    def test_replay_identity_and_precedence(self):
        body=self.body();body['unknown']={'a':1,'array':[1,2]}
        original=self.create(body,'identity')
        raw=' {"unknown":{"array":[1,2],"a":1.0},"party_size":2e0,"starts_at_local":"'+DATE+'T19:00","table_id":"t_z","restaurant_id":"r_f"} '
        replay=self.expected(self.call('POST','/reservations',key='identity',raw=raw),200)
        require(equal(original,replay),'reordered/numeric-equal receipt changed')
        for field,value in [('restaurant_id',4),('restaurant_id','missing'),('party_size',False),('unknown',{'a':True,'array':[1,2]}),('unknown',{'a':1,'array':[2,1]})]:
            changed=copy.deepcopy(body);changed[field]=value
            before=self.snapshot()
            self.expected(self.call('POST','/reservations',changed,'identity'),409,'idempotency_key_reuse')
            require(equal(before,self.snapshot()),'rejected key reuse mutated state')
        self.expected(self.call('PATCH','/reservations/'+original['reference'],{'party_size':1}),200)
        self.expected(self.call('POST','/reservations/'+original['reference']+'/cancel',{}),200)
        before=self.snapshot()
        require(equal(original,self.expected(self.call('POST','/reservations',body,'identity'),200)), 'original receipt mutated after change/cancel')
        require(equal(before,self.snapshot()),'successful replay wrote state')

    def test_user_and_path_scopes(self):
        first=self.create(key='shared')
        bea=self.expected(self.http.call('POST','/auth/login',{'email':'bea@example.test','password':'synthetic-pass'}),200)['token']
        self.expected(self.call('POST','/reservations',self.body('t_a'),'shared',token=bea),201)
        self.expected(self.call('GET','/reservations/'+first['reference'],token=bea),404,'not_found')
        moves={'moves':[{'reference':first['reference']}]}
        self.expected(self.call('POST','/reservation-moves',moves,'shared'),201)
        self.expected(self.call('GET','/reservations',token=None),401,'unauthenticated')

    def test_half_open_and_failed_patch(self):
        first=self.create();fixed=self.create(self.body('t_a'),'fixed')
        before=self.snapshot()
        self.expected(self.call('POST','/reservations',self.body(start='20:00'),'conflict'),409,'table_unavailable')
        self.expected(self.call('PATCH','/reservations/'+first['reference'],{'table_id':'t_a'}),409,'table_unavailable')
        require(equal(before,self.snapshot()),'failed overlapping operations changed state')
        self.create(self.body(start='20:30'),'adjacent')
        amended=self.expected(self.call('PATCH','/reservations/'+first['reference'],{'table_id':'t_m'}),200)
        require(amended['reservation_id']==first['reservation_id'] and amended['reference']==first['reference'],'patch changed identity')
        slot=next(s for s in self.availability()['slots'] if s['starts_at_local']==DATE+'T19:00')
        require(slot['available_table_ids']==['t_z'],'amendment occupancy wrong')

    def test_batch_swap_atomicity_and_noop(self):
        a=self.create();b=self.create(self.body('t_a'),'b')
        swap={'moves':[{'reference':a['reference'],'table_id':'t_a'},{'reference':b['reference'],'table_id':'t_z'}]}
        response=self.expected(self.call('POST','/reservation-moves',swap,'swap'),201)
        require([x['reference'] for x in response['reservations']]==[a['reference'],b['reference']],'batch order differs')
        require([x['table_id'] for x in response['reservations']]==['t_a','t_z'],'swap did not commit')
        before=self.snapshot()
        bad={'moves':[{'reference':a['reference'],'table_id':'t_m'},{'reference':b['reference'],'table_id':'missing'}]}
        self.expected(self.call('POST','/reservation-moves',bad,'failed'),404,'not_found')
        require(equal(before,self.snapshot()),'failed batch partial mutation')
        self.expected(self.call('POST','/reservation-moves',{'moves':[{'reference':a['reference']}]},'failed'),201)
        self.expected(self.call('POST','/reservations/'+a['reference']+'/cancel',{}),200)
        require(equal(response,self.expected(self.call('POST','/reservation-moves',swap,'swap'),200)),'batch receipt mutated')

    def test_batch_validation_before_overlap(self):
        a=self.create();b=self.create(self.body('t_a'),'b');self.create(self.body('t_m'),'fixed')
        before=self.snapshot()
        body={'moves':[{'reference':a['reference'],'table_id':'t_m'},
                       {'reference':b['reference'],'starts_at_local':'bad'}]}
        self.expected(self.call('POST','/reservation-moves',body,'bad'),422,'validation_failed')
        require(equal(before,self.snapshot()),'precedence rejection mutated state')
        for moves in [[],[{'reference':a['reference']},{'reference':a['reference']}],['bad']]:
            self.expected(self.call('POST','/reservation-moves',{'moves':moves},'bad'),422,'validation_failed')

    def test_concurrent_identical_and_competing(self):
        def run(keys):
            barrier=threading.Barrier(len(keys))
            def worker(key):
                barrier.wait(timeout=10)
                return self.call('POST','/reservations',self.body(),key)
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(keys)) as pool:
                return list(pool.map(worker,keys))
        identical=run(['concurrent']*50)
        require(sum(x[0]==201 for x in identical)==1 and sum(x[0]==200 for x in identical)==49,'same-key concurrent statuses wrong')
        require(all(equal(x[1],identical[0][1]) for x in identical),'same-key concurrent receipts differ')
        require(len(self.expected(self.call('GET','/reservations'),200)['reservations'])==1,'same-key duplicated booking')
        self.reset()
        competing=run(['competitor-'+str(i) for i in range(50)])
        require(sum(x[0]==201 for x in competing)==1,'competing creates did not have one winner')
        for result in competing:
            if result[0]!=201:self.expected(result,409,'table_unavailable')
        require(len(self.expected(self.call('GET','/reservations'),200)['reservations'])==1,'competing writes duplicated occupancy')

    def test_past_allowed_cutoff_and_cancel(self):
        past=self.body();past['starts_at_local']='2000-01-03T19:00'
        row=self.create(past,'past')
        for method,path,body in [('POST','/reservations/'+row['reference']+'/cancel',{}),('PATCH','/reservations/'+row['reference'],{'party_size':1})]:
            self.expected(self.call(method,path,body),409,'cutoff_passed')
        future=self.create(key='future')
        cancelled=self.expected(self.call('POST','/reservations/'+future['reference']+'/cancel',{}),200)
        require(cancelled['status']=='cancelled','cancel status wrong')
        require(equal(cancelled,self.expected(self.call('POST','/reservations/'+future['reference']+'/cancel',{}),200)),'second cancel differs')
        self.expected(self.call('PATCH','/reservations/'+future['reference'],{'party_size':1}),409,'reservation_cancelled')
        require('t_z' in next(s for s in self.availability()['slots'] if s['starts_at_local']==DATE+'T19:00')['available_table_ids'],'cancel did not release table')

    def test_dst_literal_oracles(self):
        cases=[('Europe/Berlin','2026-03-29','01:30','2026-03-29T01:30:00+01:00','2026-03-29T04:00:00+02:00','02:30'),
               ('America/New_York','2026-03-08','01:30','2026-03-08T01:30:00-05:00','2026-03-08T04:00:00-04:00','02:30'),
               ('Europe/Berlin','2026-10-25','02:30','2026-10-25T02:30:00+02:00','2026-10-25T03:00:00+01:00',None),
               ('America/New_York','2026-11-01','01:30','2026-11-01T01:30:00-04:00','2026-11-01T02:00:00-05:00',None)]
        for zone,date,clock,start,end,gap in cases:
            fixture=copy.deepcopy(FIXTURE);fixture['restaurants'][0]['timezone']=zone
            fixture['restaurants'][0]['opening_hours']=[{'weekday':d,'opens':'00:00','closes':'05:00'} for d in DAYS]
            self.reset(fixture)
            slots=self.availability(date=date)['slots']
            require(sum(s['starts_at_local']==date+'T'+clock for s in slots)==1,'repeated/local minute not unique')
            if gap:
                require(not any(s['starts_at_local']==date+'T'+gap for s in slots),'gap appeared in availability')
                body=self.body();body['starts_at_local']=date+'T'+gap
                self.expected(self.call('POST','/reservations',body,'gap'),422,'invalid_local_time')
            body=self.body();body['starts_at_local']=date+'T'+clock
            row=self.create(body,'dst')
            require(row['starts_at']==start and row['ends_at']==end,'DST fixed instant/offset oracle differs')
            require((dt.datetime.fromisoformat(row['ends_at'])-dt.datetime.fromisoformat(row['starts_at'])).total_seconds()==5400,'duration not90 real minutes')

    def test_export_replacement_receipts_and_rollback(self):
        original=self.create(key='portable');original_token=self.token
        self.expected(self.call('POST','/reservations',self.body(),'failed'),409,'table_unavailable')
        second=self.expected(self.http.call('POST','/auth/login',{'email':'ada@example.test','password':'synthetic-pass'}),200)['token']
        exported=self.snapshot();frozen=copy.deepcopy(exported)
        self.expected(self.call('POST','/reservations/'+original['reference']+'/cancel',{}),200)
        require(equal(exported,frozen),'saved export changed')
        self.reset();destination_token=self.token
        self.expected(self.http.call('POST','/_test/import',exported),204)
        self.token=original_token
        self.expected(self.call('GET','/reservations'),200)
        self.expected(self.call('GET','/reservations',token=second),200)
        self.expected(self.call('GET','/reservations',token=destination_token),401,'unauthenticated')
        require(equal(original,self.expected(self.call('POST','/reservations',self.body(),'portable'),200)),'import changed original receipt')
        self.expected(self.http.call('POST','/_test/import',exported),204)
        require(len(self.expected(self.call('GET','/reservations'),200)['reservations'])==1,'repeated import duplicated booking')
        for invalid in [{},dict(exported,track='other'),dict(exported,format_version=2),dict(exported,state={})]:
            before=self.snapshot()
            self.expected(self.http.call('POST','/_test/import',invalid),422,'validation_failed')
            require(equal(before,self.snapshot()),'invalid import changed destination')
        self.create(self.body('t_a'),'failed')
        self.expected(self.http.call('POST','/auth/login',{'email':'ada@example.test','password':'synthetic-pass'}),200)


COVERAGE={
 'public_slots_and_grid':['Q01','Q12','Q13'], 'validation_classes':['Q05','Q12'],
 'key_bounds_and_failed_reuse':['Q06'], 'replay_identity_and_precedence':['Q08','Q09'],
 'user_and_path_scopes':['Q07','Q10','Q15'], 'half_open_and_failed_patch':['Q14','Q17'],
 'batch_swap_atomicity_and_noop':['Q22'], 'batch_validation_before_overlap':['Q23'],
 'concurrent_identical_and_competing':['Q11'], 'past_allowed_cutoff_and_cancel':['Q16'],
 'dst_literal_oracles':['Q18','Q19'], 'export_replacement_receipts_and_rollback':['Q20','Q21']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list',action='store_true',help='List authored checks; performs no behavior test')
    parser.add_argument('--base-url');parser.add_argument('--revision');parser.add_argument('--output')
    args=parser.parse_args()
    names=sorted(n for n in dir(Suite) if n.startswith('test_'))
    if args.list:
        print(json.dumps({'layer':'STATIC_INVENTORY','connected_execution':'NOT_TESTED','checks':[
            {'name':n,'representative_cases':COVERAGE[n[5:]]} for n in names]},indent=2));return
    if not args.base_url or not args.output or not re.fullmatch('[a-f0-9]{40}',args.revision or ''):
        parser.error('execution requires disposable --base-url, full --revision and fresh --output directory')
    out=pathlib.Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
    started=dt.datetime.now(dt.timezone.utc).isoformat();suite=Suite(args.base_url);results=[]
    for name in names:
        begin=dt.datetime.now(dt.timezone.utc).isoformat();clock=time.monotonic()
        try:
            suite.reset();getattr(suite,name)();status='PASS';reason='Observed HTTP assertions satisfied'
        except Exception as error:
            status='FAIL';reason=str(error) if isinstance(error,AssertionError) else type(error).__name__
        results.append({'test':name,'cases':COVERAGE[name[5:]],'status':status,'reason':reason,
                        'started_utc':begin,'ended_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
                        'elapsed_seconds':time.monotonic()-clock})
        (out/'results.json').write_text(json.dumps({'work_item':'TK-QA-PLAN','candidate_commit':args.revision,
            'responsible_handle':'@frankzhu94/factory-qa','layer':'CONNECTED_HTTP','environment':'caller-supplied disposable HTTP target; isolation must be attested separately',
            'started_utc':started,'ended_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
            'results':results,'limitations':['Representative Stage1 examples only; not all matrix cases','No container, browser, upgrade cross-version or exact-cutoff proof','Private snapshots used in memory and never saved']},indent=2))
    raise SystemExit(0 if all(r['status']=='PASS' for r in results) else 1)

if __name__=='__main__':main()
