"""Admitted team tools for machine-observed runnable checkpoints."""
from __future__ import annotations

import asyncio
import json
import time

from .common import FactoryError
from .workflow_runtime import WorkflowTools


PROGRESS_TOOLS = [
    {"type": "function", "function": {
        "name": "factory_progress", "description": "Read the next runnable checkpoint and remaining progress allowance. Does not accept a stage or renew limits.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "factory_verify_checkpoint", "description": "Run the next frozen checkpoint command against this exact clean Git candidate. A pass records runnable progress only; independent acceptance is still required.",
        "parameters": {"type": "object", "properties": {"candidate_commit": {"type": "string", "pattern": "^[0-9a-f]{40}$"}},
                       "required": ["candidate_commit"], "additionalProperties": False}}},
]


class ProgressWorkflowTools(WorkflowTools):
    def __init__(self, *args, progress_guard=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_guard = progress_guard

    def get_openai_tool_schemas(self, **kwargs):
        from copy import deepcopy
        result = super().get_openai_tool_schemas(**kwargs)
        return result + deepcopy(PROGRESS_TOOLS) if self.progress_guard is not None else result

    def checkpoint_window(self):
        now = time.time()
        deadlines = [self.deadline_at]
        started = self.ledger.data.get("started_epoch")
        if started is not None:
            deadlines.append(started + self.ledger.limits["overall_timeout_seconds"])
        room_started = self.ledger.data.get("room_started_epochs", {}).get(self.ledger.room) if self.ledger.allowed_rooms else started
        if room_started is not None:
            deadlines.append(room_started + self.ledger.limits["stage_timeout_seconds"])
        return max(0, min(deadlines) - now)

    async def execute_tool_call_structured(self, tool_name, arguments):
        if tool_name not in {"factory_progress", "factory_verify_checkpoint"}:
            return await super().execute_tool_call_structured(tool_name, arguments)
        from band.runtime.tools.schema import ToolCallOutcome
        try:
            if self.progress_guard is None or self.ledger.stop.is_set() or self.ledger.reason():
                raise FactoryError("No active admitted progress allowance")
            if (self.actor_id != self.agent_id or self.room_id != self.ledger.room
                    or self.progress_guard.scope["room_id"] != self.room_id
                    or self.watchdog.scope["room_id"] != self.room_id):
                raise FactoryError("Checkpoint requires its admitted actor and room")
            turn = json.loads(self.watchdog.path.read_text())["turns"].get(self.turn_id, {})
            if turn.get("status") != "running" or turn.get("agent_id") != self.actor_id:
                raise FactoryError("Checkpoint requires a currently running admitted turn")
            remaining = self.checkpoint_window()
            if remaining <= 0:
                raise FactoryError("Checkpoint cannot exceed the current turn or session deadline")
            if tool_name == "factory_progress":
                if arguments != {}:
                    raise FactoryError("factory_progress takes no arguments")
                result = self.progress_guard.health(execution_busy=True)
            else:
                if not isinstance(arguments, dict) or set(arguments) != {"candidate_commit"}:
                    raise FactoryError("Checkpoint takes exactly candidate_commit")
                self.progress_guard.begin_verification()
                worker = asyncio.create_task(asyncio.to_thread(self.progress_guard.verify_next,
                    arguments["candidate_commit"], remaining_seconds=remaining))
                try:
                    result = await asyncio.shield(worker)
                except asyncio.CancelledError:
                    self.progress_guard.cancel_verification()
                    try:
                        await asyncio.wait_for(asyncio.shield(worker), timeout=5)
                    except BaseException:
                        self.ledger.stop.set()
                    raise
            return ToolCallOutcome(value=result, ok=True)
        except FactoryError as error:
            return ToolCallOutcome(value=str(error), ok=False, error_message="checkpoint_rejected")
