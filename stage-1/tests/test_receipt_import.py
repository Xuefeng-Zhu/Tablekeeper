import copy,unittest
from tablekeeper import codec
from tablekeeper.core import parse,Error
from tablekeeper.service import Service
from tablekeeper.state_io import validate_import
from test_storage import fixture

class ReceiptImport(unittest.TestCase):
    def setUp(self):
        self.s=Service();self.s.reset(fixture());self.token=self.s.login(dict(email='u@x',password='synthetic-password'),False)['token']
        self.body=dict(restaurant_id='r',table_id='a',party_size=2,starts_at_local='2096-01-01T12:00')
        self.first=self.call('POST','/reservations',self.body,'create')[1]
        self.second=self.call('POST','/reservations',dict(self.body,table_id='b'),'create2')[1]
        self.moves=dict(moves=[dict(reference=self.first['reference'],party_size=1),dict(reference=self.second['reference'])])
        self.batch=self.call('POST','/reservation-moves',self.moves,'batch')[1]
        self.call('PATCH','/reservations/'+self.first['reference'],dict(party_size=3))
        self.call('POST','/reservations/'+self.first['reference']+'/cancel',{})
        self.snapshot=dict(track='tablekeeper',format_version=1,state=self.s.store.export())
    def call(self,method,path,body,key=None):
        raw=codec.dumps(body);return self.s.route(method,path,{},parse(raw),raw,'Bearer '+self.token,key)
    def test_historical_changed_cancelled_responses_accepted(self):
        dest=Service();dest.store.replace(validate_import(parse(codec.dumps(self.snapshot)),dest))
        self.assertEqual(dest.store.export(),self.snapshot['state'])
        for path,body,key,expected in [('/reservations',self.body,'create',self.first),('/reservation-moves',self.moves,'batch',self.batch)]:
            raw=codec.dumps(body)
            self.assertEqual(dest.route('POST',path,{},parse(raw),raw,'Bearer '+self.token,key),(200,expected))
    def test_invalid_create_and_batch_receipts_rejected(self):
        cases=[lambda b: {'reference':b['reference']},lambda b:dict(b,reference='ORPHAN01'),lambda b:dict(b,reservation_id='missing'),lambda b:dict(b,restaurant_id='missing'),lambda b:dict(b,party_size=True),lambda b:dict(b,table_id=[]),lambda b:dict(b,starts_at='yesterday'),lambda b:dict(b,created_at='bad'),lambda b:dict(b,status=None)]
        for index in [0,2]:
            for corrupt in cases:
                with self.subTest(receipt=index,corruption=cases.index(corrupt)):
                    bad=copy.deepcopy(self.snapshot);receipt=bad['state']['receipts'][index];response=codec.loads(receipt['response'])
                    if index==0: response=corrupt(response)
                    else: response['reservations'][0]=corrupt(response['reservations'][0])
                    receipt['response']=codec.dumps(response)
                    with self.assertRaises(Error) as raised: validate_import(parse(codec.dumps(bad)),self.s)
                    self.assertEqual((raised.exception.status,raised.exception.code),(422,'validation_failed'))
        bad=copy.deepcopy(self.snapshot);r=bad['state']['receipts'][2];response=codec.loads(r['response']);response['reservations'].reverse();r['response']=codec.dumps(response)
        with self.assertRaises(Error):validate_import(parse(codec.dumps(bad)),self.s)
