"""Exercise the real supervisor/admission/configuration path without providers.

Only BAND transport startup, local server startup and the provider on_event
boundary are replaced. Adapters, configuration, wrappers, accounting, watchdog
and supervisor cleanup execute their real implementation.
"""
from contextlib import asynccontextmanager
from dataclasses import replace
import json
from pathlib import Path
import signal
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from band import Agent
from band.core.types import AgentInput, Emit, TurnUsage
from band.runtime.tools.agent import AgentTools
from band_sdk_core import AgentFailure
from pydantic import SecretStr

from factorykit import harnesses, runtime

ROOM = "00000000-0000-4000-8000-000000000001"
OTHER_ROOM = "00000000-0000-4000-8000-000000000002"
PM = "00000000-0000-4000-8000-000000000003"
EVENT = "00000000-0000-4000-8000-000000000004"


class SupervisorHarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.mandate = self.root / "mandate.md"
        self.mandate.write_text("Generic test seat mandate.\n")
        # Any accidental network connection at these replaced boundaries is a
        # test failure, not a tolerated source of external side effects.
        blocker = patch.object(socket.socket, "connect", side_effect=AssertionError("Integration test must stay offline"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def configuration(self, harness):
        model = {"codex": "test-codex-model", "claude-code": "sonnet", "opencode": "anthropic/test-model"}[harness]
        return {
            "paths": {"factory": str(self.root), "runs": str(self.root / harness),
                      "rehearsal": str(self.root), "result": str(self.root)},
            "runtime": {"harness": harness, "model": model,
                        "codex_command": "/not-executed/codex", "claude_command": "/not-executed/claude",
                        "opencode_command": "/not-executed/opencode", "approval_policy": "never",
                        "sandbox": "workspace-write" if harness == "codex" else "native-policy",
                        "native_permissions": dict(read=True, write=False, bash=False, network=False)},
            "band": {"rehearsal_room_id": ROOM, "judged_room_id": OTHER_ROOM,
                     "rest_url": "https://never-called.invalid", "ws_url": "wss://never-called.invalid"},
            "budgets": {"billing_mode": "spend_cap", "max_active_seats": 1,
                        "turn_timeout_seconds": 120, "stage_timeout_seconds": 600,
                        "overall_timeout_seconds": 600, "max_turns_per_seat": 10,
                        "max_total_tokens": 10000, "max_repairs": 2, "ack_timeout_seconds": 30},
            "seats": [{"id": "pm", "agent_id": PM, "display_name": "Factory PM", "handle": "test/pm",
                       "mandate": str(self.mandate), "harness": harnesses.harness_label(harness), "model": model,
                       "reasoning_effort": None if harness == "opencode" else "high",
                       "git_name": "Factory PM", "git_email": "pm@factory.invalid"}],
        }

    async def exercise(self, harness, *, failure=False, failure_report_lost=False, start_failure=False):
        cfg = self.configuration(harness)
        original_fingerprint = runtime.fingerprint(cfg)
        runtime.save_json(runtime.registry_path(cfg), {"token": "owned-test-token", "status": "starting"})
        backend = harnesses.adapter_class(cfg)
        captured = {"calls": [], "agents": [], "contexts": [], "configs": []}
        original_ledger = runtime.session_ledger
        original_config = runtime.adapter_config
        parent_test = self

        def ledger_factory(*args, **kwargs):
            ledger = original_ledger(*args, **kwargs)
            captured["ledger"] = ledger
            return ledger

        def real_config(*args, **kwargs):
            result = original_config(*args, **kwargs)
            captured["configs"].append(result)
            return result

        @asynccontextmanager
        async def owned_server_boundary(config, mode, seats):
            parent_test.assertIs(config, cfg)
            captured["contexts"].append("entered")
            live = {**cfg, "runtime": dict(cfg["runtime"])}
            if harness == "opencode":
                live["runtime"]["_opencode_endpoints"] = {
                    "pm": {"url": "http://127.0.0.1:43210", "password": SecretStr("never-persist-this")}}
            try:
                yield live
            finally:
                captured["contexts"].append("exited")

        async def provider_boundary(adapter, inp):
            captured["calls"].append(inp)
            parent_test.assertEqual(inp.tools.harness, harness)
            parent_test.assertEqual(inp.tools.agent_id, PM)
            parent_test.assertEqual(inp.tools.room_id, ROOM)
            usage = TurnUsage(input_tokens=10, output_tokens=6, cache_read_tokens=4, cache_write_tokens=3)
            await adapter.emit_usage(inp.tools, usage)
            await adapter.emit_usage(inp.tools, usage)  # Replay must not double count.
            if harness == "codex":
                metadata = {"codex_thread_id": "test-thread", "codex_total_tokens": 23}
                await inp.tools.send_event("usage", "task", metadata)
                await inp.tools.send_event("usage replay", "task", metadata)
            elif harness == "claude-code":
                await adapter._persist_session_id("test-claude-session", ROOM, inp.tools)
            else:
                state = SimpleNamespace(tools=inp.tools, session_id="test-opencode-session", room_id=ROOM,
                                        persisted_session_id=None)
                await adapter._emit_session_task_event(state, status="created")
            if failure:
                if failure_report_lost:
                    inp.tools.tools.send_event.side_effect = RuntimeError("simulated room outage")
                # The real SDK failure reporter handles the lost broadcast;
                # observed_turn must still retain failure on normal return.
                await inp.tools.send_failure(AgentFailure(harness if harness != "claude-code" else "claude_sdk", "provider rejected"))
                if not failure_report_lost:
                    await inp.tools.send_event("late completed event", "task", {
                        "factory_event_type": "turn_lifecycle", "factory_turn_status": "completed"})

        class LocalAgentTransport:
            def __init__(self, adapter, agent_id):
                self.adapter = adapter
                self.agent_id = agent_id
                self.agent_name = "Factory PM"
                self.stopped = False

            async def start(self):
                if start_failure:
                    raise RuntimeError("simulated transport startup failure")
                # The SDK's normal preprocessor uses AgentTools.from_context,
                # which binds ctx.agent_id as well as ctx.room_id.
                tools = AgentTools(ROOM, None, [], agent_id=self.agent_id)
                tools.send_event = AsyncMock(return_value={"ok": True})
                inp = AgentInput(msg=SimpleNamespace(id=EVENT, content="Do a bounded test turn"), tools=tools,
                                 history=None, participants_msg=None, contacts_msg=None,
                                 is_session_bootstrap=True, room_id=ROOM)
                await self.adapter.on_event(replace(inp, room_id=OTHER_ROOM))
                await self.adapter.on_event(replace(inp, msg=SimpleNamespace(id=EVENT, content="/model changed")))
                await self.adapter.on_event(inp)
                captured["ledger"].stop.set()

            async def stop(self, timeout):
                self.stopped = True
                await self.adapter.on_cleanup(ROOM)

        def create_agent(**kwargs):
            parent_test.assertIsInstance(kwargs["adapter"], backend)
            parent_test.assertIsInstance(kwargs["preprocessor"], runtime.RoomPreprocessor)
            parent_test.assertEqual(kwargs["agent_id"], PM)
            agent = LocalAgentTransport(kwargs["adapter"], kwargs["agent_id"])
            captured["agents"].append(agent)
            return agent

        with patch.object(runtime, "require_ready") as ready, \
             patch.object(runtime, "credentials", return_value={"pm": {"agent_id": PM, "api_key": "test-only"}}), \
             patch.object(runtime, "session_ledger", side_effect=ledger_factory), \
             patch.object(runtime, "adapter_config", side_effect=real_config), \
             patch.object(runtime.psutil, "Process", return_value=SimpleNamespace(children=lambda **kwargs: [])), \
             patch.object(Agent, "create", side_effect=create_agent), \
             patch.object(backend, "on_event", provider_boundary), \
             patch.object(harnesses, "start_runtime", owned_server_boundary):
            try:
                if start_failure:
                    with self.assertRaisesRegex(RuntimeError, "startup failure"):
                        await runtime.serve(cfg, "rehearsal", "owned-test-token")
                else:
                    await runtime.serve(cfg, "rehearsal", "owned-test-token")
                ready.assert_called_once_with(cfg, "rehearsal")
            finally:
                # serve installs handlers for an owned long-running process;
                # restore this test loop's handler registry after exercising it.
                import asyncio
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(sig)

        self.assertTrue(captured["agents"][0].stopped)
        self.assertEqual(runtime.read_registry(cfg)["status"], "stopped")
        self.assertEqual(runtime.fingerprint(cfg), original_fingerprint)
        self.assertNotIn("never-persist-this", runtime.registry_path(cfg).read_text())
        self.assertEqual(captured["contexts"], [] if harness == "codex" else ["entered", "exited"])
        if start_failure:
            return
        self.assertEqual(len(captured["calls"]), 1)
        self.assertEqual(captured["ledger"].data["turns"], {"pm": 1})
        self.assertEqual(captured["ledger"].data["tokens"], 23)
        conf = captured["configs"][0]
        if harness == "opencode":
            self.assertEqual((conf.provider_id, conf.model_id), ("anthropic", "test-model"))
        else:
            self.assertEqual(conf.model, cfg["runtime"]["model"])
        emitted = captured["agents"][0].adapter.features.emit
        self.assertIn(Emit.USAGE, emitted)
        self.assertEqual(Emit.TASK_EVENTS in emitted, harness != "claude-code")
        workflow = json.loads((runtime.state_dir(cfg) / f"workflow-{ROOM}.json").read_text())
        turns = list(workflow["turns"].values())
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["status"], "failed" if failure else "completed")
        if failure:
            self.assertEqual(turns[0]["reason_code"], "provider_failure")

    async def test_all_backends_use_real_configuration_guards_accounting_and_cleanup(self):
        for harness in ("codex", "claude-code", "opencode"):
            with self.subTest(harness=harness):
                await self.exercise(harness)

    async def test_failed_alternate_turn_cannot_be_completed_by_late_event(self):
        for harness in ("claude-code", "opencode"):
            with self.subTest(harness=harness):
                await self.exercise(harness, failure=True)

    async def test_failed_room_reporting_preserves_failure_and_cleanup(self):
        for harness in ("claude-code", "opencode"):
            with self.subTest(harness=harness):
                await self.exercise(harness, failure=True, failure_report_lost=True)

    async def test_owned_auxiliary_runtime_and_agent_cleanup_when_start_fails(self):
        await self.exercise("opencode", start_failure=True)

    async def test_non_codex_continuation_and_recovery_rejected_before_server_or_transport(self):
        for harness in ("claude-code", "opencode"):
            for argument in ({"recovery_id": "existing-recovery"}, {"continuation": object()}):
                with self.subTest(harness=harness, argument=next(iter(argument))):
                    cfg = self.configuration(harness)
                    with patch.object(runtime, "_serve", AsyncMock()) as serve, \
                         patch.object(harnesses, "start_runtime") as servers, \
                         patch.object(runtime, "require_ready") as ready:
                        with self.assertRaisesRegex(runtime.GateError, "bind Codex threads"):
                            await runtime.serve(cfg, "judged", "test-token", **argument)
                        serve.assert_not_called()
                        servers.assert_not_called()
                        ready.assert_not_called()


if __name__ == "__main__":
    unittest.main()
