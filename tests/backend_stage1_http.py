"""Owner HTTP regressions with literal expected values; no production imports."""
import concurrent.futures
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]

class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.processes = []
        cls.urls = []
        if os.environ.get('BACKEND_TEST_URLS'):
            cls.urls = os.environ['BACKEND_TEST_URLS'].split(',')
            return
        for _ in range(2):
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            p = subprocess.Popen([sys.executable, str(ROOT / 'stage-1/server.py')], env={**os.environ, 'PORT': str(port)}, stdout=subprocess.DEVNULL)
            cls.processes.append(p)
            cls.urls.append('http://127.0.0.1:' + str(port))
        for url in cls.urls:
            for _ in range(100):
                try:
                    urllib.request.urlopen(url + '/health', timeout=1).close(); break
                except OSError:
                    time.sleep(.02)
            else:
                raise RuntimeError('startup failed')

    @classmethod
    def tearDownClass(cls):
        for p in cls.processes:
            p.terminate(); p.wait(timeout=5)

    def request(self, method, path, body=None, token=None, key=None, destination=0, raw=None):
        headers = {'Content-Type': 'application/json'}
        if token: headers['Authorization'] = 'Bearer ' + token
        if key is not None: headers['Idempotency-Key'] = key
        data = raw.encode() if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.urls[destination]+path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            payload = response.read()
            self.assertTrue(response.headers['Content-Type'].startswith('application/json'))
            return response.status, json.loads(payload) if payload else None

    def fixture(self, zone='UTC', opening='18:00', closing='23:00', duration=90):
        return {'users': [{'id':'u', 'email':'a@b', 'password':'password1', 'display_name':'A'}, {'id':'v','email':'v@b','password':'password2','display_name':'V'}],
                'restaurants':[{'id':'r','name':'R','timezone':zone,'slot_minutes':30,'reservation_duration_minutes':duration,'cancellation_cutoff_minutes':120,
                    'opening_hours':[{'weekday':d,'opens':opening,'closes':closing} for d in ['mon','tue','wed','thu','fri','sat','sun']],
                    'tables':[{'id': t, 'label':t,'capacity':4} for t in ['A','B','C']]}], 'reservations':[]}

    def setUp(self):
        self.assertEqual(self.request('POST','/_test/reset',self.fixture())[0],204)
        self.token = self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']

    def booking(self, table='A', time='19:00'):
        return {'restaurant_id':'r','table_id':table,'party_size':4,'starts_at_local':'2030-01-03T'+time}

    def create(self, body=None, key='k'):
        return self.request('POST','/reservations',body or self.booking(),self.token,key)

    def error(self, result, status, code):
        self.assertEqual(result[0], status)
        self.assertEqual(result[1]['error']['code'],code)

    def test_grid_occupancy_and_cutoff(self):
        result = self.request('GET','/availability?restaurant_id=r&date=2030-01-03&party_size=4')
        self.assertEqual(len(result[1]['slots']),8)
        status, original = self.create(); self.assertEqual(status,201)
        self.assertEqual(self.create(self.booking(time='20:30'),'adj')[0],201)
        self.error(self.create(self.booking(time='20:00'),'over'),409,'table_unavailable')
        self.error(self.create(self.booking(time='18:15'),'grid'),422,'not_on_slot_grid')
        self.error(self.create(self.booking(time='22:00'),'close'),422,'outside_opening_hours')
        self.assertEqual(self.request('POST','/reservations/'+original['reference']+'/cancel',{},self.token)[0],200)
        self.assertEqual(self.create()[1],original)
        past = self.booking('B'); past['starts_at_local']='2020-01-02T19:00'
        status, record=self.create(past,'past'); self.assertEqual(status,201)
        self.error(self.request('PATCH','/reservations/'+record['reference'],{'party_size':0},self.token),409,'cutoff_passed')

    def test_exact_numbers_and_import(self):
        base=json.dumps(self.booking())[:-1]
        first=base+',"x":10000000000000000000000000000}'
        status, original=self.request('POST','/reservations',token=self.token,key='exact',raw=first)
        self.assertEqual(status,201)
        self.error(self.request('POST','/reservations',token=self.token,key='exact',raw=base+',"x":10000000000000000000000000001}'),409,'idempotency_key_reuse')
        equivalent=base+',"x":1e28}'
        self.assertEqual(self.request('POST','/reservations',token=self.token,key='exact',raw=equivalent),(200,original))
        exported=self.request('GET','/_test/export')[1]
        self.assertEqual(self.request('POST','/_test/import',exported,destination=1)[0],204)
        self.assertEqual(self.request('POST','/reservations',token=self.token,key='exact',raw=first,destination=1),(200,original))
        self.assertEqual(self.request('POST','/auth/login',{'email':'a@b','password':'password1'},destination=1)[0],200)
        bad=copy.deepcopy(exported);bad['state']='bad'
        self.error(self.request('POST','/_test/import',bad,destination=1),422,'validation_failed')
        bad=copy.deepcopy(exported);bad['state']['reservations'][0]['user_id']='missing'
        self.error(self.request('POST','/_test/import',bad,destination=1),422,'validation_failed')
        self.assertEqual(self.request('GET','/_test/export',destination=1)[1]['state']['reservations'],exported['state']['reservations'])

    def test_numeric_equivalence_types_huge_exponents(self):
        cases=[('1','1.0','true'),('1000','1e3','"1000"'),('-0','0','1'),('1e100000','10e99999','1e100001'),('1.0000000000000000000000000001','1.00000000000000000000000000010','1.0000000000000000000000000002')]
        for x,y,z in cases:
            self.setUp()
            base=json.dumps(self.booking())[:-1]
            a=self.request('POST','/reservations',token=self.token,key='num',raw=base+',"x":'+x+'}')
            self.assertEqual(a[0],201)
            self.assertEqual(self.request('POST','/reservations',token=self.token,key='num',raw=base+',"x":'+y+'}'),(200,a[1]))
            self.error(self.request('POST','/reservations',token=self.token,key='num',raw=base+',"x":'+z+'}'),409,'idempotency_key_reuse')

    def test_moves_final_layout_rollback(self):
        a=self.create(key='a')[1]; b=self.create(self.booking('B'),'b')[1]
        moves={'moves':[{'reference':a['reference'],'table_id':'B'},{'reference':b['reference'],'table_id':'A'}]}
        status,result=self.request('POST','/reservation-moves',moves,self.token,'swap')
        self.assertEqual(status,201);self.assertEqual([r['table_id'] for r in result['reservations']],['B','A'])
        before=self.request('GET','/reservations',token=self.token)[1]
        bad={'moves':[{'reference':a['reference'],'table_id':'C'},{'reference':b['reference'],'party_size':5}]}
        self.error(self.request('POST','/reservation-moves',bad,self.token,'failed'),422,'party_exceeds_capacity')
        self.assertEqual(self.request('GET','/reservations',token=self.token)[1],before)
        bad['moves'][1]['party_size']=4
        self.assertEqual(self.request('POST','/reservation-moves',bad,self.token,'failed')[0],201)
        self.assertEqual(self.request('POST','/reservation-moves',moves,self.token,'swap'),(200,result))

    def test_same_table_time_swap(self):
        a=self.create(self.booking(time='18:00'),'a')[1];b=self.create(self.booking(time='19:30'),'b')[1]
        moves={'moves':[{'reference':a['reference'],'starts_at_local':'2030-01-03T19:30'},{'reference':b['reference'],'starts_at_local':'2030-01-03T18:00'}]}
        self.assertEqual(self.request('POST','/reservation-moves',moves,self.token,'times')[0],201)

    def test_dst_absolute_closing(self):
        for zone,date in [('Europe/Berlin','2026-03-29'),('America/New_York','2026-03-08')]:
            self.request('POST','/_test/reset',self.fixture(zone,'00:30','03:00'))
            token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
            result=self.request('GET',f'/availability?restaurant_id=r&date={date}&party_size=4')[1]
            self.assertEqual([s['starts_at_local'] for s in result['slots']],[date+'T00:30'])
            for text,code in [('01:00','outside_opening_hours'),('02:00','invalid_local_time')]:
                body=self.booking();body['starts_at_local']=date+'T'+text
                self.error(self.request('POST','/reservations',body,token,text),422,code)
        self.request('POST','/_test/reset',self.fixture('America/New_York','00:30','01:45',60))
        result=self.request('GET','/availability?restaurant_id=r&date=2026-11-01&party_size=4')[1]
        self.assertEqual([s['starts_at_local'] for s in result['slots']],['2026-11-01T00:30'])

    def test_folds_and_gap_end(self):
        for zone,date,start,end in [('Europe/Berlin','2026-10-25','02:30','2026-10-25T03:00:00+01:00'),('America/New_York','2026-11-01','01:30','2026-11-01T02:00:00-05:00'),('Europe/Berlin','2026-03-29','01:30','2026-03-29T04:00:00+02:00')]:
            self.request('POST','/_test/reset',self.fixture(zone,'00:30','04:30'))
            token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
            body=self.booking();body['starts_at_local']=date+'T'+start
            result=self.request('POST','/reservations',body,token,'dst')
            self.assertEqual(result[0],201);self.assertEqual(result[1]['ends_at'],end)

    def test_concurrent_receipt_and_contention(self):
        def duplicate(_): return self.create()
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results=list(pool.map(duplicate,range(50)))
        self.assertEqual([s for s,b in results].count(201),1)
        self.assertEqual([s for s,b in results].count(200),49)
        self.assertTrue(all(b==results[0][1] for s,b in results))
        self.setUp()
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results=list(pool.map(lambda i:self.create(key=str(i)),range(50)))
        self.assertEqual([s for s,b in results].count(201),1)
        self.assertEqual([s for s,b in results].count(409),49)

    def test_errors_ownership_and_reuse_precedence(self):
        self.error(self.create({'restaurant_id':False}),400,'malformed_request')
        for value in [0,-1,4.5,'4',True,None]:
            body=self.booking();body['party_size']=value
            self.error(self.create(body),422,'validation_failed')
        body=self.booking();body['party_size']=4.0
        status,a=self.create(body);self.assertEqual(status,201)
        self.error(self.create({'party_size':0}),409,'idempotency_key_reuse')
        v=self.request('POST','/auth/login',{'email':'v@b','password':'password2'})[1]['token']
        self.error(self.request('GET','/reservations/'+a['reference'],token=v),404,'not_found')
        for query in ['4.0','1e9','%2B4','-1']:
            self.error(self.request('GET','/availability?restaurant_id=r&date=2030-01-03&party_size='+query),422,'validation_failed')
        self.error(self.request('POST','/_test/import',raw='{'),400,'malformed_request')

if __name__=='__main__': unittest.main(verbosity=2)
