"""Literal HTTP Stage3 oracles; policy snapshots, ledger, series and legacy migration."""
import copy,json,os,socket,subprocess,sys,time,unittest,concurrent.futures
from pathlib import Path
from backend_stage2_inherited import HTTPTests
ROOT=Path(__file__).resolve().parents[1]
class Stage3Tests(HTTPTests):
    @classmethod
    def setUpClass(cls):
        cls.processes=[];cls.urls=[]
        if os.environ.get('BACKEND_TEST_URLS'):
            cls.urls=os.environ['BACKEND_TEST_URLS'].split(',');return
        for stage in [3,3,1,2]:
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            p=subprocess.Popen([sys.executable,str(ROOT/f'stage-{stage}/server.py')],env={**os.environ,'PORT':str(port)},stdout=subprocess.DEVNULL)
            cls.processes.append(p);cls.urls.append('http://127.0.0.1:'+str(port))
        import urllib.request
        for url in cls.urls:
            for _ in range(100):
                try:urllib.request.urlopen(url+'/health',timeout=1).close();break
                except OSError:time.sleep(.02)
            else:raise RuntimeError('startup failed')
    def fixture(self,*args,**kwargs):
        f=super().fixture(*args,**kwargs);r=f['restaurants'][0]
        r['manager_user_ids']=['u'];r['combinable']=[['B','A'],['B','C']]
        r['tables']=[{'id':t,'label':t,'capacity':c} for t,c in [('A',2),('B',4),('C',3)]]
        return f
    def booking(self,table='A',time='19:00'):
        return {'restaurant_id':'r','table_id':table,'party_size':1,'starts_at_local':'2030-01-07T'+time}
    def policy(self,date='2030-01-07',**changes):
        r=self.fixture()['restaurants'][0]
        return {'effective_from':date,'slot_minutes':30,'reservation_duration_minutes':90,'cancellation_cutoff_minutes':120,'opening_hours':r['opening_hours'],'capacities':{'A':2,'B':4,'C':3},**changes}
    def publish(self,body=None,key='p'):
        return self.request('POST','/restaurants/r/policies',body or self.policy(),self.token,key)
    def history(self,ref,destination=0):return self.request('GET','/reservations/'+ref+'/history',token=self.token,destination=destination)
    def decision(self,ref):return self.request('GET','/reservations/'+ref+'/decision',token=self.token)
    def adopt(self,ref,count=3,weeks=1,key='series'):
        return self.request('POST','/series',{'anchor_reference':ref,'count':count,'interval_weeks':weeks},self.token,key)
    def series(self,sid):return self.request('GET','/series/'+sid,token=self.token)
    def test_policy_order_validation_receipts(self):
        dates=['2030-01-14','2030-01-07','2030-01-14','2029-12-31']
        for i,date in enumerate(dates):
            status,p=self.publish(self.policy(date),str(i));self.assertEqual(status,201);self.assertEqual(p['policy_version'],i+1)
            self.assertEqual(self.publish(self.policy(date),str(i)),(200,p))
        self.assertEqual([p['effective_from'] for p in self.request('GET','/restaurants/r/policies')[1]['policies']],dates)
        for date,version in [('2029-12-30',0),('2029-12-31',4),('2030-01-07',2),('2030-01-13',2),('2030-01-14',3)]:
            row=self.request('GET',f'/availability?restaurant_id=r&date={date}&party_size=1&explain=true')[1]['slots'][0]
            self.assertEqual(row['explain'][0]['policy_version'],version)
        for field,value in [('slot_minutes',True),('slot_minutes',1441),('reservation_duration_minutes',0),('cancellation_cutoff_minutes',10081),('capacities',{'A':2}),('effective_from','2030-02-30')]:
            self.error(self.publish(self.policy(**{field:value}),'invalid'),422,'validation_failed')
        v=self.request('POST','/auth/login',{'email':'v@b','password':'password2'})[1]['token']
        self.error(self.request('POST','/restaurants/r/policies',self.policy(),v,'v'),403,'forbidden')
        self.error(self.request('POST','/restaurants/r/policies',self.policy(),key='x'),401,'unauthenticated')
        self.assertEqual(len(self.request('GET','/restaurants/r/policies')[1]['policies']),4)
    def test_noop_oldterms_revisions_histories(self):
        body={**self.booking(),'table_ids':['A','B'],'party_size':6,'starts_at_local':'2030-01-07T19:30'};body.pop('table_id')
        status,original=self.create(body);self.assertEqual(status,201)
        ref=original['reference'];path='/reservations/'+ref
        history=self.history(ref)[1]
        self.assertEqual([c['field'] for c in history['entries'][0]['changes']],['table_ids','starts_at_local','party_size'])
        self.publish(self.policy(slot_minutes=60,reservation_duration_minutes=120,capacities={'A':1,'B':1,'C':1}))
        self.assertEqual(self.request('PATCH',path,{'table_ids':['B','A'],'party_size':6.0,'expected_revision':1},self.token),(200,original))
        self.assertEqual(self.history(ref)[1],history)
        self.error(self.request('PATCH',path,{'starts_at_local':'2030-01-07T20:00'},self.token),422,'party_exceeds_capacity')
        self.error(self.request('PATCH',path,{'expected_revision':2,'party_size':False},self.token),409,'stale_revision')
        self.error(self.request('PATCH',path,{'expected_revision':True},self.token),422,'validation_failed')
        changed=self.request('PATCH',path,{'party_size':2,'starts_at_local':'2030-01-07T20:00','expected_revision':1},self.token)[1]
        self.assertEqual(changed['revision'],2);self.assertEqual(changed['accepted_terms']['policy_version'],1)
        cancelled=self.request('POST',path+'/cancel',{},self.token)[1];self.assertEqual(cancelled['revision'],3)
        self.assertEqual(self.request('POST',path+'/cancel',{},self.token),(200,cancelled))
        entries=self.history(ref)[1]['entries'];self.assertEqual([e['event'] for e in entries],['created','changed','cancelled'])
        self.assertEqual([e['revision'] for e in entries],[1,2,3]);self.assertEqual(entries[-1]['changes'],[])
        self.assertEqual(self.create(body),(200,original))
        for suffix in ['/history','/decision']:
            self.error(self.request('GET',path+suffix),404,'not_found');self.error(self.request('GET',path+suffix,token='invalid'),404,'not_found')
    def test_independent_explanation_rules(self):
        self.create({**self.booking('A'),'starts_at_local':'2030-01-07T18:00'})
        self.create({**self.booking('B'),'starts_at_local':'2030-01-07T18:00'},'b')
        row=self.request('GET','/availability?restaurant_id=r&date=2030-01-07&party_size=3&explain=true')[1]['slots'][0]
        self.assertEqual([e['table_id'] for e in row['explain']],['A','B','C'])
        self.assertEqual([[r['holds'] for r in e['rules']] for e in row['explain']],[[False,False],[True,False],[True,True]])
        self.assertEqual(row['available_table_ids'],['C'])
        self.assertNotIn('explain',self.request('GET','/availability?restaurant_id=r&date=2030-01-07&party_size=3')[1]['slots'][0])
        for value in ['false','1','']:
            self.error(self.request('GET','/availability?restaurant_id=r&date=2030-01-07&party_size=3&explain='+value),422,'validation_failed')
    def test_anchor_revision_series_counters_and_native_import(self):
        original=self.create({**self.booking(),'expected_revision':'ignored-on-create'})[1];ref=original['reference'];path='/reservations/'+ref
        self.request('PATCH',path,{'party_size':2},self.token)
        anchor=self.request('PATCH',path,{'starts_at_local':'2030-01-07T19:30'},self.token)[1];self.assertEqual(anchor['revision'],3)
        oldhist=self.history(ref)[1]
        self.publish(self.policy('2030-01-14',reservation_duration_minutes=120))
        status,series=self.adopt(ref);self.assertEqual(status,201)
        self.assertEqual(series['occurrences'][0]['reservation'],anchor);self.assertEqual(self.history(ref)[1],oldhist)
        self.assertEqual([o['reservation']['accepted_terms']['policy_version'] for o in series['occurrences']],[0,1,1])
        sid=series['series_id'];refs=[o['reference'] for o in series['occurrences']]
        changed={'moves':[{'reference':r,'party_size':1} for r in refs[:2]]}
        status,moved=self.request('POST','/reservation-moves',changed,self.token,'batch');self.assertEqual(status,201)
        current=self.series(sid)[1];self.assertEqual(current['revision'],2)
        self.assertEqual([o['exception'] for o in current['occurrences']],[True,True,False])
        self.request('POST','/reservations/'+refs[2]+'/cancel',{},self.token)
        self.assertEqual(self.series(sid)[1]['revision'],3);self.assertFalse(self.series(sid)[1]['occurrences'][2]['exception'])
        self.assertEqual(self.adopt(ref),(200,series))
        self.assertEqual(self.create({**self.booking(),'expected_revision':'ignored-on-create'}),(200,original))
        exported=self.request('GET','/_test/export')[1]
        self.assertEqual(self.request('POST','/_test/import',exported,destination=1)[0],204)
        self.assertEqual(self.request('POST','/series',{'anchor_reference':ref,'count':3,'interval_weeks':1},self.token,'series',destination=1),(200,series))
        self.assertEqual(self.request('GET','/series/'+sid,token=self.token,destination=1)[1],self.series(sid)[1])
        for mutation in ['seq','terms','membership']:
            bad=copy.deepcopy(exported)
            if mutation=='seq':bad['state']['histories'][ref][0]['seq']=2
            if mutation=='terms':bad['state']['receipts'][0]['response']['accepted_terms']['capacities']['A']=100
            if mutation=='membership':bad['state']['series'][0]['occurrences'][1]['reference']=ref
            self.error(self.request('POST','/_test/import',bad,destination=1),422,'validation_failed')
            self.assertEqual(self.request('GET','/_test/export',destination=1)[1],exported)
    def test_first_index_failure_rollback_and_failed_key(self):
        anchor=self.create({**self.booking(),'party_size':2})[1]
        self.create({**self.booking(),'starts_at_local':'2030-01-14T19:00'},'conflict')
        self.publish(self.policy('2030-01-21',capacities={'A':1,'B':1,'C':1}))
        before=self.request('GET','/_test/export')[1]
        self.error(self.adopt(anchor['reference']),409,'table_unavailable')
        self.assertEqual(self.request('GET','/_test/export')[1],before)
        self.publish(self.policy('2030-01-21'),'restore')
        self.assertEqual(self.adopt(anchor['reference'],2,2)[0],201)
        self.setUp()
        anchor=self.create({**self.booking(),'party_size':2})[1]
        self.publish(self.policy('2030-01-14',capacities={'A':1,'B':1,'C':1}))
        self.create({**self.booking(),'starts_at_local':'2030-01-21T19:00'},'later-conflict')
        before=self.request('GET','/_test/export')[1]
        self.error(self.adopt(anchor['reference']),422,'party_exceeds_capacity')
        self.assertEqual(self.request('GET','/_test/export')[1],before)
        self.publish(self.policy('2030-01-14'),'restore')
        self.assertEqual(self.adopt(anchor['reference'],2)[0],201)
    def test_concurrent_expected_revision_and_series_replays(self):
        record=self.create()[1];path='/reservations/'+record['reference']
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda t:self.request('PATCH',path,{'starts_at_local':'2030-01-07T'+t,'expected_revision':1},self.token),['19:30','20:00']))
        self.assertEqual(sorted(r[0] for r in results),[200,409]);self.assertEqual(next(r[1]['error']['code'] for r in results if r[0]==409),'stale_revision')
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results=list(pool.map(lambda _:self.adopt(record['reference']),range(50)))
        self.assertEqual([r[0] for r in results].count(201),1);self.assertEqual([r[0] for r in results].count(200),49)
        self.assertTrue(all(r[1]==results[0][1] for r in results))
    def test_mixed_multiseries_noop_dedup_and_counter_snapshot(self):
        anchors=[self.create(self.booking(t),t)[1] for t in ['A','B','C']]
        series=[self.adopt(a['reference'],2,key='series'+str(i))[1] for i,a in enumerate(anchors)]
        refs=[[o['reference'] for o in s['occurrences']] for s in series]
        before=self.request('GET','/_test/export')[1]
        body={'moves':[{'reference':r,'party_size':2} for r in refs[0]]+[{'reference':refs[1][0]},{'reference':refs[2][0],'party_size':2}]}
        self.assertEqual(self.request('POST','/reservation-moves',body,self.token,'mixed')[0],201)
        self.assertEqual([self.series(s['series_id'])[1]['revision'] for s in series],[2,1,2])
        after=self.request('GET','/_test/export')[1]
        self.assertEqual(after['state']['restaurant_revisions']['r'],before['state']['restaurant_revisions']['r']+1)
        self.assertEqual(self.request('POST','/reservation-moves',{'moves':[{'reference':refs[1][0]}]},self.token,'noop')[0],201)
        self.assertEqual(self.request('GET','/_test/export')[1]['state']['restaurant_revisions'],after['state']['restaurant_revisions'])
        self.request('POST','/reservations/'+refs[1][0]+'/cancel',{},self.token)
        current=self.series(series[1]['series_id'])[1]
        self.assertEqual(current['revision'],2);self.assertFalse(current['occurrences'][0]['exception']);self.assertEqual(current['occurrences'][1]['reservation']['status'],'confirmed')
        snapshot=self.request('GET','/_test/export')[1]
        self.assertEqual(self.request('POST','/_test/import',snapshot,destination=1)[0],204)
        bad=copy.deepcopy(snapshot);bad['state']['series'][0]['revision']=999
        self.error(self.request('POST','/_test/import',bad,destination=1),422,'validation_failed')
        bad=copy.deepcopy(snapshot);bad['state']['series'][0]['occurrences'][0]['exception']=False
        self.error(self.request('POST','/_test/import',bad,destination=1),422,'validation_failed')
    def test_calendar_dst_bounds_and_policy0_large_native(self):
        fixture=self.fixture('America/New_York','00:00','04:30')
        self.request('POST','/_test/reset',fixture);self.token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
        anchor=self.create({**self.booking(),'starts_at_local':'2026-10-25T01:30'})[1]
        status,series=self.adopt(anchor['reference']);self.assertEqual(status,201)
        self.assertEqual([o['reservation']['starts_at'] for o in series['occurrences']],['2026-10-25T01:30:00-04:00','2026-11-01T01:30:00-04:00','2026-11-08T01:30:00-05:00'])
        self.assertEqual([o['reservation']['starts_at_local'] for o in series['occurrences']],['2026-10-25T01:30','2026-11-01T01:30','2026-11-08T01:30'])
        fixture=self.fixture();fixture['restaurants'][0]['slot_minutes']=1000000000000;fixture['restaurants'][0]['tables'][0]['capacity']=1000000000000
        self.request('POST','/_test/reset',fixture);self.token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
        original=self.create({**self.booking(time='18:00'),'party_size':1000000000000})[1]
        status,series=self.adopt(original['reference'],12,4);self.assertEqual(status,201);self.assertEqual(len(series['occurrences']),12)
        snapshot=self.request('GET','/_test/export')[1]
        self.assertEqual(self.request('POST','/_test/import',snapshot,destination=1)[0],204)
        self.assertEqual(self.request('GET','/series/'+series['series_id'],token=self.token,destination=1)[1],series)
        for count,weeks in [(True,1),(1,1),(13,1),(2,True),(2,0),(2,5)]:
            self.error(self.adopt(original['reference'],count,weeks,'invalid'+str(count)+str(weeks)),422,'validation_failed')

    def test_actual_legacy_stage1_stage2_exports_and_adoption(self):
        for destination in [2,3]:
            fixture=super().fixture();self.assertEqual(self.request('POST','/_test/reset',fixture,destination=destination)[0],204)
            token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'},destination=destination)[1]['token']
            body={**self.booking(),'expected_revision':'unknown-at-old-stage'}
            if destination==2:body['table_ids']=['ignored-old-field']
            original=self.request('POST','/reservations',body,token,'legacy',destination=destination)[1]
            self.assertNotIn('revision',original)
            exported=self.request('GET','/_test/export',destination=destination)[1]
            self.assertEqual(self.request('POST','/_test/import',exported,destination=1)[0],204)
            self.assertEqual(self.request('POST','/reservations',body,token,'legacy',destination=1),(200,original))
            self.assertEqual(self.request('POST','/auth/login',{'email':'a@b','password':'password1'},destination=1)[0],200)
            current=self.request('GET','/reservations/'+original['reference'],token=token,destination=1)[1]
            self.assertEqual(current['revision'],1);self.assertEqual(current['accepted_terms']['policy_version'],0)
            self.assertEqual(self.request('POST','/series',{'anchor_reference':original['reference'],'count':2,'interval_weeks':1},token,'legacyseries',destination=1)[0],201)
            native=self.request('GET','/_test/export',destination=1)[1]
            self.assertEqual(self.request('POST','/_test/import',native,destination=1)[0],204)
for name in vars(HTTPTests):
    if name.startswith('test_') and name not in vars(Stage3Tests):setattr(Stage3Tests,name,None)
if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Stage3Tests))
    sys.exit(not result.wasSuccessful())
