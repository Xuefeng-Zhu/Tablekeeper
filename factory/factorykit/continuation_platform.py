"""Pinned BAND 4.0 ingress filter for explicitly reconciled terminal triggers.

No SDK files or server receipts are changed. Use via
Agent(runtime=ReceiptPreservingPlatformRuntime(...), adapter=..., preprocessor=...).
The default SDK AgentRuntime/ExecutionContext and their lifecycle stay intact.
"""
from __future__ import annotations

import asyncio
from importlib.metadata import version
from uuid import UUID

from band.core.types import metadata_to_dict
from band.platform.event import MessageEvent
from band.platform.link import BandLink
from band.runtime.platform_runtime import PlatformRuntime
from band.runtime.types import PlatformMessage

from .common import FactoryError


def _identity(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise FactoryError("Continuation filter requires canonical UUIDs") from None
    return value


class ReceiptPreservingBandLink(BandLink):
    """Exclude only bound terminal IDs before SDK execution or receipt marking.

GET /next cannot exclude an ID. If its oldest item is excluded, cursor-scan
the documented actionable-message GET instead. This avoids blocking newer
work behind an intentionally retained failed receipt, including after reconnect.
"""

    def __init__(self, *args, continuation_room_id, excluded_event_ids,
                 on_filter_failure, **kwargs):
        self.continuation_room_id = _identity(continuation_room_id)
        self.excluded_event_ids = frozenset(_identity(x) for x in excluded_event_ids)
        if not callable(on_filter_failure):
            raise FactoryError("Continuation filter requires a fail-closed callback")
        self.on_filter_failure = on_filter_failure
        super().__init__(*args, **kwargs)

    def excluded(self, room_id, event_id):
        return room_id == self.continuation_room_id and event_id in self.excluded_event_ids

    def _fail(self):
        self.on_filter_failure("continuation ingress or receipt guard failed")
        raise FactoryError("Continuation ingress or receipt guard failed")

    async def __anext__(self):
        while True:
            event = await super().__anext__()
            if (isinstance(event, MessageEvent) and event.payload is not None
                    and self.excluded(event.room_id, event.payload.id)):
                continue
            return event

    async def get_next_message(self, room_id):
        first = await super().get_next_message(room_id)
        if first is None or not self.excluded(room_id, first.id):
            return first
        cursor, seen_cursors = None, set()
        try:
            async with asyncio.timeout(20):
                for _ in range(10):
                    kwargs = dict(chat_id=room_id, sort_order="asc", limit=100,
                                  request_options={"max_retries": 0, "timeout_in_seconds": 5})
                    if cursor:
                        kwargs["cursor"] = cursor
                    response = await self.rest.agent_api_messages.list_agent_messages(**kwargs)
                    for item in response.data:
                        if item.chat_room_id not in (None, room_id):
                            raise FactoryError("Actionable message belongs to another room")
                        _identity(item.id)
                        if self.excluded(room_id, item.id):
                            continue
                        if item.inserted_at is None or item.inserted_at.tzinfo is None:
                            raise FactoryError("Actionable message has no authoritative timestamp")
                        metadata = metadata_to_dict(item.metadata, exclude_none=True)
                        own = (metadata.get("delivery_status") or {}).get(self.agent_id, {})
                        if own.get("status") == "processed":
                            continue
                        return PlatformMessage(id=item.id, room_id=room_id, content=item.content,
                            sender_id=item.sender_id, sender_type=item.sender_type,
                            sender_name=item.sender_name or "", message_type=item.message_type,
                            metadata=metadata, created_at=item.inserted_at)
                    if not response.metadata.has_more:
                        return None
                    cursor = response.metadata.next_cursor
                    if not cursor or cursor in seen_cursors:
                        raise FactoryError("Actionable-message pagination is incomplete")
                    seen_cursors.add(cursor)
                raise FactoryError("Actionable-message scan exceeded its bound")
        except Exception:
            self._fail()

    async def get_stale_processing_messages(self, room_id):
        return [msg for msg in await super().get_stale_processing_messages(room_id)
                if not self.excluded(room_id, msg.id)]

    async def mark_processing(self, room_id, message_id):
        if self.excluded(room_id, message_id):
            self._fail()
        return await super().mark_processing(room_id, message_id)

    async def mark_processed(self, room_id, message_id):
        if self.excluded(room_id, message_id):
            self._fail()
        return await super().mark_processed(room_id, message_id)

    async def mark_failed(self, room_id, message_id, error):
        if self.excluded(room_id, message_id):
            self._fail()
        return await super().mark_failed(room_id, message_id, error)


class ReceiptPreservingPlatformRuntime(PlatformRuntime):
    """Override only pre-WebSocket link construction in the pinned SDK.

PlatformRuntime does not expose a link factory. This small initialization
override mirrors 4.0.0's constructor arguments and uses its metadata fetch;
start, ownership, reconnect, context creation, controls and stop are inherited.
"""

    def __init__(self, *args, continuation_room_id, excluded_event_ids,
                 on_filter_failure, **kwargs):
        if version("band-sdk") != "4.0.0":
            raise FactoryError("Continuation ingress requires reviewed BAND SDK 4.0.0")
        self._continuation_options = dict(continuation_room_id=continuation_room_id,
            excluded_event_ids=tuple(excluded_event_ids), on_filter_failure=on_filter_failure)
        super().__init__(*args, **kwargs)

    async def initialize(self):
        if self._link:
            return
        self._link = ReceiptPreservingBandLink(agent_id=self._agent_id, api_key=self._api_key,
            ws_url=self._ws_url, rest_url=self._rest_url,
            conflict_policy=self._config.conflict_policy, **self._continuation_options)
        await self._fetch_agent_metadata()
