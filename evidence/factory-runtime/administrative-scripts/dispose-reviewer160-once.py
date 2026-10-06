#!/usr/bin/env python3
"""Record one known interrupted SDK attempt failure; never replay or mark success."""
import argparse,asyncio,json,os,sys,importlib.util,hashlib,logging
from pathlib import Path
O=Path(__file__).resolve().parent;D=O.parent/'local-sol-continuation-source-20261006'
sys.path.insert(0,str(D))
from factorykit.common import load_config,digest,canonical,utc_now,write_json
from factorykit.runtime import credentials,launch_lock
from band.client.rest import AsyncRestClient
import httpx
spec=importlib.util.spec_from_file_location('guard',O/'amend-token-300m.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
ROOM='4084182d-0b85-46d6-a137-f0bd7d44b498';EVENT='b065cc30-6e77-4e05-9422-719b3b614dd4';ACK='75ed753b-f1ce-4f8c-a9d5-6167a4388698'
ATTEMPT=O/'readiness/reviewer160-failure-disposition-attempt.json';RESPONSE=O/'readiness/reviewer160-failure-disposition-response.json'
PROOF=O/'readiness/reviewer160-sdk-reconciliation-evidence.json'
OPTS={'max_retries':0,'timeout_in_seconds':25}
def need(v,m):
    if not v:raise ValueError(m)
def read(p):return json.loads(p.read_bytes())
def ref(p):return {'path':str(p.resolve()),'sha256':digest(p)}
def guard_local(c):
    need(c['launch']['practice_mode'] is True and c['band']['rehearsal_room_id']==ROOM,'Local room changed')
    owner=read(O/'runtime/owner.json');need(owner['status']=='stopped' and owner['mode']=='rehearsal','Runtime must be stopped')
    guard.require_process_gone(owner['parent'])
    for ident in owner['children']:guard.require_process_gone(ident)
    need(not owner['workflow']['active_turn_ids'] and not owner['workflow']['sdk_execution_busy'],'Old SDK execution has not drained')
    workflow=read(O/f'runtime/workflow-{ROOM}.json');turn=workflow['turns']['reviewer:160:'+EVENT]
    need(turn['status']=='interrupted' and turn['reason_code']=='interrupted','Original callback was not known interrupted')
    ledger=read(O/'runtime/budget-subscription.json');need(ledger['stopped_reason']=='overall time budget exhausted','Original halt differs')
    journal=read(O/f'runtime/handoffs-{ROOM}-reviewer.json')
    batch=next(b for b in journal['batches'].values() if b['trigger_event_id']==EVENT)
    need(batch['status']=='blocked' and batch['receipt_status']=='confirmed' and batch['receipt_event_id']==ACK,'Original interrupted batch or confirmed ACK changed')
    return batch
async def messages(rest,status,event):
    out=await rest.agent_api_messages.list_agent_messages(chat_id=ROOM,status=status,sort_order='desc',limit=100,request_options=OPTS)
    data=out.data
    if isinstance(data,list):rows=data
    else:rows=data.messages
    return next((m for m in rows if m.id==event),None)
async def run(apply):
    c=load_config(O/'factory.yaml');batch=guard_local(c);creds=credentials(c)
    reviewer=next(s for s in c['seats'] if s['id']=='reviewer');pm=next(s for s in c['seats'] if s['id']=='pm')
    async with httpx.AsyncClient(timeout=25) as transport:
        rest=AsyncRestClient(base_url=c['band']['rest_url'],api_key=creds['reviewer']['api_key'],httpx_client=transport)
        pmrest=AsyncRestClient(base_url=c['band']['rest_url'],api_key=creds['pm']['api_key'],httpx_client=transport)
        me=(await rest.agent_api_identity.get_agent_me(request_options=OPTS)).data
        need(me.id==reviewer['agent_id'] and me.handle.lstrip('@')==reviewer['handle'].lstrip('@'),'Reviewer authenticated identity differs')
        participants=(await rest.agent_api_participants.list_agent_chat_participants(chat_id=ROOM,request_options=OPTS)).data
        peers={p.id:p for p in participants}
        need(all(s['agent_id'] in peers and peers[s['agent_id']].handle.lstrip('@')==s['handle'].lstrip('@') for s in c['seats']),'Current roster differs')
        trigger=await messages(rest,'processing',EVENT);ack=await messages(pmrest,'processing',ACK)
        if ack is None:ack=await messages(pmrest,'processed',ACK)
        need(trigger is not None and trigger.sender_id==pm['agent_id'],'Exact old trigger is not known processing')
        event=read(O/f'runtime/handoffs-{ROOM}-reviewer.json')['events'][EVENT]
        need(trigger.content==event['content'],'Actual SDK trigger bytes differ')
        expected=f"HANDOFF-ACK delivery {batch['binding']['delivery']}; SHA-256 {batch['binding']['digest']}; sender @[[{pm['agent_id']}]]"
        need(ack is not None and ack.sender_id==reviewer['agent_id'] and ack.content==expected,'Actual SDK ACK content/sender differs')
        mentions=[m.get("id") if isinstance(m,dict) else m.id for m in ack.metadata.mentions]
        need(mentions==[pm['agent_id']],'Actual SDK ACK recipient differs')
        before={'status':'VERIFIED_PROCESSING_INTERRUPTED','at':utc_now(),'room_id':ROOM,'trigger_event_id':EVENT,'receipt_event_id':ACK,'authenticated_reviewer':me.id,'participant_ids':sorted(s['agent_id'] for s in c['seats']),'trigger_content_sha256':hashlib.sha256(trigger.content.encode()).hexdigest(),'receipt_content_sha256':hashlib.sha256(ack.content.encode()).hexdigest(),'local_owner':ref(O/'runtime/owner.json'),'local_workflow':ref(O/f'runtime/workflow-{ROOM}.json'),'local_journal':ref(O/f'runtime/handoffs-{ROOM}-reviewer.json'),'action':'SDK mark_agent_message_failed once; exact known interrupted attempt only; no model or direct mark_processed'}
        if not apply:return {'status':'INSPECTED_NOT_APPLIED','proof':before}
        guard_local(c)
        fd=os.open(ATTEMPT,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(canonical(before));stream.flush();os.fsync(stream.fileno())
        response=await rest.agent_api_messages.mark_agent_message_failed(chat_id=ROOM,id=EVENT,error='Operator reconciliation: original Reviewer160 callback interrupted by approved overall clock halt; local failed claim preserved; do not replay original callback.',request_options=OPTS)
        need(response is not None,'SDK failure disposition response missing; do not retry consumed attempt')
        write_json(RESPONSE,{'at':utc_now(),'method':'mark_agent_message_failed','event_id':EVENT,'response':response.model_dump(mode='json',exclude_none=True),'attempt':ref(ATTEMPT),'scope':'Records interrupted attempt failure only. No mark_processed or message send.'})
        failed=await messages(rest,'failed',EVENT);processing=await messages(rest,'processing',EVENT)
        need(failed is not None and processing is None,'SDK failed state not freshly confirmed; preserve consumed attempt')
        ack_processing=await messages(pmrest,'processing',ACK)
        ack_state='processing' if ack_processing is not None else 'processed'
        proof={'status':'VERIFIED_READ_ONLY','at':utc_now(),'room_id':ROOM,'trigger_event_id':EVENT,'receipt_event_id':ACK,'recipient_id':reviewer['agent_id'],'trigger_sdk_status':'failed','receipt_sdk_status':ack_state,'receipt_sender_id':reviewer['agent_id'],'receipt_recipient_id':pm['agent_id'],'receipt_content_sha256':hashlib.sha256(ack.content.encode()).hexdigest(),'evidence':[ref(ATTEMPT),ref(RESPONSE),ref(O/'readiness/reviewer160-sdk-status-20261006.json')],'limits':'Receipt existence/send is confirmed; recipient processing is not receipt success or task acceptance. Failed original model attempt remains retained. Next exact transport-only skip suppresses replay; new watchdog recovery event admits any further model work.'}
        # Existing BLOCKED report is retained separately; never overwrite it.
        if PROOF.exists():
            old=PROOF.with_name('reviewer160-sdk-reconciliation-evidence-before-failure.json')
            need(not old.exists(),'Prior proof archive already exists');os.rename(PROOF,old)
        write_json(PROOF,proof)
        return {'status':'FAILED_ATTEMPT_CONFIRMED','receipt':str(RESPONSE),'proof':str(PROOF)}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args();logging.disable(logging.CRITICAL)
    try:
        with launch_lock(load_config(O/'factory.yaml')):result=asyncio.run(run(a.apply))
        print(json.dumps(result,indent=2))
    except Exception as e:
        print(json.dumps({'status':'BLOCKED_NO_AUTOMATIC_RETRY','error_type':type(e).__name__,'detail':str(e) if isinstance(e,(ValueError,KeyError,AttributeError)) else 'SDK operation failed; preserve exclusive attempt and inspect status; no raw provider body printed'}));raise SystemExit(1)
