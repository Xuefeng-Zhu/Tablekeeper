"""Offline integration at the maintained Codex, Claude and OpenCode tool doors.

Synthetic command results prove routing and guards, never a product checkpoint.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from band.adapters import CodexAdapter, CodexAdapterConfig
from band.client.rest import MessageSentResponse
from band.client.streaming import MessageCreatedPayload
from band.core.exceptions import BandToolError
from band.integrations.claude_sdk.tools import build_band_sdk_tools
from band.integrations.mcp.engine import build_resolved_band_mcp_tool_registrations
from band.runtime.custom_tools import custom_tool_effects
from band.runtime.tools.agent import AgentTools
from band.runtime.tools.types import TurnEffect

from factorykit.common import FactoryError
from factorykit.handoff_batching import HandoffJournal
from factorykit.progress import ProgressGuard
from factorykit.progress_tools import ProgressWorkflowTools
from factorykit.runtime import BudgetLedger
from factorykit.task_board import TaskBoard
from factorykit.task_board_adapters import TaskBoardAdapterTools
from factorykit.workflow import WorkflowWatchdog


def uid(n):
    return f'00000000-0000-4000-8000-{n:012d}'


ROOM, PM, ACTOR, OTHER = map(uid, range(1, 5))
SHA = 'c' * 40
ROSTER = [dict(id='pm', agent_id=PM, handle='owner/pm'),
          dict(id='worker', agent_id=ACTOR, handle='owner/worker')]
OPERATIONAL = {'factory_handoff_ack', 'factory_turn_budget', 'factory_progress', 'factory_verify_checkpoint'}


class OperationalToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.serial = 0
        blocker = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline tests only'))
        blocker.start(); self.addCleanup(blocker.stop)
        self.fixture()

    def fixture(self):
        self.serial += 1
        root = self.root / str(self.serial); root.mkdir()
        self.now = time.time(); self.turn = 'worker:admitted'
        self.ledger = BudgetLedger(dict(max_active_seats=1, overall_timeout_seconds=3600,
            stage_timeout_seconds=1800, max_turns_per_seat=4, max_total_tokens=100000,
            turn_timeout_seconds=180, max_repairs=2), root/'budget.json', ROOM)
        self.ledger.reserve('worker')
        self.watchdog = WorkflowWatchdog(root/'workflow.json', ROOM, PM, [PM, ACTOR], 60)
        self.watchdog.begin_turn(ACTOR, self.turn, self.now+180, trigger_event_id=uid(90))
        self.board = TaskBoard(root/'board.json', ROOM, ROSTER)
        self.post = AsyncMock(return_value=SimpleNamespace(data=MessageSentResponse(
            id=uid(300), success=True, recipients=[dict(id=PM,handle='owner/pm')])))
        rest = SimpleNamespace(agent_api_messages=SimpleNamespace(create_agent_chat_message=self.post))
        self.base = AgentTools(ROOM, rest, [], agent_id=ACTOR)
        self.journal = HandoffJournal(root/'handoffs.json', ROOM, ACTOR, [PM, ACTOR])
        self.commands = []; self.command_exit = 0
        def runner(argv, cwd, timeout):
            self.commands.append(list(argv))
            if argv[:3] == ['/usr/bin/git', 'rev-parse', 'HEAD']:
                return dict(exit_code=0, stdout=SHA+'\n', stderr='')
            if argv[:3] == ['/usr/bin/git', 'status', '--porcelain']:
                return dict(exit_code=0, stdout='', stderr='')
            return dict(exit_code=self.command_exit, stdout='synthetic checkpoint output', stderr='')
        policy = {'milestones':[dict(id='first-runnable', max_turns=4, max_seconds=300,
            command=['/usr/bin/true'], timeout_seconds=30, max_attempts=2)]}
        self.progress = ProgressGuard(root/'progress.json', ROOM, root, policy, runner=runner)
        self.progress.admit_turn(self.turn)
        self.wrapped = ProgressWorkflowTools(self.base, self.ledger, 'worker', root/'audit.jsonl', ROSTER,
            watchdog=self.watchdog, actor_id=ACTOR, turn_id=self.turn, deadline_at=self.now+180,
            task_board=self.board, handoff_journal=self.journal, progress_guard=self.progress)
        self.router = TaskBoardAdapterTools(ROOM, ACTOR, include_operational=True, include_progress=True)

    def claude(self):
        return {t.name:t for t in build_band_sdk_tools(tool_definitions=[], get_tools=lambda _:None,
            additional_tools=self.router.additional_tools)}

    def opencode(self):
        return {t.name:t for t in build_resolved_band_mcp_tool_registrations(tool_definitions=[],
            get_tools=lambda _:None, additional_tools=self.router.additional_tools)}

    async def invoke(self, door, name, arguments, *, chat_id=ROOM):
        if door == 'codex':
            adapter = CodexAdapter(CodexAdapterConfig(), emit=[])
            reply = AsyncMock()
            adapter._active_room.set(ROOM)
            adapter._room_clients[ROOM] = SimpleNamespace(client=SimpleNamespace(respond=reply))
            event = SimpleNamespace(id=1, method='item/tool/call',
                params=dict(tool=name, arguments=arguments, callId='synthetic-tool-call'))
            await adapter._handle_server_request(tools=self.wrapped,msg=SimpleNamespace(),room_id=ROOM,event=event)
            response=reply.await_args.args[1]
            if not response['success']:
                raise BandToolError(response['contentItems'][0]['text'])
            return json.loads(response['contentItems'][0]['text'])
        with self.router.bind(self.wrapped):
            if door == 'opencode':
                return json.loads(await self.opencode()[name].execute(dict(arguments,chat_id=chat_id)))
            result = await self.claude()[name].handler(dict(arguments,chat_id=chat_id))
            if result.get('is_error'):
                raise BandToolError(result['content'][0]['text'])
            return json.loads(result['content'][0]['text'])

    def prepare_handoff(self):
        body = 'Complete verified task\nwith original whitespace. '
        digest = hashlib.sha256(body.encode()).hexdigest()
        content = f'WORK-1 delivery DELIVERY-1 part 1/1; SHA-256 {digest}; recipient @[[{ACTOR}]]\n{body}\nEND OF HANDOFF'
        event_id = uid(200)
        self.watchdog.observe_outbound(event_id,PM,[ACTOR],content)
        payload = MessageCreatedPayload(id=event_id,content=content,sender_id=PM,sender_type='Agent',
            message_type='text',chat_room_id=ROOM,inserted_at='2026-10-05T01:00:00+00:00',updated_at='2026-10-05T01:00:00+00:00')
        self.journal.observe(payload,event_room_id=ROOM,confirmed=json.loads(self.watchdog.path.read_text())['events'][event_id])
        self.journal.claim(event_id)

    def test_actual_codex_dynamic_schema_and_mcp_names(self):
        adapter = CodexAdapter(CodexAdapterConfig())
        schemas = {s['name']:s for s in adapter._build_dynamic_tools(self.wrapped)}
        self.assertTrue(OPERATIONAL <= schemas.keys())
        self.assertTrue(OPERATIONAL <= self.claude().keys())
        self.assertTrue(OPERATIONAL <= self.opencode().keys())
        effects = custom_tool_effects(self.router.additional_tools)
        self.assertEqual(effects['factory_handoff_ack'],TurnEffect.REPLY)
        self.assertEqual(effects['factory_verify_checkpoint'],TurnEffect.ACT)
        self.assertEqual(effects['factory_progress'],TurnEffect.OBSERVE)
        self.assertEqual(effects['factory_turn_budget'],TurnEffect.OBSERVE)

    async def test_every_door_reads_and_verifies_without_product_acceptance(self):
        for door in ('codex','claude','opencode'):
            with self.subTest(door=door):
                self.fixture()
                status = await self.invoke(door,'factory_progress',{})
                self.assertEqual(status['product_acceptance'],'not_established')
                result = await self.invoke(door,'factory_verify_checkpoint',{'candidate_commit':SHA})
                self.assertEqual(result['outcome'],'passed')
                self.assertEqual(result['progress']['product_acceptance'],'not_established')
                self.assertEqual(result['progress']['completed_milestones'],['first-runnable'])
                self.assertEqual(self.board.snapshot()['items'],{})
                self.post.assert_not_awaited()

    async def test_failed_checkpoint_is_retained_and_does_not_accept_or_advance(self):
        for door in ('codex','claude','opencode'):
            with self.subTest(door=door):
                self.fixture(); self.command_exit = 7
                result = await self.invoke(door,'factory_verify_checkpoint',{'candidate_commit':SHA})
                self.assertEqual(result['outcome'],'failed')
                self.assertEqual(result['progress']['completed_milestones'],[])
                self.assertEqual(result['progress']['product_acceptance'],'not_established')
                evidence = json.loads(Path(result['evidence_path']).read_text())
                self.assertEqual(evidence['execution']['exit_code'],7)
                self.assertEqual(self.board.snapshot()['items'],{})

    async def test_every_door_honors_stopped_token_and_deadline_gates(self):
        for door in ('codex','claude','opencode'):
            for reason in ('stopped','tokens','deadline'):
                with self.subTest(door=door,reason=reason):
                    self.fixture()
                    if reason == 'stopped':self.ledger.stop.set()
                    elif reason == 'tokens':self.ledger.data['tokens']=self.ledger.limits['max_total_tokens']
                    else:self.wrapped.deadline_at=time.time()-1
                    with self.assertRaises((BandToolError,FactoryError)):
                        await self.invoke(door,'factory_verify_checkpoint',{'candidate_commit':SHA})
                    self.assertEqual(self.commands,[])

    async def test_final_admitted_turn_can_complete_checkpoint(self):
        self.ledger.data['turns']['worker']=self.ledger.limits['max_turns_per_seat']
        result=await self.invoke('opencode','factory_verify_checkpoint',{'candidate_commit':SHA})
        self.assertEqual(result['outcome'],'passed')

    async def test_actor_room_and_progress_scope_mismatches_fail_before_command(self):
        for field in ('actor_id','agent_id','room_id','progress_room'):
            with self.subTest(field=field):
                self.fixture()
                if field=='progress_room':self.progress.scope['room_id']=OTHER
                elif field=='actor_id':self.wrapped.actor_id=OTHER
                elif field=='agent_id':self.wrapped.tools=AgentTools(ROOM,self.base.rest,[],agent_id=OTHER)
                else:setattr(self.base,field,OTHER)
                with self.assertRaises((BandToolError,FactoryError)):
                    await self.invoke('codex','factory_verify_checkpoint',{'candidate_commit':SHA})
                self.assertEqual(self.commands,[])

    async def test_alternate_room_argument_cannot_redirect_bound_execution(self):
        # The maintained custom-tool doors discard chat_id before dispatch. The
        # router remains bound to its admitted room; it must never follow that input.
        for door in ('claude','opencode'):
            with self.subTest(door=door):
                result=await self.invoke(door,'factory_progress',{},chat_id=OTHER)
                self.assertEqual(result['product_acceptance'],'not_established')
                self.assertEqual(self.progress.scope['room_id'],ROOM)
                self.assertEqual(self.commands,[])

    async def test_structured_ack_across_all_doors_sends_once_and_never_accepts(self):
        self.prepare_handoff()
        statuses=[]
        for door in ('codex','claude','opencode'):
            result=await self.invoke(door,'factory_handoff_ack',{'delivery_id':'DELIVERY-1'})
            self.assertTrue(result['receipt_only']);self.assertEqual(result['event_id'],uid(300))
            statuses.append(result['status'])
        self.assertEqual(statuses,['acknowledged','already_acknowledged','already_acknowledged'])
        self.post.assert_awaited_once()
        self.assertEqual(self.progress.health(execution_busy=True)['completed_milestones'],[])
        self.assertEqual(self.board.snapshot()['items'],{})

    async def test_codex_custom_ack_registration_satisfies_reply_without_fallback(self):
        self.prepare_handoff()
        adapter=CodexAdapter(CodexAdapterConfig(), emit=[], additional_tools=self.router.additional_tools)
        schemas=adapter._build_dynamic_tools(self.wrapped)
        names=[s['name'] for s in schemas]
        self.assertEqual(len(names),len(set(names)))
        self.assertTrue(OPERATIONAL <= set(names))
        reply=AsyncMock();adapter._active_room.set(ROOM)
        adapter._room_clients[ROOM]=SimpleNamespace(client=SimpleNamespace(respond=reply))
        event=SimpleNamespace(id=1,method='item/tool/call',params=dict(tool='factory_handoff_ack',
            arguments={'delivery_id':'DELIVERY-1'},callId='synthetic-ack'))
        with self.router.bind(self.wrapped):
            settled=await adapter._handle_server_request(tools=self.wrapped,msg=SimpleNamespace(),room_id=ROOM,event=event)
        self.assertTrue(settled)
        self.assertTrue(reply.await_args.args[1]['success'])
        self.post.assert_awaited_once()
        # A retained schema cannot execute after the admitted router binding ends.
        with patch('band.adapters.codex.logger.exception'):
            settled=await adapter._handle_server_request(tools=self.wrapped,msg=SimpleNamespace(),room_id=ROOM,event=event)
        self.assertFalse(settled)
        self.assertFalse(reply.await_args.args[1]['success'])
        self.post.assert_awaited_once()

    async def test_cancelled_checkpoint_is_stopped_and_reaped_before_tool_returns(self):
        started=threading.Event(); finished=threading.Event(); cancelled=threading.Event()
        def verify(candidate_commit, *, remaining_seconds):
            started.set()
            try:cancelled.wait(5)
            finally:finished.set()
            return {'outcome':'cancelled'}
        self.progress.verify_next=verify
        self.progress.cancel_verification=cancelled.set
        task=asyncio.create_task(self.wrapped.execute_tool_call_structured('factory_verify_checkpoint',{'candidate_commit':SHA}))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait,1))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertTrue(cancelled.is_set(),'Cancellation must reach the owned checkpoint worker')
            self.assertTrue(finished.is_set(),'The tool must wait for its owned checkpoint worker to exit')
        finally:
            cancelled.set()
            await asyncio.to_thread(finished.wait,1)

    async def test_cancellation_before_worker_enters_guard_cannot_be_cleared(self):
        waiting=threading.Event(); release=threading.Event()
        original_verify=self.progress.verify_next
        original_cancel=self.progress.cancel_verification
        def delayed_verify(*args, **kwargs):
            waiting.set(); release.wait(2)
            return original_verify(*args, **kwargs)
        def cancel():
            original_cancel(); release.set()
        self.progress.verify_next=delayed_verify
        self.progress.cancel_verification=cancel
        task=asyncio.create_task(self.wrapped.execute_tool_call_structured('factory_verify_checkpoint',{'candidate_commit':SHA}))
        try:
            self.assertTrue(await asyncio.to_thread(waiting.wait,1))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertNotIn(['/usr/bin/true'],self.commands)
            state=json.loads(self.progress.path.read_text())
            self.assertEqual(state['completed'],[])
        finally:
            release.set()


if __name__=='__main__':unittest.main()
