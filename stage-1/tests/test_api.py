import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from tablekeeper.app import create_app

FIXTURE={'users':[{'id':'u','email':'a@example.com','password':'password1','display_name':'Ada'}], 'restaurants':[{'id':'r','name':'Restaurant','timezone':'America/New_York','slot_minutes':30,'reservation_duration_minutes':90,'cancellation_cutoff_minutes':0,'opening_hours':[{'weekday':d,'opens':'00:00','closes':'23:30'} for d in ['mon','tue','wed','thu','fri','sat','sun']], 'tables':[{'id':'a','label':'A','capacity':4},{'id':'b','label':'B','capacity':4}]}], 'reservations':[]}

class API(unittest.TestCase):
    def setUp(self):
        self.app=create_app();self.client=self.app.test_client()
        self.assertEqual(self.client.post('/_test/reset',json=FIXTURE).status_code,204)
        self.token=self.client.post('/auth/login',json={'email':'a@example.com','password':'password1'}).json['token']
        self.headers={'Authorization':'Bearer '+self.token}
    def book(self,key='key',table='a',time='2030-11-01T18:00'):
        return self.client.post('/reservations',headers={**self.headers,'Idempotency-Key':key},json={'restaurant_id':'r','table_id':table,'party_size':2,'starts_at_local':time})
    def test_concurrent_identical_receipts(self):
        body={'restaurant_id':'r','table_id':'a','party_size':2,'starts_at_local':'2030-11-01T18:00'}
        def send(_):
            with self.app.test_client() as client:
                r=client.post('/reservations',headers={**self.headers,'Idempotency-Key':'many'},json=body)
                return r.status_code,r.json
        with ThreadPoolExecutor(max_workers=50) as pool: results=list(pool.map(send,range(50)))
        self.assertEqual([s for s,_ in results].count(201),1)
        self.assertEqual([s for s,_ in results].count(200),49)
        self.assertTrue(all(v==results[0][1] for _,v in results))
        self.assertEqual(self.book('other').status_code,409)
    def test_swap_and_rollback(self):
        a=self.book('a').json;b=self.book('b','b').json
        response=self.client.post('/reservation-moves',headers={**self.headers,'Idempotency-Key':'swap'},json={'moves':[{'reference':a['reference'],'table_id':'b'},{'reference':b['reference'],'table_id':'a'}]})
        self.assertEqual(response.status_code,201)
        before=self.client.get('/_test/export').json
        failed=self.client.post('/reservation-moves',headers={**self.headers,'Idempotency-Key':'bad'},json={'moves':[{'reference':a['reference'],'starts_at_local':'2030-11-01T20:00'},{'reference':b['reference'],'party_size':10}]})
        self.assertEqual(failed.status_code,422)
        self.assertEqual(before,self.client.get('/_test/export').json)
    def test_portability_and_original_receipt(self):
        original=self.book().json
        snapshot=self.client.get('/_test/export').json
        destination=create_app().test_client()
        self.assertEqual(destination.post('/_test/import',json=snapshot).status_code,204)
        self.assertEqual(destination.get('/reservations',headers=self.headers).json['reservations'],[original])
        self.assertEqual(destination.post('/auth/login',json={'email':'a@example.com','password':'password1'}).status_code,200)
        self.client.post('/reservations/'+original['reference']+'/cancel',headers=self.headers)
        replay=self.book();self.assertEqual(replay.status_code,200);self.assertEqual(replay.json,original)
        baseline=destination.get('/_test/export').json
        bad=copy.deepcopy(snapshot);bad['state']['entities']['sessions']['rows'][0][0]='bad'
        self.assertEqual(destination.post('/_test/import',json=bad).status_code,422)
        self.assertEqual(destination.get('/_test/export').json,baseline)
    def test_dst_and_boundary(self):
        for zone,gap,fold,offset in [('America/New_York','2026-03-08T02:00','2026-11-01T01:30','-04:00'),('Europe/Berlin','2026-03-29T02:00','2026-10-25T02:30','+02:00')]:
            fixture=copy.deepcopy(FIXTURE);fixture['restaurants'][0]['timezone']=zone
            self.client.post('/_test/reset',json=fixture)
            self.headers={'Authorization':'Bearer '+self.client.post('/auth/login',json={'email':'a@example.com','password':'password1'}).json['token']}
            self.assertEqual(self.book('gap',time=gap).json['error']['code'],'invalid_local_time')
            result=self.book('fold',time=fold)
            self.assertEqual(result.status_code,201);self.assertTrue(result.json['starts_at'].endswith(offset))
            if zone=='America/New_York': self.assertEqual(result.json['ends_at'],'2026-11-01T02:00:00-05:00')
        self.assertEqual(self.book('first',time='2030-11-01T18:00').status_code,201)
        self.assertEqual(self.book('boundary',time='2030-11-01T19:30').status_code,201)
    def test_receipt_precedence(self):
        self.book()
        response=self.client.post('/reservations',headers={**self.headers,'Idempotency-Key':'key'},json={'party_size':False})
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json['error']['code'],'idempotency_key_reuse')

if __name__=='__main__': unittest.main()
