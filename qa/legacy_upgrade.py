#!/usr/bin/env python3
"""Destructive S1/S2 -> S3+ upgrade checks against TWO assigned disposable services.
Only records safe outcomes; credential-bearing exports remain in memory.
Private-ledger fidelity is an architectural contract, not a required S1 public endpoint.
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import time
from stage1_http import Checks, fixture, numeric_body, same


def now(): return dt.datetime.now(dt.timezone.utc)


def timed(operation):
    before=now()
    result=operation()
    return result,(before,now())


def check_history(entries, created, windows):
    assert [e['event'] for e in entries]==['created','changed','cancelled'], 'event kinds/order differ'
    assert [e['seq'] for e in entries]==[1,2,3]
    assert [e['revision'] for e in entries]==[1,2,3]
    assert entries[0]['changes']==[
        {'field':'table_id','from':None,'to':'t1'},
        {'field':'starts_at_local','from':None,'to':created['starts_at_local']},
        {'field':'party_size','from':None,'to':4}], 'creation reconstructed from changed current fields'
    assert entries[1]['changes']==[{'field':'table_id','from':'t1','to':'t2'}]
    assert entries[2]['changes']==[]
    ats=[dt.datetime.fromisoformat(e['at']) for e in entries]
    for at,(lower,upper) in zip(ats,windows):
        # Clock bracket allows second-precision timestamps; exact private facts need source inspection.
        assert lower-dt.timedelta(seconds=1)<=at<=upper+dt.timedelta(seconds=1), 'event outside actual operation bracket'
    assert ats[0]<ats[1]<ats[2], 'separated operations were assigned a synthetic common timestamp'
    assert dt.datetime.fromisoformat(created['created_at'])==ats[0]
    terms=entries[0]['accepted_terms']
    assert terms['policy_version']==0 and terms['capacities']=={'t1':4,'t2':4}
    assert terms['slot_minutes']==30 and terms['reservation_duration_minutes']==90
    assert terms['cancellation_cutoff_minutes']==0
    assert terms['opening_hours']==fixture()['restaurants'][0]['opening_hours']
    assert all(same(terms,e['accepted_terms']) for e in entries)


def run(source, target, source_stage):
    source.reset()
    created,w1=timed(lambda: source.expect('POST','/reservations',key='legacy-precise',status=201,raw_body=numeric_body('1.0000000000000001')))
    ref=created['reference']
    # Distinguish real operation times even when service timestamps use whole seconds.
    time.sleep(1.1)
    changed,w2=timed(lambda: source.expect('PATCH','/reservations/'+ref,{'table_id':'t2'}))
    time.sleep(1.1)
    cancelled,w3=timed(lambda: source.expect('POST','/reservations/'+ref+'/cancel',{}))
    token=source.token
    export=source.expect('GET','/_test/export',raw_response=True)
    source.reset()  # Source world removed before target import; full source-offline isolation is a separate gate.
    target.expect('POST','/_test/import',status=204,raw_body=export)
    target.token=token
    current=target.expect('GET','/reservations/'+ref)
    assert same(cancelled,{k:current[k] for k in cancelled}), 'legacy fields changed during upgrade'
    assert current['status']=='cancelled' and current['table_id']=='t2'
    assert current['reservation_id']==created['reservation_id'] and current['created_at']==created['created_at']
    entries=target.expect('GET','/reservations/'+ref+'/history')['entries']
    check_history(entries,created,[w1,w2,w3])
    assert current['revision']==3 and same(current['accepted_terms'],entries[-1]['accepted_terms'])
    replay=target.expect('POST','/reservations',key='legacy-precise',raw_body=numeric_body('1.00000000000000010'))
    assert same(created,replay), 'original receipt gained live values/new fields'
    for token_value in ['1.0','true']:
        target.expect('POST','/reservations',key='legacy-precise',status=409,code='idempotency_key_reuse',raw_body=numeric_body(token_value))
    assert same(entries,target.expect('GET','/reservations/'+ref+'/history')['entries'])
    # Existing S3 history must be exact across another export/import, not reprojected.
    upgraded=target.expect('GET','/_test/export',raw_response=True)
    target.expect('POST','/_test/import',status=204,raw_body=upgraded)
    assert same(entries,target.expect('GET','/reservations/'+ref+'/history')['entries'])
    # Separate fixture-initialization control, never infer a prior cancellation from a seeded status.
    seeded=fixture()
    record=source.booking(id='seed1',reference='SEED01',user_id='u1')
    if source_stage==2: record['status']='cancelled'
    seeded['reservations']=[record]
    source.reset(seeded)
    seed_token=source.token
    export=source.expect('GET','/_test/export',raw_response=True)
    target.expect('POST','/_test/import',status=204,raw_body=export)
    target.token=seed_token
    baseline=target.expect('GET','/reservations/SEED01/history')['entries']
    assert len(baseline)==1 and baseline[0]['event']=='created'
    assert baseline[0]['revision']==1
    assert baseline[0].get('provenance')=='fixture_seed'
    assert baseline[0].get('initial_status')==('cancelled' if source_stage==2 else 'confirmed')
    assert not any(e['event']=='cancelled' for e in baseline), 'invented seeded cancellation'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for flag in ['source-url','target-url','source-candidate','target-candidate','out']: p.add_argument('--'+flag,required=True)
    p.add_argument('--source-stage',type=int,choices=[1,2],default=1)
    args=p.parse_args()
    for revision in [args.source_candidate,args.target_candidate]:
        if not re.fullmatch('[0-9a-f]{40}',revision): p.error('full 40-hex revisions required')
    if args.source_url.rstrip('/')==args.target_url.rstrip('/'): p.error('distinct service URLs required')
    record={'work_item':'S3-QA','responsible_handle':'@frankzhu94/factory-qa','source_candidate':args.source_candidate,'target_candidate':args.target_candidate,'case_ids':['Q06','Q25','Q32'],'started_at_utc':now().isoformat(),'layer':'HTTP upgrade; private event times bracketed, no offline/container/resource claim'}
    with pathlib.Path(args.out).open('x') as out:
        try:
            run(Checks(args.source_url),Checks(args.target_url),args.source_stage)
            record['status']='PASS'
        except Exception as e:
            record['status']='FAIL'
            record['detail']=type(e).__name__+(': '+str(e) if isinstance(e,AssertionError) else '; transport/parsing error details withheld')
        record['finished_at_utc']=now().isoformat()
        json.dump(record,out,indent=2)
    print(json.dumps({'status':record['status'],'results_path':str(pathlib.Path(args.out).resolve())}))
    return int(record['status']=='FAIL')

if __name__=='__main__': raise SystemExit(main())
