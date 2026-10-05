#!/usr/bin/env python3
"""Destructive synthetic HTTP checks: run only against an assigned disposable service.
No product imports or helpers. Results omit tokens, passwords and exported state.
"""
import argparse
import concurrent.futures
import datetime as dt
from decimal import Decimal
import json
import pathlib
import re
import threading
import time
import urllib.error
import urllib.request


def exact_loads(raw):
    def reject(token):
        raise ValueError('non-JSON numeric constant')
    return json.loads(raw, parse_int=Decimal, parse_float=Decimal, parse_constant=reject)


def same(a, b):
    # Floats have already lost information; fail closed rather than bless a rounded oracle.
    if isinstance(a, float) or isinstance(b, float):
        raise TypeError('binary float is not an exact JSON oracle value')
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, Decimal)) and isinstance(b, (int, Decimal)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def numeric_body(token):
    # Deliberately construct wire bytes from a numeric token, never a Python float.
    return ('{"restaurant_id":"r1","table_id":"t1",'
            '"starts_at_local":"2030-06-01T19:00","party_size":4,'
            '"meta":{"n":' + token + '}}').encode()


def fixture(zone='Europe/Berlin', opening='18:00', closing='23:00'):
    return {'users': [{'id': 'u1', 'email': 'qa@example.invalid', 'password': 'synthetic-only', 'display_name': 'QA'}],
            'restaurants': [{'id': 'r1', 'name': 'Test Room', 'timezone': zone, 'slot_minutes': 30,
                'reservation_duration_minutes': 90, 'cancellation_cutoff_minutes': 0,
                'opening_hours': [{'weekday': d, 'opens': opening, 'closes': closing} for d in ['mon','tue','wed','thu','fri','sat','sun']],
                'tables': [{'id': 't1', 'label': 'Window', 'capacity': 4}, {'id': 't2', 'label': 'Garden', 'capacity': 4}]}], 'reservations': []}


class Checks:
    def __init__(self, base):
        self.base, self.token = base.rstrip('/'), None

    def request(self, method, path, body=None, key=None, *, raw_body=None, raw_response=False):
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if key is not None:
            headers['Idempotency-Key'] = key
        if raw_body is not None and body is not None:
            raise ValueError('choose raw bytes or structured body')
        if raw_body is not None and not isinstance(raw_body, bytes):
            raise TypeError('raw_body must be bytes')
        data = raw_body if raw_body is not None else (None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode())
        req = urllib.request.Request(self.base+path, data=data, headers=headers, method=method)
        start = time.monotonic()
        try:
            response = urllib.request.urlopen(req, timeout=10 if path.startswith('/_test/') else 5)
        except urllib.error.HTTPError as e:
            response = e
        with response:
            raw = response.read()
            parsed = exact_loads(raw) if raw else None
            status = response.status
        assert time.monotonic()-start <= (10 if path.startswith('/_test/') else 5), 'request deadline exceeded'
        assert status < 500, 'unexpected server 5xx'
        return status, raw if raw_response else parsed

    def expect(self, method, path, body=None, key=None, status=200, code=None, **wire):
        actual, value = self.request(method, path, body, key, **wire)
        assert actual == status, f'HTTP status expected {status}, observed {actual}'
        if code:
            assert value.get('error', {}).get('code') == code, f'expected error code {code}'
            assert isinstance(value['error'].get('message'), str) and value['error']['message'], 'missing error message'
        return value

    def reset(self, f=None):
        self.token = None
        self.expect('POST', '/_test/reset', f or fixture(), status=204)
        self.token = self.expect('POST', '/auth/login', {'email':'qa@example.invalid','password':'synthetic-only'})['token']

    def booking(self, **changes):
        b = {'restaurant_id':'r1','table_id':'t1','starts_at_local':'2030-06-01T19:00','party_size':4}
        b.update(changes)
        return b

    def query_boundaries(self):
        self.reset()
        for value in ['1', '0004']:
            self.expect('GET', '/availability?restaurant_id=r1&date=2030-06-01&party_size='+value)
        for value in ['0','-1','4.0','1e9','%2B4','']:
            self.expect('GET', '/availability?restaurant_id=r1&date=2030-06-01&party_size='+value, status=422, code='validation_failed')
        for value in [True,'4',0,1.5]:
            self.expect('POST','/reservations',self.booking(party_size=value),'invalid',422,'validation_failed')
        self.expect('POST','/reservations',self.booking(table_id=4),'invalid',400,'malformed_request')
        self.expect('POST','/reservations',self.booking(),'invalid',201)

    def receipt_boundaries(self):
        self.reset()
        b = self.booking(meta={'n':1,'a':[1,2],'b':True,'z':None})
        for key in [None,'']:
            self.expect('POST','/reservations',b,key,400,'missing_idempotency_key')
        self.expect('POST','/reservations',b,'x'*256,422,'validation_failed')
        original = self.expect('POST','/reservations',b,'x',201)
        reordered = dict(reversed(list(b.items())))
        reordered['meta'] = {'z':None,'b':True,'a':[1,2],'n':1.0}
        assert same(original,self.expect('POST','/reservations',reordered,'x'))
        for meta in [{'n':True,'a':[1,2],'b':True,'z':None}, {'n':1,'a':[2,1],'b':True,'z':None}]:
            self.expect('POST','/reservations',self.booking(meta=meta),'x',409,'idempotency_key_reuse')
        self.expect('POST','/reservations',self.booking(party_size=False),'x',409,'idempotency_key_reuse')
        self.expect('POST','/reservations',self.booking(table_id='t2'),'y'*255,201)
        self.expect('POST','/reservations/'+original['reference']+'/cancel',{})
        assert same(original,self.expect('POST','/reservations',b,'x'))
        assert self.expect('GET','/reservations/'+original['reference'])['status']=='cancelled'

    def dst(self):
        vectors = [('Europe/Berlin','2026-03-29','01:30','+01:00','04:00:00+02:00'),
                   ('America/New_York','2026-03-08','01:30','-05:00','04:00:00-04:00'),
                   ('Europe/Berlin','2026-10-25','02:30','+02:00','03:00:00+01:00'),
                   ('America/New_York','2026-11-01','01:30','-04:00','02:00:00-05:00')]
        for zone,date,clock,offset,end in vectors:
            self.reset(fixture(zone,'00:00','05:00'))
            slots=self.expect('GET',f'/availability?restaurant_id=r1&date={date}&party_size=4')['slots']
            matching=[s for s in slots if s['starts_at_local']==date+'T'+clock]
            assert len(matching)==1, 'expected exactly one valid/fold slot'
            assert matching[0]['starts_at']==date+'T'+clock+':00'+offset
            if '-03-' in date:
                assert not any(s['starts_at_local'][11:13]=='02' for s in slots), 'gap slot exposed'
                self.expect('POST','/reservations',self.booking(starts_at_local=date+'T02:30'),'gap',422,'invalid_local_time')
            record=self.expect('POST','/reservations',self.booking(starts_at_local=date+'T'+clock),'dst',201)
            assert record['starts_at']==date+'T'+clock+':00'+offset
            assert record['ends_at']==date+'T'+end
            assert (dt.datetime.fromisoformat(record['ends_at'])-dt.datetime.fromisoformat(record['starts_at'])).total_seconds()==5400

    def gap_opening(self):
        f = fixture('Europe/Berlin', '02:15', '04:45')
        f['restaurants'][0]['reservation_duration_minutes'] = 30
        self.reset(f)
        slots = self.expect('GET', '/availability?restaurant_id=r1&date=2026-03-29&party_size=4')['slots']
        expected = ['03:15', '03:45', '04:15']
        assert [s['starts_at_local'] for s in slots] == ['2026-03-29T'+t for t in expected]
        assert [s['starts_at'] for s in slots] == ['2026-03-29T'+t+':00+02:00' for t in expected]
        for i, t in enumerate(expected):
            result = self.expect('POST', '/reservations', self.booking(starts_at_local='2026-03-29T'+t), 'gap-grid'+str(i), 201)
            assert result['starts_at'] == '2026-03-29T'+t+':00+02:00'
            assert result['ends_at'] == '2026-03-29T'+['03:45','04:15','04:45'][i]+':00+02:00'
        self.expect('POST', '/reservations', self.booking(starts_at_local='2026-03-29T03:00'), 'off-grid', 422, 'not_on_slot_grid')
        self.expect('POST', '/reservations', self.booking(starts_at_local='2026-03-29T02:15'), 'missing-wall', 422, 'invalid_local_time')

    def numeric_receipt(self):
        self.reset()
        original = self.expect('POST', '/reservations', key='decimal', status=201,
                               raw_body=numeric_body('1.0000000000000001'))
        def retry_controls():
            assert same(original, self.expect('POST', '/reservations', key='decimal',
                        raw_body=numeric_body('1.00000000000000010')))
            for token in ['1.0', 'true']:
                self.expect('POST', '/reservations', key='decimal', status=409,
                            code='idempotency_key_reuse', raw_body=numeric_body(token))
        retry_controls()
        # Same-service reset/import continuation. Separate-image upgrades are another layer.
        snapshot = self.expect('GET', '/_test/export', raw_response=True)
        old_token = self.token
        self.expect('POST', '/_test/reset', fixture(), status=204)
        self.expect('POST', '/_test/import', status=204, raw_body=snapshot)
        self.token = old_token
        retry_controls()
        assert len(self.expect('GET', '/reservations')['reservations']) == 1
        self.reset()
        original = self.expect('POST', '/reservations', key='equiv', status=201, raw_body=numeric_body('1'))
        assert same(original, self.expect('POST', '/reservations', key='equiv', raw_body=numeric_body('1.0')))
        self.expect('POST', '/reservations', key='equiv', status=409, code='idempotency_key_reuse', raw_body=numeric_body('true'))

    def concurrent(self):
        for identical in [True,False]:
            self.reset()
            barrier=threading.Barrier(50)
            def submit(i):
                barrier.wait(timeout=5)
                return self.request('POST','/reservations',self.booking(),'same' if identical else 'key'+str(i))
            with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
                results=list(pool.map(submit,range(50)))
            codes=[status for status,_ in results]
            assert codes.count(201)==1, 'not exactly one successful creation'
            assert codes.count(200 if identical else 409)==49, 'unexpected competing outcomes'
            if identical:
                assert all(same(results[0][1],b) for _,b in results), 'replay response differs'
            else:
                assert all(b['error']['code']=='table_unavailable' for s,b in results if s==409)
            assert len(self.expect('GET','/reservations')['reservations'])==1, 'duplicate live booking'

    def atomic_moves(self):
        self.reset()
        a=self.expect('POST','/reservations',self.booking(),'a',201)
        b=self.expect('POST','/reservations',self.booking(table_id='t2'),'b',201)
        moves={'moves':[{'reference':a['reference'],'table_id':'t2'},{'reference':b['reference'],'table_id':'t1'}]}
        swapped=self.expect('POST','/reservation-moves',moves,'swap',201)['reservations']
        assert [r['reference'] for r in swapped]==[a['reference'],b['reference']]
        assert [r['table_id'] for r in swapped]==['t2','t1']
        before=self.expect('GET','/reservations')
        bad={'moves':[{'reference':a['reference'],'table_id':'t1'},{'reference':b['reference']}]}
        self.expect('POST','/reservation-moves',bad,'retry',409,'table_unavailable')
        assert same(before,self.expect('GET','/reservations')), 'failed move changed records'
        bad['moves'][1]['party_size']=0
        self.expect('POST','/reservation-moves',bad,'retry',422,'validation_failed')
        assert same(before,self.expect('GET','/reservations'))
        self.expect('POST','/reservation-moves',{'moves':[{'reference':a['reference']}]},'retry',201)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url',required=True)
    p.add_argument('--candidate',required=True,help='full tested service Git revision')
    p.add_argument('--out',required=True,help='new results file; parent directory must exist')
    args=p.parse_args()
    if not re.fullmatch('[0-9a-f]{40}',args.candidate): p.error('--candidate must be full 40-hex revision')
    out=pathlib.Path(args.out)
    # Reserve output before destructive service operations; never overwrite prior failures.
    with out.open('x') as result_file:
        record={'work_item':'S1-QA','candidate_commit':args.candidate,'responsible_handle':'@frankzhu94/factory-qa','started_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'layer':'HTTP only; container limits not established by driver','results':[]}
        checker=Checks(args.base_url)
        for name,ids in [('query_boundaries',['Q03']),('receipt_boundaries',['Q04','Q06','Q08']),('dst',['Q12','Q13']),('gap_opening',['Q14']),('numeric_receipt',['Q06','Q19']),('concurrent',['Q09']),('atomic_moves',['Q17','Q18'])]:
            start=time.monotonic()
            try:
                getattr(checker,name)()
                status,detail='PASS','Observed selected cases; full matrix remains broader.'
            except Exception as e:
                status,detail='FAIL',type(e).__name__+(': '+str(e) if isinstance(e,AssertionError) else '; transport/parsing failure, details withheld')
            record['results'].append({'check':name,'case_ids':ids,'status':status,'detail':detail,'elapsed_seconds':time.monotonic()-start})
            result_file.seek(0);json.dump(record,result_file,indent=2);result_file.truncate();result_file.flush()
        record['finished_at_utc']=dt.datetime.now(dt.timezone.utc).isoformat()
        result_file.seek(0);json.dump(record,result_file,indent=2);result_file.truncate()
    print(json.dumps({'checks':len(record['results']),'failed':sum(r['status']=='FAIL' for r in record['results']),'results_path':str(out.resolve())}))
    return int(any(r['status']=='FAIL' for r in record['results']))

if __name__=='__main__':
    raise SystemExit(main())
