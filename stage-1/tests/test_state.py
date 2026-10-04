import copy
import json
import unittest
import test_api
FIXTURE=test_api.FIXTURE

class State(unittest.TestCase):
    setUp=test_api.API.setUp
    book=test_api.API.book
    tearDown=test_api.API.tearDown
    def test_corrupt_imports_are_atomic(self):
        self.book()
        original=self.client.get('/_test/export').json
        def field(snapshot,table,column,value):
            e=snapshot['state']['entities'][table];e['rows'][0][e['columns'].index(column)]=value
        def json_field(snapshot,table,key,value):
            e=snapshot['state']['entities'][table];i=e['columns'].index('data');d=json.loads(e['rows'][0][i]);d[key]=value;e['rows'][0][i]=json.dumps(d)
        mutations=[lambda s:s.update(state=[]),lambda s:s['state'].update(schema_version=True),
                   lambda s:field(s,'restaurants','position',True),lambda s:field(s,'restaurants','counter',-1),
                   lambda s:field(s,'sessions','user_id','missing'),lambda s:field(s,'users','data','[]'),
                   lambda s:json_field(s,'restaurants','slot_minutes',0),lambda s:json_field(s,'restaurants','timezone','invalid/zone'),
                   lambda s:json_field(s,'reservations','ends_at','2030-11-01T20:00:00-04:00'),
                   lambda s:json_field(s,'reservations','party_size',True),lambda s:json_field(s,'reservations','history',[]),
                   lambda s:json_field(s,'reservations','table_ids',['missing']),lambda s:field(s,'receipts','response','{}'),
                   lambda s:field(s,'receipts','method','GET'),lambda s:field(s,'allocations','restaurant_id','missing')]
        for mutate in mutations:
            snapshot=copy.deepcopy(original);mutate(snapshot)
            with self.subTest(mutation=mutations.index(mutate)):
                response=self.client.post('/_test/import',json=snapshot)
                self.assertEqual((response.status_code,response.json['error']['code']),(422,'validation_failed'))
                self.assertEqual(self.client.get('/_test/export').json,original)
        for _ in range(2):
            self.assertEqual(self.client.post('/_test/import',json=original).status_code,204)
            self.assertEqual(self.client.get('/_test/export').json,original)
    def test_reset_shape_and_rollback(self):
        original=self.client.get('/_test/export').json
        for value,status in [('bad',400),([False],400),([{'id':'u'}],422)]:
            body=copy.deepcopy(FIXTURE);body['users']=value
            self.assertEqual(self.client.post('/_test/reset',json=body).status_code,status)
            self.assertEqual(self.client.get('/_test/export').json,original)
        for key,value in [('slot_minutes',0),('reservation_duration_minutes',-1),('timezone','nowhere'),('id','x'*65)]:
            body=copy.deepcopy(FIXTURE);body['restaurants'][0][key]=value
            self.assertEqual(self.client.post('/_test/reset',json=body).status_code,422)
            self.assertEqual(self.client.get('/_test/export').json,original)
    def test_history_and_receipts_roundtrip(self):
        a=self.book('a').json;b=self.book('b','b').json
        move={'moves':[{'reference':a['reference'],'table_id':'b'},{'reference':b['reference'],'table_id':'a'}]}
        headers={**self.headers,'Idempotency-Key':'batch'}
        receipt=self.client.post('/reservation-moves',headers=headers,json=move).json
        self.client.post('/reservations/'+a['reference']+'/cancel',headers=self.headers)
        export=self.client.get('/_test/export').json
        self.assertEqual(self.client.post('/_test/import',json=export).status_code,204)
        self.assertEqual(self.client.post('/reservation-moves',headers=headers,json=move).json,receipt)
        rows=export['state']['entities']['reservations']['rows']
        reservation=json.loads(rows[0][-1])
        self.assertEqual(reservation['revision'],3)
        self.assertEqual(reservation['history'][-1]['changed_fields'],['status'])
        self.assertIn('tables',reservation['accepted_terms'])
        before=self.client.get('/_test/export').json
        self.client.post('/reservations/'+a['reference']+'/cancel',headers=self.headers)
        self.assertEqual(self.client.get('/_test/export').json,before)
