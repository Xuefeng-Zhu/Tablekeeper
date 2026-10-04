import concurrent.futures
import copy
import json
import unittest
from datetime import datetime, timezone
from domain import Service, Error, parse, equal, resolve, local_value
from zoneinfo import ZoneInfo
from portability import fixture, imported

class DomainTests(unittest.TestCase):
    def setUp(self):
        self.s=Service(lambda:datetime(2026,1,1,tzinfo=timezone.utc))
        self.s.state=fixture(self.s,{'users':[{'id':'u','email':'a@b','password':'password','display_name':'A'}], 'restaurants':[{'id':'r','name':'R','timezone':'UTC','slot_minutes':30,'reservation_duration_minutes':90,'cancellation_cutoff_minutes':120,'opening_hours':[{'weekday':'thu','opens':'18:00','closes':'23:00'}], 'tables':[{'id':'a','label':'A','capacity':4},{'id':'b','label':'B','capacity':4}]}],'reservations':[]})
        _,login=self.s.auth('/auth/login',{'email':'a@b','password':'password'})
        self.headers={'Authorization':'Bearer '+login['token']}
        self.body={'restaurant_id':'r','table_id':'a','party_size':2,'starts_at_local':'2026-09-24T18:00'}
    def call(self,path='/reservations',body=None,key='k',method='POST'):
        body=self.body if body is None else body
        raw=json.dumps(body)
        return self.s.transact(method,path,parse(raw),raw,dict(self.headers,**{'Idempotency-Key':key}),{})
    def test_concurrent_receipt(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool: result=list(pool.map(lambda _:self.call(),range(50)))
        self.assertEqual(sum(x[0]==201 for x in result),1)
        self.assertTrue(all(x[1]==result[0][1] for x in result))
        self.assertEqual(len(self.s.state['reservations']),1)
    def test_semantic_equality(self):
        self.assertTrue(equal(parse('{"a":1}'),parse('{"a":1.0}')))
        self.assertFalse(equal(parse('{"a":true}'),parse('{"a":1}')))
        self.assertFalse(equal(parse('{"a":1.0000000000000000001}'),parse('{"a":1}')))
        self.call()
        with self.assertRaises(Error) as ctx:self.call(body={'party_size':False})
        self.assertEqual(ctx.exception.code,'idempotency_key_reuse')
    def test_swap_and_portability(self):
        _,a=self.call()
        _,b=self.call(body=dict(self.body,table_id='b'),key='b')
        body={'moves':[{'reference':a['reference'],'table_id':'b'},{'reference':b['reference'],'table_id':'a'}]}
        _,m=self.call('/reservation-moves',body,'swap')
        envelope={'track':'tablekeeper','format_version':1,'state':copy.deepcopy(self.s.state)}
        other=Service()
        other.state=imported(other,envelope)
        self.assertEqual(other.state,self.s.state)
        self.call('/reservations/'+a['reference']+'/cancel',{})
        self.assertEqual(self.call('/reservation-moves',body,'swap'),(200,m))
        self.assertEqual(self.call(),(200,a))
    def test_failed_move_atomic(self):
        _,a=self.call()
        before=copy.deepcopy(self.s.state)
        with self.assertRaises(Error):self.call('/reservation-moves',{'moves':[{'reference':a['reference'],'party_size':0}]},'bad')
        self.assertEqual(before,self.s.state)
        self.assertEqual(self.call('/reservation-moves',{'moves':[{'reference':a['reference']}]},'bad')[0],201)
    def test_dst(self):
        for zone,label in [('Europe/Berlin','2026-03-29T02:30'),('America/New_York','2026-03-08T02:30')]:
            with self.assertRaises(Error) as ctx:resolve(local_value(label),ZoneInfo(zone))
            self.assertEqual(ctx.exception.code,'invalid_local_time')
        self.assertEqual(resolve(local_value('2026-11-01T01:30'),ZoneInfo('America/New_York')).hour,5)
        self.assertEqual(resolve(local_value('2026-10-25T02:30'),ZoneInfo('Europe/Berlin')).hour,0)
    def test_auth_and_invalid_import(self):
        with self.assertRaises(Error):self.s.auth('/auth/login',{'email':'a@b','password':'x'})
        before=copy.deepcopy(self.s.state)
        with self.assertRaises(Error):imported(self.s,{'track':'other','format_version':1,'state':before})
        self.assertEqual(before,self.s.state)
        self.assertNotIn('password',self.s.state['users']['u'])

if __name__=='__main__':unittest.main()
