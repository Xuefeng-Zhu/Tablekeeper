"""Offline tests at the actual SDK schema, dispatcher and lifecycle seams.

No Agent, app-server, credentials, model turn or live BAND request is created.
"""
from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from band.adapters import CodexAdapter, CodexAdapterConfig
from band.client.rest import MessageSentResponse
from band.client.streaming import MessageCreatedPayload
from band.core.protocols import TurnResultAlreadyReported
from band.core.types import AgentInput, Capability, Emit
from band.runtime.tools.agent import AgentTools

from factorykit.runtime import BudgetLedger, GateError, cmd_status
from factorykit.handoff_batching import HandoffJournal
from factorykit.workflow import WorkflowWatchdog
from factorykit.workflow_runtime import WorkflowTools, confirmed_message, observed_turn, send_due_notice

ROOM = '00000000-0000-4000-8000-000000000001'
PM = '00000000-0000-4000-8000-000000000002'
BACKEND = '00000000-0000-4000-8000-000000000003'
ROSTER = [dict(id='pm',agent_id=PM,handle='owner/pm'),dict(id='backend',agent_id=BACKEND,handle='owner/backend')]
DIGEST = 'a' * 64


def eid(n):
    return f'00000000-0000-4000-8000-{n:012d}'


def part(n):
    return f'WORK-1 delivery RESULT-1 part {n}/5; SHA-256 {DIGEST}; recipient @owner/pm\nExisting payload {n}.' + ('\nEND OF HANDOFF' if n == 5 else '')


def response(n, recipient=PM):
    return SimpleNamespace(data=MessageSentResponse(id=eid(n), success=True, recipients=[dict(id=recipient,handle='owner/pm' if recipient==PM else 'owner/backend')]))


class WorkflowIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = time.time()
        self.watchdog = WorkflowWatchdog(self.root/'workflow.json',ROOM,PM,[PM,BACKEND],120,clock=lambda:self.now)
        self.ledger = BudgetLedger(dict(max_active_seats=1,overall_timeout_seconds=10000,
            stage_timeout_seconds=10000,max_turns_per_seat=100,max_total_tokens=100000000,
            turn_timeout_seconds=600,max_repairs=2),self.root/'budget.json',ROOM)
        self.ledger.data['tokens'] = 24009633
        self.ledger.data['turns'] = {'pm':70,'backend':7}
        self.ledger.save()
        self.base = AgentTools(ROOM, None, [])
        self.post = AsyncMock(return_value=response(900))
        self.base.rest = SimpleNamespace(agent_api_messages=SimpleNamespace(create_agent_chat_message=self.post))
        self.base.send_event = AsyncMock(return_value={})
        self.guard = self.wrapper()
        self.inp = AgentInput(msg=SimpleNamespace(id=eid(90)),tools=self.base,history=None,
            participants_msg=None,contacts_msg=None,is_session_bootstrap=False,room_id=ROOM)
        blocker = patch.object(socket.socket,'connect',side_effect=AssertionError('Tests must remain offline'))
        blocker.start()
        self.addCleanup(blocker.stop)

    def wrapper(self, actor=BACKEND, turn='backend-turn'):
        seat = next(s['id'] for s in ROSTER if s['agent_id']==actor)
        return WorkflowTools(self.base,self.ledger,seat,self.root/'audit.jsonl',ROSTER,
            watchdog=self.watchdog,actor_id=actor,turn_id=turn,deadline_at=self.now+600,clock=lambda:self.now)

    async def run_callback(self, callback, wrapped=None, inp=None):
        wrapped = wrapped or self.guard
        return await observed_turn(callback,inp or self.inp,wrapped,self.watchdog,
            actor_id=wrapped.actor_id,turn_id=wrapped.turn_id,deadline_at=wrapped.deadline_at)

    async def test_actual_dispatch_tracks_four_parts_and_reported_sdk_timeout_wakes_pm(self):
        original_budget = json.loads(self.ledger.path.read_text())
        async def adapter(inp):
            for i in range(1,5):
                self.post.return_value = response(100+i)
                outcome = await inp.tools.execute_tool_call_structured('band_send_message', {'content':part(i),'mentions':['@owner/pm']})
                self.assertTrue(outcome.ok)
            self.now += 600.4
            raise TurnResultAlreadyReported('Codex turn timed out after 600.0s')
        with self.assertRaises(TurnResultAlreadyReported):
            await self.run_callback(adapter)
        self.assertEqual(self.watchdog.health()['state'],'stalled')
        self.post.return_value = response(800)
        self.assertEqual(await send_due_notice(self.watchdog,self.ledger,{BACKEND:self.base},ROSTER),'sent')
        kwargs = self.post.await_args.kwargs
        self.assertEqual([m.model_dump(exclude_unset=True) for m in kwargs['message'].mentions],[{'id':PM,'handle':'owner/pm'}])
        self.assertIn('missing 5',kwargs['message'].content)
        self.assertEqual(kwargs['chat_id'],ROOM)
        self.assertEqual(kwargs['request_options']['max_retries'],0)
        self.assertLessEqual(kwargs['request_options']['timeout_in_seconds'],10)
        self.assertEqual(json.loads(self.ledger.path.read_text()),original_budget)
        self.assertEqual(self.watchdog.health()['notice_attempts'],1)

    async def test_complete_recipient_ack_ends_wait_without_task_acceptance_claim(self):
        async def adapter(inp):
            for i in range(1,6):
                self.post.return_value=response(200+i)
                await inp.tools.execute_tool_call('band_send_message',{'content':part(i),'mentions':[PM]})
        await self.run_callback(adapter)
        pm_guard=self.wrapper(PM,'pm-ack')
        self.post.return_value=response(210,BACKEND)
        async def acknowledge(inp):
            outcome=await inp.tools.execute_tool_call_structured('band_send_message',{
                'content':f'HANDOFF-ACK delivery RESULT-1; SHA-256 {DIGEST}; sender @owner/backend','mentions':[BACKEND]})
            self.assertTrue(outcome.ok)
        await self.run_callback(acknowledge,pm_guard,replace(self.inp,msg=SimpleNamespace(id=eid(205))))
        self.now+=200
        self.assertEqual(self.watchdog.health()['state'],'idle')
        self.assertEqual(await send_due_notice(self.watchdog,self.ledger,{BACKEND:self.base},ROSTER),'none')
        state=json.loads(self.watchdog.path.read_text())
        self.assertTrue(state['deliveries']['RESULT-1']['acknowledged'])
        self.assertNotIn('accepted',self.watchdog.path.read_text())

    async def test_ack_prose_is_correctable_before_post_without_poisoning_receipt(self):
        for i in range(1,6):
            self.watchdog.observe_outbound(eid(500+i),BACKEND,[PM],part(i).replace('@owner/pm','@[['+PM+']]'))
        guard=self.wrapper(PM,'ack-correction')
        self.post.return_value=response(600,BACKEND)
        ack=f'HANDOFF-ACK delivery RESULT-1; SHA-256 {DIGEST}; sender @owner/backend'
        async def adapter(inp):
            before=self.watchdog.path.read_bytes();budget=self.ledger.path.read_bytes()
            failed=await inp.tools.execute_tool_call_structured('band_send_message',{
                'content':ack+'. Verified 5/5 parts; receipt is not acceptance.','mentions':[BACKEND]})
            self.assertFalse(failed.ok)
            self.assertIn('standalone canonical HANDOFF-ACK',failed.error_message)
            self.post.assert_not_awaited()
            self.assertEqual(self.watchdog.path.read_bytes(),before)
            self.assertEqual(self.ledger.path.read_bytes(),budget)
            self.assertFalse(self.ledger.stop.is_set());self.assertFalse(inp.tools.delivery_blocked)
            corrected=await inp.tools.execute_tool_call_structured('band_send_message',{'content':ack,'mentions':[BACKEND]})
            self.assertTrue(corrected.ok)
        await self.run_callback(adapter,guard)
        self.assertEqual(self.post.await_count,1)
        self.assertTrue(json.loads(self.watchdog.path.read_text())['deliveries']['RESULT-1']['acknowledged'])
        self.assertEqual(self.watchdog.health()['state'],'idle')

    async def test_malformed_part_and_recipient_mismatch_do_not_post_or_mutate(self):
        async def adapter(inp):
            before=self.watchdog.path.read_bytes()
            invalid=[(part(1).replace('part 1/5','part 0/5'),[PM]),
                     (part(5).replace('END OF HANDOFF','still incomplete'),[PM]),
                     (part(1),[BACKEND])]
            for text,mentions in invalid:
                outcome=await inp.tools.execute_tool_call_structured('band_send_message',{'content':text,'mentions':mentions})
                self.assertFalse(outcome.ok)
                self.assertEqual(self.watchdog.path.read_bytes(),before)
                self.assertFalse(self.ledger.stop.is_set());self.assertFalse(inp.tools.delivery_blocked)
            self.post.assert_not_awaited()
            corrected=await inp.tools.execute_tool_call_structured('band_send_message',{'content':part(1),'mentions':[PM]})
            self.assertTrue(corrected.ok)
        await self.run_callback(adapter)
        self.assertEqual(self.post.await_count,1)
        self.assertNotEqual(self.watchdog.health()['state'],'blocked')

    async def test_existing_part_binding_conflicts_are_correctable_without_post(self):
        async def adapter(inp):
            self.post.return_value=response(610)
            first=await inp.tools.execute_tool_call_structured('band_send_message',{'content':part(1),'mentions':[PM]})
            self.assertTrue(first.ok)
            before=self.watchdog.path.read_bytes();self.post.reset_mock()
            for text in (part(1).replace('Original','Altered').replace('Existing','Altered'),part(2).replace(DIGEST,'b'*64),part(2).replace('2/5','2/6')):
                outcome=await inp.tools.execute_tool_call_structured('band_send_message',{'content':text,'mentions':[PM]})
                self.assertFalse(outcome.ok)
                self.assertEqual(self.watchdog.path.read_bytes(),before)
                self.assertFalse(self.ledger.stop.is_set());self.assertFalse(inp.tools.delivery_blocked)
            self.post.assert_not_awaited()
            self.post.return_value=response(611)
            corrected=await inp.tools.execute_tool_call_structured('band_send_message',{'content':part(2),'mentions':[PM]})
            self.assertTrue(corrected.ok)
        await self.run_callback(adapter)
        self.assertEqual(self.post.await_count,1)
        self.assertNotEqual(self.watchdog.health()['state'],'blocked')

    async def test_normal_non_pm_completion_does_not_require_notice_authority(self):
        await self.run_callback(AsyncMock())
        self.assertEqual(self.watchdog.health()['state'],'idle')

    def ack_guard(self, *, complete=True, claim=True):
        journal = HandoffJournal(self.root/'handoffs.json', ROOM, PM, [PM, BACKEND])
        digest = hashlib.sha256('first\nsecond'.encode()).hexdigest()
        for n, body in enumerate(('first\n', 'second'), 1):
            if n == 2 and not complete:
                break
            content = f'WORK-1 delivery VERIFIED-1 part {n}/2; SHA-256 {digest}; recipient @[[{PM}]]\n{body}'
            if n == 2:
                content += '\nEND OF HANDOFF'
            event_id = eid(700+n)
            self.watchdog.observe_outbound(event_id, BACKEND, [PM], content)
            payload = MessageCreatedPayload(id=event_id, content=content, sender_id=BACKEND, sender_type='Agent',
                message_type='text', chat_room_id=ROOM, inserted_at='2026-10-05T01:00:00+00:00', updated_at='2026-10-05T01:00:00+00:00')
            journal.observe(payload, event_room_id=ROOM, confirmed=json.loads(self.watchdog.path.read_text())['events'][event_id])
        if complete and claim:
            journal.claim(eid(702))
        guard = self.wrapper(PM, 'structured-ack')
        guard.handoff_journal = journal
        self.post.return_value = response(703, BACKEND)
        return guard, digest

    async def test_structured_ack_uses_verified_binding_and_never_resends_confirmed_receipt(self):
        guard, digest = self.ack_guard()
        schema = next(s for s in guard.get_openai_tool_schemas() if s.get('function',s).get('name') == 'factory_handoff_ack')
        self.assertEqual(schema['function']['parameters']['required'], ['delivery_id'])
        async def adapter(inp):
            # The final already-admitted turn may finish its receipt.
            self.ledger.data['turns']['pm'] = self.ledger.limits['max_turns_per_seat']
            first = await inp.tools.execute_tool_call_structured('factory_handoff_ack', {'delivery_id':'VERIFIED-1'})
            self.assertTrue(first.ok); self.assertTrue(first.value['receipt_only'])
            self.assertEqual(first.value['event_id'], eid(703))
            request = self.post.await_args.kwargs['message']
            self.assertEqual(request.content, f'HANDOFF-ACK delivery VERIFIED-1; SHA-256 {digest}; sender @[[{BACKEND}]]')
            self.assertEqual([m.id for m in request.mentions], [BACKEND])
            second = await inp.tools.execute_tool_call_structured('factory_handoff_ack', {'delivery_id':'VERIFIED-1'})
            self.assertTrue(second.ok); self.assertEqual(second.value['status'],'already_acknowledged')
        await self.run_callback(adapter, guard)
        self.post.assert_awaited_once()

    async def test_structured_ack_partial_or_unadmitted_delivery_is_local_error(self):
        guard, _ = self.ack_guard(complete=False)
        async def adapter(inp):
            for args in ({'delivery_id':'VERIFIED-1'}, {'delivery_id':'missing'}, {'delivery_id':'VERIFIED-1','sender':BACKEND}):
                result = await inp.tools.execute_tool_call_structured('factory_handoff_ack', args)
                self.assertFalse(result.ok)
                self.assertFalse(self.ledger.stop.is_set())
            self.post.assert_not_awaited()
        await self.run_callback(adapter, guard)

    async def test_structured_ack_requires_matching_actor_and_live_deadline(self):
        guard, _ = self.ack_guard()
        async def adapter(inp):
            original_actor = inp.tools.actor_id
            inp.tools.actor_id = BACKEND
            wrong_actor = await inp.tools.execute_tool_call_structured('factory_handoff_ack', {'delivery_id':'VERIFIED-1'})
            self.assertFalse(wrong_actor.ok)
            inp.tools.actor_id = original_actor
            inp.tools.deadline_at = time.time()-1
            late = await inp.tools.execute_tool_call_structured('factory_handoff_ack', {'delivery_id':'VERIFIED-1'})
            self.assertFalse(late.ok)
            self.post.assert_not_awaited()
        await self.run_callback(adapter, guard)

    async def test_structured_ack_uncertain_post_blocks_subsequent_attempt(self):
        guard, _ = self.ack_guard()
        self.post.side_effect = RuntimeError('synthetic unknown delivery')
        async def adapter(inp):
            for _ in range(2):
                result = await inp.tools.execute_tool_call_structured('factory_handoff_ack', {'delivery_id':'VERIFIED-1'})
                self.assertFalse(result.ok)
            self.assertTrue(self.ledger.stop.is_set()); self.assertTrue(inp.tools.delivery_blocked)
        await self.run_callback(adapter, guard)
        self.post.assert_awaited_once()

    async def test_sdk_lifecycle_failure_survives_best_effort_broadcast_loss(self):
        self.base.send_event.side_effect=RuntimeError('unavailable')
        sdk=CodexAdapter(CodexAdapterConfig(emit_turn_lifecycle_events=True,emit_turn_task_markers=False),emit=[Emit.TASK_EVENTS])
        async def adapter(inp):
            await sdk._emit_turn_outcome(tools=inp.tools,msg=inp.msg,room_id=ROOM,thread_id='sdk-thread',
                turn_id='sdk-turn',turn_status='failed',turn_error='private error must not persist',final_text='',
                settled_reply=False,duration_s=600,include_reply=False)
        await self.run_callback(adapter)
        self.assertEqual(self.watchdog.health()['state'],'stalled')
        state=self.watchdog.path.read_text()
        self.assertIn('provider_failure',state)
        self.assertNotIn('private error',state)
        self.assertNotIn('private error',(self.root/'audit.jsonl').read_text())

    async def test_clock_uses_actual_admission_deadline_and_sdk_dynamic_schema(self):
        sdk=CodexAdapter(CodexAdapterConfig(),capabilities=[Capability.TASKS])
        schemas=sdk._build_dynamic_tools(self.guard)
        tool=next(x for x in schemas if x['name']=='factory_turn_budget')
        self.assertEqual(tool['inputSchema']['properties'],{})
        old=self.ledger.path.read_bytes()
        self.now+=571
        outcome=await self.guard.execute_tool_call_structured('factory_turn_budget',{})
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.value['remaining_seconds'],29)
        self.assertEqual(outcome.value['handoff_reserve_seconds'],60)
        self.assertFalse(outcome.value['extends_limits'])
        bad=await self.guard.execute_tool_call_structured('factory_turn_budget',{'extend':600})
        self.assertFalse(bad.ok)
        self.assertEqual(self.ledger.path.read_bytes(),old)

    async def fail_turn(self):
        async def adapter(inp):
            raise TurnResultAlreadyReported('failed')
        with self.assertRaises(TurnResultAlreadyReported):
            await self.run_callback(adapter)

    async def test_cumulative_pm_budget_and_active_writer_prevent_notice_without_claim(self):
        await self.fail_turn()
        self.ledger.data['turns']['pm']=100
        self.assertEqual(await send_due_notice(self.watchdog,self.ledger,{BACKEND:self.base},ROSTER),'deferred')
        self.ledger.data['turns']['pm']=70
        async with self.ledger.semaphore:
            self.assertEqual(await send_due_notice(self.watchdog,self.ledger,{BACKEND:self.base},ROSTER),'deferred')
        self.ledger.stop.set()
        self.assertEqual(await send_due_notice(self.watchdog,self.ledger,{BACKEND:self.base},ROSTER),'deferred')
        self.post.assert_not_awaited()
        self.assertEqual(self.watchdog.health()['notice_attempts'],0)

    async def test_uncertain_notice_send_is_durably_blocked_and_not_retried(self):
        await self.fail_turn()
        async def failed_send(*args,**kwargs):
            state=json.loads(self.watchdog.path.read_text())
            self.assertEqual(next(iter(state['notices'].values()))['status'],'claimed')
            self.assertTrue(self.ledger.semaphore.locked())
            raise RuntimeError('lost response')
        self.post.side_effect=failed_send
        with self.assertRaises(RuntimeError):
            await send_due_notice(self.watchdog,self.ledger,{BACKEND:self.base},ROSTER)
        self.assertEqual(self.watchdog.health()['state'],'blocked')
        self.assertEqual(await send_due_notice(self.watchdog,self.ledger,{BACKEND:self.base},ROSTER),'none')
        self.assertEqual(self.post.await_count,1)

    async def test_protocol_send_error_remains_blocked_when_sdk_dispatch_catches_exception(self):
        self.post.side_effect=RuntimeError('unknown send outcome')
        async def adapter(inp):
            outcome=await inp.tools.execute_tool_call_structured('band_send_message',{'content':part(1),'mentions':[PM]})
            self.assertFalse(outcome.ok)
            second=await inp.tools.execute_tool_call_structured('band_send_message',{'content':part(1),'mentions':[PM]})
            self.assertFalse(second.ok)
        await self.run_callback(adapter)
        self.assertEqual(self.post.await_count,1)
        self.assertEqual(self.watchdog.health()['state'],'blocked')

    async def test_missing_original_sdk_context_does_not_invent_an_agent_or_send(self):
        await self.fail_turn()
        self.assertEqual(await send_due_notice(self.watchdog,self.ledger,{},ROSTER),'blocked_missing_sender_context')
        self.assertEqual(self.watchdog.health()['notice_attempts'],0)
        self.post.assert_not_awaited()

    async def test_completed_pm_notification_turn_closes_only_operational_incident(self):
        await self.fail_turn()
        await send_due_notice(self.watchdog,self.ledger,{BACKEND:self.base},ROSTER)
        pm=self.wrapper(PM,'pm-notice')
        await self.run_callback(AsyncMock(),pm,replace(self.inp,msg=SimpleNamespace(id=eid(900))))
        self.assertEqual(self.watchdog.health()['state'],'idle')

    async def test_failed_pm_is_visible_blocker_not_an_undeliverable_self_notice(self):
        pm=self.wrapper(PM,'failed-pm')
        async def adapter(inp):
            raise TurnResultAlreadyReported('failed')
        with self.assertRaises(TurnResultAlreadyReported):
            await self.run_callback(adapter,pm)
        self.assertEqual(await send_due_notice(self.watchdog,self.ledger,{PM:self.base},ROSTER),'blocked_coordinator_self_notice')
        self.post.assert_not_awaited()
        self.assertEqual(self.watchdog.health()['notice_attempts'],0)

    async def test_budget_expiry_during_claim_prevents_transport_request(self):
        await self.fail_turn()
        original=self.watchdog.claim_notice
        def expires(*args,**kwargs):
            claim=original(*args,**kwargs)
            self.ledger.data['turns']['pm']=100
            return claim
        with patch.object(self.watchdog,'claim_notice',side_effect=expires),self.assertRaises(GateError):
            await send_due_notice(self.watchdog,self.ledger,{BACKEND:self.base},ROSTER)
        self.post.assert_not_awaited()
        self.assertEqual(self.watchdog.health()['state'],'blocked')

    async def test_request_timeout_is_capped_by_remaining_room_budget(self):
        await self.fail_turn()
        self.ledger.data['started_epoch']=time.time()-9999
        await send_due_notice(self.watchdog,self.ledger,{BACKEND:self.base},ROSTER)
        options=self.post.await_args.kwargs['request_options']
        self.assertEqual(options['max_retries'],0)
        self.assertGreater(options['timeout_in_seconds'],0)
        self.assertLessEqual(options['timeout_in_seconds'],1)

    def test_live_process_status_does_not_hide_blocked_workflow(self):
        config={'paths':{'runs':str(self.root)},'budgets':{'billing_mode':'subscription_only'}}
        record={'status':'running','parent':{'pid':123},'token':'owner','workflow':{'state':'blocked'}}
        out=io.StringIO()
        with patch('factorykit.runtime.read_registry',return_value=record),patch('factorykit.runtime.is_owned',return_value=True),redirect_stdout(out):
            cmd_status(SimpleNamespace(loaded_config=config))
        result=json.loads(out.getvalue())
        self.assertEqual(result['process_status'],'running')
        self.assertEqual(result['workflow']['state'],'blocked')

    def test_missing_success_or_recipient_identity_never_counts_as_confirmation(self):
        for raw in ({'id':eid(10),'success':False,'recipients':[]},None,{'sent':True}):
            with self.subTest(raw=raw),self.assertRaises(GateError):
                confirmed_message(raw)


    async def pm_delivery(self):
        async def adapter(inp):
            for n in range(1,6):
                self.post.return_value=response(1200+n,BACKEND)
                outcome=await inp.tools.execute_tool_call_structured('band_send_message',{
                    'content':part(n).replace('@owner/pm','@owner/backend'),'mentions':[BACKEND]})
                self.assertTrue(outcome.ok)
        await self.run_callback(adapter,self.wrapper(PM,'pm-original'))
        self.post.reset_mock();self.now+=120

    async def test_pm_notice_uses_original_sender_transport_and_peer_callback_ack_resolves_receipt(self):
        await self.pm_delivery();before=self.ledger.path.read_bytes()
        self.post.return_value=response(1210,BACKEND)
        self.assertEqual(await send_due_notice(self.watchdog,self.ledger,{PM:self.base},ROSTER),'sent')
        request=self.post.await_args.kwargs
        self.assertEqual([m.id for m in request['message'].mentions],[BACKEND])
        self.assertEqual(request['request_options']['max_retries'],0)
        self.assertIn('bounded receipt/reassembly only',request['message'].content)
        self.post.return_value=response(1211,PM)
        async def acknowledge(inp):
            result=await inp.tools.execute_tool_call_structured('band_send_message',{
                'content':f'HANDOFF-ACK delivery RESULT-1; SHA-256 {DIGEST}; sender @owner/pm',
                'mentions':[PM]})
            self.assertTrue(result.ok)
        await self.run_callback(acknowledge,self.wrapper(BACKEND,'backend-notice'),
            replace(self.inp,msg=SimpleNamespace(id=eid(1210))))
        state=json.loads(self.watchdog.path.read_text())
        self.assertTrue(state['deliveries']['RESULT-1']['acknowledged'])
        self.assertEqual(next(iter(state['notices'].values()))['handled_by'],[BACKEND])
        self.assertEqual(self.watchdog.health()['state'],'idle')
        self.assertEqual(self.ledger.path.read_bytes(),before)

    async def test_pm_origin_notice_checks_both_sender_and_recipient_cap_before_claim(self):
        await self.pm_delivery()
        for seat in ('pm','backend'):
            saved=self.ledger.data['turns'][seat];self.ledger.data['turns'][seat]=100
            self.assertEqual(await send_due_notice(self.watchdog,self.ledger,{PM:self.base},ROSTER),'deferred')
            self.ledger.data['turns'][seat]=saved
        self.post.assert_not_awaited()
        self.assertEqual(self.watchdog.health()['notice_attempts'],0)

    async def test_pm_origin_recipient_budget_expiring_after_claim_never_reaches_transport(self):
        await self.pm_delivery();original=self.watchdog.claim_notice
        def expires(*args,**kwargs):
            claim=original(*args,**kwargs);self.ledger.data['turns']['backend']=100;return claim
        with patch.object(self.watchdog,'claim_notice',side_effect=expires),self.assertRaises(GateError):
            await send_due_notice(self.watchdog,self.ledger,{PM:self.base},ROSTER)
        self.post.assert_not_awaited()
        self.assertEqual(self.watchdog.health()['state'],'blocked')
        self.assertEqual(self.watchdog.health()['notice_attempts'],1)

    async def test_pm_origin_ambiguous_post_is_not_retried_after_restart(self):
        await self.pm_delivery();self.post.side_effect=RuntimeError('Response lost')
        with self.assertRaises(RuntimeError):
            await send_due_notice(self.watchdog,self.ledger,{PM:self.base},ROSTER)
        self.watchdog=WorkflowWatchdog(self.watchdog.path,ROOM,PM,[PM,BACKEND],120,clock=lambda:self.now)
        self.assertEqual(await send_due_notice(self.watchdog,self.ledger,{PM:self.base},ROSTER),'none')
        self.assertEqual(self.post.await_count,1)
        self.assertEqual(self.watchdog.health()['state'],'blocked')
        self.assertEqual(self.watchdog.health()['notice_attempts'],1)
