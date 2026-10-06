#!/usr/bin/env python3
"""Dry-run by default: preserve all authority and create one first-admission journal."""
import argparse, ast, copy, importlib.util, json, shutil, sys, time, uuid
from pathlib import Path
O=Path(__file__).resolve().parent
D=O.parent/'local-sol-continuation-source-20261006'
T=O.parent/'local-sol-turn-source-20261006'
sys.path.insert(0,str(T))
from factorykit.common import artifact_path,canonical,digest,load_config,utc_now,verify_sources,write_json
from factorykit.source_snapshot import source_fingerprint
from factorykit.runtime import launch_lock
from factorykit.tasks import generate
spec=importlib.util.spec_from_file_location('prior',O/'amend-token-300m.py');prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
ROOM='4084182d-0b85-46d6-a137-f0bd7d44b498'
EVENT='29f45c31-acb1-447e-9f27-7be641802f84'
KEY='02e4a78d-7d9d-4c77-bf32-b3810a9cfacb:s3-release-review-accepted-001'
THREAD='01a10f2e-841b-70c3-8689-33ab5149e1c7'
RECEIPT=O/'readiness/local-turn-1000-amendment.json'
MANIFEST=O/'readiness/local-turn-1000-reconciliation.json'
AUTH={'user_answer':'can you increate the turn limit','old_cap':769,'new_cap':1000}
def need(v,m):
    if not v:raise ValueError(m)
def read(p):return json.loads(Path(p).read_bytes())
def ref(p):return {'path':str(Path(p).resolve()),'sha256':digest(Path(p))}
def files(p):return {str(f.relative_to(p)):digest(f) for f in p.rglob('*') if f.is_file()}
def inspect(require_review=True,require_failed=True):
    need(not RECEIPT.exists() and not MANIFEST.exists(),'Amendment already exists; never reset or repeat')
    old=load_config(O/'factory.yaml');new=copy.deepcopy(old)
    need(old['paths']['factory']==str(D) and old['launch']['practice_mode'] is True and old['band']['rehearsal_room_id']==ROOM,'Original local source/scope changed')
    need(old['budgets']['max_turns_per_seat']==769 and old['budgets']['max_active_seats']==1 and old['budgets']['max_total_tokens']==656587855,'Original turn/token limits changed')
    need(old['budgets']['overall_timeout_seconds']==249139 and old['budgets']['stage_timeout_seconds']==43200,'Approved clocks changed')
    need(len(old['seats'])==7 and old['runtime']['model']=='gpt-6.1-sol' and all(s['model']=='gpt-6.1-sol' and s['rehearsal_cwd']==old['paths']['rehearsal'] for s in old['seats']),'Model/workspace/seats changed')
    new['paths']['factory']=str(T);new['budgets']['max_turns_per_seat']=1000
    owner=read(O/'runtime/owner.json');need(owner['status']=='stopped' and owner['mode']=='rehearsal','Owner must be stopped')
    prior.require_process_gone(owner['parent'])
    for p in owner['children']:prior.require_process_gone(p)
    need(not owner['workflow']['active_turn_ids'] and not owner['workflow']['sdk_execution_busy'] and not owner['workflow']['unknown_notices'],'Old runtime/notice authority has not drained')
    ledgerpath=O/'runtime/budget-subscription.json';ledgerraw=ledgerpath.read_bytes();ledger=json.loads(ledgerraw)
    need(ledger['tokens']==523174913 and ledger['turns']['pm']==769 and all(n<769 for k,n in ledger['turns'].items() if k!='pm'),'Stopped consumption or turn counters changed')
    need(ledger['stopped_reason']=='handoff model claim did not complete; preserve before retry' and not ledger['room_stopped_reasons'].get(ROOM),'Unexpected halt prevents amendment')
    need(time.time()<ledger['started_epoch']+new['budgets']['overall_timeout_seconds'] and time.time()<ledger['room_started_epochs'][ROOM]+new['budgets']['stage_timeout_seconds'],'Original approved clock expired')
    workflowpath=O/f'runtime/workflow-{ROOM}.json';workflow=read(workflowpath)
    need(not any(t['trigger_event_id']==EVENT for t in workflow['turns'].values()),'Exact deferred event already had a model turn')
    oldfailed='reviewer:160:b065cc30-6e77-4e05-9422-719b3b614dd4'
    need(all(t['status']=='completed' or k==oldfailed and t['status']=='interrupted' and t['reason_code']=='interrupted' for k,t in workflow['turns'].items()),'Another incomplete callback prevents restart')
    pmjournal=O/f'runtime/handoffs-{ROOM}-pm.json';journal=read(pmjournal);batch=journal['batches'][KEY]
    need(batch['status']=='blocked' and batch['receipt_status']=='unsent' and batch['receipt_event_id'] is None and batch['trigger_event_id']==EVENT,'Deferred never-acknowledged batch changed')
    bad=[]
    for p in (O/'runtime').glob(f'handoffs-{ROOM}-*.json'):
        for k,b in read(p)['batches'].items():
            need(b['receipt_status']!='claimed','Unknown ACK attempt prevents continuation')
            if b['status'] in ('blocked','claimed'):bad.append((p.name,k))
    need(len(bad)==2 and (pmjournal.name,KEY) in bad,'Unexpected retained claim prevents continuation')
    from factorykit.local_terminal_reconciliation import load_terminal_reconciliations
    oldmanifest=read(O/'readiness/local-8h-reconciliation.json');acceptance_ref=oldmanifest['entries'][0]['acceptance_record']
    retained_acceptance=O/'readiness/local-turn-1000-retained-stage2-verdict.json'
    need(digest(retained_acceptance)==acceptance_ref['sha256'],'Exact originally referenced Stage2 acceptance bytes unavailable')
    terminal=load_terminal_reconciliations(old,ROOM,workflow,acceptance_relocations={acceptance_ref['path']:ref(retained_acceptance)})
    need(set(terminal)=={'reviewer'},'Prior Reviewer160 terminal authority differs')
    board=read(O/f'runtime/task-board-{ROOM}.json');need(not any(v.get('pending') for v in board['items'].values()),'Unknown task-board write remains')
    sdkpath=O/('readiness/pm-stage3-release-never-admitted-sdk-proof-after-failure.json' if require_failed else 'readiness/pm-stage3-release-never-admitted-sdk-proof.json');sdk=read(sdkpath)
    need(sdk['status']=='VERIFIED_NEVER_ADMITTED_READ_ONLY' and sdk['trigger_event_id']==EVENT and sdk['codex_thread_id']==THREAD,'Fresh SDK/thread proof missing')
    need(sdk['trigger_sdk_status'] in (('failed','pending') if require_failed else ('processing','failed','pending')),'Exact prior SDK processing attempt must have truthful failed disposition')
    profilepath=O/'readiness/codex-config-turn1000-revalidation.json';profile=read(profilepath)
    need(profile['rpc_status']=='PASS' and profile['host_config_unchanged'] is True and profile['authentication']['account_type']=='chatgpt','Fresh exact profile/account proof missing')
    need(profile['configured_profile']==old['runtime']['permission_profile'] and profile['current_sha256']==digest(Path(profile['external_config_path'])),'Host/effective profile changed')
    need({str(T),old['paths']['rehearsal']}<={r['cwd'] for r in profile['fresh_effective_reads']},'Exact new source/product profile was not read')
    def node(p,n):return ast.dump(next(t for t in ast.parse(p.read_text()).body if isinstance(t,(ast.FunctionDef,ast.AsyncFunctionDef)) and t.name==n))
    need(node(D/'factorykit/runtime.py','adapter_config')==node(T/'factorykit/runtime.py','adapter_config'),'Adapter/profile configuration changed')
    for n in ('factorykit/validation.py','factorykit/harnesses.py','factorykit/permissions.py','factorykit/handoff_batching.py'):
        need(digest(D/n)==digest(T/n),'Unreviewed unrelated implementation changed: '+n)
    reviewpath=O/'readiness/local-turn-1000-source-review.json'
    if require_review:
        review=read(reviewpath);need(review['status']=='PASS','Independent source review missing')
        for r in review['source_files']:need(digest(Path(r['path']))==r['sha256'],'Reviewed source changed')
    lockpath=artifact_path(old,'source_lock');lock=read(lockpath);failures=verify_sources(old)
    need(failures in ([],['Locked reference document changed or missing: config.toml']),'Other pinned inputs changed: '+str(failures))
    observations=read(O/'readiness/observations.json');need(observations['configuration_sha256']==digest(canonical(old)) and observations['factory_source_sha256']==source_fingerprint(old) and observations['source_lock_sha256']==digest(lockpath),'Original capability binding changed')
    for r in observations['observations']:
        need(r['status']=='PASS' and r['observed'] is True and r['observed_at'],'Original actual capability proof missing')
        for e in r['evidence']:need(digest(Path(e['path']))==e['sha256'],'Capability evidence changed')
    return locals()
def apply(x):
    stamp=utc_now();backup=O/('turn-amendment-1000-before-'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
    for n in ('factory.yaml','runtime','readiness','tasks','source-lock.json','authorization.json','auth','authentication'):
        p=O/n
        if p.is_dir():shutil.copytree(p,backup/n)
        elif p.is_file():shutil.copy2(p,backup/n)
    write_json(backup/'original-config.json',x['old']);write_json(backup/'archive-manifest.json',{'at':stamp,'files':files(backup)})
    before=files(O/'runtime');dispatch=files(O/'dispatch');need(x['ledgerpath'].read_bytes()==x['ledgerraw'],'Ledger changed during stopped amendment')
    lock=copy.deepcopy(x['lock'])
    for r in lock['instruction_inputs']:
        if Path(r['path']).name=='config.toml' and r['sha256']!=x['profile']['current_sha256']:
            r.setdefault('hash_history',[]).append({'sha256':r['sha256'],'superseded_at':stamp,'reason':'Fresh exact selected effective profile/account RPC; no unrelated drift assertion'})
            r['sha256']=x['profile']['current_sha256'];r['revalidation_evidence']=ref(x['profilepath'])
    write_json(x['lockpath'],lock);need(not verify_sources(x['new']),'New sources failed verification')
    derived=O/f'runtime/deferred-handoffs-{ROOM}-pm.json';need(not derived.exists(),'Never reset an existing deferred journal')
    initial=copy.deepcopy(x['journal']);initial['batches'][KEY]['status']='ready'
    initialpath=O/'readiness/local-turn-1000-initial-derived-journal.json';write_json(initialpath,initial);write_json(derived,initial)
    sf=source_fingerprint(x['new']);cf=digest(canonical(x['new']))
    acceptancecopy=x['retained_acceptance']
    need(digest(acceptancecopy)==x['acceptance_ref']['sha256'],'Retained exact verdict copy changed')
    manifest={'status':'APPROVED_FIRST_ADMISSION','at':stamp,'authorization':AUTH,'configuration_sha256':cf,'room_id':ROOM,'participant_ids':sorted(s['agent_id'] for s in x['old']['seats']),'original_configuration':ref(backup/'original-config.json'),'stopped_owner':ref(backup/'runtime/owner.json'),'original_ledger':ref(backup/'runtime/budget-subscription.json'),'original_workflow':ref(backup/x['workflowpath'].relative_to(O)),'sdk_evidence':ref(x['sdkpath']),'acceptance_relocations':{x['acceptance_ref']['path']:ref(acceptancecopy)},'entries':[{'seat':'pm','trigger_event_id':EVENT,'batch_key':KEY,'batch_sha256':digest(canonical(x['batch'])),'codex_thread_id':THREAD,'original_journal':ref(x['pmjournal']),'derived_journal':str(derived),'initial_derived_journal':ref(initialpath)}]}
    write_json(MANIFEST,manifest)
    from factorykit.local_deferred_admission import load_deferred_admission
    from factorykit.handoff_batching import HandoffJournal
    baseline,paths,_=load_deferred_admission(x['new'],ROOM,x['workflow'])
    HandoffJournal(paths['pm'],ROOM,next(s['agent_id'] for s in x['new']['seats'] if s['id']=='pm'),manifest['participant_ids'],workflow=x['workflow'])
    auditpath=O/'readiness/local-turn-1000-permission-revalidation.json'
    write_json(auditpath,{'at':stamp,'configuration_sha256':cf,'factory_source_sha256':sf,'source_lock_sha256':digest(x['lockpath']),'original_observations':ref(backup/'readiness/observations.json'),'fresh_selected_profile':ref(x['profilepath']),'source_review':ref(x['reviewpath']),'scope':'Retained actual capability timestamps and outputs; cap-only configuration amendment plus independently reviewed transport recovery source. No new capability execution claimed; adapter/profile/model/network/workspace permissions unchanged.'})
    obs=copy.deepcopy(x['observations']);obs.update(configuration_sha256=cf,factory_source_sha256=sf,source_lock_sha256=digest(x['lockpath']),revalidated_at=stamp,revalidation=ref(auditpath))
    for r in obs['observations']:r['factory_source_sha256']=sf;r['evidence'].append(ref(auditpath))
    import yaml
    prior.replace_bytes(O/'factory.yaml',yaml.safe_dump(x['new'],sort_keys=False).encode());cleared=copy.deepcopy(x['ledger']);cleared['stopped_reason']=None;prior.replace_bytes(x['ledgerpath'],canonical(cleared));write_json(O/'readiness/observations.json',obs)
    generate(x['new']);need(files(O/'dispatch')==dispatch,'Original dispatch changed')
    for n,sha in before.items():
        if n!='budget-subscription.json':need(digest(O/'runtime'/n)==sha,'Original runtime authority changed: '+n)
    need(read(x['ledgerpath'])==cleared,'Unexpected ledger mutation')
    receipt={'status':'APPLIED_NOT_LAUNCHED','at':stamp,'authorization':AUTH,'backup':str(backup),'configuration_sha256':cf,'factory_source_sha256':sf,'recorded_tokens_preserved':x['ledger']['tokens'],'absolute_token_cap':656587855,'local_total_allowance':300000000,'remaining_tokens':656587855-x['ledger']['tokens'],'turn_counters_preserved':x['ledger']['turns'],'sole_ledger_delta':'Exact causal handoff pre-model budget-denial halt -> null; every other field preserved','overall_deadline':x['ledger']['started_epoch']+x['new']['budgets']['overall_timeout_seconds'],'room_deadline':x['ledger']['room_started_epochs'][ROOM]+x['new']['budgets']['stage_timeout_seconds'],'original_pm_blocked_journal':ref(x['pmjournal']),'deferred_first_admission_journal':ref(derived),'reconciliation':ref(MANIFEST),'permission_revalidation':ref(auditpath),'source_review':ref(x['reviewpath']),'dispatch_inventory':dispatch,'scope':'No old model replay, ACK synthesis, acceptance claim, counter/clock reset or human redispatch; normal same-room queue must perform actual first callback/ACK.'}
    write_json(RECEIPT,receipt);return receipt
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args()
    try:
        with launch_lock(load_config(O/'factory.yaml')):
            x=inspect();result=apply(x) if a.apply else {'status':'INSPECTED_NOT_APPLIED','new_cap':1000,'recorded_tokens':x['ledger']['tokens'],'deferred_trigger':EVENT}
        print(json.dumps(result,indent=2))
    except Exception as e:
        print(json.dumps({'status':'BLOCKED_PRESERVE_STATE','detail':str(e)}));raise SystemExit(1)
