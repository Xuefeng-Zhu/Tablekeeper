#!/usr/bin/env python3
"""Independent HTTP-only Stage 1 probes. Destructive: use dedicated synthetic services.
Run --list without a service. Run --base-url URL --revision FULL_SHA --out NEW.json.
Optional --destination-url URL exercises portable import across separate containers.
No product helpers, source imports, network dependencies, or receipt/token logging.
"""
import argparse
import concurrent.futures
import copy
import datetime as dt
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

METHODS = ['test_auth', 'test_types', 'test_keys', 'test_scope', 'test_grid',
           'test_patch', 'test_cutoff', 'test_dst', 'test_moves',
           'test_concurrency', 'test_portability']
PASSWORD = 'synthetic-password'
FUTURE = '2096-09-24'


def fixture(zone='UTC', opens='18:00', closes='23:00', grid=30, duration=90, cutoff=120):
    return {'users': [{'id': u, 'email': u+'@example.test', 'password': PASSWORD,
                       'display_name': u} for u in ('alice', 'bob')],
            'restaurants': [{'id': r, 'name': r, 'timezone': zone,
                             'slot_minutes': grid, 'reservation_duration_minutes': duration,
                             'cancellation_cutoff_minutes': cutoff,
                             'opening_hours': [{'weekday': day, 'opens': opens, 'closes': closes}
                                               for day in ['mon','tue','wed','thu','fri','sat','sun']],
                             'tables': [{'id': r+'-a','label': 'Window','capacity': 2},
                                        {'id': r+'-b','label': 'Garden','capacity': 4}]} for r in ('r', 'other')],
            'reservations': []}


def http(base, method, path, body=None, token=None, key=None, raw=None):
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    if token is not None:
        headers['Authorization'] = 'Bearer '+token
    if key is not None:
        headers['Idempotency-Key'] = key
    data = raw.encode() if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base.rstrip('/')+path, data=data, headers=headers, method=method)
    started = time.monotonic()
    try:
        response = urllib.request.urlopen(req, timeout=10 if path.startswith('/_test/') else 5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        status = response.status
        payload = response.read()
        assert status < 500, f'{method} {path}: unexpected {status}'
        if status == 204:
            assert not payload, '204 must have no body'
            return status, None
        assert 'application/json' in response.headers.get('Content-Type', ''), 'missing JSON content type'
        parsed = json.loads(payload)
        if status >= 400:
            assert isinstance(parsed.get('error', {}).get('code'), str), 'missing error code'
            assert isinstance(parsed['error'].get('message'), str) and parsed['error']['message'], 'missing error message'
        assert time.monotonic()-started < (10 if path.startswith('/_test/') else 5), 'timeout exceeded'
        return status, parsed


class Suite:
    def __init__(self, base, destination):
        self.base, self.destination = base, destination
        self.tokens = {}
        self.serial = 0

    def request(self, method, path, body=None, user='alice', key=None, raw=None, base=None):
        return http(base or self.base, method, path, body, self.tokens.get(user), key, raw)

    def expect(self, response, status, code=None):
        actual, body = response
        assert actual == status, f'expected HTTP {status}; got {actual}'
        if code:
            assert body['error']['code'] == code, f'expected {code}; got {body["error"]["code"]}'
        return body

    def reset(self, data=None):
        self.expect(http(self.base, 'POST', '/_test/reset', data or fixture()), 204)
        self.tokens = {}
        for user in ('alice','bob'):
            self.tokens[user] = self.expect(http(self.base, 'POST', '/auth/login',
                {'email': user+'@example.test', 'password': PASSWORD}), 200)['token']

    def body(self, **changes):
        body = {'restaurant_id': 'r', 'table_id': 'r-a', 'starts_at_local': FUTURE+'T18:00', 'party_size': 2}
        body.update(changes)
        return body

    def create(self, body=None, key=None, user='alice'):
        self.serial += 1
        return self.request('POST', '/reservations', body or self.body(), user, key or f'qa-{self.serial}')

    def get(self, reference):
        return self.expect(self.request('GET', '/reservations/'+reference), 200)

    def listing(self):
        return self.expect(self.request('GET', '/reservations'), 200)['reservations']

    def availability(self, date=FUTURE, party=2):
        q=urllib.parse.urlencode({'restaurant_id':'r','date':date,'party_size':party})
        return self.expect(http(self.base, 'GET', '/availability?'+q), 200)

    def test_auth(self):
        self.reset()
        self.expect(http(self.base,'GET','/health'),200)
        for path in ['/restaurants','/restaurants/r','/availability?restaurant_id=r&date='+FUTURE+'&party_size=2']:
            self.expect(http(self.base,'GET',path),200)
        for password,status in [('1234567',422),('12345678',201)]:
            self.expect(http(self.base,'POST','/auth/signup',{'email':'new@example.test','display_name':'New','password':password}),status)
        self.expect(http(self.base,'POST','/auth/signup',{'email':'new@example.test','display_name':'New','password':'12345678'}),409,'email_taken')
        self.expect(http(self.base,'POST','/auth/login',{'email':'alice@example.test','password':'incorrect'}),401,'unauthenticated')
        old=self.tokens['alice']
        self.expect(http(self.base,'POST','/auth/login',{'email':'alice@example.test','password':PASSWORD}),200)
        self.expect(http(self.base,'GET','/reservations',token=old),200)
        a=self.expect(self.create(),201)
        for method,path,body in [('GET','',None),('POST','/cancel',{}),('PATCH','',{'party_size':1})]:
            self.expect(self.request(method,'/reservations/'+a['reference']+path,body,user='bob'),404,'not_found')
        self.expect(http(self.base,'GET','/reservations'),401,'unauthenticated')
        self.expect(http(self.base,'GET','/reservations',token='unknown'),401,'unauthenticated')

    def test_types(self):
        self.reset()
        for raw in ['{', '[]', 'null']:
            self.expect(self.request('POST','/reservations',key='bad-json',raw=raw),400,'malformed_request')
        for value in [0,-1,1.5,'2',True,None]:
            self.expect(self.create(self.body(party_size=value)),422,'validation_failed')
        for value in [4,False,[],{}]:
            self.expect(self.create(self.body(table_id=value)),400,'malformed_request')
        for value in [FUTURE+'T18:00Z',FUTURE+'T18:00:00',FUTURE+'T18:00+00:00','2096-02-30T18:00']:
            self.expect(self.create(self.body(starts_at_local=value)),422,'validation_failed')
        for value in [False,42,[],{}]:
            self.expect(self.create(self.body(starts_at_local=value)),400,'malformed_request')
        for value in ['1e9','4.0','+4','-1','']:
            path='/availability?restaurant_id=r&date='+FUTURE+'&party_size='+urllib.parse.quote(value,safe='')
            self.expect(http(self.base,'GET',path),422,'validation_failed')
        self.expect(self.create(self.body(party_size=3)),422,'party_exceeds_capacity')
        self.expect(self.create(self.body(party_size=1)),201)

    def test_keys(self):
        self.reset()
        for key in [None,'']:
            self.expect(self.request('POST','/reservations',self.body(),key=key),400,'missing_idempotency_key')
        self.expect(self.create(key='x'*256),422,'validation_failed')
        first=self.expect(self.create(key='x'),201)
        reversed_body=dict(reversed(list(self.body().items())))
        reversed_body['party_size']=2.0
        self.expect(self.request('POST','/reservations',key='x',raw=json.dumps(reversed_body,indent=2)),200)
        assert self.expect(self.create(key='x'),200)==first
        self.expect(self.create(self.body(ignored=True),key='x'),409,'idempotency_key_reuse')
        self.expect(self.create(self.body(table_id=42,party_size=False),key='x'),409,'idempotency_key_reuse')
        self.expect(self.request('POST','/reservations',key='x',raw='{'),400,'malformed_request')
        self.expect(self.request('POST','/reservations/'+first['reference']+'/cancel',{}),200)
        assert self.expect(self.create(key='x'),200)==first
        assert self.get(first['reference'])['status']=='cancelled'
        self.expect(self.create(self.body(table_id='r-b'),key='y'*255),201)
        self.expect(self.create(self.body(party_size=0),key='failed'),422,'validation_failed')
        self.expect(self.create(key='failed'),201)

    def test_scope(self):
        self.reset()
        a=self.expect(self.create(key='shared',user='alice'),201)
        self.expect(self.create(self.body(table_id='r-b'),key='shared',user='bob'),201)
        both=self.body(starts_at_local=FUTURE+'T20:00', moves=[{'reference':a['reference']}])
        self.expect(self.request('POST','/reservations',both,key='paths'),201)
        self.expect(self.request('POST','/reservation-moves',both,key='paths'),201)
        self.expect(self.request('POST','/reservation-moves',both,key='paths'),200)

    def test_grid(self):
        self.reset(fixture(opens='18:10',closes='22:10'))
        slots=self.availability()['slots']
        expected=['18:10','18:40','19:10','19:40','20:10','20:40']
        assert [s['starts_at_local'][-5:] for s in slots]==expected
        self.expect(self.create(self.body(starts_at_local=FUTURE+'T18:11')),422,'not_on_slot_grid')
        self.expect(self.create(self.body(starts_at_local=FUTURE+'T21:10')),422,'outside_opening_hours')
        a=self.expect(self.create(self.body(starts_at_local=FUTURE+'T18:10')),201)
        assert re.fullmatch('[A-Z0-9]{6,12}',a['reference'])
        assert len(a['reservation_id'])<=64
        assert dt.datetime.fromisoformat(a['starts_at']).utcoffset() is not None
        slots=self.availability()['slots']
        assert slots[0]['available_table_ids']==['r-b']
        assert slots[3]['available_table_ids']==['r-a','r-b']
        self.expect(self.create(self.body(starts_at_local=FUTURE+'T19:40')),201)
        self.expect(self.create(self.body(table_id='other-a')),404,'not_found')
        f=fixture();f['restaurants'][0]['opening_hours']=[];self.reset(f)
        assert self.availability()['slots']==[]

    def test_patch(self):
        self.reset()
        a=self.expect(self.create(),201)
        self.expect(self.create(self.body(table_id='r-b')),201)
        self.expect(self.request('PATCH','/reservations/'+a['reference'],{'table_id':'r-b'}),409,'table_unavailable')
        assert self.get(a['reference'])==a
        changed=self.expect(self.request('PATCH','/reservations/'+a['reference'],{'starts_at_local':FUTURE+'T20:00','party_size':1}),200)
        for field in ['reservation_id','reference','created_at']:
            assert a[field]==changed[field]
        assert self.availability()['slots'][0]['available_table_ids']==['r-a']
        self.expect(self.request('POST','/reservations/'+a['reference']+'/cancel',{}),200)
        self.expect(self.request('PATCH','/reservations/'+a['reference'],{'party_size':2}),409,'reservation_cancelled')

    def test_cutoff(self):
        self.reset()
        a=self.expect(self.create(self.body(starts_at_local='2000-01-01T18:00')),201)
        self.expect(self.request('POST','/reservations/'+a['reference']+'/cancel',{}),409,'cutoff_passed')
        self.expect(self.request('PATCH','/reservations/'+a['reference'],{'starts_at_local':FUTURE+'T18:00'}),409,'cutoff_passed')
        b=self.expect(self.create(),201)
        cancelled=self.expect(self.request('POST','/reservations/'+b['reference']+'/cancel',{}),200)
        assert self.expect(self.request('POST','/reservations/'+b['reference']+'/cancel',{}),200)==cancelled
        # Safe margins around cutoff; exact equality is a separate controlled-clock plan.
        now=dt.datetime.now(dt.timezone.utc)
        zone='UTC'
        if now.hour >= 21:
            zone='Pacific/Honolulu'
            now=now.astimezone(dt.timezone(dt.timedelta(hours=-10)))
        for minutes,status in [(122,200),(118,409)]:
            self.reset(fixture(zone=zone,opens='00:00',closes='23:59',grid=1,duration=1,cutoff=120))
            local=(now+dt.timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M')
            r=self.expect(self.create(self.body(starts_at_local=local)),201)
            self.expect(self.request('POST','/reservations/'+r['reference']+'/cancel',{}),status, 'cutoff_passed' if status==409 else None)

    def test_dst(self):
        cases=[('Europe/Berlin','2026-03-29','01:30','+01:00','04:00','+02:00','02:30'),
               ('Europe/Berlin','2026-10-25','02:30','+02:00','03:00','+01:00',None),
               ('America/New_York','2026-03-08','01:30','-05:00','04:00','-04:00','02:30'),
               ('America/New_York','2026-11-01','01:30','-04:00','02:00','-05:00',None)]
        for zone,date,clock,start_offset,end_clock,end_offset,gap in cases:
            self.reset(fixture(zone=zone,opens='00:00',closes='06:00'))
            labels=[s['starts_at_local'] for s in self.availability(date)['slots']]
            assert labels.count(date+'T'+clock)==1
            if gap:
                assert date+'T'+gap not in labels
                self.expect(self.create(self.body(starts_at_local=date+'T'+gap)),422,'invalid_local_time')
            a=self.expect(self.create(self.body(starts_at_local=date+'T'+clock)),201)
            start=dt.datetime.fromisoformat(a['starts_at']);end=dt.datetime.fromisoformat(a['ends_at'])
            assert start.isoformat().endswith(start_offset)
            assert end.isoformat().endswith(end_offset)
            assert end.strftime('%H:%M')==end_clock
            assert (end-start).total_seconds()==5400

    def test_moves(self):
        self.reset()
        a=self.expect(self.create(),201); b=self.expect(self.create(self.body(table_id='r-b')),201)
        for moves in [[],[{'reference':a['reference']}]*2,[{}]*9,[True]]:
            self.expect(self.request('POST','/reservation-moves',{'moves':moves},key='bad'),422,'validation_failed')
        swap={'moves':[{'reference':a['reference'],'table_id':'r-b'},{'reference':b['reference'],'table_id':'r-a'}]}
        receipt=self.expect(self.request('POST','/reservation-moves',swap,key='swap'),201)
        assert [r['reference'] for r in receipt['reservations']]==[a['reference'],b['reference']]
        assert [r['table_id'] for r in receipt['reservations']]==['r-b','r-a']
        before=[self.get(a['reference']),self.get(b['reference'])]
        bad={'moves':[{'reference':a['reference'],'table_id':'r-a'},{'reference':b['reference'],'party_size':0}]}
        self.expect(self.request('POST','/reservation-moves',bad,key='reusable'),422,'validation_failed')
        assert [self.get(a['reference']),self.get(b['reference'])]==before
        self.expect(self.request('POST','/reservation-moves',{'moves':[{'reference':a['reference']}]},key='reusable'),201)
        self.expect(self.request('POST','/reservations/'+a['reference']+'/cancel',{}),200)
        assert self.expect(self.request('POST','/reservation-moves',swap,key='swap'),200)==receipt
        # Cutoff precedes invalid target fields for the same booking.
        past=self.expect(self.create(self.body(starts_at_local='2000-01-01T18:00')),201)
        self.expect(self.request('POST','/reservation-moves',{'moves':[{'reference':past['reference'],'party_size':0}]},key='cutoff'),409,'cutoff_passed')
        # First nonoccupancy error follows input order, even when a later item differs.
        for moves,code,status in [
            ([{'reference':b['reference'],'party_size':0},{'reference':'UNKNOWN'}],'validation_failed',422),
            ([{'reference':'UNKNOWN'},{'reference':b['reference'],'party_size':0}],'not_found',404)]:
            self.expect(self.request('POST','/reservation-moves',{'moves':moves},key='ordered'),status,code)
        # Valid upper bound with eight non-overlapping future bookings, no-op batch in reverse order.
        self.reset(); refs=[]
        for day in range(1,9):
            refs.append(self.expect(self.create(self.body(starts_at_local=f'2096-10-{day:02d}T18:00')),201)['reference'])
        result=self.expect(self.request('POST','/reservation-moves',{'moves':[{'reference':r} for r in reversed(refs)]},key='eight'),201)
        assert [r['reference'] for r in result['reservations']]==list(reversed(refs))

    def test_concurrency(self):
        for identical in [True,False]:
            self.reset(); barrier=threading.Barrier(50)
            def worker(i):
                barrier.wait(timeout=10)
                return self.request('POST','/reservations',self.body(),key='race' if identical else f'race-{i}')
            with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
                results=list(pool.map(worker,range(50)))
            assert sum(status==201 for status,_ in results)==1
            winner=next(body for status,body in results if status==201)
            if identical:
                assert sum(status==200 for status,_ in results)==49
                assert all(body==winner for _,body in results)
            else:
                assert sum(status==409 and body['error']['code']=='table_unavailable' for status,body in results)==49
            assert len(self.listing())==1
            self.expect(self.request('POST','/reservations/'+winner['reference']+'/cancel',{}),200)
            if not identical:
                loser=next(i for i,(status,_) in enumerate(results) if status==409)
                self.expect(self.request('POST','/reservations',self.body(),key=f'race-{loser}'),201)

    def test_portability(self):
        if not self.destination:
            raise NotImplementedError('requires --destination-url on a distinct dedicated service/container')
        assert self.destination.rstrip('/')!=self.base.rstrip('/'), 'destination must be distinct'
        self.reset();original=self.expect(self.create(key='export-book'),201)
        move={'moves':[{'reference':original['reference'],'party_size':1}]}
        moved=self.expect(self.request('POST','/reservation-moves',move,key='export-move'),201)
        self.expect(self.create(self.body(party_size=0),key='export-failed'),422,'validation_failed')
        snapshot=self.expect(http(self.base,'GET','/_test/export'),200)
        assert snapshot['track']=='tablekeeper' and snapshot['format_version']==1 and isinstance(snapshot['state'],dict)
        self.expect(self.request('POST','/reservations/'+original['reference']+'/cancel',{}),200)
        dest=copy.deepcopy(fixture());dest['users'][0]['email']='destination-only@example.test'
        self.expect(http(self.destination,'POST','/_test/reset',dest),204)
        displaced=self.expect(http(self.destination,'POST','/auth/login',{'email':'destination-only@example.test','password':PASSWORD}),200)['token']
        self.expect(http(self.destination,'POST','/_test/import',snapshot),204)
        self.expect(http(self.destination,'GET','/reservations',token=displaced),401,'unauthenticated')
        current=self.expect(self.request('GET','/reservations/'+original['reference'],base=self.destination),200)
        assert current==moved['reservations'][0]
        assert self.expect(self.request('POST','/reservations',self.body(),key='export-book',base=self.destination),200)==original
        assert self.expect(self.request('POST','/reservation-moves',move,key='export-move',base=self.destination),200)==moved
        self.expect(http(self.destination,'POST','/auth/login',{'email':'alice@example.test','password':PASSWORD}),200)
        for invalid in [{},dict(snapshot,track='wrong'),dict(snapshot,format_version=2),dict(snapshot,state=None)]:
            self.expect(http(self.destination,'POST','/_test/import',invalid),422,'validation_failed')
            assert self.expect(self.request('GET','/reservations/'+original['reference'],base=self.destination),200)==current
        self.expect(self.request('POST','/reservations',self.body(table_id='r-b'),key='export-failed',base=self.destination),201)
        self.expect(http(self.destination,'POST','/_test/import',snapshot),204)
        assert len(self.expect(self.request('GET','/reservations',base=self.destination),200)['reservations'])==1
        self.expect(http(self.destination,'POST','/_test/reset',fixture()),204)
        self.expect(self.request('GET','/reservations',base=self.destination),401,'unauthenticated')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list',action='store_true')
    parser.add_argument('--base-url');parser.add_argument('--destination-url')
    parser.add_argument('--revision');parser.add_argument('--out')
    args=parser.parse_args()
    if args.list:
        print('\n'.join(METHODS));return 0
    if not args.base_url or not args.out or not re.fullmatch('[0-9a-f]{40}',args.revision or ''):
        parser.error('--base-url, --out and full --revision required')
    output=Path(args.out)
    if output.exists():
        parser.error('--out must be a new evidence path; preserve earlier attempts')
    suite=Suite(args.base_url,args.destination_url)
    report={'work_item':'QA-S1','responsible_handle':'@frankzhu94/factory-qa','candidate_commit':args.revision,
            'environment':'HTTP endpoints supplied by runner; container/isolation provenance must accompany this report',
            'started_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'results':[]}
    for name in METHODS:
        started=time.monotonic()
        result={'test':name}
        try:
            getattr(suite,name)();result['status']='PASS'
        except NotImplementedError as error:
            result.update(status='NOT_TESTED',detail=str(error))
        except Exception as error:
            # Assertions are intentionally credential-free; never serialize response bodies/exports.
            result.update(status='FAIL',detail=type(error).__name__+': '+str(error))
        result['elapsed_seconds']=round(time.monotonic()-started,3)
        report['results'].append(result)
        print(name,result['status'],flush=True)
        report['ended_at_utc']=dt.datetime.now(dt.timezone.utc).isoformat()
        output.write_text(json.dumps(report,indent=2)+'\n')
    return 1 if any(r['status']!='PASS' for r in report['results']) else 0

if __name__=='__main__':
    sys.exit(main())
