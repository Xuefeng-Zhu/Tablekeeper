"""Literal Cartesian seating oracle and actual HTTP atomic/migration regressions."""
import copy,itertools,json,os,socket,subprocess,sys,time,unittest,concurrent.futures,random
from pathlib import Path
from datetime import datetime
from backend_stage4_inherited import Stage4InheritedTests


class Stage4Tests(Stage4InheritedTests):
    @classmethod
    def setUpClass(cls):
        cls.processes=[];cls.urls=[]
        if os.environ.get('BACKEND_TEST_URLS'):
            cls.urls=os.environ['BACKEND_TEST_URLS'].split(',');return
        import urllib.request
        for stage in [4,4,1,2,3]:
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            process=subprocess.Popen([sys.executable,str(Path(__file__).resolve().parents[1]/f'stage-{stage}/server.py')],env={**os.environ,'PORT':str(port)},stdout=subprocess.DEVNULL)
            cls.processes.append(process);url='http://127.0.0.1:'+str(port);cls.urls.append(url)
            for _ in range(100):
                try:urllib.request.urlopen(url+'/health',timeout=1).close();break
                except OSError:time.sleep(.02)
            else:raise RuntimeError('startup')
    def preview(self,table='A',start='2030-01-07T19:00:00+00:00',end='2030-01-07T20:30:00+00:00',key='plan'):
        return self.request('POST','/restaurants/r/replans',{'table_id':table,'from':start,'to':end},self.token,key)
    def apply(self,p,key='apply',destination=0):return self.request('POST','/restaurants/r/replans/'+p['plan_id']+'/apply',{},self.token,key,destination)
    def amend(self,s,time='20:00',first=0,key='amend',revision=None):return self.request('POST','/series/'+s['series_id']+'/amend',{'expected_revision':s['revision'] if revision is None else revision,'from_index':first,'local_time':time},self.token,key)
    def snapshot(self,destination=0):return self.request('GET','/_test/export',destination=destination)[1]
    def create(self,table='A',clock='19:00',key='create'):
        status,r=self.request('POST','/reservations',self.booking(table,clock),self.token,key);self.assertEqual(status,201);return r
    def test_preview_apply_counter_history_closure_and_native(self):
        old=self.create();before=self.snapshot();status,p=self.preview();self.assertEqual(status,201)
        snap=self.snapshot();self.assertEqual(snap['state']['restaurant_revisions'],{'r':1});self.assertEqual(snap['state']['reservations'],before['state']['reservations'])
        self.assertEqual(p['assignments'],[{'reference':old['reference'],'table_ids':['C'],'changed':True}])
        self.assertEqual(self.request('POST','/_test/import',snap,destination=1)[0],204)
        status,result=self.apply(p,destination=1);self.assertEqual(status,201);moved=result['reservations'][0]
        self.assertEqual((moved['table_ids'],moved['revision']),(['C'],2));self.assertEqual(moved['accepted_terms'],old['accepted_terms'])
        self.assertEqual((moved['starts_at'],moved['ends_at']),(old['starts_at'],old['ends_at']))
        hist=self.history(old['reference'],1)[1]['entries'];self.assertEqual(hist[-1]['event'],'reassigned');self.assertEqual(hist[-1]['changes'],[{'field':'table_ids','from':['A'],'to':['C']}]);self.assertEqual(hist[-1]['plan_id'],p['plan_id'])
        self.error(self.apply(p,'other',1),409,'plan_already_applied');self.assertEqual(self.apply(p,destination=1),(200,result))
        native=self.snapshot(1);self.assertEqual(self.request('POST','/_test/import',native)[0],204)
        self.assertEqual(self.apply(p),(200,result));self.assertEqual(self.request('POST','/reservations',self.booking(),self.token,'create'),(200,old))
        row=next(x for x in self.request('GET','/availability?restaurant_id=r&date=2030-01-07&party_size=3&explain=true')[1]['slots'] if x['starts_at_local'].endswith('19:00'))
        a=row['explain'][0];self.assertEqual(a['rules'],[{'rule':'capacity','holds':False},{'rule':'no_overlap','holds':False}]);self.assertNotIn('A',row['available_table_ids']);self.assertTrue(all('A' not in o['table_ids'] for o in row['available_options']))
        self.error(self.request('POST','/reservations',self.booking(),self.token,'closed'),409,'table_unavailable')
        self.error(self.request('PATCH','/reservations/'+old['reference'],{'table_id':'A'},self.token),409,'table_unavailable')
        self.error(self.request('POST','/reservation-moves',{'moves':[{'reference':old['reference'],'table_ids':['B','A']}]},self.token,'moves'),409,'table_unavailable')
        for mutate in [lambda s:s['closures'][0].update(table_id='C'),lambda s:s['plans'][0]['response'].update(unused_seats=99),lambda s:s['histories'][old['reference']][-1].update(plan_id='missing')]:
            broken=copy.deepcopy(native);mutate(broken['state']);self.error(self.request('POST','/_test/import',broken),422,'validation_failed');self.assertEqual(self.snapshot(),native)
    def test_closure_only_stale_cross_restaurant_and_apply_race(self):
        status,p=self.preview();self.assertEqual(status,201);self.assertEqual(p['assignments'],[])
        status,q=self.preview('B',key='second');self.assertEqual(status,201)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda key:self.apply(p,key),['race1','race2']))
        self.assertEqual(sorted(x[0] for x in results),[201,409]);self.assertEqual(next(x for x in results if x[0]==409)[1]['error']['code'],'plan_already_applied')
        self.error(self.apply(q),409,'stale_plan');self.assertEqual(self.snapshot()['state']['restaurant_revisions'],{'r':1})
        self.assertEqual(self.request('POST','/_test/import',self.snapshot(),destination=1)[0],204)
        # Failed keys remain reusable; adjacent boundary is free.
        self.assertEqual(self.preview('A','2030-01-07T20:30:00Z','2030-01-07T21:00:00Z','apply')[0],201)
        self.assertEqual(self.request('POST','/reservations',self.booking('A','21:00'),self.token,'adjacent')[0],201)
        other=self.fixture();other['restaurants'].append({**copy.deepcopy(other['restaurants'][0]),'id':'other'})
        self.request('POST','/_test/reset',other);self.token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
        p=self.preview()[1]
        body={**self.booking('A','19:00'),'restaurant_id':'other'};self.assertEqual(self.request('POST','/reservations',body,self.token,'other-r')[0],201)
        self.assertEqual(self.apply(p)[0],201)
    def test_series_original_schedule_amend_noop_exceptions_and_native(self):
        a=self.create();a=self.request('PATCH','/reservations/'+a['reference'],{'starts_at_local':'2030-01-08T19:00'},self.token)[1]
        status,s=self.adopt(a['reference'],4);self.assertEqual(status,201)
        self.request('PATCH','/reservations/'+a['reference'],{'starts_at_local':'2030-01-09T19:00'},self.token)
        cancelled=s['occurrences'][2]['reference'];self.request('POST','/reservations/'+cancelled+'/cancel',{},self.token)
        current=self.series(s['series_id'])[1];before=self.snapshot();status,result=self.amend(current);self.assertEqual(status,201)
        self.assertEqual(result['revision'],current['revision']+1)
        self.assertEqual([o['exception'] for o in result['occurrences']],[True,False,False,False])
        self.assertEqual([o['reservation']['starts_at_local'] for o in result['occurrences']],['2030-01-09T19:00','2030-01-15T20:00','2030-01-22T19:00','2030-01-29T20:00'])
        native=self.snapshot();self.assertEqual(native['state']['restaurant_revisions']['r'],before['state']['restaurant_revisions']['r']+1)
        self.assertEqual(self.request('POST','/_test/import',native,destination=1)[0],204)
        self.assertEqual(self.amend(current),(200,result))
        corrupt=copy.deepcopy(native);corrupt['state']['series'][0]['scheduled_dates'][1]='2030-01-16';self.error(self.request('POST','/_test/import',corrupt),422,'validation_failed')
        corrupt=copy.deepcopy(native);corrupt['state']['series'][0]['mutation_log'][-1]['kind']='diner';self.error(self.request('POST','/_test/import',corrupt),422,'validation_failed')
        self.error(self.amend(current,key='stale'),409,'stale_revision')
        self.error(self.amend(result,key='invalid',revision=True),422,'validation_failed')
        unchanged=self.snapshot();self.assertEqual(self.amend(result,key='noop')[0],201);after=self.snapshot();self.assertEqual(after['state']['histories'],unchanged['state']['histories']);self.assertEqual(after['state']['restaurant_revisions'],unchanged['state']['restaurant_revisions'])
    def test_repair_series_counter_dedup_preserves_flags(self):
        a=self.create();s=self.adopt(a['reference'])[1]
        o=s['occurrences'][1];self.request('PATCH','/reservations/'+o['reference'],{'party_size':2},self.token)
        before=self.series(s['series_id'])[1]
        p=self.preview('A','2030-01-07T18:00:00Z','2030-01-22T23:00:00Z')[1]
        self.assertEqual(p['moved_count'],3);self.assertEqual(self.apply(p)[0],201)
        after=self.series(s['series_id'])[1];self.assertEqual(after['revision'],before['revision']+1);self.assertEqual([o['exception'] for o in after['occurrences']],[False,True,False])
        native=self.snapshot();self.assertEqual(self.request('POST','/_test/import',native,destination=1)[0],204)
        self.assertEqual(self.amend(after)[0],201)
    def test_series_nonoccupancy_precedence_and_two_writers(self):
        a=self.create();s=self.adopt(a['reference'],2)[1]
        self.create('A','21:00','conflict')
        badpolicy=self.policy('2030-01-14',capacities={'A':1,'B':4,'C':3})
        # Make retained party2 valid before policy publication.
        child=s['occurrences'][1]['reference'];self.request('PATCH','/reservations/'+child,{'party_size':2},self.token)
        # Individual edit is an exception, so use a fresh series for precedence.
        f=self.fixture();f['restaurants'][0]['tables'][0]['capacity']=2
        self.request('POST','/_test/reset',f);self.token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
        body={**self.booking(),'party_size':2};a=self.request('POST','/reservations',body,self.token,'a')[1];s=self.adopt(a['reference'],2)[1]
        self.create('A','21:00','conflict');self.publish(badpolicy)
        before=self.snapshot();self.error(self.amend(s,'21:00'),422,'party_exceeds_capacity');self.assertEqual(self.snapshot(),before)
        self.publish(self.policy('2030-01-14'),'valid-policy')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda t:self.amend(s,t,key=t),['18:00','20:00']))
        self.assertEqual(sorted(x[0] for x in results),[201,409]);self.assertEqual(next(x for x in results if x[0]==409)[1]['error']['code'],'stale_revision')
    def test_actual_stage3_dates_after_live_anchor_exception(self):
        f=self.fixture();self.request('POST','/_test/reset',f,destination=4)
        token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'},destination=4)[1]['token']
        body=self.booking();a=self.request('POST','/reservations',body,token,'old3',4)[1]
        self.request('PATCH','/reservations/'+a['reference'],{'starts_at_local':'2030-01-08T19:00'},token,destination=4)
        adopt={'anchor_reference':a['reference'],'count':3,'interval_weeks':1};s=self.request('POST','/series',adopt,token,'old-series',4)[1]
        self.request('PATCH','/reservations/'+a['reference'],{'starts_at_local':'2030-01-09T19:00'},token,destination=4)
        self.request('POST','/reservations/'+s['occurrences'][2]['reference']+'/cancel',{},token,destination=4)
        exported=self.snapshot(4);self.assertEqual(exported['state']['schema_version'],3)
        self.assertEqual(self.request('POST','/_test/import',exported)[0],204);self.token=token
        self.assertEqual(self.request('POST','/reservations',body,token,'old3'),(200,a));self.assertEqual(self.request('POST','/series',adopt,token,'old-series'),(200,s))
        current=self.series(s['series_id'])[1];result=self.amend(current)[1]
        self.assertEqual(result['occurrences'][1]['reservation']['starts_at_local'],'2030-01-15T20:00')
        native=self.snapshot();self.assertEqual(self.request('POST','/_test/import',native,destination=1)[0],204)
        self.assertEqual(self.request('POST','/auth/login',{'email':'a@b','password':'password1'},destination=1)[0],200)
    def test_noop_inside_old_cutoff_and_closure_all_paths(self):
        body={**self.booking(),'starts_at_local':'2026-10-07T19:00'}
        anchor=self.request('POST','/reservations',body,self.token,'tomorrow')[1]
        s=self.adopt(anchor['reference'],2)[1]
        self.publish(self.policy('2026-10-07',cancellation_cutoff_minutes=10080))
        status,current=self.amend(s,'20:00');self.assertEqual(status,201)
        before=self.snapshot();self.assertEqual(self.amend(current,'20:00',key='noop-cutoff')[0],201)
        after=self.snapshot();self.assertEqual(before['state']['restaurant_revisions'],after['state']['restaurant_revisions']);self.assertEqual(before['state']['histories'],after['state']['histories'])
        self.error(self.amend(current,'21:00',key='real-cutoff'),409,'cutoff_passed')
        self.error(self.request('PATCH','/reservations/'+anchor['reference'],{},self.token),409,'cutoff_passed')
        self.assertEqual(self.request('POST','/_test/import',after,destination=1)[0],204)
        # Closure-only windows forbid both recurrence creation and collective amendment.
        self.request('POST','/_test/reset',self.fixture());self.token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
        a=self.create('A','18:00');s=self.adopt(a['reference'],2)[1]
        p=self.preview('A','2030-01-14T20:00:00Z','2030-01-14T21:30:00Z')[1];self.assertEqual(p['assignments'],[]);self.assertEqual(self.apply(p)[0],201)
        before=self.snapshot();self.error(self.amend(s,'20:00'),409,'table_unavailable');self.assertEqual(self.snapshot(),before)
        future={**self.booking('A','20:00'),'starts_at_local':'2030-01-07T20:00'}
        a2=self.request('POST','/reservations',future,self.token,'a2')[1]
        self.error(self.adopt(a2['reference'],2,key='blocked-series'),409,'table_unavailable')
        self.request('POST','/reservations/'+a2['reference']+'/cancel',{},self.token)
        self.error(self.request('POST','/reservations',{**future,'starts_at_local':'2030-01-14T20:00'},self.token,'stillclosed'),409,'table_unavailable')
    def test_literal_cartesian_oracle_and_full_interval_fixed_constraint(self):
        def oracle(f,records,closed,start,end):
            options=[[t['id']] for t in f['restaurants'][0]['tables']]+f['restaurants'][0]['combinable'];records=sorted(records,key=lambda r:r['reference']);best=None;winner=None
            stamp=lambda t:datetime.fromisoformat(t).timestamp()
            for choices in itertools.product(range(len(options)),repeat=len(records)):
                if any(closed in options[k] and stamp(r['starts_at'])<stamp(end) and stamp(start)<stamp(r['ends_at']) for r,k in zip(records,choices)):continue
                if any(sum(r['accepted_terms']['capacities'][t] for t in options[k])<r['party_size'] for r,k in zip(records,choices)):continue
                if any(set(options[choices[i]])&set(options[choices[j]]) and stamp(records[i]['starts_at'])<stamp(records[j]['ends_at']) and stamp(records[j]['starts_at'])<stamp(records[i]['ends_at']) for i in range(len(records)) for j in range(i)):continue
                cost=(sum(set(options[k])!=set(r['table_ids']) for r,k in zip(records,choices)),sum(sum(r['accepted_terms']['capacities'][t] for t in options[k])-r['party_size'] for r,k in zip(records,choices)),choices)
                if best is None or cost<best:best=cost;winner=[{'reference':r['reference'],'table_ids':options[k],'changed':set(options[k])!=set(r['table_ids'])} for r,k in zip(records,choices)]
            return best,winner
        for index in range(18):
            f=self.fixture();f['restaurants'][0]['tables']=[{'id':t,'label':t,'capacity':c} for t,c in zip('ABC',[(index%4)+1,4,3])]
            self.request('POST','/_test/reset',f);self.token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
            records=[self.create('A','18:00','a'),self.create('B','19:30','b')]
            self.publish(self.policy(capacities={'A':2,'B':3+(index%3),'C':1+(index%3)}))
            records.append(self.create('C','18:30','c'))
            start='2030-01-07T18:00:00+00:00';end='2030-01-07T21:00:00+00:00';closed='ABC'[index%3]
            expected,assignments=oracle(f,records,closed,start,end);status,p=self.preview(closed,start,end)
            if expected is None:self.error((status,p),409,'no_feasible_plan')
            else:self.assertEqual(status,201);self.assertEqual(p['assignments'],assignments);self.assertEqual((p['moved_count'],p['unused_seats']),expected[:2])
        # Fixed A18:00 ends19:30 outside closure20:00–20:30, but intersects B19:00's full interval.
        self.request('POST','/_test/reset',self.fixture());self.token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
        self.create('A','18:00','fixed');considered=self.create('B','19:00','considered')
        p=self.preview('B','2030-01-07T20:00:00Z','2030-01-07T20:30:00Z')[1]
        self.assertEqual(p['assignments'],[{'reference':considered['reference'],'table_ids':['C'],'changed':True}])
    def test_bounded_adverse_search_and_limits(self):
        f=self.fixture(duration=90);r=f['restaurants'][0];r['tables']=[{'id':t,'label':t,'capacity':(i%3)+2} for i,t in enumerate('ABCDEF')];r['combinable']=[['B','A'],['C','D'],['E','F'],['B','D']]
        self.request('POST','/_test/reset',f);self.token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
        for i,t in enumerate('ABCDEF'):self.create(t,'19:00',str(i))
        start=time.perf_counter();response=self.preview('A');elapsed=time.perf_counter()-start
        self.assertLess(elapsed,5);self.error(response,409,'no_feasible_plan');print('ADVERSE6/4/6_HTTP_SECONDS',elapsed)
        r['tables'].append({'id':'G','label':'G','capacity':4});self.request('POST','/_test/reset',f);self.token=self.request('POST','/auth/login',{'email':'a@b','password':'password1'})[1]['token']
        self.error(self.preview(),422,'planning_limit')
    def test_explicit_instant_precision_offset_and_boundary_controls(self):
        old=self.create()
        for index,(start,end,moved) in enumerate([
            ('2030-01-07T18:00:00Z','2030-01-07T19:00:00.0000001Z',True),
            ('2030-01-07T18:00:00Z','2030-01-07T19:00:00Z',False),
            ('2030-01-07t18:00:00z','2030-01-07t19:00:01z',True),
            ('2030-01-07T20:00:00+01:00','2030-01-07T20:30:00+01:00',True),
            ('2030-01-07T20:30:00.0000001Z','2030-01-07T21:00:00Z',False)]):
            status,p=self.preview('A',start,end,'precision'+str(index));self.assertEqual(status,201)
            self.assertEqual(p['moved_count'],int(moved));self.assertEqual(len(p['assignments']),int(moved))
            self.assertEqual(p['closure']['from'],start);self.assertEqual(p['closure']['to'],end)
        snap=self.snapshot();self.assertEqual(self.request('POST','/_test/import',snap,destination=1)[0],204)
        p=self.preview('A','2030-01-07T18:00:00Z','2030-01-07T19:00:00.0000001Z','actual-fraction')[1]
        self.assertEqual(self.apply(p)[0],201);native=self.snapshot();self.assertEqual(self.request('POST','/_test/import',native,destination=1)[0],204)
        self.error(self.request('POST','/reservations',self.booking(),self.token,'fraction-closed'),409,'table_unavailable')


for name in vars(Stage4InheritedTests):
    if name.startswith('test_') and name not in vars(Stage4Tests):setattr(Stage4Tests,name,None)
if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Stage4Tests));sys.exit(not result.wasSuccessful())
