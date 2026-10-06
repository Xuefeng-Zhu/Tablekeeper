import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from factorykit.common import canonical, digest, FactoryError
from factorykit.handoff_batching import HandoffJournal, BatchingError
from factorykit.local_deferred_admission import load_deferred_admission
from factorykit.runtime import claim_budgeted_handoff, GateError
from test_handoff_batching import ROOM, PM, WORKER, ROSTER, payload, authority, fragments


class DeferredAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root/'runtime').mkdir(); (self.root/'readiness').mkdir()
        self.config = {'paths': {'runs':str(self.root),'factory':'new-source'},'launch':{'practice_mode':True},'band':{'rehearsal_room_id':ROOM},'seats':[{'id':n,'agent_id':i} for n,i in zip(['reviewer','pm','qa'],ROSTER)],'budgets':{'max_turns_per_seat':1000,'max_total_tokens':600,'overall_timeout_seconds':99999,'stage_timeout_seconds':99999}}
        old=copy.deepcopy(self.config);old['paths']['factory']='old-source';old['budgets']['max_turns_per_seat']=769
        self.workflow={'scope':{'room_id':ROOM,'participant_ids':sorted(ROSTER)},'turns':{}}
        self.source=self.root/'runtime'/f'handoffs-{ROOM}-pm.json'
        j=HandoffJournal(self.source,ROOM,WORKER,ROSTER)
        self.parts=[payload(t,100+i) for i,t in enumerate(fragments([' exact\n','body  ']))]
        for p in self.parts:j.observe(p,event_room_id=ROOM,confirmed=authority(p))
        j.claim(self.parts[-1].id);j.finish(self.parts[-1].id,completed=False)
        self.original=json.loads(self.source.read_text());self.key=next(iter(self.original['batches']))
        self.batch=self.original['batches'][self.key]; self.trigger=self.parts[-1].id
        self.target=self.root/'runtime'/f'deferred-handoffs-{ROOM}-pm.json'
        initial=copy.deepcopy(self.original);initial['batches'][self.key]['status']='ready'
        self.target.write_bytes(canonical(initial));self.target.chmod(0o600)
        self.sdk={'status':'VERIFIED_NEVER_ADMITTED_READ_ONLY','room_id':ROOM,'participant_ids':sorted(ROSTER),'recipient_id':WORKER,'sender_id':PM,'trigger_event_id':self.trigger,'trigger_sdk_status':'failed','codex_thread_id':'same-thread','parts':[{'event_id':p.id,'sender_id':PM,'recipient_id':WORKER,'content_sha256':hashlib.sha256(p.content.encode()).hexdigest(),'sdk_status':'failed' if p.id==self.trigger else 'processed'} for p in self.parts],'evidence':[self.write('sdk-actual.json',{'actual_sdk_read':'fixture'})]}
        self.record={'status':'APPROVED_FIRST_ADMISSION','configuration_sha256':digest(canonical(self.config)),'authorization':{'user_answer':'can you increate the turn limit','old_cap':769,'new_cap':1000},'room_id':ROOM,'participant_ids':sorted(ROSTER),'original_configuration':self.write('old-config.json',old),'original_workflow':self.write('old-workflow.json',self.workflow),'stopped_owner':self.write('owner.json',{'status':'stopped','mode':'rehearsal','workflow':{'active_turn_ids':[],'sdk_execution_busy':False,'unknown_notices':[]}}),'original_ledger':self.write('ledger.json',{'stopped_reason':'handoff model claim did not complete; preserve before retry','tokens':500,'room_stopped_reasons':{},'turns':{'pm':769,'reviewer':10,'qa':10}}),'sdk_evidence':self.write('sdk.json',self.sdk),'entries':[{'seat':'pm','trigger_event_id':self.trigger,'batch_key':self.key,'batch_sha256':digest(canonical(self.batch)),'codex_thread_id':'same-thread','original_journal':{'path':str(self.source),'sha256':digest(self.source)},'derived_journal':str(self.target),'initial_derived_journal':self.write('initial.json',initial)}]}
    def write(self,name,value):
        p=self.root/name;p.write_bytes(canonical(value));return {'path':str(p),'sha256':digest(p)}
    def load(self):
        self.write('readiness/local-turn-1000-reconciliation.json',self.record)
        return load_deferred_admission(self.config,ROOM,self.workflow)
    def test_first_model_admission_and_real_ack_preserve_original_failure(self):
        before=self.source.read_bytes();old,paths,_=self.load()
        self.assertEqual(old['budgets']['max_turns_per_seat'],769)
        j=HandoffJournal(paths['pm'],ROOM,WORKER,ROSTER,workflow=self.workflow)
        self.assertEqual(j.observe(self.parts[-1],event_room_id=ROOM,confirmed=authority(self.parts[-1])).kind,'complete')
        self.assertTrue(j.claim(self.trigger));j.claim_acknowledgement('RESULT-1');j.confirm_acknowledgement('RESULT-1','00000000-0000-4000-8000-000000000777');j.finish(self.trigger,completed=True)
        self.assertFalse(j.claim(self.trigger));self.assertEqual(self.source.read_bytes(),before)
        self.workflow['turns']['pm:770:'+self.trigger]={'status':'completed','trigger_event_id':self.trigger,'agent_id':WORKER}
        binding=self.batch['binding'];ack='00000000-0000-4000-8000-000000000777'
        self.workflow['deliveries']={'RESULT-1':{'complete':True,'sender_id':PM,'recipient_ids':[WORKER],'digest':binding['digest'],'acks':{WORKER:ack}}}
        content=f"HANDOFF-ACK delivery RESULT-1; SHA-256 {binding['digest']}; sender @[[{PM}]]"
        self.workflow['events']={ack:{'sender_id':WORKER,'recipient_ids':[PM],'content_sha256':digest(content.encode())}}
        self.load()  # A completed real attempt persists without being reset.
        self.workflow['events'][ack]['sender_id']='wrong'
        with self.assertRaises(FactoryError):self.load()
    def test_completed_derived_requires_real_completed_current_turn(self):
        j=HandoffJournal(self.target,ROOM,WORKER,ROSTER);j.claim(self.trigger);j.finish(self.trigger,completed=True)
        with self.assertRaises(FactoryError):self.load()
        self.workflow['turns']['pm:770:'+self.trigger]={'status':'interrupted','trigger_event_id':self.trigger,'agent_id':WORKER}
        with self.assertRaises(FactoryError):self.load()
        self.workflow['turns']['pm:770:'+self.trigger]['status']='completed'
        with self.assertRaises(FactoryError):self.load() # Missing real receipt.
    def test_reset_ready_after_current_model_admission_is_rejected(self):
        for status in ('completed','interrupted','active'):
            self.workflow['turns']['pm:770:'+self.trigger]={'trigger_event_id':self.trigger,'status':status,'agent_id':WORKER}
            with self.assertRaises(FactoryError):self.load()
    def test_prior_completed_or_interrupted_model_turn_forbids_recovery(self):
        for status in ('completed','interrupted','active'):
            w=copy.deepcopy(self.workflow);w['turns']['pm:770:'+self.trigger]={'trigger_event_id':self.trigger,'status':status}
            self.record['original_workflow']=self.write('old-workflow.json',w)
            with self.assertRaises(FactoryError):self.load()
    def test_failed_or_claimed_first_model_attempt_cannot_restart(self):
        self.load();j=HandoffJournal(self.target,ROOM,WORKER,ROSTER);j.claim(self.trigger)
        with self.assertRaises(BatchingError):HandoffJournal(self.target,ROOM,WORKER,ROSTER)
        j.finish(self.trigger,completed=False)
        with self.assertRaises(BatchingError):HandoffJournal(self.target,ROOM,WORKER,ROSTER)
    def test_wrong_sdk_sender_recipient_thread_or_content_rejected(self):
        for field in ('sender_id','recipient_id','codex_thread_id','trigger_event_id'):
            s=copy.deepcopy(self.sdk);s[field]='wrong';self.record['sdk_evidence']=self.write('sdk.json',s)
            with self.assertRaises(FactoryError):self.load()
        s=copy.deepcopy(self.sdk);s['parts'][0]['content_sha256']='0'*64;self.record['sdk_evidence']=self.write('sdk.json',s)
        with self.assertRaises(FactoryError):self.load()
    def test_source_journal_mutation_or_other_budget_raise_rejected(self):
        self.config['budgets']['max_total_tokens']+=1;self.record['configuration_sha256']=digest(canonical(self.config))
        with self.assertRaises(FactoryError):self.load()
        self.config['budgets']['max_total_tokens']-=1;self.record['configuration_sha256']=digest(canonical(self.config));self.source.write_bytes(canonical({**self.original,'version':3}))
        with self.assertRaises(FactoryError):self.load()
    def test_judged_mode_or_extra_recovery_entry_rejected(self):
        self.config['launch']['practice_mode']=False
        with self.assertRaises(FactoryError):self.load()
        self.config['launch']['practice_mode']=True;self.record['entries']*=2
        with self.assertRaises(FactoryError):self.load()
    def test_budget_denial_precedes_journal_claim_and_preserves_counters(self):
        ledger=SimpleNamespace(reason=lambda seat:'turn budget exhausted for pm',stop=SimpleNamespace(is_set=lambda:False),halt=Mock(),data={'turns':{'pm':769}})
        journal=Mock()
        with self.assertRaises(GateError):claim_budgeted_handoff(ledger,'pm',journal,self.trigger)
        journal.claim.assert_not_called();self.assertEqual(ledger.data['turns']['pm'],769)
        ledger.halt.assert_called_once_with('turn budget exhausted for pm')

if __name__=='__main__':unittest.main()
