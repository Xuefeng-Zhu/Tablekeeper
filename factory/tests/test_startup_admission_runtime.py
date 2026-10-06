"""Offline seven-seat runtime callback ordering; health proof tested separately."""
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from band import Agent
from band.core.types import AgentInput, TurnUsage
from band.runtime.tools.agent import AgentTools
from factorykit import runtime, harnesses
from factorykit.startup_admission import AdmissionController, request_release
from tests import test_runtime_harness_integration as fixtures


class StartupRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = fixtures.SupervisorHarnessTests(); self.f.setUp()
        self._cleanups.extend(self.f._cleanups); self.f._cleanups.clear()
        self.f.root = self.f.root.resolve()
        self.config = self.f.configuration('codex')
        self.config['runtime']['strict_membership_recovery'] = True
        template = self.config['seats'][0]
        self.config['seats'] = [dict(template, id=name, display_name=name,
            agent_id=f'00000000-0000-4000-8000-{index:012d}', handle='owner/'+name)
            for index, name in enumerate(('pm','architect','designer','backend','frontend','qa','reviewer'), 1)]
        self.parent = {'pid': os.getpid(), 'created': 1, 'cmdline': []}
        runtime.save_json(runtime.registry_path(self.config), {'token': 'owner', 'status': 'starting',
            'parent': self.parent, 'mode': 'rehearsal', 'config_sha256': runtime.fingerprint(self.config)})
        self.calls, self.starts, self.stops, self.pending = [], [], [], []
        self.ledger = runtime.session_ledger(self.config, 'rehearsal')

    async def exercise(self, *, hold=False, fail_close=False, exhausted=False, all_exhausted=False):
        test = self
        backend = harnesses.adapter_class(self.config)
        if exhausted:
            self.ledger.data['turns']['pm'] = self.config['budgets']['max_turns_per_seat']
        if all_exhausted:
            self.ledger.data['turns'] = {s['id']: self.config['budgets']['max_turns_per_seat'] for s in self.config['seats']}
        self.ledger.save()
        async def provider(adapter, inp):
            self.calls.append('provider')
            self.assertEqual(len(self.starts), 7)
            await adapter.emit_usage(inp.tools, TurnUsage(input_tokens=2, output_tokens=1))
            await inp.tools.send_event('usage', 'task', {'codex_thread_id':'synthetic', 'codex_total_tokens':3})
            self.ledger.stop.set()
        class Observer:
            state = {'blocked_reason': None}
            def __init__(self, *args): pass
            def _validate(self): pass
            def _active(self): return None
            async def full_ready(self, agents):
                test.assertEqual(len(test.starts), 7)
                test.assertEqual(test.calls, [])
                return {'busy': True, 'contexts': [], 'membership_gap': None, 'authenticated_roster': {'complete': True}}
            async def snapshot(self, agents):
                return {'busy': True, 'contexts': [], 'membership_gap': None}
        class Transport:
            def __init__(self, adapter, agent_id):
                self.adapter, self.agent_id = adapter, agent_id
                self.agent_name = next(s['display_name'] for s in test.config['seats'] if s['agent_id']==agent_id)
            async def start(self):
                test.starts.append(self.agent_name)
                if self.agent_name == 'pm':
                    tools = AgentTools(fixtures.ROOM, None, [], agent_id=self.agent_id)
                    tools.send_event = AsyncMock(return_value={'ok': True})
                    inp = AgentInput(msg=SimpleNamespace(id=fixtures.EVENT, content='queued original packet'),
                        tools=tools, history=None, participants_msg=None, contacts_msg=None,
                        is_session_bootstrap=True, room_id=fixtures.ROOM)
                    task = asyncio.create_task(self.adapter.on_event(inp)); test.pending.append(task)
                    await asyncio.sleep(0)
                    test.assertFalse(task.done()); test.assertEqual(test.calls, [])
                    test.assertFalse(test.ledger.semaphore.locked())
            async def stop(self, timeout):
                test.stops.append(self.agent_name)
                for task in test.pending: task.cancel()
                await asyncio.gather(*test.pending, return_exceptions=True)
        async def release():
            for _ in range(100):
                record = runtime.read_registry(self.config)
                if record.get('status') == 'ready_held':
                    self.assertEqual(self.calls, [])
                    self.assertEqual(self.ledger.data['turns'], {})
                    request_release(self.config, record)
                    return
                await asyncio.sleep(.01)
            self.fail('No held readiness published')
        original_close = AdmissionController.close
        async def broken_close(controller):
            await original_close(controller)
            raise OSError('simulated admission journal failure')
        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime, 'require_ready'))
            stack.enter_context(patch.object(runtime, 'is_owned', return_value=True))
            stack.enter_context(patch.object(runtime, 'credentials', return_value={s['id']:{'agent_id':s['agent_id'],'api_key':'fake'} for s in self.config['seats']}))
            stack.enter_context(patch.object(runtime, 'session_ledger', return_value=self.ledger))
            stack.enter_context(patch.object(runtime.psutil, 'Process', return_value=SimpleNamespace(children=lambda **kw: [])))
            def create(**kwargs):
                test.assertEqual(kwargs['session_config'].max_cycle_seconds,
                                 test.config['budgets']['turn_timeout_seconds'] + 20 + (385 if hold else 325))
                return Transport(kwargs['adapter'], kwargs['agent_id'])
            stack.enter_context(patch.object(Agent, 'create', side_effect=create))
            stack.enter_context(patch.object(backend, 'on_event', provider))
            stack.enter_context(patch('factorykit.membership_gap.MembershipGapObserver', Observer))
            if fail_close: stack.enter_context(patch.object(AdmissionController, 'close', broken_close))
            release_task = asyncio.create_task(release()) if hold else None
            try:
                await asyncio.wait_for(runtime.serve(self.config, 'rehearsal', 'owner', hold_admission=hold), 4)
            finally:
                if release_task:
                    await release_task
                for sig in (signal.SIGINT, signal.SIGTERM): asyncio.get_running_loop().remove_signal_handler(sig)

    async def test_automatic_seven_start_barrier_preserves_queued_callback_and_accounting(self):
        await self.exercise()
        self.assertEqual(self.calls, ['provider']); self.assertEqual(len(self.stops), 7)
        self.assertEqual(self.ledger.data['turns'], {'pm': 1}); self.assertEqual(self.ledger.data['tokens'], 3)

    async def test_manual_hold_waits_without_turn_then_owned_release_executes_once(self):
        await self.exercise(hold=True)
        self.assertEqual(self.calls, ['provider']); self.assertEqual(len(self.stops), 7)
        self.assertEqual(self.ledger.data['turns'], {'pm': 1})

    async def test_close_journal_failure_still_stops_every_seat(self):
        with self.assertRaisesRegex(runtime.GateError, 'close evidence failed'):
            await self.exercise(fail_close=True)
        self.assertEqual(len(self.stops), 7)

    async def test_exhausted_claimed_seat_cancels_without_provider_or_success(self):
        await self.exercise(exhausted=True)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.pending[0].cancelled())
        self.assertEqual(self.ledger.data['turns']['pm'], self.config['budgets']['max_turns_per_seat'])

    async def test_all_seats_exhausted_cannot_start_or_release(self):
        with self.assertRaises(runtime.GateError): await self.exercise(all_exhausted=True)
        self.assertEqual(self.starts, []); self.assertEqual(self.calls, [])

    async def test_normal_restart_restores_exact_sender_for_retained_notice_without_model(self):
        import hashlib
        import time
        from unittest.mock import PropertyMock
        from band.client.rest import MessageSentResponse
        from band.runtime.execution import ExecutionContext
        from factorykit.workflow import WorkflowWatchdog
        self.config['runtime']['strict_membership_recovery'] = False
        runtime.save_json(runtime.registry_path(self.config), {'token': 'owner', 'status': 'starting',
            'parent': self.parent, 'mode': 'rehearsal', 'config_sha256': runtime.fingerprint(self.config)})
        pm, recipient = [s['agent_id'] for s in self.config['seats'][:2]]
        original_id = '00000000-0000-4000-8000-000000000101'
        notice_id = '00000000-0000-4000-8000-000000000102'
        roster = [s['agent_id'] for s in self.config['seats']]
        watchdog = WorkflowWatchdog(runtime.state_dir(self.config)/f'workflow-{fixtures.ROOM}.json',
            fixtures.ROOM, pm, roster, self.config['budgets']['ack_timeout_seconds'], clock=lambda: time.time()-60)
        body = 'original work remains unchanged'
        digest = hashlib.sha256(body.encode()).hexdigest()
        content = f'WORK-1 delivery RETAINED-1 part 1/1; SHA-256 {digest}; recipient @[[{recipient}]]\n{body}\nEND OF HANDOFF'
        watchdog.begin_turn(pm, 'old-completed', time.time()+600)
        watchdog.observe_outbound(original_id, pm, [recipient], content, 'old-completed')
        watchdog.end_turn('old-completed', 'completed')
        before_workflow = json.loads(watchdog.path.read_text())
        from factorykit.handoff_batching import HandoffJournal
        for seat in self.config['seats']:
            HandoffJournal(runtime.state_dir(self.config)/f"handoffs-{fixtures.ROOM}-{seat['id']}.json",
                fixtures.ROOM, seat['agent_id'], roster)
        before_budget = self.ledger.path.read_bytes()
        posts, contexts, callbacks, agents = [], [], [], []
        async def post(**kwargs):
            posts.append(kwargs); self.ledger.stop.set()
            return SimpleNamespace(data=MessageSentResponse(id=notice_id, success=True,
                recipients=[dict(id=recipient, handle='owner/architect')]))
        class Observer:
            def __init__(self, *args): pass
            async def full_ready(self, agents):
                return {'busy': False, 'contexts': [], 'authenticated_roster': {'complete': True}}
            async def snapshot(self, agents):
                return {'busy': False, 'contexts': []}
        test = self
        class Transport:
            def __init__(self, identity):
                rest = SimpleNamespace(agent_api_messages=SimpleNamespace(create_agent_chat_message=AsyncMock(side_effect=post)))
                link = SimpleNamespace(agent_id=identity, rest=rest)
                callback = AsyncMock(); ctx = ExecutionContext(fixtures.ROOM, link, callback, agent_id=identity)
                callbacks.append(callback); contexts.append(ctx)
                self.runtime = SimpleNamespace(agent_id=identity, link=link,
                    runtime=SimpleNamespace(agent_id=identity, link=link, active_sessions={fixtures.ROOM: ctx}))
                self.agent_name = next(s['id'] for s in test.config['seats'] if s['agent_id']==identity)
            async def start(self): test.starts.append(self.agent_name)
            async def stop(self, timeout): test.stops.append(self.agent_name)
        def create(**kwargs):
            agent = Transport(kwargs['agent_id']); agents.append(agent); return agent
        backend = harnesses.adapter_class(self.config)
        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime, 'require_ready'))
            stack.enter_context(patch.object(runtime, 'is_owned', return_value=True))
            stack.enter_context(patch.object(runtime, 'credentials', return_value={s['id']:{'agent_id':s['agent_id'],'api_key':'fake'} for s in self.config['seats']}))
            stack.enter_context(patch.object(runtime, 'session_ledger', return_value=self.ledger))
            stack.enter_context(patch.object(runtime.psutil, 'Process', return_value=SimpleNamespace(children=lambda **kw: [])))
            stack.enter_context(patch.object(ExecutionContext, 'is_running', new_callable=PropertyMock, return_value=True))
            stack.enter_context(patch.object(Agent, 'create', side_effect=create))
            provider = stack.enter_context(patch.object(backend, 'on_event', new_callable=AsyncMock))
            stack.enter_context(patch('factorykit.membership_advisory.AdvisoryMembershipObserver', Observer))
            try:
                await asyncio.wait_for(runtime.serve(self.config, 'rehearsal', 'owner'), 4)
            finally:
                for sig in (signal.SIGINT, signal.SIGTERM): asyncio.get_running_loop().remove_signal_handler(sig)
        self.assertEqual(len(self.starts), 7); self.assertEqual(len(self.stops), 7)
        self.assertEqual(len(posts), 1); self.assertEqual(posts[0]['chat_id'], fixtures.ROOM)
        self.assertEqual([m.id for m in posts[0]['message'].mentions], [recipient])
        self.assertEqual(posts[0]['request_options']['max_retries'], 0)
        for agent in agents:
            sender_post = agent.runtime.link.rest.agent_api_messages.create_agent_chat_message
            self.assertEqual(sender_post.await_count, 1 if agent.runtime.agent_id == pm else 0)
        provider.assert_not_awaited()
        for callback in callbacks: callback.assert_not_awaited()
        self.assertEqual(self.ledger.path.read_bytes(), before_budget)
        final = json.loads(watchdog.path.read_text())
        self.assertEqual(final['turns'], before_workflow['turns'])
        self.assertEqual(final['events'][original_id], before_workflow['events'][original_id])
        self.assertNotIn('recovery_blocker', runtime.read_registry(self.config)['workflow'])


if __name__ == '__main__': unittest.main()
