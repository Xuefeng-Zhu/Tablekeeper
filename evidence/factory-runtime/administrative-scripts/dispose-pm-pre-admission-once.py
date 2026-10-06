#!/usr/bin/env python3
"""Truthfully fail one denied pre-model SDK attempt; never mark success/send."""
import argparse,asyncio,copy,importlib.util,json,logging,os,sys
from pathlib import Path
O=Path(__file__).resolve().parent;T=O.parent/'local-sol-turn-source-20261006';sys.path.insert(0,str(T))
from factorykit.common import canonical,digest,load_config,utc_now,write_json
from factorykit.runtime import credentials,launch_lock
from band.client.rest import AsyncRestClient
import httpx
spec=importlib.util.spec_from_file_location('amend',O/'amend-turn-1000.py');amend=importlib.util.module_from_spec(spec);spec.loader.exec_module(amend)
EVENT=amend.EVENT;ROOM=amend.ROOM
ATTEMPT=O/'readiness/pm-pre-admission-failure-disposition-attempt.json'
RESPONSE=O/'readiness/pm-pre-admission-failure-disposition-response.json'
AFTER=O/'readiness/pm-stage3-release-never-admitted-sdk-proof-after-failure.json'
OPTS={'max_retries':0,'timeout_in_seconds':25}
def need(v,m):
    if not v:raise ValueError(m)
def ref(p):return {'path':str(p.resolve()),'sha256':digest(p)}
async def get(rest,status):
    result=await rest.agent_api_messages.list_agent_messages(chat_id=ROOM,status=status,sort_order='desc',limit=100,request_options=OPTS)
    rows=result.data if isinstance(result.data,list) else result.data.messages
    return next((m for m in rows if m.id==EVENT),None)
async def run(apply):
    x=amend.inspect(require_review=True,require_failed=False);c=x['old'];creds=credentials(c)
    pm=next(s for s in c['seats'] if s['id']=='pm');sender=next(s for s in c['seats'] if s['id']=='reviewer')
    async with httpx.AsyncClient(timeout=25) as client:
        rest=AsyncRestClient(base_url=c['band']['rest_url'],api_key=creds['pm']['api_key'],httpx_client=client)
        me=(await rest.agent_api_identity.get_agent_me(request_options=OPTS)).data
        need(me.id==pm['agent_id'] and me.handle.lstrip('@')==pm['handle'].lstrip('@'),'Authenticated PM differs')
        peers=(await rest.agent_api_participants.list_agent_chat_participants(chat_id=ROOM,request_options=OPTS)).data
        roster={p.id:p for p in peers};need(all(s['agent_id'] in roster and roster[s['agent_id']].handle.lstrip('@')==s['handle'].lstrip('@') for s in c['seats']),'Actual room roster differs')
        trigger=await get(rest,'processing');need(trigger is not None and trigger.sender_id==sender['agent_id'],'Exact denied attempt is not processing; inspect without mutation')
        event=x['journal']['events'][EVENT];need(trigger.content==event['content'],'Actual SDK trigger bytes differ')
        mentions=[m.get('id') if isinstance(m,dict) else m.id for m in trigger.metadata.mentions]
        need(mentions==[pm['agent_id']],'Actual recipient differs')
        before={'status':'VERIFIED_PROCESSING_NEVER_ADMITTED','at':utc_now(),'authorization':amend.AUTH,'room_id':ROOM,'event_id':EVENT,'sender_id':sender['agent_id'],'recipient_id':pm['agent_id'],'content_sha256':digest(trigger.content.encode()),'original_owner':ref(O/'runtime/owner.json'),'original_ledger':ref(x['ledgerpath']),'original_workflow':ref(x['workflowpath']),'original_blocked_journal':ref(x['pmjournal']),'sdk_before':ref(x['sdkpath']),'source_review':ref(x['reviewpath']),'scope':'Finish the denied SDK processing attempt as failed, preserving blocked claim and no-model outcome. A separate reviewed journal may admit its first real model turn under raised cap. No mark_processed, ACK or message send.'}
        if not apply:return {'status':'INSPECTED_NOT_APPLIED','before':before}
        need(not RESPONSE.exists() and not AFTER.exists(),'Outcome already exists; no retry')
        amend.inspect(require_review=True,require_failed=False)
        fd=os.open(ATTEMPT,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as f:f.write(canonical(before));f.flush();os.fsync(f.fileno())
        response=await rest.agent_api_messages.mark_agent_message_failed(chat_id=ROOM,id=EVENT,error='Operator reconciliation: PM turn cap denied reservation before any model admission. Original blocked journal and all counters retained; user approved cap1000 and guarded first-admission continuation.',request_options=OPTS)
        need(response is not None,'Unknown disposition outcome; consumed attempt must not be retried')
        write_json(RESPONSE,{'at':utc_now(),'method':'mark_agent_message_failed','event_id':EVENT,'attempt':ref(ATTEMPT),'response':response.model_dump(mode='json',exclude_none=True)})
        failed=await get(rest,'failed');processing=await get(rest,'processing')
        need(failed is not None and processing is None and failed.content==event['content'] and failed.sender_id==sender['agent_id'],'Fresh failed status not confirmed; preserve attempt and inspect')
        after=copy.deepcopy(x['sdk']);after.update(at=utc_now(),trigger_sdk_status='failed')
        for p in after['parts']:
            if p['event_id']==EVENT:p['sdk_status']='failed'
        after['evidence'] += [ref(ATTEMPT),ref(RESPONSE)]
        after['limits']='Actual failed SDK processing disposition confirmed; original event content/IDs and local blocked history retained. Archived workflow/ledger prove no prior model admission; normal runtime may perform first actual turn, not replay any old model work.'
        write_json(AFTER,after)
        return {'status':'FAILED_PRE_MODEL_ATTEMPT_CONFIRMED','receipt':str(RESPONSE),'proof':str(AFTER)}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args();logging.disable(logging.CRITICAL)
    try:
        with launch_lock(load_config(O/'factory.yaml')):result=asyncio.run(run(a.apply))
        print(json.dumps(result,indent=2))
    except Exception as e:
        print(json.dumps({'status':'BLOCKED_NO_AUTOMATIC_RETRY','error_type':type(e).__name__,'detail':str(e) if isinstance(e,(ValueError,KeyError,AttributeError)) else 'Operation failed; no raw provider body printed. Preserve exclusive attempt and inspect actual state.'}));raise SystemExit(1)
