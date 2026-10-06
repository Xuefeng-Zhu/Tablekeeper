"""Offline original-sender restoration using the installed SDK's public seam."""
from pathlib import Path
import socket
import json
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, PropertyMock, patch

from band.client.rest import MessageSentResponse
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from factorykit.runtime import BudgetLedger, GateError, continuation_notice_tools
from factorykit.workflow import WorkflowWatchdog
from factorykit.workflow_runtime import send_due_notice

ROOM = '00000000-0000-4000-8000-000000000001'
PM = '00000000-0000-4000-8000-000000000002'
BACKEND = '00000000-0000-4000-8000-000000000003'
EVENT = '00000000-0000-4000-8000-000000000004'
ROSTER = [dict(id='pm', agent_id=PM, handle='owner/pm'),
          dict(id='backend', agent_id=BACKEND, handle='owner/backend')]


class ContinuationNoticeToolsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agents, self.contexts, self.links, self.callbacks = [], {}, {}, {}
        for identity in (PM, BACKEND):
            rest = SimpleNamespace(agent_api_messages=SimpleNamespace(
                create_agent_chat_message=AsyncMock(return_value=SimpleNamespace(data=MessageSentResponse(
                    id=EVENT, success=True, recipients=[dict(id=PM, handle='owner/pm')])))))
            link = SimpleNamespace(agent_id=identity, rest=rest, mark_processed=AsyncMock(),
                                   mark_processing=AsyncMock(), mark_failed=AsyncMock())
            callback = AsyncMock()
            ctx = ExecutionContext(ROOM, link, callback, agent_id=identity)
            self.contexts[identity], self.links[identity], self.callbacks[identity] = ctx, link, callback
            self.agents.append(SimpleNamespace(runtime=SimpleNamespace(
                agent_id=identity, link=link, runtime=SimpleNamespace(agent_id=identity, link=link, active_sessions={ROOM: ctx}))))
        # Construct actual contexts but never start them; only their public
        # liveness property is stubbed to represent a connected idle fixture.
        running = patch.object(ExecutionContext, 'is_running', new_callable=PropertyMock, return_value=True)
        self.running = running.start(); self.addCleanup(running.stop)
        network = patch.object(socket.socket, 'connect', side_effect=AssertionError('offline test forbids network'))
        network.start(); self.addCleanup(network.stop)

    def restore(self, available=None):
        return continuation_notice_tools(self.agents, ROOM, [PM, BACKEND], available or {})

    def assert_no_execution(self):
        for identity, link in self.links.items():
            self.callbacks[identity].assert_not_awaited()
            link.mark_processed.assert_not_awaited(); link.mark_processing.assert_not_awaited()
            link.mark_failed.assert_not_awaited()
            link.rest.agent_api_messages.create_agent_chat_message.assert_not_awaited()

    def test_public_constructor_is_local_and_preserves_execution_state(self):
        snapshots = {key: dict(ctx.__dict__) for key, ctx in self.contexts.items()}
        with patch.object(AgentTools, 'from_context', wraps=AgentTools.from_context) as constructor:
            tools = self.restore()
        self.assertEqual(constructor.call_count, 2)
        for identity, value in tools.items():
            self.assertIs(type(value), AgentTools)
            self.assertEqual((value.agent_id, value.room_id), (identity, ROOM))
            self.assertIs(value.rest, self.links[identity].rest)
            self.assertEqual(self.contexts[identity].__dict__, snapshots[identity])
        self.assert_no_execution()

    def test_existing_callback_tools_are_preserved_and_only_missing_sender_added(self):
        existing = AgentTools.from_context(self.contexts[PM])
        available = {PM: existing}
        additions = self.restore(available)
        self.assertEqual(set(additions), {BACKEND})
        self.assertEqual(available, {PM: existing})
        self.assert_no_execution()

    def test_wrong_room_is_rejected(self):
        self.contexts[BACKEND].room_id = 'other-room'
        with self.assertRaises(GateError): self.restore()
        self.assert_no_execution()

    def test_wrong_agent_identity_is_rejected(self):
        self.agents[1].runtime.agent_id = PM
        with self.assertRaises(GateError): self.restore()
        self.assert_no_execution()

    def test_wrong_context_agent_is_rejected(self):
        ctx = ExecutionContext(ROOM, self.links[BACKEND], AsyncMock(), agent_id=PM)
        self.agents[1].runtime.runtime.active_sessions[ROOM] = ctx
        with self.assertRaises(GateError): self.restore()
        self.assert_no_execution()

    def test_replaced_link_is_rejected(self):
        self.agents[1].runtime.link = SimpleNamespace(agent_id=BACKEND, rest=self.links[BACKEND].rest)
        with self.assertRaises(GateError): self.restore()
        self.assert_no_execution()

    def test_link_agent_mismatch_is_rejected(self):
        self.links[BACKEND].agent_id = PM
        with self.assertRaises(GateError): self.restore()
        self.assert_no_execution()

    def test_sdk_runtime_identity_or_link_mismatch_is_rejected(self):
        sdk_runtime = self.agents[1].runtime.runtime
        sdk_runtime.agent_id = PM
        with self.assertRaises(GateError): self.restore()
        sdk_runtime.agent_id = BACKEND
        sdk_runtime.link = SimpleNamespace(agent_id=BACKEND, rest=self.links[BACKEND].rest)
        with self.assertRaises(GateError): self.restore()
        self.assert_no_execution()

    def test_existing_misbound_tools_are_rejected_without_replacement(self):
        for mismatch in ('rest', 'room', 'agent'):
            with self.subTest(mismatch=mismatch):
                tools = AgentTools.from_context(self.contexts[PM])
                if mismatch == 'rest': tools.rest = object()
                if mismatch == 'room': tools.room_id = 'other-room'
                if mismatch == 'agent': tools = AgentTools(ROOM, self.links[PM].rest, agent_id=BACKEND)
                available = {PM: tools}
                with self.assertRaises(GateError): self.restore(available)
                self.assertIs(available[PM], tools)
                self.assertEqual(set(available), {PM})
        self.assert_no_execution()

    def test_stopped_context_is_rejected(self):
        self.running.return_value = False
        with self.assertRaises(GateError): self.restore()
        self.assert_no_execution()

    def test_missing_or_noncanonical_context_is_rejected_without_partial_update(self):
        for replacement in (None, SimpleNamespace(is_running=True, room_id=ROOM, agent_id=BACKEND,
                                                   link=self.links[BACKEND])):
            with self.subTest(replacement=replacement):
                self.agents[1].runtime.runtime.active_sessions[ROOM] = replacement
                available = {}
                with self.assertRaises(GateError): self.restore(available)
                self.assertEqual(available, {})
        self.assert_no_execution()

    def test_missing_roster_member_is_rejected(self):
        self.agents.pop()
        with self.assertRaises(GateError): self.restore()
        self.assert_no_execution()

    def test_constructor_result_must_retain_original_rest_room_and_agent(self):
        for mismatch in ('rest', 'room', 'agent'):
            with self.subTest(mismatch=mismatch):
                tools = AgentTools.from_context(self.contexts[PM])
                if mismatch == 'rest': tools.rest = object()
                if mismatch == 'room': tools.room_id = 'other-room'
                if mismatch == 'agent': tools = AgentTools(ROOM, self.links[PM].rest, agent_id=BACKEND)
                with patch.object(AgentTools, 'from_context', return_value=tools):
                    with self.assertRaises(GateError): self.restore()
        self.assert_no_execution()

    async def test_restored_sender_uses_unchanged_bounded_notice_without_new_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = time.time()
            watchdog = WorkflowWatchdog(root/'workflow.json', ROOM, PM, [PM, BACKEND], 120)
            watchdog.begin_turn(BACKEND, 'backend-interrupted', now+600, trigger_event_id=EVENT)
            watchdog.end_turn('backend-interrupted', 'interrupted', 'interrupted')
            ledger = BudgetLedger(dict(max_active_seats=1, overall_timeout_seconds=10000,
                stage_timeout_seconds=10000, max_turns_per_seat=100, max_total_tokens=1000000,
                turn_timeout_seconds=600, max_repairs=2), root/'budget.json', ROOM)
            before_workflow, before_budget = watchdog.path.read_bytes(), ledger.path.read_bytes()
            tools = self.restore()
            self.assertEqual(watchdog.path.read_bytes(), before_workflow)
            self.assertEqual(ledger.path.read_bytes(), before_budget)
            self.assert_no_execution()
            self.assertEqual(await send_due_notice(watchdog, ledger, tools, ROSTER), 'sent')
            post = self.links[BACKEND].rest.agent_api_messages.create_agent_chat_message
            self.assertEqual(post.await_count, 1)
            self.assertEqual(post.await_args.kwargs['chat_id'], ROOM)
            self.assertEqual(post.await_args.kwargs['request_options']['max_retries'], 0)
            self.assertEqual(await send_due_notice(watchdog, ledger, tools, ROSTER), 'none')
            self.assertEqual(post.await_count, 1)
            self.assertEqual(ledger.path.read_bytes(), before_budget)
            self.assertEqual(json.loads(watchdog.path.read_bytes())['turns'], json.loads(before_workflow)['turns'])
            for callback in self.callbacks.values(): callback.assert_not_awaited()
            for link in self.links.values():
                link.mark_processed.assert_not_awaited(); link.mark_processing.assert_not_awaited()
                link.mark_failed.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
