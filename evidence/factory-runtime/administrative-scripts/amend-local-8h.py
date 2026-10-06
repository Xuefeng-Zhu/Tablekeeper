#!/usr/bin/env python3
"""Local clock continuation: dry-run by default, preserve all prior authority."""
import argparse,ast,copy,json,os,shutil,sys,tempfile,uuid,time
from pathlib import Path
O=Path(__file__).resolve().parent
S=O.parent/'local-sol-transport-source-20261005'
D=O.parent/'local-sol-continuation-source-20261006'
sys.path.insert(0,str(D))
from factorykit.common import canonical,digest,load_config,artifact_path,verify_sources,write_json,utc_now
from factorykit.source_snapshot import source_fingerprint
from factorykit.runtime import launch_lock
from factorykit.tasks import generate
import importlib.util
spec=importlib.util.spec_from_file_location('prior_amend',O/'amend-token-300m.py')
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
ROOM='4084182d-0b85-46d6-a137-f0bd7d44b498'
TRIGGER='b065cc30-6e77-4e05-9422-719b3b614dd4'
ACK='75ed753b-f1ce-4f8c-a9d5-6167a4388698'
TURN='reviewer:160:'+TRIGGER
CAP=656587855
BASE=356587855
RECEIPT=O/'readiness/local-8h-amendment.json'
MANIFEST=O/'readiness/local-8h-reconciliation.json'
def need(v,m):
    if not v:raise ValueError(m)
def read(p):return json.loads(Path(p).read_bytes())
def ref(p):return {'path':str(Path(p).resolve()),'sha256':digest(Path(p))}
def inventory(p):return {str(f.relative_to(p)):digest(f) for f in p.rglob('*') if f.is_file()}
def inspect():
    need(not RECEIPT.exists() and not MANIFEST.exists(),'This amendment has already been applied; never repeat')
    old=load_config(O/'factory.yaml');new=copy.deepcopy(old)
    need(old['paths']['factory']==str(S) and old['launch']['practice_mode'] is True,'Original local source or scope changed')
    need(old['band']['rehearsal_room_id']==ROOM and old['budgets']['max_total_tokens']==CAP,'Original room/token allowance changed')
    need(old['budgets']['overall_timeout_seconds']==220339 and old['budgets']['stage_timeout_seconds']==14400,'Original clocks changed')
    new['paths']['factory']=str(D)
    for field in ('overall_timeout_seconds','stage_timeout_seconds'):new['budgets'][field]+=28800
    owner=read(O/'runtime/owner.json');need(owner['status']=='stopped' and owner['mode']=='rehearsal','Original runtime must be stopped')
    prior.require_process_gone(owner['parent'])
    for ident in owner['children']:prior.require_process_gone(ident)
    ledgerpath=O/'runtime/budget-subscription.json';ledgerraw=ledgerpath.read_bytes();ledger=json.loads(ledgerraw)
    need(ledger['stopped_reason']=='overall time budget exhausted' and not ledger['room_stopped_reasons'].get(ROOM),'Only the approved exact overall-clock halt may be cleared')
    need(BASE<=ledger['tokens']<CAP and all(n<old['budgets']['max_turns_per_seat'] for n in ledger['turns'].values()),'Other original budget exhausted')
    need(time.time()<ledger['started_epoch']+new['budgets']['overall_timeout_seconds'] and time.time()<ledger['room_started_epochs'][ROOM]+new['budgets']['stage_timeout_seconds'],'Approved added time has already expired')
    workflowpath=O/f'runtime/workflow-{ROOM}.json';workflow=read(workflowpath)
    need(all(v['status']=='completed' or k==TURN and v['status']=='interrupted' and v['reason_code']=='interrupted' for k,v in workflow['turns'].items()),'Another incomplete callback requires explicit reconciliation')
    need(workflow['turns'][TURN]['trigger_event_id']==TRIGGER,'Original trigger changed')
    incident=workflow['incidents']['turn:'+TURN]
    need(incident['failed'] is True and incident['closed'] is False and incident['blocked'] is None and not incident['deliveries'],'Interrupted incident has a conflicting outcome')
    need(not owner['workflow']['active_turn_ids'] and not owner['workflow']['sdk_execution_busy'] and not owner['workflow']['unknown_notices'],'Runtime or notice authority is uncertain')
    bad=[]
    for p in (O/'runtime').glob(f'handoffs-{ROOM}-*.json'):
        for key,b in read(p)['batches'].items():
            need(b['receipt_status']!='claimed','Unknown ACK send prevents amendment')
            if b['status'] in ('claimed','blocked'):bad.append((p,key,b))
    need(len(bad)==1,'Exactly the reviewed interrupted claim is required')
    jp,key,b=bad[0];need(jp.name.endswith('-reviewer.json') and b['status']=='blocked' and b['receipt_status']=='confirmed' and b['trigger_event_id']==TRIGGER and b['receipt_event_id']==ACK,'Interrupted handoff evidence changed')
    board=read(O/f'runtime/task-board-{ROOM}.json');need(not any(v.get('pending') for v in board['items'].values()),'Task board has an unknown write')
    verdict=Path(old['paths']['rehearsal'])/'.evidence/reviewer-s2-release-20261006T0653Z/record.json'
    v=read(verdict);need(v['decision']=='ACCEPTED' and v['integrated_candidate']=='108bcd4c48fa4fb4d7dedf286e94c8e4937f7f10' and v['handoff_event']==TRIGGER and v['receipt_event']==ACK,'Stage2 evidence differs')
    profilepath=O/'readiness/codex-config-continuation-revalidation.json';profile=read(profilepath)
    need(profile['rpc_status']=='PASS' and profile['host_config_unchanged'] is True and profile['authentication']['account_type']=='chatgpt','Fresh selected profile proof is missing')
    need(profile['configured_profile']==old['runtime']['permission_profile'] and profile['current_sha256']==digest(Path(profile['external_config_path'])),'Fresh selected profile or host changed')
    need({new['paths']['rehearsal'],str(D)}<={r['cwd'] for r in profile['fresh_effective_reads']},'Fresh profile was not read for exact new source/product cwd')
    # Derivation does not change executable, adapter settings or permissions.
    def node(path,name):return ast.dump(next(n for n in ast.parse(path.read_text()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name))
    need(node(S/'factorykit/runtime.py','adapter_config')==node(D/'factorykit/runtime.py','adapter_config'),'Adapter configuration changed')
    for name in ('factorykit/validation.py','factorykit/harnesses.py','factorykit/permissions.py'):
        need(digest(S/name)==digest(D/name),'Permission/harness implementation changed: '+name)
    reviewpath=O/'readiness/local-8h-source-review.json'
    review=read(reviewpath);need(review['status']=='PASS','Independent source review did not pass')
    for r in review['source_files']:need(digest(Path(r['path']))==r['sha256'],'Reviewed source changed')
    sdkpath=O/'readiness/reviewer160-sdk-reconciliation-evidence.json';sdk=read(sdkpath)
    need(sdk['status']=='VERIFIED_READ_ONLY' and sdk['trigger_event_id']==TRIGGER and sdk['trigger_sdk_status']=='failed','Actual SDK interrupted attempt must first have a confirmed failed disposition')
    need(sdk['receipt_event_id']==ACK,'Actual ACK differs')
    lockpath=artifact_path(old,'source_lock');lock=read(lockpath)
    failures=verify_sources(old);need(failures in ([],['Locked reference document changed or missing: config.toml']),'Other pinned sources changed: '+str(failures))
    observations=read(O/'readiness/observations.json');need(observations['configuration_sha256']==digest(canonical(old)),'Previous capability binding differs')
    for row in observations['observations']:
        need(row['status']=='PASS' and row['observed'] is True and row['observed_at'],'Original actual capability observation is not confirmed')
        for r in row['evidence']:need(digest(Path(r['path']))==r['sha256'],'Original capability evidence changed')
    return locals()
def apply(x):
    stamp=utc_now();backup=O/('deadline-amendment-8h-before-'+uuid.uuid4().hex)
    backup.mkdir(mode=0o700)
    for name in ('factory.yaml','runtime','readiness','tasks','source-lock.json','authorization.json','auth','authentication'):
        src=O/name
        if src.is_dir():shutil.copytree(src,backup/name)
        elif src.is_file():shutil.copy2(src,backup/name)
    write_json(backup/'original-config.json',x['old']);write_json(backup/'archive-manifest.json',{'at':stamp,'files':inventory(backup)})
    before=inventory(O/'runtime');dispatch=inventory(O/'dispatch')
    need(x['ledgerpath'].read_bytes()==x['ledgerraw'],'Ledger changed during stopped amendment')
    lock=copy.deepcopy(x['lock'])
    for row in lock['instruction_inputs']:
        if Path(row['path']).name=='config.toml':
            row.setdefault('hash_history',[]).append({'sha256':row['sha256'],'superseded_at':stamp,'reason':'Fresh exact selected effective profile/account RPC proof; prior raw TOML unavailable, no unrelated-drift claim.'})
            row['sha256']=x['profile']['current_sha256'];row['revalidation_evidence']=ref(x['profilepath'])
    write_json(x['lockpath'],lock)
    need(not verify_sources(x['new']),'New preparation sources failed verification')
    sf=source_fingerprint(x['new']);cf=digest(canonical(x['new']))
    reconciliation={'status':'APPROVED_TERMINAL_NO_REPLAY','at':stamp,'authorization':{'source':'Direct user answer in current Codex chat','user_answer':'Extend 8 hours and resume','additional_seconds':28800},'configuration_sha256':cf,'room_id':ROOM,'participant_ids':sorted(s['agent_id'] for s in x['old']['seats']), 'original_configuration':ref(backup/'original-config.json'),'original_workflow':ref(backup/x['workflowpath'].relative_to(O)),'sdk_evidence':ref(x['sdkpath']),'entries':[{'seat':'reviewer','turn_id':TURN,'batch_key':x['key'],'batch_sha256':digest(canonical(x['b'])),'trigger_event_id':TRIGGER,'receipt_event_id':ACK,'original_journal':ref(backup/x['jp'].relative_to(O)),'acceptance_record':ref(x['verdict'])}]}
    write_json(MANIFEST,reconciliation)
    from factorykit.local_terminal_reconciliation import load_terminal_reconciliations
    load_terminal_reconciliations(x['new'],ROOM,x['workflow'])
    auditpath=O/'readiness/local-8h-permission-revalidation.json'
    audit={'at':stamp,'scope':'Retained actual capability timestamps/outputs; fresh account/config RPC and reviewed journal-only source change. No new capability execution claimed.','old_observations':ref(backup/'readiness/observations.json'),'profile':ref(x['profilepath']),'source_review':ref(x['reviewpath']),'configuration_sha256':cf,'factory_source_sha256':sf,'source_lock_sha256':digest(x['lockpath']),'changes':['both clock caps +28800','isolated factory source','reviewed inherited config pin']}
    write_json(auditpath,audit)
    obs=copy.deepcopy(x['observations']);obs.update(configuration_sha256=cf,factory_source_sha256=sf,source_lock_sha256=digest(x['lockpath']),revalidated_at=stamp,revalidation=ref(auditpath))
    for row in obs['observations']:
        row['factory_source_sha256']=sf;row['evidence'].append(ref(auditpath))
    import yaml
    prior.replace_bytes(O/'factory.yaml',yaml.safe_dump(x['new'],sort_keys=False).encode())
    cleared=copy.deepcopy(x['ledger']);cleared['stopped_reason']=None
    prior.replace_bytes(x['ledgerpath'],canonical(cleared))
    write_json(O/'readiness/observations.json',obs)
    generate(x['new'])
    need(inventory(O/'dispatch')==dispatch,'Original dispatched packet changed')
    for name,sha in before.items():
        if name!='budget-subscription.json':need(digest(O/'runtime'/name)==sha,'Runtime authority changed: '+name)
    need(read(x['ledgerpath'])==cleared,'Ledger changed beyond approved exact clock halt clear')
    receipt={'status':'APPLIED_NOT_LAUNCHED','at':stamp,'backup':str(backup),'authorization':reconciliation['authorization'],'old_configuration':ref(backup/'factory.yaml'),'new_configuration':ref(O/'factory.yaml'),'factory_source_sha256':sf,'source_review':ref(x['reviewpath']),'reconciliation':ref(MANIFEST),'permission_revalidation':ref(auditpath),'recorded_tokens_preserved':x['ledger']['tokens'],'absolute_cap':CAP,'local_total_cap':300000000,'remaining_tokens':CAP-x['ledger']['tokens'],'original_epochs_preserved':{'started_epoch':x['ledger']['started_epoch'],'room_started_epoch':x['ledger']['room_started_epochs'][ROOM]},'timeouts':{k:{'before':x['old']['budgets'][k],'after':x['new']['budgets'][k]} for k in ('overall_timeout_seconds','stage_timeout_seconds')},'sole_ledger_delta':'stopped_reason overall time budget exhausted -> null','runtime_unchanged_except_ledger_halt':True,'dispatch':dispatch,'required_next':'Fresh registration proof, normal held rehearsal start, verify sealed source/all seven contexts, release existing queue; no task redispatch'}
    write_json(RECEIPT,receipt)
    return receipt
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--apply',action='store_true');args=a.parse_args()
    try:
        with launch_lock(load_config(O/'factory.yaml')):
            x=inspect();result=apply(x) if args.apply else {'status':'INSPECTED_NOT_APPLIED','new_budgets':x['new']['budgets'],'recorded_tokens':x['ledger']['tokens']}
        print(json.dumps(result,indent=2))
    except Exception as e:
        print(json.dumps({'status':'BLOCKED_PRESERVE_STATE','detail':str(e)}));raise SystemExit(1)
