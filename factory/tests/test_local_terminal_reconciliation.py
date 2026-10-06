import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from factorykit.common import canonical, digest, FactoryError
from factorykit.local_terminal_reconciliation import load_terminal_reconciliations


class TerminalManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root/'readiness').mkdir()
        self.room='room'; self.actor='reviewer'; self.sender='pm'; self.event='old-event'; self.ack='real-ack'
        self.config={'paths':{'runs':str(self.root),'factory':'derived'},'launch':{'practice_mode':True},'band':{'rehearsal_room_id':self.room},'seats':[{'id':'reviewer','agent_id':self.actor},{'id':'pm','agent_id':self.sender}],'budgets':{'overall_timeout_seconds':38800,'stage_timeout_seconds':43200,'max_total_tokens':100000}}
        self.workflow={'scope':{'room_id':self.room,'participant_ids':sorted([self.actor,self.sender])},'turns':{'reviewer:1:old-event':{'status':'interrupted','reason_code':'interrupted','agent_id':self.actor,'trigger_event_id':self.event}},'incidents':{'turn:reviewer:1:old-event':{'failed':True,'closed':False,'blocked':None,'deliveries':[]}}}
        self.batch={'status':'blocked','receipt_status':'confirmed','trigger_event_id':self.event,'receipt_event_id':self.ack,'binding':{'sender_id':self.sender,'delivery':'release','digest':'d'*64}}
        ack=f"HANDOFF-ACK delivery release; SHA-256 {'d'*64}; sender @[[pm]]"
        self.sdk={'status':'VERIFIED_READ_ONLY','room_id':self.room,'trigger_sdk_status':'failed','receipt_sdk_status':'processing','recipient_id':self.actor,'trigger_event_id':self.event,'receipt_event_id':self.ack,'receipt_sender_id':self.actor,'receipt_recipient_id':self.sender,'receipt_content_sha256':hashlib.sha256(ack.encode()).hexdigest(),'evidence':[]}
        raw=self.write('sdk-read.json',{'actual':'read-only evidence'}); self.sdk['evidence']=[raw]
        old=copy.deepcopy(self.config);old['paths']['factory']='original';old['budgets']['overall_timeout_seconds']-=28800;old['budgets']['stage_timeout_seconds']-=28800
        self.record={'status':'APPROVED_TERMINAL_NO_REPLAY','configuration_sha256':digest(canonical(self.config)),'authorization':{'user_answer':'Extend 8 hours and resume','additional_seconds':28800},'room_id':self.room,'participant_ids':sorted([self.actor,self.sender]),'original_configuration':self.write('old.json',old),'original_workflow':self.write('workflow.json',self.workflow),'sdk_evidence':self.write('sdk.json',self.sdk),'entries':[{'seat':'reviewer','turn_id':'reviewer:1:old-event','trigger_event_id':self.event,'receipt_event_id':self.ack,'batch_key':'pm:release','batch_sha256':digest(canonical(self.batch)),'original_journal':self.write('journal.json',{'scope':{'room_id':self.room,'participant_ids':sorted([self.actor,self.sender]),'recipient_id':self.actor},'batches':{'pm:release':self.batch}}),'acceptance_record':self.write('verdict.json',{'decision':'ACCEPTED','handoff_event':self.event,'receipt_event':self.ack})}]}

    def write(self,name,value):
        p=self.root/name;p.write_bytes(canonical(value));return {'path':str(p),'sha256':digest(p)}

    def load(self):
        self.write('readiness/local-8h-reconciliation.json',self.record)
        return load_terminal_reconciliations(self.config,self.room,self.workflow)

    def test_failed_attempt_processing_ack_is_receipt_only_and_authorized(self):
        before=canonical(self.workflow)
        self.assertEqual(self.load(),{'reviewer':{'pm:release':digest(canonical(self.batch))}})
        self.assertEqual(canonical(self.workflow),before)

    def test_processing_trigger_cannot_be_falsely_disposed(self):
        self.sdk['trigger_sdk_status']='processing';self.record['sdk_evidence']=self.write('sdk.json',self.sdk)
        with self.assertRaises(FactoryError):self.load()

    def test_actual_ack_sender_recipient_content_must_all_match(self):
        for key in ('receipt_sender_id','receipt_recipient_id','receipt_content_sha256'):
            with self.subTest(key=key):
                sdk=copy.deepcopy(self.sdk);sdk[key]='wrong';self.record['sdk_evidence']=self.write('sdk.json',sdk)
                with self.assertRaises(FactoryError):self.load()

    def test_time_only_approval_cannot_raise_tokens(self):
        self.config['budgets']['max_total_tokens']+=1;self.record['configuration_sha256']=digest(canonical(self.config))
        with self.assertRaises(FactoryError):self.load()

    def test_changed_original_turn_or_evidence_is_rejected(self):
        self.workflow['turns']['reviewer:1:old-event']['status']='completed'
        with self.assertRaises(FactoryError):self.load()

    def test_judged_mode_and_unapproved_extra_claim_are_rejected(self):
        self.config['launch']['practice_mode']=False
        with self.assertRaises(FactoryError):self.load()


if __name__=='__main__':unittest.main()
