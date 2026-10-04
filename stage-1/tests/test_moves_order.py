"""Section 11: input-order non-occupancy errors precede collective conflicts."""
import copy
import unittest
from tablekeeper.app import create_app
from test_api import FIXTURE


class MoveOrder(unittest.TestCase):
    def setUp(self):
        self.app=create_app();self.client=self.app.test_client()
        fixture=copy.deepcopy(FIXTURE)
        fixture['users'].append({'id':'other','email':'other@example.com','password':'password1','display_name':'Other'})
        second=copy.deepcopy(fixture['restaurants'][0]);second['id']='second'
        fixture['restaurants'].append(second)
        self.assertEqual(self.client.post('/_test/reset',json=fixture).status_code,204)
        self.headers=self.login('a@example.com')
        self.other_headers=self.login('other@example.com')
        self.a=self.create('a','a');self.b=self.create('b','b')
        self.past=self.create('past','a',start='2020-01-06T18:00')
        self.foreign=self.create('foreign','a',headers=self.other_headers,start='2030-11-01T21:00')
        self.second=self.create('second','a',restaurant='second')
        self.cancelled=self.create('cancelled','b',start='2030-11-01T21:00')
        self.assertEqual(self.client.post('/reservations/'+self.cancelled['reference']+'/cancel',headers=self.headers).status_code,200)

    def tearDown(self): self.app.config['STORE'].db.close()

    def login(self,email):
        response=self.client.post('/auth/login',json={'email':email,'password':'password1'})
        self.assertEqual(response.status_code,200)
        return {'Authorization':'Bearer '+response.json['token']}

    def create(self,key,table,headers=None,start='2030-11-01T18:00',restaurant='r'):
        response=self.client.post('/reservations',headers={**(headers or self.headers),'Idempotency-Key':key},json={'restaurant_id':restaurant,'table_id':table,'party_size':2,'starts_at_local':start})
        self.assertEqual(response.status_code,201)
        return response.json

    def move(self,moves,key='batch'):
        return self.client.post('/reservation-moves',json={'moves':moves},headers={**self.headers,'Idempotency-Key':key})

    def item(self,booking,**changes): return {'reference':booking['reference'],**changes}

    def assert_failure(self,moves,status,code,key='failed'):
        before=self.client.get('/_test/export').json
        response=self.move(moves,key)
        self.assertEqual((response.status_code,response.json.get('error',{}).get('code')),(status,code))
        # Includes reservations, allocation, history, counters and retry receipts.
        self.assertEqual(self.client.get('/_test/export').json,before)

    def test_input_order_for_lookup_cutoff_cancel_and_changes(self):
        missing={'reference':'ABSENT'}
        invalid=self.item(self.a,party_size=0)
        cases=[
            ([invalid,missing],422,'validation_failed'),
            ([missing,invalid],404,'not_found'),
            ([self.item(self.past,party_size=0),missing],409,'cutoff_passed'),
            ([missing,self.item(self.past,party_size=0)],404,'not_found'),
            ([invalid,self.item(self.foreign)],422,'validation_failed'),
            ([self.item(self.foreign),invalid],404,'not_found'),
            ([self.item(self.cancelled,party_size=0),missing],409,'reservation_cancelled'),
            ([missing,self.item(self.cancelled)],404,'not_found'),
            ([self.item(self.a,table_id='b'),self.item(self.b,party_size=0)],422,'validation_failed'),
        ]
        for moves,status,code in cases:
            with self.subTest(moves=moves): self.assert_failure(moves,status,code)
        # Every preceding rejection left this key available for a different body.
        self.assertEqual(self.move([self.item(self.a)],'failed').status_code,201)

    def test_restaurant_checks_follow_input_order(self):
        self.assert_failure([self.item(self.a,party_size=5),self.item(self.second)],422,'party_exceeds_capacity')
        self.assert_failure([self.item(self.past),self.item(self.second)],409,'cutoff_passed')
        self.assert_failure([self.item(self.a),self.item(self.second),{'reference':'ABSENT'}],422,'validation_failed')
        self.assert_failure([self.item(self.a),{'reference':'ABSENT'},self.item(self.second)],404,'not_found')
        self.assert_failure([self.item(self.cancelled),self.item(self.second)],409,'reservation_cancelled')

    def test_global_shape_before_resource_checks(self):
        self.assert_failure([{'reference':'ABSENT'},False],422,'validation_failed')
        self.assert_failure([self.item(self.past),self.item(self.past)],422,'validation_failed')

    def test_swap_rollback_noop_and_original_receipt(self):
        self.assert_failure([self.item(self.a,table_id='b'),self.item(self.b,party_size=5)],422,'party_exceeds_capacity')
        swap=[self.item(self.a,table_id='b'),self.item(self.b,table_id='a')]
        response=self.move(swap,'failed');self.assertEqual(response.status_code,201)
        original=response.json
        before=self.client.get('/reservations',headers=self.headers).json
        no_op=self.move([self.item(self.a),self.item(self.b)],'noop')
        self.assertEqual(no_op.status_code,201);self.assertEqual(no_op.json,original)
        self.assertEqual(self.client.get('/reservations',headers=self.headers).json,before)
        self.assert_failure([self.item(self.a,table_id='a'),self.item(self.b)],409,'table_unavailable','blocked')
        self.assertEqual(self.move([self.item(self.a)],'blocked').status_code,201)
        self.assertEqual(self.client.post('/reservations/'+self.a['reference']+'/cancel',headers=self.headers).status_code,200)
        replay=self.move(swap,'failed');self.assertEqual(replay.status_code,200);self.assertEqual(replay.json,original)
