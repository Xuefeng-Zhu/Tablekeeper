import unittest
import copy
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timedelta
from decimal import Decimal
import codec
from domain import Service
from rules import UTC, Error

FIXTURE={'users':[{'id':'u','email':'u@example.test','password':'password','display_name':'U'}], 'restaurants':[{'id':'r','name':'R','timezone':'Europe/Berlin','slot_minutes':30,'reservation_duration_minutes':90,'cancellation_cutoff_minutes':120,'opening_hours':[{'weekday':d,'opens':'00:00','closes':'23:30'} for d in ['mon','tue','wed','thu','fri','sat','sun']], 'tables':[{'id':'a','label':'A','capacity':4},{'id':'b','label':'B','capacity':4}]}],'reservations':[]}

class Tests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2030,1,1,tzinfo=UTC)
        self.s=Service(lambda:self.now)
        self.call('POST','/_test/reset',FIXTURE)
        _,login=self.call('POST','/auth/login',{'email':'u@example.test','password':'password'})
        self.headers={'Authorization':'Bearer '+login['token'],'Idempotency-Key':'key'}

    def call(self,method,path,body=None):
        status,text=self.s.execute(method,path,{},body or {},getattr(self,'headers',{}))
        return status,codec.loads(text) if text else None

    def create(self,**fields):
        return self.call('POST','/reservations',dict(restaurant_id='r',table_id='a',starts_at_local='2030-01-02T12:00',party_size=2,**fields))[1]

    def test_cutoff_exact_and_atomic(self):
        r=self.create(); self.now=datetime.fromisoformat(r['starts_at']).astimezone(UTC)-timedelta(minutes=120)
        before=self.call('GET','/_test/export')[1]
        with self.assertRaises(Error) as caught:self.call('PATCH','/reservations/'+r['reference'],{'party_size':3})
        self.assertEqual(caught.exception.code,'cutoff_passed')
        self.assertTrue(codec.equal(before,self.call('GET','/_test/export')[1]))
        self.now-=timedelta(microseconds=1)
        self.assertEqual(self.call('PATCH','/reservations/'+r['reference'],{'party_size':3})[0],200)

    def test_ledger_receipt_and_import(self):
        r=self.create(); self.now+=timedelta(seconds=1)
        self.call('PATCH','/reservations/'+r['reference'],{'table_id':'b'})
        self.now+=timedelta(seconds=1); self.call('POST','/reservations/'+r['reference']+'/cancel')
        facts=self.s.ledger(r['reference'])
        self.assertEqual([f['type'] for f in facts],['created','changed','cancelled'])
        self.assertEqual(facts[0]['changes']['table_id']['after'],'a')
        self.assertEqual(facts[1]['changes']['table_id'],{'before':'a','after':'b'})
        self.assertEqual(len(set(f['at'] for f in facts)),3)
        self.assertTrue(codec.equal(self.create(),r))
        export=self.call('GET','/_test/export')[1]
        self.call('POST','/_test/reset',FIXTURE); self.call('POST','/_test/import',export)
        self.assertEqual(self.s.ledger(r['reference']),facts)
        self.assertTrue(codec.equal(self.create(),r))

    def test_table_ids_are_restaurant_scoped(self):
        fixture=copy.deepcopy(FIXTURE)
        second=copy.deepcopy(fixture['restaurants'][0]); second['id']='other'
        fixture['restaurants'].append(second)
        self.assertEqual(self.call('POST','/_test/reset',fixture)[0],204)
        export=self.call('GET','/_test/export')[1]
        self.assertEqual(self.call('POST','/_test/import',export)[0],204)
        invalid=copy.deepcopy(fixture)
        invalid['restaurants'][0]['tables'].append(invalid['restaurants'][0]['tables'][0])
        with self.assertRaises(Error):self.call('POST','/_test/reset',invalid)

    def test_import_then_real_amend(self):
        r=self.create()
        export=self.call('GET','/_test/export')[1]
        self.call('POST','/_test/import',export)
        status,amended=self.call('PATCH','/reservations/'+r['reference'],{'party_size':3})
        self.assertEqual(status,200)
        self.assertEqual(amended['party_size'],3)

    def test_import_invalid_relationship_atomic(self):
        self.create(); original=self.call('GET','/_test/export')[1]
        malformed=codec.loads(codec.dumps(original)); malformed['state']['reservations'][0]['user_id']='missing'
        with self.assertRaises(Error):self.call('POST','/_test/import',malformed)
        self.assertTrue(codec.equal(original,self.call('GET','/_test/export')[1]))

    def test_lossless_number_range(self):
        for token in ('1.0000000000000001','1e-999','123456789012345678901234567890123456789','1e999'):
            value=codec.loads('{"nested":['+token+']}')
            self.assertTrue(codec.equal(value,codec.loads(codec.dumps(value))))
        self.assertFalse(codec.equal(codec.loads('true'),codec.loads('1')))
        self.assertFalse(codec.equal(codec.loads('1.0000000000000001'),codec.loads('1.0')))
        self.assertTrue(codec.equal(codec.loads('1.00'),codec.loads('1e0')))

if __name__=='__main__':unittest.main()
