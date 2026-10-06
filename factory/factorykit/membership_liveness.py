"""Read-only control-path liveness for the pinned BAND 4.0 membership repair.

BAND exposes no public control-topic or presence-consumer liveness accessor.
The private ``BandLink._ws`` / PHX lifecycle / ``RoomPresence._event_task``
seams below are deliberately pinned and fail closed. Merely checking Agent's
started flag or BandLink's connected flag misses transient disconnections.
This checks local transport state, not a remote membership or delivery outcome.
"""
from __future__ import annotations

import asyncio
from importlib.metadata import version
from uuid import UUID


def require_live_agents(agents, expected_ids):
    """Return seven exactly bound live Agents, or raise a non-secret GateError."""
    from band import Agent
    from band.client.streaming.client import WebSocketClient
    from band.platform.link import BandLink
    from band.runtime.platform_runtime import PlatformRuntime
    from band.runtime.presence import RoomPresence
    from band.runtime.runtime import AgentRuntime
    from band_sdk_core import AgentTopicKind
    from phoenix_channels_python_client.client import PHXChannelsClient
    from phoenix_channels_python_client.client_types import ClientState
    from phoenix_channels_python_client.topic_subscription import TopicSubscription
    from websockets import ClientConnection
    from websockets.protocol import State
    from .runtime import GateError

    def need(value):
        if not value:
            raise GateError("Supported live SDK membership control path is unavailable.")

    def live_task(value):
        return isinstance(value, asyncio.Task) and not value.done() and not value.cancelling()

    try:
        need(version('band-sdk') == '4.0.0')
        expected, values = list(expected_ids), list(agents)
        need(len(expected) == len(values) == 7 and len(set(expected)) == 7)
        need(all(isinstance(value, str) and str(UUID(value)) == value for value in expected))
        result = {}
        for agent in values:
            need(type(agent) is Agent and agent.is_running is True)
            platform = agent.runtime
            need(type(platform) is PlatformRuntime)
            identity, link, runtime = platform.agent_id, platform.link, platform.runtime
            need(identity in expected and identity not in result)
            need(type(link) is BandLink and type(runtime) is AgentRuntime)
            need(link.agent_id == runtime.agent_id == identity and runtime.link is link)
            need(link.is_connected is True and link.last_disconnect_reason is None)
            presence = runtime.presence
            need(type(presence) is RoomPresence and presence.link is link)
            need(live_task(presence._event_task))
            # These bound callbacks are the restoration path, not just a live task.
            need(presence.on_room_joined == runtime._on_room_joined
                 and presence.on_room_left == runtime._on_room_left)
            ws = link._ws
            need(type(ws) is WebSocketClient and ws.agent_id == identity
                 and ws.last_disconnect_reason is None)
            client = ws.client
            need(type(client) is PHXChannelsClient and client._state is ClientState.CONNECTED)
            need(live_task(client._supervisor_task) and live_task(client._message_routing_task))
            need(not client._shutdown_event.is_set() and client._connected_event.is_set())
            need(type(client.connection) is ClientConnection and client.connection.state is State.OPEN)
            subscriptions = client.get_current_subscriptions()
            for topic in (AgentTopicKind.Control.topic(identity), AgentTopicKind.Rooms.topic(identity)):
                subscription = subscriptions.get(topic)
                need(type(subscription) is TopicSubscription and subscription.name == topic)
                ready = subscription.current_join_ready
                need(isinstance(ready, asyncio.Future) and ready.done() and not ready.cancelled())
                need(ready.exception() is None and not subscription.leave_requested.is_set())
                need(subscription.conn_generation == client._conn_generation)
                need(live_task(subscription.process_topic_messages_task)
                     and callable(subscription.async_callback))
            result[identity] = agent
        need(set(result) == set(expected))
        return result
    except GateError:
        raise
    except Exception:
        raise GateError("Supported live SDK membership control path is unavailable.") from None
