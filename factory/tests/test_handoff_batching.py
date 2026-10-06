"""Offline utility proof at actual SDK preprocessor and runtime adapter seams."""
import asyncio
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from band import Agent
from band.adapters import CodexAdapter
from band.client.streaming import MessageCreatedPayload
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.platform.event import MessageEvent
from factorykit.handoff_batching import HandoffJournal, BatchingError, split_fragment
from factorykit.runtime import BudgetLedger, GateError, RoomPreprocessor, create_handoff_journals, serve
from factorykit.workflow import WorkflowWatchdog


def uid(n):
    return f'00000000-0000-4000-8000-{n:012d}'

ROOM, PM, WORKER, OTHER = map(uid, range(1,5))
ROSTER = [PM, WORKER, OTHER]
NOW = '2026-10-05T01:00:00+00:00'


def fragments(bodies, delivery='RESULT-1', recipients=(WORKER,)):
    digest = hashlib.sha256(''.join(bodies).encode()).hexdigest()
    return [f'WORK-1 delivery {delivery} part {i+1}/{len(bodies)}; SHA-256 {digest}; recipient ' +
            ', '.join('@[[' + x + ']]' for x in recipients) + '\n' + body +
            ('\nEND OF HANDOFF' if i+1 == len(bodies) else '') for i, body in enumerate(bodies)]


def payload(text, n, **kwargs):
    return MessageCreatedPayload(id=uid(n), content=text, sender_id=kwargs.pop('sender_id', PM),
        sender_type=kwargs.pop('sender_type', 'Agent'), message_type='text', chat_room_id=kwargs.pop('chat_room_id', ROOM),
        inserted_at=NOW, updated_at=NOW, **kwargs)


def authority(p, recipients=(WORKER,)):
    return dict(sender_id=p.sender_id, recipient_ids=list(recipients),
                content_sha256=hashlib.sha256(p.content.encode()).hexdigest(), turn_id=None)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'handoffs.json'
        self.j = self.open()

    def open(self, **kwargs):
        return HandoffJournal(self.path, ROOM, WORKER, ROSTER, **kwargs)

    def observe(self, p, **kwargs):
        return self.j.observe(p, event_room_id=kwargs.pop('event_room_id', ROOM), confirmed=kwargs.pop('confirmed', authority(p)), **kwargs)

    def complete(self):
        parts = fragments([' alpha\n', 'β🙂  \n\n'])
        self.observe(payload(parts[0],100))
        result = self.observe(payload(parts[1],101))
        return parts, result

    def test_exact_utf8_whitespace_final_first_and_actual_trigger(self):
        parts = fragments([' alpha\n', 'β🙂  ', '\n\n'])
        self.assertEqual(self.observe(payload(parts[2],102)).kind,'skip')
        self.assertEqual(self.observe(payload(parts[0],100)).kind,'skip')
        done = self.observe(payload(parts[1],101))
        self.assertEqual(done.kind,'complete')
        self.assertTrue(done.content.endswith(' alpha\nβ🙂  \n\n'))
        self.assertIn('complete parts: 3/3', done.content)
        self.assertIn("Original header prefix: 'WORK-1 '",done.content)
        self.assertIn('actual last arrival): '+uid(101),done.content)
        self.assertEqual(done.event_ids,(uid(100),uid(101),uid(102)))
        self.assertTrue(self.j.claim(uid(101)))
        self.j.finish(uid(101),completed=True)
        self.assertFalse(self.j.claim(uid(101)))

    def test_partial_restart_and_ready_unclaimed_redelivery(self):
        parts=fragments(['one','two'])
        self.observe(payload(parts[0],100)); self.j=self.open()
        self.assertEqual(self.observe(payload(parts[1],101)).kind,'complete')
        self.j=self.open()
        self.assertEqual(self.observe(payload(parts[1],101)).kind,'complete')
        self.assertEqual(self.observe(payload(parts[1],102)).kind,'skip')
        self.assertTrue(self.j.claim(uid(101)))

    def test_claim_crash_or_failed_callback_cannot_restart(self):
        self.complete(); self.j.claim(uid(101))
        with self.assertRaises(BatchingError):self.open()
        self.j.finish(uid(101),completed=False)
        with self.assertRaises(BatchingError):self.open()

    def test_reviewed_terminal_failure_never_replays_and_allows_new_delivery(self):
        parts,_=self.complete(); self.j.claim(uid(101))
        self.j.claim_acknowledgement('RESULT-1'); self.j.confirm_acknowledgement('RESULT-1',uid(300))
        self.j.finish(uid(101),completed=False)
        before=self.path.read_bytes(); d=json.loads(before)
        key,b=next(iter(d['batches'].items()))
        from factorykit.common import canonical
        approvals={key:hashlib.sha256(canonical(b)).hexdigest()}
        self.j=self.open(terminal_reconciliations=approvals)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(self.j.claim(uid(101)))
        self.assertEqual(self.observe(payload(parts[1],101)).kind,'skip')
        self.assertEqual(self.path.read_bytes(),before)
        with self.assertRaises(BatchingError):self.observe(payload(parts[1],102))
        next_part=fragments(['new result'],delivery='RESULT-2')[0]
        self.assertEqual(self.observe(payload(next_part,103)).kind,'complete')
        self.assertTrue(self.j.claim(uid(103)))
        self.j.finish(uid(103),completed=True)
        self.assertEqual(json.loads(self.path.read_bytes())['batches'][key],b)

    def test_terminal_allowlist_cannot_authorize_unacknowledged_or_changed_claim(self):
        self.complete(); self.j.claim(uid(101)); self.j.finish(uid(101),completed=False)
        from factorykit.common import canonical
        key,b=next(iter(json.loads(self.path.read_bytes())['batches'].items()))
        with self.assertRaises(BatchingError):self.open(terminal_reconciliations={key:hashlib.sha256(canonical(b)).hexdigest()})
        with self.assertRaises(BatchingError):self.open(terminal_reconciliations={'unknown':'0'*64})

    def test_completed_duplicate_new_transport_id_is_recorded_not_reexecuted(self):
        parts,_=self.complete(); self.j.claim(uid(101)); self.j.finish(uid(101),completed=True)
        self.j=self.open()
        self.assertEqual(self.observe(payload(parts[0],103)).kind,'skip')
        state=json.loads(self.path.read_text())
        self.assertIn(uid(103),state['events'])
        self.assertEqual(next(iter(self.j.summary().values()))['ack_required'],True)
        self.assertFalse(self.j.claim(uid(103)))

    def test_later_ack_requires_matching_confirmed_sender_recipient_digest(self):
        self.complete(); self.j.claim(uid(101)); self.j.finish(uid(101),completed=True)
        b=next(iter(json.loads(self.path.read_text())['batches'].values()))['binding']
        delivery=dict(sender_id=PM,recipient_ids=[WORKER],digest=b['digest'],total=2,complete=True,acks={WORKER:uid(300)})
        content=f"HANDOFF-ACK delivery RESULT-1; SHA-256 {b['digest']}; sender @[[{PM}]]"
        state=dict(scope=dict(room_id=ROOM,participant_ids=sorted(ROSTER)),deliveries={'RESULT-1':delivery},
                   events={uid(300):dict(sender_id=OTHER,recipient_ids=[PM],content_sha256=hashlib.sha256(content.encode()).hexdigest())})
        self.j.reconcile_acknowledgements(state)
        self.assertTrue(next(iter(self.j.summary().values()))['ack_required'])
        state['events'][uid(300)]['sender_id']=WORKER
        self.j.reconcile_acknowledgements(state)
        self.assertFalse(next(iter(self.j.summary().values()))['ack_required'])

    def test_complete_unclaimed_delivery_cannot_authorize_receipt(self):
        self.complete()
        with self.assertRaises(BatchingError):self.j.acknowledgement_binding('RESULT-1')
        self.j.claim(uid(101))
        binding=self.j.acknowledgement_binding('RESULT-1')
        self.assertEqual(binding['recipient_id'],WORKER)
        self.assertEqual(binding['sender_id'],PM)

    def test_receipt_send_crash_blocks_restart_unless_confirmed_exact_evidence_exists(self):
        self.complete();self.j.claim(uid(101));self.j.finish(uid(101),completed=True)
        binding=self.j.acknowledgement_binding('RESULT-1')
        self.j.claim_acknowledgement('RESULT-1')
        original=self.path.read_bytes()
        with self.assertRaises(BatchingError):self.open()
        with self.assertRaises(BatchingError):self.j.acknowledgement_binding('RESULT-1')
        self.assertEqual(self.path.read_bytes(),original)
        content=f"HANDOFF-ACK delivery RESULT-1; SHA-256 {binding['digest']}; sender @[[{PM}]]"
        state=dict(scope=dict(room_id=ROOM,participant_ids=sorted(ROSTER)),
                   deliveries={'RESULT-1':dict(sender_id=PM,recipient_ids=[WORKER],digest=binding['digest'],total=2,complete=True,acks={WORKER:uid(300)})},
                   events={uid(300):dict(sender_id=OTHER,recipient_ids=[PM],content_sha256=hashlib.sha256(content.encode()).hexdigest())})
        with self.assertRaises(BatchingError):self.open(workflow=state)
        self.assertEqual(self.path.read_bytes(),original)
        state['events'][uid(300)]['sender_id']=WORKER
        self.j=self.open(workflow=state)
        self.assertFalse(next(iter(self.j.summary().values()))['ack_required'])
        self.assertFalse(self.j.claim(uid(101)))
        with self.assertRaises(BatchingError):self.j.claim_acknowledgement('RESULT-1')

    def test_same_event_or_part_conflicts_rejected_without_destroying_original(self):
        parts=fragments(['one','two']); self.observe(payload(parts[0],100)); original=self.path.read_bytes()
        for n in (100,101):
            with self.assertRaises(BatchingError):self.observe(payload(parts[0]+'changed',n))
            self.assertEqual(self.path.read_bytes(),original)

    def test_declared_digest_and_numbering_and_delimiter_fail_closed(self):
        parts=fragments(['one','two'])
        self.observe(payload(parts[0],100))
        for bad in (parts[1].replace('two','different'),parts[1]+'\n',parts[1].replace('2/2','3/2')):
            with self.assertRaises(BatchingError):self.observe(payload(bad,101))
        self.assertEqual(next(iter(self.j.summary().values()))['status'],'collecting')

    def test_conflicting_work_identity_digest_and_recipient_set(self):
        parts=fragments(['one','two']); self.observe(payload(parts[0],100))
        bads=[parts[1].replace('WORK-1','WORK-2'), fragments(['other','two'])[1],
              fragments(['one','two'],recipients=(WORKER,OTHER))[1]]
        for text in bads:
            with self.assertRaises(BatchingError):self.observe(payload(text,101))

    def test_sender_and_room_and_transport_recipient_binding(self):
        text=fragments(['one'])[0]
        for p,confirmed in [(payload(text,100,chat_room_id=OTHER),None),
             (payload(text,100,sender_type='User'),None),(payload(text,100,sender_id=uid(55)),None),
             (payload(text,100),{}),(payload(text,100),authority(payload(text,100),recipients=(OTHER,))),
             (payload(text,100,metadata={'mentions':[{'id':OTHER}]}),authority(payload(text,100)))]:
            with self.assertRaises(BatchingError):self.observe(p,confirmed=confirmed)
        p=payload(text,101)  # SDK metadata absent is supported via confirmed REST binding.
        self.assertEqual(self.observe(p).kind,'complete')

    def test_optional_payload_room_uses_explicit_verified_envelope_only(self):
        text=fragments(['one'])[0]
        p=payload(text,100,chat_room_id=None)
        for room in (None,OTHER,''):
            with self.assertRaises(BatchingError):self.observe(p,event_room_id=room)
        self.assertEqual(self.observe(p,event_room_id=ROOM).kind,'complete')
        self.assertIsNone(p.chat_room_id)  # Do not rewrite or fabricate wire fields.
        self.assertEqual(json.loads(self.path.read_text())['events'][p.id]['room_id'],ROOM)

    def test_different_authenticated_senders_cannot_mix_parts(self):
        parts=fragments(['one','two'])
        self.observe(payload(parts[0],100))
        self.assertEqual(self.observe(payload(parts[1],101,sender_id=OTHER)).kind,'skip')
        self.assertEqual(len(self.j.summary()),2)

    def test_private_nosymlink_and_corrupted_or_missing_retained_state(self):
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        original=self.path.read_bytes(); self.path.unlink()
        with self.assertRaises(BatchingError):self.open()
        target=self.root/'target';target.write_bytes(original);self.path.symlink_to(target)
        with self.assertRaises(BatchingError):self.open()
        self.path.unlink();self.path.write_text('{}');self.path.chmod(0o600)
        with self.assertRaises(BatchingError):self.open()

    def test_ordinary_human_or_ack_text_is_not_batched(self):
        for text in ('Please begin the task.', 'HANDOFF-ACK delivery RESULT-1; SHA-256 '+'a'*64+'; sender @[['+PM+']]'):
            self.assertEqual(self.j.observe(payload(text,100,sender_type='User'),event_room_id=ROOM).kind,'ordinary')


class PreprocessorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.path=self.root/'handoffs.json';self.j=HandoffJournal(self.path,ROOM,WORKER,ROSTER)
        self.workflow=self.root/'workflow.json';self.workflow.write_text(json.dumps({'events':{}}))
        self.halt=Mock();self.allowed=True
        self.guard=RoomPreprocessor(ROOM,batching=self.j,halt=self.halt,workflow_path=self.workflow,can_process=lambda:self.allowed)
        self.ctx=SimpleNamespace(room_id=ROOM,participants=[{'id':PM,'handle':'owner/pm'},{'id':WORKER,'handle':'owner/worker'}])
        self.adapter=AsyncMock()
        self.agent=Agent.__new__(Agent)
        self.agent._preprocessor=self.guard;self.agent._runtime=SimpleNamespace(agent_id=WORKER);self.agent._adapter=self.adapter
        self.guard.default.process=AsyncMock(side_effect=self.hydrate)
        self.history=[]
        self.network=patch.object(socket.socket,'connect',side_effect=AssertionError('Offline utility tests only'))
        self.network.start();self.addCleanup(self.network.stop)

    async def hydrate(self,ctx,event,agent_id):
        p=event.payload
        return AgentInput(msg=PlatformMessage(p.id,ROOM,p.content,p.sender_id,p.sender_type,'owner','text',{},datetime.now(timezone.utc)),
            tools=object(),history=HistoryProvider(raw=self.history),participants_msg='roster',contacts_msg='contacts',is_session_bootstrap=True,room_id=ROOM)

    def confirm(self,p):
        state=json.loads(self.workflow.read_text());state['events'][p.id]=authority(p);self.workflow.write_text(json.dumps(state))

    async def send(self,p,confirm=True):
        if confirm:self.confirm(p)
        await self.agent._on_execute(self.ctx,MessageEvent(room_id=ROOM,payload=p))
        # The real SDK calls mark_processed only after _on_execute returns;
        # verify durable content is present at exactly that callback boundary.
        if split_fragment(p.content) and p.sender_id!=WORKER:
            self.assertIn(p.id,json.loads(self.path.read_text())['events'])

    async def test_twelve_parts_one_sdk_adapter_call_no_partial_hydration(self):
        parts=fragments([f'fragment-{i}\n' for i in range(12)])
        for i,text in enumerate(parts):
            await self.send(payload(text,100+i))
            self.assertEqual(self.adapter.on_event.await_count, int(i==11))
        self.guard.default.process.assert_awaited_once()
        inp=self.adapter.on_event.await_args.args[0]
        self.assertTrue(inp.msg.content.endswith(''.join(f'fragment-{i}\n' for i in range(12))))
        self.assertEqual(inp.msg.id,uid(111));self.assertTrue(inp.is_session_bootstrap)
        self.assertEqual(inp.contacts_msg,'contacts')

    async def test_actual_sdk_minimal_websocket_payload_without_nested_room(self):
        from band.platform.link import BandLink
        parts=fragments(['one','two'])
        for i,constructor in enumerate((MessageCreatedPayload.model_validate,MessageCreatedPayload.model_construct)):
            raw=payload(parts[i],120+i).model_dump(exclude={'chat_room_id'})
            p=constructor(raw) if i==0 else constructor(**raw)
            self.assertNotIn('chat_room_id',p.model_fields_set)
            self.assertIsNone(p.chat_room_id)
            captured=[]
            link=BandLink.__new__(BandLink);link._queue_event=captured.append
            await link._on_message_created(ROOM,p)
            self.confirm(p)
            await self.agent._on_execute(self.ctx,captured[0])
            self.assertIs(captured[0].payload,p)
        self.adapter.on_event.assert_awaited_once()
        self.assertEqual(self.adapter.on_event.await_args.args[0].msg.id,uid(121))
        retained=json.loads(self.path.read_text())
        self.assertEqual({e['room_id'] for e in retained['events'].values()},{ROOM})
        self.halt.assert_not_called()

    async def test_explicit_payload_room_conflict_halts_without_hydration(self):
        p=payload(fragments(['a'])[0],100,chat_room_id=OTHER)
        with self.assertRaises(BatchingError):await self.send(p)
        self.halt.assert_called_once();self.guard.default.process.assert_not_called()
        self.assertFalse(json.loads(self.path.read_text())['events'])

    async def test_outer_room_filter_and_context_recipient_crosschecks(self):
        p=payload(fragments(['a'])[0],100,chat_room_id=None);self.confirm(p)
        await self.agent._on_execute(self.ctx,MessageEvent(room_id=OTHER,payload=p))
        self.halt.assert_not_called();self.guard.default.process.assert_not_called()
        self.ctx.room_id=OTHER
        with self.assertRaises(GateError):await self.agent._on_execute(self.ctx,MessageEvent(room_id=ROOM,payload=p))
        self.ctx.room_id=ROOM
        with self.assertRaises(GateError):await self.guard.process(self.ctx,MessageEvent(room_id=ROOM,payload=p),OTHER)
        self.assertEqual(self.halt.call_count,2)
        self.assertFalse(json.loads(self.path.read_text())['events'])

    async def test_partial_persist_failure_halts_before_successful_callback(self):
        p=payload(fragments(['a','b'])[0],100)
        with patch.object(self.j,'_write',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):await self.send(p)
        self.adapter.on_event.assert_not_called();self.guard.default.process.assert_not_called();self.halt.assert_called_once()
        self.assertNotIn(p.id,json.loads(self.path.read_text())['events'])

    async def test_confirmed_outbound_race_then_success_and_never_arrives(self):
        p=payload(fragments(['a'])[0],100)
        async def confirm_later(_):self.confirm(p)
        with patch('factorykit.runtime.asyncio.sleep',side_effect=confirm_later) as wait:
            await self.send(p,confirm=False)
        self.assertEqual(wait.call_count,1)
        q=payload(fragments(['b'],delivery='RESULT-2')[0],101)
        with patch('factorykit.runtime.asyncio.sleep',new=AsyncMock()):
            with self.assertRaises(BatchingError):await self.send(q,confirm=False)
        self.halt.assert_called_once()
        self.assertEqual(self.adapter.on_event.await_count,1)
        self.assertNotIn(q.id,json.loads(self.path.read_text())['events'])

    async def test_confirmation_wait_respects_new_stop_without_model(self):
        p=payload(fragments(['a'])[0],100)
        async def stop(_):self.allowed=False
        with patch('factorykit.runtime.asyncio.sleep',side_effect=stop):
            with self.assertRaises(GateError):await self.send(p,confirm=False)
        self.halt.assert_called_once();self.adapter.on_event.assert_not_called()

    async def test_complete_view_preserves_maintained_sdk_history_and_session_metadata(self):
        parts=fragments(['a','b']);await self.send(payload(parts[0],100))
        from band.runtime.formatters import replace_uuid_mentions
        self.history=[dict(content=replace_uuid_mentions(parts[0],self.ctx.participants),sender_type='Agent',message_type='text'),
                      dict(content=parts[0],sender_type='Agent',message_type='task_event',metadata={'thread':'retained'}),
                      dict(content='human kickoff',sender_type='User',message_type='text')]
        await self.send(payload(parts[1],101))
        inp=self.adapter.on_event.await_args.args[0]
        self.assertEqual(inp.history.raw,self.history)
        self.assertEqual(len(self.history),3)

    async def test_ordinary_human_kickoff_and_self_filter_unchanged(self):
        await self.send(payload('Start the task.',100,sender_type='User'))
        self.adapter.on_event.assert_awaited_once()
        await self.send(payload(fragments(['self'])[0],101,sender_id=WORKER))
        self.assertEqual(self.adapter.on_event.await_count,1)


class RuntimeAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.config=dict(paths={'runs':str(self.root)}, band=dict(judged_room_id=ROOM,rest_url='https://invalid.example',ws_url='wss://invalid.example'),
            seats=[dict(id='pm',agent_id=PM,display_name='pm',handle='owner/pm'),dict(id='worker',agent_id=WORKER,display_name='worker',handle='owner/worker')],
            budgets=dict(max_active_seats=1,overall_timeout_seconds=10000,stage_timeout_seconds=10000,max_turns_per_seat=100,
                         max_total_tokens=100000000,turn_timeout_seconds=600,max_repairs=2,ack_timeout_seconds=120))
        self.root.joinpath('runtime').mkdir()
        self.ledger=BudgetLedger(self.config['budgets'],self.root/'runtime/budget.json',ROOM)
        self.inputs=[];self.outcome=None;self.deny=False;self.normal_result=True;self.terminal=None

    async def run_fixture(self):
        parts=fragments(['a','b'])
        owner=self
        async def on_model(adapter,inp):
            owner.inputs.append(inp)
            if owner.outcome:raise owner.outcome
            if owner.terminal:inp.tools.terminal_status=owner.terminal
        def create(**kwargs):
            adapter=kwargs['adapter'];guard=kwargs['preprocessor']
            seat=adapter.seat['id']
            class FakeAgent:
                agent_name=seat
                async def start(self):
                    if seat!='worker':return
                    ctx=SimpleNamespace(participants=[])
                    async def hydrate(ctx,event,agent_id):
                        p=event.payload
                        return AgentInput(msg=PlatformMessage(p.id,ROOM,p.content,p.sender_id,p.sender_type,'pm','text',{},datetime.now(timezone.utc)),
                            tools=SimpleNamespace(agent_id=WORKER,room_id=ROOM),history=HistoryProvider(raw=[]),participants_msg=None,contacts_msg=None,is_session_bootstrap=True,room_id=ROOM)
                    guard.default.process=AsyncMock(side_effect=hydrate)
                    watchdog=WorkflowWatchdog(owner.root/f'runtime/workflow-{ROOM}.json',ROOM,PM,[PM,WORKER],120)
                    for i,text in enumerate(parts):
                        p=payload(text,100+i);watchdog.observe_outbound(p.id,PM,[WORKER],p.content)
                        inp=await guard.process(ctx,MessageEvent(room_id=ROOM,payload=p),WORKER)
                        if i==0:
                            if owner.ledger.data['turns']:raise AssertionError('Partial charged a turn')
                        if inp:
                            if owner.deny:owner.ledger.stop.set()
                            await adapter.on_event(inp)
                    # Duplicate after successful completion does not reserve/model.
                    if owner.normal_result and not owner.outcome and not owner.deny:
                        p=payload(parts[0],102);watchdog.observe_outbound(p.id,PM,[WORKER],p.content)
                        if await guard.process(ctx,MessageEvent(room_id=ROOM,payload=p),WORKER) is not None:
                            raise AssertionError('Completed duplicate reached model')
                    owner.ledger.stop.set()
                async def stop(self,**_):pass
            return FakeAgent()
        with ExitStack() as stack:
            stack.enter_context(patch('factorykit.runtime.require_ready'))
            stack.enter_context(patch('factorykit.runtime.session_ledger',return_value=self.ledger))
            stack.enter_context(patch('factorykit.runtime.credentials',return_value={s['id']:dict(agent_id=s['agent_id'],api_key='synthetic') for s in self.config['seats']}))
            stack.enter_context(patch('factorykit.runtime.adapter_config',return_value=None))
            stack.enter_context(patch('factorykit.runtime.read_registry',return_value={'token':'test'}))
            stack.enter_context(patch('factorykit.runtime.psutil.Process',return_value=SimpleNamespace(children=lambda **_:[])))
            stack.enter_context(patch.object(CodexAdapter,'__init__',return_value=None))
            stack.enter_context(patch.object(CodexAdapter,'on_event',on_model))
            stack.enter_context(patch.object(Agent,'create',side_effect=create))
            stack.enter_context(patch.object(asyncio.get_running_loop(),'add_signal_handler'))
            stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('No network in utility tests')))
            await serve(self.config,'judged','test')

    async def test_real_runtime_reserves_once_for_complete_not_partial_or_duplicate(self):
        await self.run_fixture()
        self.assertEqual(len(self.inputs),1);self.assertEqual(self.ledger.data['turns'],{'worker':1})
        self.assertEqual(self.ledger.data['tokens'],0)
        path=self.root/f'runtime/handoffs-{ROOM}-worker.json'
        self.assertEqual(next(iter(json.loads(path.read_text())['batches'].values()))['status'],'completed')

    async def test_budget_denial_after_claim_blocks_and_halts_without_model(self):
        self.deny=True
        with self.assertRaises(GateError):await self.run_fixture()
        self.assertEqual(self.inputs,[]);self.assertEqual(self.ledger.data['turns'],{})
        self.assertIn('did not complete',self.ledger.data['stopped_reason'])
        path=self.root/f'runtime/handoffs-{ROOM}-worker.json'
        with self.assertRaises(BatchingError):HandoffJournal(path,ROOM,WORKER,[PM,WORKER])

    async def test_provider_failure_and_cancellation_preserve_blocked_claim(self):
        for error in (RuntimeError('synthetic provider failure'),asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                # Each subcase owns an independent fake room state.
                await self.asyncSetUp();self.outcome=error
                with self.assertRaises(type(error)):await self.run_fixture()
                self.assertEqual(len(self.inputs),1);self.assertEqual(self.ledger.data['turns'],{'worker':1})
                self.assertIn('did not complete',self.ledger.data['stopped_reason'])
                path=self.root/f'runtime/handoffs-{ROOM}-worker.json'
                with self.assertRaises(BatchingError):HandoffJournal(path,ROOM,WORKER,[PM,WORKER])

    async def test_caught_timeout_or_reported_failure_never_returns_success_to_sdk(self):
        for error,terminal in ((TimeoutError(),None),(None,'failed'),(None,'interrupted')):
            with self.subTest(terminal=terminal,error=type(error).__name__):
                await self.asyncSetUp();self.outcome=error;self.terminal=terminal
                with self.assertRaises(GateError):await self.run_fixture()
                self.assertEqual(len(self.inputs),1)
                self.assertIn('did not complete' if error is None else 'outer turn deadline',self.ledger.data['stopped_reason'])
                path=self.root/f'runtime/handoffs-{ROOM}-worker.json'
                self.assertEqual(next(iter(json.loads(path.read_text())['batches'].values()))['status'],'blocked')

    async def test_batched_state_cannot_be_bypassed_by_legacy_continuation_or_recovery(self):
        path=self.root/f'runtime/handoffs-{ROOM}-worker.json'
        HandoffJournal(path,ROOM,WORKER,[PM,WORKER])
        for kwargs in ({'continuation':object()},{'recovery':object()}):
            with self.assertRaises(GateError):create_handoff_journals(self.config,ROOM,self.config['seats'],self.ledger,None,**kwargs)
        path.unlink()
        with self.assertRaises(GateError):create_handoff_journals(self.config,ROOM,self.config['seats'],self.ledger,None,continuation=object())
        path.with_suffix('.json.lock').unlink()
        self.assertEqual(create_handoff_journals(self.config,ROOM,self.config['seats'],self.ledger,None,continuation=object()),{})


if __name__=='__main__':unittest.main()
