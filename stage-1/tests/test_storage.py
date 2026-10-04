import copy,json,unittest
from unittest.mock import patch
from tablekeeper.service import Service
from tablekeeper.core import parse,Error
from tablekeeper.store import Store


def fixture():
    return dict(users=[dict(id='u',email='u@x',display_name='User',password='synthetic-password')],restaurants=[dict(id='r',name='Room',timezone='UTC',slot_minutes=30,reservation_duration_minutes=60,cancellation_cutoff_minutes=0,opening_hours=[dict(weekday=d,opens='00:00',closes='23:59') for d in ['mon','tue','wed','thu','fri','sat','sun']],tables=[dict(id='a',label='A',capacity=4),dict(id='b',label='B',capacity=4)])],reservations=[])

class Storage(unittest.TestCase):
    def setUp(self):
        self.s=Service(); self.s.reset(fixture()); self.token=self.s.login(dict(email='u@x',password='synthetic-password'),False)['token']
        self.body=dict(restaurant_id='r',table_id='a',party_size=2,starts_at_local='2096-01-01T12:00')
        self.booking=self.call('POST','/reservations',self.body,'original')[1]
    def call(self,method,path,body=None,key=None):
        raw=json.dumps(body or {})
        return self.s.route(method,path,{},parse(raw),raw,'Bearer '+self.token,key)
    def test_replay_and_reads_ignore_receipts_and_histories(self):
        sentinel='unrelated-large-payload-'+'x'*65536
        for i in range(8): self.call('POST','/reservation-moves',dict(moves=[dict(reference=self.booking['reference'])],unknown=sentinel),str(i))
        for i in range(8): self.call('PATCH','/reservations/'+self.booking['reference'],dict(party_size=1+i%2))
        statements=[]; self.s.store.db.set_trace_callback(statements.append)
        original_loads=json.loads
        def guarded(raw,*args,**kwargs):
            self.assertNotIn('unrelated-large-payload-',raw)
            self.assertNotIn('"_history"',raw)
            return original_loads(raw,*args,**kwargs)
        with patch('json.loads',side_effect=guarded):
            self.assertEqual(self.call('POST','/reservations',self.body,'original'),(200,self.booking))
            for path in ['/health','/restaurants','/restaurants/r']:
                self.assertEqual(self.call('GET',path)[0],200)
        self.assertFalse(any(x.startswith(('UPDATE','INSERT','DELETE')) for x in statements))
        self.assertFalse(any('FROM histories' in x for x in statements))
        receipt_selects=[x for x in statements if 'FROM receipts' in x]
        self.assertEqual(len(receipt_selects),1)
        self.assertIn("key='original'",receipt_selects[0])
    def test_noop_and_repeated_cancel_have_no_writes(self):
        statements=[]; self.s.store.db.set_trace_callback(statements.append)
        self.call('PATCH','/reservations/'+self.booking['reference'],{})
        self.assertFalse(any(x.startswith(('UPDATE','INSERT','DELETE')) for x in statements))
        self.call('POST','/reservations/'+self.booking['reference']+'/cancel')
        statements.clear(); self.call('POST','/reservations/'+self.booking['reference']+'/cancel')
        self.assertFalse(any(x.startswith(('UPDATE','INSERT','DELETE')) for x in statements))
    def test_failed_batch_and_receipt_insert_roll_back(self):
        before=self.s.store.export()
        with self.assertRaises(Error):
            self.call('POST','/reservation-moves',dict(moves=[dict(reference=self.booking['reference'],party_size=1),dict(reference='UNKNOWN')]),'failed')
        self.assertEqual(self.s.store.export(),before)
        with patch.object(Store,'insert_receipt',side_effect=RuntimeError('injected insert failure')):
            with self.assertRaises(RuntimeError): self.call('POST','/reservations',dict(self.body,table_id='b'),'rollback')
        self.assertEqual(self.s.store.export(),before)
        self.assertEqual(self.call('POST','/reservations',dict(self.body,table_id='b'),'rollback')[0],201)
    def test_legacy_logical_snapshot_and_atomic_replace(self):
        snapshot=dict(track='tablekeeper',format_version=1,state=self.s.store.export())
        from tablekeeper.state_io import validate_import
        destination=Service(); destination.store.replace(validate_import(parse(json.dumps(snapshot)),destination))
        self.assertEqual(destination.store.export(),snapshot['state'])
        raw=json.dumps(self.body)
        self.assertEqual(destination.route('POST','/reservations',{},parse(raw),raw,'Bearer '+self.token,'original'),(200,self.booking))
        self.call('PATCH','/reservations/'+self.booking['reference'],dict(party_size=1))
        self.assertEqual(destination.store.export(),snapshot['state'])
        before=destination.store.export()
        with patch.object(Store,'insert_receipt',side_effect=RuntimeError('injected replacement failure')):
            with self.assertRaises(RuntimeError): destination.store.replace(snapshot['state'])
        self.assertEqual(destination.store.export(),before)
        destination.reset(fixture()); self.assertEqual(destination.store.export()['receipts'],[])
        self.assertEqual(destination.store.export()['sessions'],[])
