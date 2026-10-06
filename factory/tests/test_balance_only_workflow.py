"""Offline money-only workflow/config/packet regressions; no actual inference."""
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from band.core.types import AgentInput
from band.runtime.tools.agent import AgentTools

from factorykit.common import FactoryError, load_config
from factorykit.runtime import GateError
from factorykit.tasks import launcher_boundary, render_packet
from factorykit.workflow import WorkflowError, WorkflowWatchdog
from factorykit.workflow_runtime import WorkflowTools, observed_turn, post_once, send_window
from tests.test_toolkit import Fixture

ROOM='00000000-0000-4000-8000-000000000001'
PM='00000000-0000-4000-8000-000000000002'
BACKEND='00000000-0000-4000-8000-000000000003'
DIGEST='a'*64


def eid(n):
    return f'00000000-0000-4000-8000-{n:012d}'


class BalanceWorkflowTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'workflow.json';self.now=1000.0
        self.kwargs=dict(path=self.path,room_id=ROOM,pm_id=PM,participant_ids=[PM,BACKEND],
                         ack_timeout_seconds=60,max_notices=2,clock=lambda:self.now,balance_only=True)
        self.watchdog=WorkflowWatchdog(**self.kwargs)

    def test_none_deadline_turn_stays_running_after_all_legacy_expiries(self):
        self.watchdog.begin_turn(BACKEND,'backend',None,trigger_event_id=eid(40))
        self.now+=1000000
        health=self.watchdog.queue_timeout_notices()
        self.assertEqual(health['state'],'busy')
        self.assertIsNone(health['seconds_to_next_deadline'])
        self.assertIs(health['time_limits_enforced'],False)
        data=json.loads(self.path.read_text())
        self.assertEqual(data['turns']['backend']['status'],'running')
        self.assertEqual(data['incidents'],{})
        self.watchdog.end_turn('backend','completed')
        self.assertEqual(self.watchdog.health()['state'],'idle')

    def test_legacy_deadline_is_observational_when_money_only_and_never_expires_turn(self):
        self.watchdog.begin_turn(BACKEND,'backend',1001)
        self.now+=1000000
        self.assertEqual(self.watchdog.health()['state'],'busy')
        self.watchdog.end_turn('backend','failed','provider_failure')
        self.assertEqual(json.loads(self.path.read_text())['turns']['backend']['status'],'failed')
        self.assertIsNotNone(self.watchdog.due_notice(can_notify=True))

    def test_ack_wait_and_six_notices_do_not_exhaust_a_repair_or_turn_cap(self):
        self.watchdog.begin_turn(BACKEND,'backend',None)
        part=f'WORK delivery D1 part 1/2; SHA-256 {DIGEST}; recipient @[[{PM}]]\nExact original payload.'
        self.watchdog.observe_outbound(eid(50),BACKEND,[PM],part,'backend')
        self.watchdog.end_turn('backend','completed')
        self.now+=61
        for n in range(1,7):
            notice=self.watchdog.due_notice(can_notify=True)
            self.assertIsNotNone(notice)
            self.assertEqual(notice['attempt'],n)
            self.assertNotIn('attempt '+str(n)+'/2',notice['content'])
            self.assertNotIn('renew any deadline',notice['content'])
            self.assertIn('not a complete handoff or acceptance',notice['content'])
            self.assertIsNotNone(self.watchdog.claim_notice(notice['notice_id'],can_notify=True))
            self.watchdog.complete_notice(notice['notice_id'],eid(100+n))
            self.now+=61
            self.assertNotEqual(self.watchdog.health()['state'],'blocked')
        self.assertEqual(self.watchdog.health()['notice_attempts'],6)
        self.assertIsNone(self.watchdog.scope['max_notices'])
        self.assertFalse(json.loads(self.path.read_text())['deliveries']['D1']['complete'])
        self.assertFalse(json.loads(self.path.read_text())['deliveries']['D1']['acknowledged'])

    def test_unknown_actual_notice_write_remains_unknown_and_not_blindly_retried(self):
        self.watchdog.begin_turn(BACKEND,'backend',None)
        self.watchdog.end_turn('backend','failed','provider_failure')
        notice=self.watchdog.due_notice(can_notify=True)
        self.watchdog.claim_notice(notice['notice_id'],can_notify=True)
        self.watchdog=WorkflowWatchdog(**self.kwargs)
        self.now+=1000000
        self.assertEqual(self.watchdog.health()['unknown_notices'],[notice['notice_id']])
        self.assertIsNone(self.watchdog.due_notice(can_notify=True))
        self.assertIsNone(self.watchdog.claim_notice(notice['notice_id'],can_notify=True))
        self.assertEqual(self.watchdog.health()['notice_attempts'],1)

    def test_lifecycle_identity_provenance_is_still_exact(self):
        self.watchdog.begin_turn(BACKEND,'backend',None,trigger_event_id=eid(40))
        with self.assertRaises(WorkflowError):
            self.watchdog.begin_turn(PM,'backend',None,trigger_event_id=eid(40))
        with self.assertRaises(WorkflowError):
            self.watchdog.begin_turn(BACKEND,'backend',None,trigger_event_id=eid(41))
        with self.assertRaises(WorkflowError):
            self.watchdog.begin_turn(ROOM,'unknown',None)
        self.assertEqual(len(json.loads(self.path.read_text())['turns']),1)

    def test_existing_legacy_journal_is_not_relabelled_or_reset_as_money_only(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        path=Path(tmp.name)/'legacy.json'
        values=dict(self.kwargs,path=path,balance_only=False)
        legacy=WorkflowWatchdog(**values);legacy.begin_turn(BACKEND,'backend',1600)
        before=path.read_bytes()
        with self.assertRaises(WorkflowError):WorkflowWatchdog(**dict(values,balance_only=True))
        self.assertEqual(path.read_bytes(),before)

    def test_balance_flag_must_be_boolean_and_only_balance_mode_accepts_none_deadline(self):
        with self.assertRaises(WorkflowError):WorkflowWatchdog(**dict(self.kwargs,balance_only='true'))
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        legacy=WorkflowWatchdog(**dict(self.kwargs,path=Path(tmp.name)/'legacy.json',balance_only=False))
        with self.assertRaises(WorkflowError):legacy.begin_turn(BACKEND,'backend',None)
        legacy.begin_turn(BACKEND,'backend',1600);self.now=1600
        self.assertEqual(legacy.health()['state'],'stalled')


class BalanceWorkflowRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.now=time.time()
        self.watchdog=WorkflowWatchdog(self.root/'workflow.json',ROOM,PM,[PM,BACKEND],60,
            max_notices=2,balance_only=True,clock=lambda:self.now)
        self.stop=asyncio.Event();self.semaphore=asyncio.Semaphore(1)
        self.ledger=SimpleNamespace(limits=dict(balance_only=True,spend_cap_usd=25,
            turn_timeout_seconds=900,overall_timeout_seconds=21600,stage_timeout_seconds=21600),
            data=dict(started_epoch=0,room_started_epochs={ROOM:0}),allowed_rooms=[ROOM],room=ROOM,
            stop=self.stop,semaphore=self.semaphore,reason=lambda seat=None:None)
        self.base=AgentTools(ROOM,None,[])
        self.post=AsyncMock(return_value=SimpleNamespace(data=dict(id=eid(100),success=True,recipients=[dict(id=PM)])))
        self.base.rest=SimpleNamespace(agent_api_messages=SimpleNamespace(create_agent_chat_message=self.post))
        self.base.send_event=AsyncMock(return_value={})
        self.roster=[dict(id='pm',agent_id=PM,handle='owner/pm'),dict(id='backend',agent_id=BACKEND,handle='owner/backend')]
        self.wrapper=WorkflowTools(self.base,self.ledger,'backend',self.root/'audit.jsonl',self.roster,
            watchdog=self.watchdog,actor_id=BACKEND,turn_id='backend',deadline_at=None,clock=lambda:self.now)
        self.inp=AgentInput(msg=SimpleNamespace(id=eid(40)),tools=self.base,history=None,
            participants_msg=None,contacts_msg=None,is_session_bootstrap=False,room_id=ROOM)
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('No actual sockets allowed'))
        p.start();self.addCleanup(p.stop)

    async def test_clock_tool_truthfully_reports_no_time_limit_and_25_only(self):
        self.now+=1000000
        result=await self.wrapper.execute_tool_call_structured('factory_turn_budget',{})
        self.assertTrue(result.ok)
        for key in ('remaining_seconds','deadline_utc','finish_work_by_utc'):
            self.assertIsNone(result.value[key])
        self.assertEqual(result.value['handoff_reserve_seconds'],0)
        self.assertEqual(result.value['approved_spend_cap_usd'],25)
        self.assertIs(result.value['time_limits_enforced'],False)
        schemas=self.wrapper.get_openai_tool_schemas()
        tool=next(s for s in schemas if s['function']['name']=='factory_turn_budget')
        self.assertIn('Only the approved $25',tool['function']['description'])
        self.assertFalse((await self.wrapper.execute_tool_call_structured('factory_turn_budget',{'extend':900})).ok)

    async def test_send_ignores_old_budget_deadlines_but_keeps_exact_room_and_no_retries(self):
        self.assertEqual(send_window(self.ledger,deadline_at=0),10)
        await post_once(self.base,ROOM,'Exact room payload',[dict(id=PM,handle='owner/pm')],
                        self.ledger,deadline_at=0)
        self.post.assert_awaited_once()
        options=self.post.await_args.kwargs['request_options']
        self.assertEqual(options,dict(max_retries=0,timeout_in_seconds=10))
        with self.assertRaises(GateError):
            await post_once(self.base,PM,'Wrong room',[dict(id=PM,handle='owner/pm')],self.ledger)
        self.assertEqual(self.post.await_count,1)

    async def test_money_or_unknown_accounting_stop_still_prevents_actual_send(self):
        for reason in ('approved $25 charge cap exhausted','unknown paid usage'):
            self.ledger.reason=lambda seat=None,reason=reason:reason
            with self.assertRaises(GateError):
                await post_once(self.base,ROOM,'Payload',[dict(id=PM,handle='owner/pm')],self.ledger)
        self.ledger.reason=lambda seat=None:None;self.stop.set()
        with self.assertRaises(GateError):
            await post_once(self.base,ROOM,'Payload',[dict(id=PM,handle='owner/pm')],self.ledger)
        self.post.assert_not_awaited()

    async def test_late_actual_completion_preserves_original_callback_and_lifecycle(self):
        async def callback(inp):
            self.assertIs(inp.msg,self.inp.msg);self.now+=1000000
        await observed_turn(callback,self.inp,self.wrapper,self.watchdog,actor_id=BACKEND,
                            turn_id='backend',deadline_at=None)
        turn=json.loads(self.watchdog.path.read_text())['turns']['backend']
        self.assertEqual(turn['trigger_event_id'],self.inp.msg.id)
        self.assertEqual(turn['status'],'completed')
        self.assertIsNone(turn['deadline_at'])
        self.assertFalse(self.stop.is_set())

    async def test_uncertain_protocol_write_keeps_evidence_and_does_not_blindly_repeat(self):
        self.post.side_effect=RuntimeError('synthetic lost response')
        self.watchdog.begin_turn(BACKEND,'backend',None,trigger_event_id=self.inp.msg.id)
        content=f'WORK delivery D1 part 1/2; SHA-256 {DIGEST}; recipient @owner/pm\nOriginal bytes.'
        for _ in range(2):
            result=await self.wrapper.execute_tool_call_structured('band_send_message',{'content':content,'mentions':[PM]})
            self.assertFalse(result.ok)
        self.assertEqual(self.post.await_count,1)
        self.assertEqual(self.watchdog.health()['state'],'blocked')
        self.assertIn('outbound_delivery_unknown',self.watchdog.path.read_text())

    async def test_existing_verified_handoff_ack_still_works_without_a_turn_deadline(self):
        from band.client.streaming import MessageCreatedPayload
        from factorykit.handoff_batching import HandoffJournal
        journal=HandoffJournal(self.root/'handoffs.json',ROOM,BACKEND,[PM,BACKEND])
        digest=hashlib.sha256(b'first\nsecond').hexdigest()
        for number,body in enumerate(('first\n','second'),1):
            content=f'WORK delivery VERIFIED-1 part {number}/2; SHA-256 {digest}; recipient @[[{BACKEND}]]\n{body}'
            if number==2:content+='\nEND OF HANDOFF'
            event_id=eid(700+number)
            self.watchdog.observe_outbound(event_id,PM,[BACKEND],content)
            payload=MessageCreatedPayload(id=event_id,content=content,sender_id=PM,sender_type='Agent',
                message_type='text',chat_room_id=ROOM,inserted_at='2026-10-05T01:00:00+00:00',
                updated_at='2026-10-05T01:00:00+00:00')
            journal.observe(payload,event_room_id=ROOM,confirmed=json.loads(self.watchdog.path.read_text())['events'][event_id])
        journal.claim(eid(702));self.wrapper.handoff_journal=journal
        names={s['function']['name'] for s in self.wrapper.get_openai_tool_schemas()}
        self.assertTrue({'factory_turn_budget','factory_handoff_ack'}<=names)
        self.watchdog.begin_turn(BACKEND,'backend',None,trigger_event_id=self.inp.msg.id)
        self.now+=1000000
        first=await self.wrapper.execute_tool_call_structured('factory_handoff_ack',{'delivery_id':'VERIFIED-1'})
        self.assertTrue(first.ok);self.assertTrue(first.value['receipt_only'])
        request=self.post.await_args.kwargs['message']
        self.assertEqual(request.content,f'HANDOFF-ACK delivery VERIFIED-1; SHA-256 {digest}; sender @[[{PM}]]')
        self.assertEqual([m.id for m in request.mentions],[PM])
        second=await self.wrapper.execute_tool_call_structured('factory_handoff_ack',{'delivery_id':'VERIFIED-1'})
        self.assertTrue(second.ok);self.assertEqual(second.value['status'],'already_acknowledged')
        self.post.assert_awaited_once();self.assertFalse(self.stop.is_set())


    async def test_confirmed_invalid_receipt_can_be_corrected_without_stopping_roles(self):
        from factorykit.runtime import workflow_requires_stop
        self.watchdog.begin_turn(PM, 'pm-delivery', None)
        content = f'WORK delivery D1 part 1/1; SHA-256 {DIGEST}; recipient @[[{BACKEND}]]\nOriginal payload.\nEND OF HANDOFF'
        self.watchdog.observe_outbound(eid(41), PM, [BACKEND], content, 'pm-delivery')
        self.watchdog.end_turn('pm-delivery', 'completed')
        bad_ack = f'HANDOFF-ACK delivery D1; SHA-256 {"b"*64}; sender @owner/pm'
        good_ack = f'HANDOFF-ACK delivery D1; SHA-256 {DIGEST}; sender @owner/pm'
        async def callback(inp):
            result = await inp.tools.execute_tool_call_structured('band_send_message', {'content': bad_ack, 'mentions': [PM]})
            self.assertTrue(result.ok)  # Actual POST succeeded; protocol did not.
            self.assertEqual(result.value['factory_protocol_validation']['status'], 'REJECTED')
            self.assertFalse(result.value['factory_protocol_validation']['receipt_validated'])
            self.assertFalse(self.stop.is_set())
            self.assertFalse(inp.tools.delivery_blocked)
            health = self.watchdog.health()
            self.assertEqual(health['state'], 'blocked')
            self.assertTrue(health['communication_advisory'])
            self.assertFalse(workflow_requires_stop(health, balance_only=True))
            state = json.loads(self.watchdog.path.read_text())
            self.assertFalse(state['deliveries']['D1']['acknowledged'])
            self.first_bad_event = state['events'][eid(100)]
            self.post.return_value = SimpleNamespace(data=dict(id=eid(101), success=True, recipients=[dict(id=PM)]))
            result = await inp.tools.execute_tool_call_structured('band_send_message', {'content': good_ack, 'mentions': [PM]})
            self.assertTrue(result.ok)
            self.assertNotIn('factory_protocol_validation', result.value)
        # Current development also previews before POST. Exercise the deployed
        # confirmed-message seam; that earlier prevalidation stays unchanged.
        with patch.object(self.watchdog, 'preview_outbound', create=True):
            await observed_turn(callback, self.inp, self.wrapper, self.watchdog,
                                actor_id=BACKEND, turn_id='backend', deadline_at=None)
        state = json.loads(self.watchdog.path.read_text())
        self.assertTrue(state['deliveries']['D1']['acknowledged'])
        self.assertEqual(state['events'][eid(100)], self.first_bad_event)
        self.assertEqual(state['deliveries']['D1']['acks'], {BACKEND: eid(101)})
        self.assertEqual(state['turns']['backend']['status'], 'completed')
        self.assertFalse(self.stop.is_set())
        self.assertNotIn('accepted', self.watchdog.path.read_text())
        self.assertEqual(self.post.await_count, 2)

    async def test_recovery_advisory_does_not_hide_unsafe_or_legacy_stops(self):
        from factorykit.runtime import workflow_requires_stop
        self.watchdog.begin_turn(BACKEND, 'backend', None)
        malformed = f'WORK delivery D1 part 1/1; SHA-256 {DIGEST}; recipient @owner/pm\nNo final marker.'
        with patch.object(self.watchdog, 'preview_outbound', create=True):
            await self.wrapper.send_message(malformed, [PM])
        health = self.watchdog.health()
        self.assertFalse(workflow_requires_stop(health, balance_only=True))
        self.assertTrue(workflow_requires_stop(health, balance_only=False))
        self.assertTrue(workflow_requires_stop({**health, 'recovery_blocker': 'blocked_notice_delivery_unknown'}, balance_only=True))
        self.watchdog.observe_outbound(eid(100), BACKEND, [PM], malformed + ' Changed bytes.', 'backend')
        health = self.watchdog.health()
        self.assertFalse(health['communication_advisory'])
        self.assertTrue(workflow_requires_stop(health, balance_only=True))
        before = self.post.await_count
        self.wrapper.delivery_blocked = False
        self.ledger.reason = lambda seat=None: 'unknown paid usage'
        with patch.object(self.watchdog, 'preview_outbound', create=True), self.assertRaises(GateError):
            await self.wrapper.send_message(malformed, [PM])
        self.assertEqual(self.post.await_count, before)
        self.assertTrue(self.stop.is_set())

    async def test_finite_mode_confirmed_invalid_receipt_keeps_legacy_stop(self):
        self.ledger.limits['balance_only'] = False
        self.ledger.data['started_epoch'] = self.now
        self.ledger.data['room_started_epochs'][ROOM] = self.now
        self.wrapper.balance_only = False
        self.wrapper.deadline_at = self.now + 600
        self.wrapper.watchdog = WorkflowWatchdog(self.root/'finite-workflow.json', ROOM, PM, [PM, BACKEND],
                                                60, max_notices=2, clock=lambda: self.now)
        self.wrapper.watchdog.begin_turn(BACKEND, 'backend', self.wrapper.deadline_at)
        malformed = f'WORK delivery D1 part 1/1; SHA-256 {DIGEST}; recipient @owner/pm\nNo final marker.'
        with patch.object(self.wrapper.watchdog, 'preview_outbound', create=True):
            response = await self.wrapper.send_message(malformed, [PM])
        self.assertNotIn('factory_protocol_validation', response)
        self.assertTrue(self.wrapper.delivery_blocked)
        self.assertTrue(self.stop.is_set())


class BalanceConfigPacketTests(Fixture):
    def setUp(self):
        super().setUp()
        self.config['budgets'].update(balance_only=True,spend_cap_usd=25,
            billing_mode='spend_cap',accounting_scope='session',approved=True)
        self.config['runtime']['strict_membership_recovery']=False
        for seat in self.config['seats']:
            seat['agent_id']=eid(100+self.config['seats'].index(seat))
        self.save()

    def test_explicit_balance_only_and_advisory_membership_load_without_renewal_expiry_lookup(self):
        # This isolates the parser's explicit flags. Production money-only
        # profiles require the separately tested guarded OpenCode selection.
        with patch('factorykit.harnesses.validate_selection',return_value=[]), \
             patch('factorykit.allowance_renewal.load_time_renewal',side_effect=AssertionError('No old expiry lookup')):
            self.assertEqual(load_config(self.config_path),self.config)

    def test_nonboolean_or_strict_recovery_flags_reject_before_runtime(self):
        for mapping,flag,value in (('budgets','balance_only','true'),('runtime','strict_membership_recovery','false'),
                                   ('runtime','strict_membership_recovery',True)):
            old=self.config[mapping][flag];self.config[mapping][flag]=value;self.save()
            with self.assertRaises(FactoryError):load_config(self.config_path)
            self.config[mapping][flag]=old
        self.save()

    def test_packet_operational_header_contains_only_active_balance_policy_and_preserves_specs(self):
        packet,payloads=render_packet(self.config,'toy',[1,2,3,4],'practice-all')
        text=packet.decode();header=text[:text.index('## Exact official specification')]
        self.assertIn('Only the existing $25 balance cap limits work',header)
        self.assertIn('Do not purchase credits or top up',header)
        self.assertIn(launcher_boundary(self.config),header)
        self.assertIn('Observe ordinary message delivery.',header)
        self.assertIn('Do not remove participants or create a missing-peer fixture',header)
        self.assertNotIn('Observe missing-peer and delayed-message handling',header)
        for stale in ('## Finite work limits','approved finite budgets','repair ceiling',
                      'turn_timeout_seconds','ack_timeout_seconds','max_total_tokens','max_turns_per_seat',
                      'stage_timeout_seconds','overall_timeout_seconds','max_repairs'):
            self.assertNotIn(stale,header)
        for p in payloads:
            original=(Path(self.config['paths']['challenge'])/p['source']).read_bytes()
            self.assertEqual(packet[p['offset']:p['offset']+p['bytes']],original)
        self.assertIn('Official synthetic toy specification 4',text)

    def test_legacy_packet_guidance_and_inactive_budget_values_remain_unchanged(self):
        self.config['budgets']['balance_only']=False
        packet=render_packet(self.config,'toy',[1,2,3,4],'practice-all')[0].decode()
        self.assertIn('## Finite work limits',packet)
        self.assertIn('stop/replan after the repair ceiling',packet)
        self.assertIn('turn_timeout_seconds',packet)


if __name__=='__main__':
    unittest.main()
