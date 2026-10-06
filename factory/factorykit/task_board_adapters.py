"""Room- and actor-bound task tools for the SDK's Claude/OpenCode MCP doors.

Those adapters use ``additional_tools``, not AgentTools' OpenAI schemas or its
structured dispatcher. Give each adapter its own router and bind its admitted
WorkflowTools only for the duration of the guarded callback. Imports are inert.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from .common import FactoryError
from .task_board import ITEM_SCHEMA, TOOLS


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Factory_Work_ItemInput(_Input):
    """Publish a complete work item through this admitted actor's guarded board.

    PM creates; the owner delivers; the nominated independent reviewer decides.
    Read factory_task_board for versions. This does not send a room handoff.
    """

    item: dict[str, Any] = Field(json_schema_extra=deepcopy(ITEM_SCHEMA))
    expected_version: StrictInt = Field(ge=0)


class Factory_Task_BoardInput(_Input):
    """Read a compact task index; pass id for its complete authoritative work item."""

    id: StrictStr | None = None


class Factory_Reconcile_TaskInput(_Input):
    """Read an exact BAND task to reconcile an uncertain write; never resend it."""

    id: StrictStr
    task_id: StrictStr


class Factory_Handoff_AckInput(_Input):
    """Send one exact receipt for a complete verified handoff; never accept work."""
    delivery_id: StrictStr


class Factory_Turn_BudgetInput(_Input):
    """Read the current admitted turn deadline without extending it."""


class Factory_ProgressInput(_Input):
    """Read remaining allowance for the next runnable checkpoint."""


class Factory_Verify_CheckpointInput(_Input):
    """Execute the next frozen checkpoint against an exact clean candidate."""
    candidate_commit: StrictStr = Field(pattern=r"^[0-9a-f]{40}$")


_MODELS = (Factory_Work_ItemInput, Factory_Task_BoardInput, Factory_Reconcile_TaskInput)


class TaskBoardAdapterTools:
    """One router per seat adapter, with no process-wide tools or credentials.

    Integration::

        router = TaskBoardAdapterTools(room_id, seat["agent_id"])
        adapter = Adapter(..., additional_tools=router.additional_tools)
        with router.bind(wrapped_workflow_tools):
            await guarded_admitted_callback(...)

    The native mutation methods must also be blocked on WorkflowTools: the SDK
    dispatches builtins directly and does not consult its structured dispatcher.
    """

    def __init__(self, room_id: str, actor_id: str, *, include_operational=False, include_progress=False):
        from band.runtime.custom_tools import declares_turn_effect
        from band.runtime.tools.types import TurnEffect

        self.room_id, self.actor_id = room_id, actor_id
        self._bound = None
        self._definitions = []
        for model, schema in zip(_MODELS, TOOLS, strict=True):
            name = schema["function"]["name"]

            def handler_for(tool_name):
                async def handler(arguments):
                    return await self._dispatch(tool_name, arguments)
                return handler

            # A board update cannot satisfy the separate room-handoff duty.
            handler = declares_turn_effect(TurnEffect.OBSERVE)(handler_for(name))
            self._definitions.append((model, handler))
        if include_operational:
            for model, name in ((Factory_Handoff_AckInput, "factory_handoff_ack"),
                                (Factory_Turn_BudgetInput, "factory_turn_budget")):
                handler = declares_turn_effect(TurnEffect.REPLY if name == "factory_handoff_ack" else TurnEffect.OBSERVE)(handler_for(name))
                self._definitions.append((model, handler))
        if include_progress:
            for model, name in ((Factory_ProgressInput, "factory_progress"),
                                (Factory_Verify_CheckpointInput, "factory_verify_checkpoint")):
                handler = declares_turn_effect(TurnEffect.ACT if name == "factory_verify_checkpoint" else TurnEffect.OBSERVE)(handler_for(name))
                self._definitions.append((model, handler))

    @property
    def additional_tools(self):
        return list(self._definitions)

    def _check(self, tools):
        if (tools is None or getattr(tools, "actor_id", None) != self.actor_id
                or getattr(tools, "agent_id", None) != self.actor_id
                or getattr(tools, "room_id", None) != self.room_id
                or getattr(tools, "task_board", None) is None):
            raise FactoryError("Task tools require the current admitted actor and room")
        if tools.board_window() <= 0:
            raise FactoryError("No approved time remains for this task tool")

    @contextmanager
    def bind(self, tools):
        if self._bound is not None:
            raise FactoryError("Task tools already have an admitted turn")
        self._check(tools)
        self._bound = tools
        try:
            yield self
        finally:
            self._bound = None

    async def _dispatch(self, name, arguments):
        from band.core.exceptions import BandToolError

        try:
            tools = self._bound
            self._check(tools)
            outcome = await tools.execute_tool_call_structured(name, arguments.model_dump())
            if not outcome.ok:
                raise BandToolError(str(outcome.value))
            return outcome.value
        except FactoryError as error:
            raise BandToolError(str(error)) from None
