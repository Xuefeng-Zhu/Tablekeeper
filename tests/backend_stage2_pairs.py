"""Pair HTTP, atomic final layouts and actual accepted Stage1 migration oracles."""
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
from backend_stage2_inherited import HTTPTests

class PairTests(HTTPTests):
    # Run inherited tests separately, rather than duplicating them in this class.
    def fixture(self, *args, **kwargs):
        f = super().fixture(*args, **kwargs)
        r = f['restaurants'][0]
        r['tables'] = [{'id':t,'label':t,'capacity':c} for t,c in [('A',2),('B',4),('C',3)]]
        r['combinable'] = [['B','A'],['B','C']]
        return f

    def booking(self, table='A', time='19:00'):
        return {'restaurant_id':'r','table_ids':['A','B'] if table=='pair' else [table], 'party_size':1,'starts_at_local':'2030-01-03T'+time}

    def test_options_and_second_member(self):
        expected={1:[['A'],['B'],['C'],['B','A'],['B','C']],4:[['B'],['B','A'],['B','C']],5:[['B','A'],['B','C']],6:[['B','A'],['B','C']],7:[['B','C']],8:[]}
        for party, ids in expected.items():
            status,b=self.request('GET',f'/availability?restaurant_id=r&date=2030-01-03&party_size={party}')
            self.assertEqual(status,200)
            self.assertEqual([o['table_ids'] for o in b['slots'][0]['available_options']],ids)
            self.assertEqual(b['slots'][0]['available_table_ids'],[i[0] for i in ids if len(i)==1])
        a=self.create()[1]
        self.error(self.create(self.booking('pair'),'blocked'),409,'table_unavailable')
        row=self.request('GET','/availability?restaurant_id=r&date=2030-01-03&party_size=1')[1]['slots'][2]
        self.assertNotIn(['B','A'],[o['table_ids'] for o in row['available_options']])
        self.request('POST','/reservations/'+a['reference']+'/cancel',{},self.token)
        status,pair=self.create(self.booking('pair'),'blocked');self.assertEqual(status,201)
        self.assertEqual(pair['table_ids'],['B','A']);self.assertNotIn('table_id',pair)
        for t in ['A','B']:
            self.error(self.create(self.booking(t),'x'+t),409,'table_unavailable')
        self.assertEqual(self.create(self.booking('pair','20:30'),'adj')[0],201)

    def test_selection_errors_and_replacement(self):
        base=self.booking('pair')
        for fields,status,code in [({'table_id':'A'},422,'validation_failed'),({'table_ids':[]},422,'validation_failed'),({'table_ids':['A','A']},422,'validation_failed'),({'table_ids':['A','B','C']},422,'combination_not_allowed'),({'table_ids':['A','C']},422,'combination_not_allowed'),({'table_ids':'A'},400,'malformed_request'),({'table_ids':[True]},400,'malformed_request'),({'table_ids':['missing']},404,'not_found')]:
            self.error(self.create({**base,**fields},'invalid'),status,code)
        status,single=self.create();self.assertEqual(status,201)
        self.assertEqual(single['table_ids'],['A']);self.assertEqual(single['table_id'],'A')
        path='/reservations/'+single['reference']
        pair=self.request('PATCH',path,{'table_ids':['A','B']},self.token)[1]
        self.assertEqual(pair['table_ids'],['B','A']);self.assertNotIn('table_id',pair)
        self.assertEqual(self.request('PATCH',path,{},self.token),(200,pair))
        single2=self.request('PATCH',path,{'table_id':'C'},self.token)[1]
        self.assertEqual(single2['table_ids'],['C']);self.assertEqual(single2['table_id'],'C')
        for k in ['reservation_id','reference','created_at']:self.assertEqual(single[k],single2[k])

    def test_all_final_layouts_and_rollback(self):
        selections=[['A'],['B'],['C'],['B','A'],['B','C']]
        passes=failures=0
        for x in selections:
            for y in selections:
                self.setUp()
                a=self.create(key='a')[1];b=self.create(self.booking('C'),'c')[1]
                before=self.request('GET','/_test/export')[1]
                moves={'moves':[{'reference':a['reference'],'table_ids':x},{'reference':b['reference'],'table_ids':y}]}
                result=self.request('POST','/reservation-moves',moves,self.token,'layout')
                if set(x)&set(y):
                    self.error(result,409,'table_unavailable');failures+=1
                    self.assertEqual(self.request('GET','/_test/export')[1],before)
                    moves['moves'][0]['table_ids']=['A'];moves['moves'][1]['table_ids']=['C']
                    self.assertEqual(self.request('POST','/reservation-moves',moves,self.token,'layout')[0],201)
                else:
                    passes+=1;self.assertEqual(result[0],201)
                    self.assertEqual([r['reference'] for r in result[1]['reservations']],[a['reference'],b['reference']])
        self.assertEqual((passes,failures),(10,15))

    def test_pair_receipts_precision_and_import(self):
        body=self.booking('pair');raw=json.dumps(body)[:-1]+',"x":10000000000000000000000000001}'
        first=self.request('POST','/reservations',raw=raw,token=self.token,key='precise');self.assertEqual(first[0],201)
        self.error(self.create({**body,'table_ids':['B','A']},'precise'),409,'idempotency_key_reuse')
        same=raw.replace('10000000000000000000000000001','10000000000000000000000000001.0')
        self.assertEqual(self.request('POST','/reservations',raw=same,token=self.token,key='precise'),(200,first[1]))
        self.request('POST','/reservations/'+first[1]['reference']+'/cancel',{},self.token)
        snapshot=self.request('GET','/_test/export')[1]
        self.assertEqual(self.request('POST','/_test/import',snapshot,destination=1)[0],204)
        self.assertEqual(self.request('POST','/reservations',raw=raw,token=self.token,key='precise',destination=1),(200,first[1]))
        bad=copy.deepcopy(snapshot);bad['state']['reservations'][0]['table_ids']=['A','C']
        self.error(self.request('POST','/_test/import',bad,destination=1),422,'validation_failed')
        self.assertEqual(self.request('GET','/_test/export',destination=1)[1],snapshot)
        bad=copy.deepcopy(snapshot);bad['state']['reservations'][0]['table_id']=None
        self.error(self.request('POST','/_test/import',bad,destination=1),422,'validation_failed')
        self.assertEqual(self.request('GET','/_test/export',destination=1)[1],snapshot)

    def test_pair_races(self):
        body=self.booking('pair')
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results=list(pool.map(lambda _:self.create(body,'race'),range(50)))
        self.assertEqual([r[0] for r in results].count(201),1);self.assertEqual([r[0] for r in results].count(200),49)
        self.assertTrue(all(r[1]==results[0][1] for r in results))
        self.setUp()
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results=list(pool.map(lambda i:self.create(body if i%2 else self.booking('B'),str(i)),range(50)))
        self.assertEqual([r[0] for r in results].count(201),1);self.assertEqual([r[0] for r in results].count(409),49)

    def test_pair_calendar_capacity_and_moves_replay(self):
        for zone,date,opening,closing,start,end in [
            ('America/New_York','9999-12-31','18:00','23:00','19:00','9999-12-31T20:30:00-05:00'),
            ('Etc/GMT-14','0001-01-01','00:00','04:00','00:30','0001-01-01T02:00:00+14:00'),
            ('Europe/Berlin','2026-10-25','00:30','04:30','02:30','2026-10-25T03:00:00+01:00')]:
            self.assertEqual(self.request('POST','/_test/reset',self.fixture(zone,opening,closing))[0],204)
            self.token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
            body={**self.booking('pair'),'starts_at_local':date+'T'+start,'party_size':6}
            self.error(self.create({**body,'party_size':7},'capacity'),422,'party_exceeds_capacity')
            status,record=self.create(body,'capacity');self.assertEqual(status,201)
            self.assertEqual(record['ends_at'],end);self.assertEqual(record['table_ids'],['B','A'])
            exported=self.request('GET','/_test/export')[1]
            self.assertEqual(self.request('POST','/_test/import',exported,destination=1)[0],204)
            self.assertEqual(self.request('POST','/reservations',body,self.token,'capacity',destination=1),(200,record))
        self.setUp()
        a=self.create(key='a')[1];b=self.create(self.booking('C'),'c')[1]
        moves={'moves':[{'reference':a['reference'],'table_ids':['A','B']},{'reference':b['reference'],'table_ids':['C']}]}
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results=list(pool.map(lambda _:self.request('POST','/reservation-moves',moves,self.token,'moves-race'),range(50)))
        self.assertEqual([r[0] for r in results].count(201),1);self.assertEqual([r[0] for r in results].count(200),49)
        self.assertTrue(all(r[1]==results[0][1] for r in results))
        before=self.request('GET','/_test/export')[1]
        invalid={'moves':[{'reference':a['reference'],'table_ids':['C']},{'reference':b['reference'],'party_size':99}]}
        self.error(self.request('POST','/reservation-moves',invalid,self.token,'rollback'),422,'party_exceeds_capacity')
        self.assertEqual(self.request('GET','/_test/export')[1],before)

    def test_seed_status_and_invalid_declaration(self):
        fixture=self.fixture()
        fixture['reservations']=[{'id':'seed','reference':'SEED00','user_id':'u',**self.booking('pair'),'status':'cancelled'}]
        self.assertEqual(self.request('POST','/_test/reset',fixture)[0],204)
        self.token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
        self.assertEqual(self.create(self.booking('pair'))[0],201)
        before=self.request('GET','/_test/export')[1]
        fixture['restaurants'][0]['combinable']=[['A','B','C']]
        self.error(self.request('POST','/_test/reset',fixture),422,'validation_failed')
        self.assertEqual(self.request('GET','/_test/export')[1],before)

# Only pair-specific tests; inherited originals have their own unchanged execution.
for name in list(vars(HTTPTests)):
    if name.startswith('test_') and name not in vars(PairTests):
        setattr(PairTests,name,None)

class UpgradeTests(HTTPTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stage2_urls=list(cls.urls)
        if os.environ.get('BACKEND_STAGE1_URL'):
            cls.old_url=os.environ['BACKEND_STAGE1_URL'];return
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        p=subprocess.Popen([sys.executable,str(Path(__file__).resolve().parents[1]/'stage-1/server.py')],env={**os.environ,'PORT':str(port)},stdout=subprocess.DEVNULL)
        cls.processes.append(p);cls.old_url='http://127.0.0.1:'+str(port)
        for _ in range(100):
            try:urllib.request.urlopen(cls.old_url+'/health',timeout=1).close();break
            except OSError:time.sleep(.02)
        else:raise RuntimeError('Stage1 startup failed')
    def test_real_stage1_export_receipts_sessions(self):
        self.urls=[self.old_url]+self.stage2_urls
        self.setUp()
        original=self.create(key='old')[1];self.assertNotIn('table_ids',original)
        precise_body={**self.booking('C'),'starts_at_local':'2030-01-04T19:00'}
        precise_raw=json.dumps(precise_body)[:-1]+',"x":10000000000000000000000000001}'
        precise=self.request('POST','/reservations',raw=precise_raw,token=self.token,key='oldprecise')[1]
        failed_body={**self.booking('A'),'starts_at_local':'2030-01-05T19:00','party_size':99}
        self.error(self.request('POST','/reservations',failed_body,self.token,'oldfailed'),422,'party_exceeds_capacity')
        b=self.create(self.booking('B'),'oldB')[1]
        moves={'moves':[{'reference':b['reference'],'table_id':'C'},{'reference':original['reference'],'table_id':'B'}]}
        moved=self.request('POST','/reservation-moves',moves,self.token,'oldmoves')[1]
        self.request('POST','/reservations/'+original['reference']+'/cancel',{},self.token)
        other_token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
        snapshot=self.request('GET','/_test/export')[1]
        self.assertEqual(snapshot['state']['schema_version'],1)
        destination_fixture=self.fixture();destination_fixture['users']=[{'id':'other','email':'other@b','password':'password3','display_name':'Other'}]
        self.assertEqual(self.request('POST','/_test/reset',destination_fixture,destination=1)[0],204)
        destination_token=self.request('POST','/auth/login',{'email':'other@b','password':'password3'},destination=1)[1]['token']
        self.assertEqual(self.request('POST','/_test/import',snapshot,destination=1)[0],204)
        self.error(self.request('GET','/reservations',token=destination_token,destination=1),401,'unauthenticated')
        current=self.request('GET','/reservations/'+original['reference'],token=other_token,destination=1)[1]
        self.assertEqual(current['status'],'cancelled');self.assertEqual(current['table_ids'],['B'])
        self.assertEqual(self.request('POST','/reservations',self.booking(),self.token,'old',destination=1),(200,original))
        self.assertEqual(self.request('POST','/reservation-moves',moves,self.token,'oldmoves',destination=1),(200,moved))
        self.assertEqual(self.request('POST','/auth/login',{'email':'a@b','password':'password1'},destination=1)[0],200)
        exported=self.request('GET','/_test/export',destination=1)[1]
        self.assertEqual(exported['state']['schema_version'],2)
        self.assertEqual(exported['state']['receipts'],snapshot['state']['receipts'])
        self.assertEqual(exported['state']['users'],snapshot['state']['users'])
        self.assertEqual(self.request('POST','/reservations',raw=precise_raw,token=self.token,key='oldprecise',destination=1),(200,precise))
        self.error(self.request('POST','/reservations',raw=precise_raw.replace('10000000000000000000000000001','10000000000000000000000000002'),token=self.token,key='oldprecise',destination=1),409,'idempotency_key_reuse')
        failed_body['party_size']=4
        self.assertEqual(self.request('POST','/reservations',failed_body,self.token,'oldfailed',destination=1)[0],201)
        self.assertEqual(exported['state']['sessions'][:len(snapshot['state']['sessions'])],snapshot['state']['sessions'])
        self.assertEqual(self.request('POST','/_test/import',exported,destination=2)[0],204)
        self.assertEqual(self.request('POST','/reservations',self.booking(),self.token,'old',destination=2),(200,original))
        self.urls=self.stage2_urls

for name in list(vars(HTTPTests)):
    if name.startswith('test_') and name not in vars(UpgradeTests):setattr(UpgradeTests,name,None)

if __name__=='__main__':
    suite=unittest.TestSuite()
    for cls in [PairTests,UpgradeTests]:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
