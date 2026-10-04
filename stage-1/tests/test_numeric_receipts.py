"""Exact JSON numeric values participate in both portable receipt identities."""
import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import localcontext
import test_api
from tablekeeper.app import create_app
from tablekeeper.validation import canonical, parse

LARGE='10000000000000000000000000001'

class NumericReceipts(unittest.TestCase):
    setUp=test_api.API.setUp
    tearDown=test_api.API.tearDown
    book=test_api.API.book

    def body(self,path):
        if path=='/reservations':
            return {'restaurant_id':'r','table_id':'a','party_size':2,'starts_at_local':'2030-11-01T18:00'}
        booking=self.book('seed').json
        return {'moves':[{'reference':booking['reference']}]}

    def raw(self,body,number):
        value={**body,'ignored':{'nested':[{'number':'__NUMBER__'}],'other':True}}
        return json.dumps(value).replace('"__NUMBER__"',number)

    def send(self,client,path,body,number,key='numeric'):
        return client.post(path,data=self.raw(body,number),content_type='application/json',headers={**self.headers,'Idempotency-Key':key})

    def test_large_numeric_receipts_survive_mutation_and_import(self):
        for path in ('/reservations','/reservation-moves'):
            with self.subTest(path=path):
                if path=='/reservation-moves':
                    self.tearDown();self.setUp()
                body=self.body(path)
                original=self.send(self.client,path,body,LARGE)
                self.assertEqual(original.status_code,201)
                reference=(original.json if path=='/reservations' else original.json['reservations'][0])['reference']
                self.assertEqual(self.client.post('/reservations/'+reference+'/cancel',headers=self.headers).status_code,200)
                for number in (LARGE,LARGE+'.000',LARGE+'e0'):
                    replay=self.send(self.client,path,body,number)
                    self.assertEqual((replay.status_code,replay.json),(200,original.json))
                exported=self.client.get('/_test/export').json
                stored=exported['state']['entities']['receipts']
                numeric_rows=[r for r in stored['rows'] if r[stored['columns'].index('key')]=='numeric']
                self.assertIn(LARGE,numeric_rows[0][stored['columns'].index('body')])
                destination=create_app();self.addCleanup(destination.config['STORE'].db.close)
                client=destination.test_client()
                self.assertEqual(client.post('/_test/import',json=exported).status_code,204)
                for target in (self.client,client):
                    before=target.get('/_test/export').json
                    for number in ('10000000000000000000000000000',LARGE+'1','true'):
                        rejected=self.send(target,path,body,number)
                        self.assertEqual((rejected.status_code,rejected.json['error']['code']),(409,'idempotency_key_reuse'))
                    self.assertEqual(target.get('/_test/export').json,before)
                    replay=self.send(target,path,body,LARGE+'.0')
                    self.assertEqual((replay.status_code,replay.json),(200,original.json))

    def test_decimal_exponent_zero_and_boolean_identity(self):
        for path in ('/reservations','/reservation-moves'):
            for first,equivalents,different in [
                ('1',['1.0','1e0'],'true'),
                ('-0',['0','0.000','0e99'],'false'),
                ('0.1234567890123456789012345678',['0.123456789012345678901234567800','1234567890123456789012345678e-28'],'0.1234567890123456789012345679'),
                ('1e999',['10e998'],'2e999'),
            ]:
                with self.subTest(path=path,first=first):
                    self.tearDown();self.setUp();body=self.body(path)
                    original=self.send(self.client,path,body,first)
                    self.assertEqual(original.status_code,201)
                    export=self.client.get('/_test/export').json
                    self.assertEqual(self.client.post('/_test/import',json=export).status_code,204)
                    for value in equivalents:
                        result=self.send(self.client,path,body,value)
                        self.assertEqual((result.status_code,result.json),(200,original.json))
                    result=self.send(self.client,path,body,different)
                    self.assertEqual((result.status_code,result.json['error']['code']),(409,'idempotency_key_reuse'))

    def test_concurrent_different_numeric_bodies(self):
        for path in ('/reservations','/reservation-moves'):
            with self.subTest(path=path):
                self.tearDown();self.setUp();body=self.body(path)
                def submit(index):
                    with self.app.test_client() as client:
                        response=self.send(client,path,body,str(10**40+index),'race')
                        return index,response.status_code,response.json
                with ThreadPoolExecutor(max_workers=50) as pool: results=list(pool.map(submit,range(50)))
                winners=[r for r in results if r[1]==201]
                self.assertEqual(len(winners),1)
                self.assertEqual(sum(r[1]==409 for r in results),49)
                self.assertTrue(all(r[2]['error']['code']=='idempotency_key_reuse' for r in results if r[1]==409))
                winner=winners[0]
                replay=self.send(self.client,path,body,str(10**40+winner[0]),'race')
                self.assertEqual((replay.status_code,replay.json),(200,winner[2]))

    def test_failed_key_reuse_and_nonfinite_rejection(self):
        for path in ('/reservations','/reservation-moves'):
            with self.subTest(path=path):
                self.tearDown();self.setUp();body=self.body(path)
                invalid=copy.deepcopy(body)
                if path=='/reservations': invalid['party_size']=0
                else: invalid['moves'][0]['party_size']=0
                self.assertEqual(self.send(self.client,path,invalid,LARGE).status_code,422)
                self.assertEqual(self.send(self.client,path,body,LARGE+'1').status_code,201)
                before=self.client.get('/_test/export').json
                for value in ('NaN','Infinity','-Infinity'):
                    response=self.send(self.client,path,body,value,'not-json')
                    self.assertEqual((response.status_code,response.json['error']['code']),(400,'malformed_request'))
                self.assertEqual(self.client.get('/_test/export').json,before)

    def test_canonicalization_is_independent_of_decimal_context(self):
        with localcontext() as context:
            context.prec=1
            self.assertNotEqual(canonical(parse('{"n":'+LARGE+'}')),canonical(parse('{"n":'+str(int(LARGE)+1)+'}')))
            self.assertEqual(canonical(parse('{"n":'+LARGE+'}')),canonical(parse('{"n":'+LARGE+'.00}')))
