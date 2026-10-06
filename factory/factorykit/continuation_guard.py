"""Fail-closed Codex thread and event continuity for an explicitly approved resume.

Inert until constructed by a reviewed continuation entrypoint. No platform calls.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable
from uuid import UUID

from .common import FactoryError


def _uuid(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise FactoryError("Continuity identity must be a canonical UUID")
    return value


class BoundCodexClient:
    """Wrap a pinned SDK client: a known thread can never fall back to start.

    The transport and every unrelated method stay with the maintained SDK.
    Raising FactoryError, rather than CodexJsonRpcError, prevents the SDK's
    resume-error fallback from silently creating another thread.
    """

    def __init__(self, client, expected_thread: str | None, record_thread: Callable[[str], None], *, on_exception=None):
        self.client = client
        self.expected_thread = _uuid(expected_thread) if expected_thread else None
        self.record_thread = record_thread
        self.ready_thread = None
        self.on_exception = on_exception

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def request(self, method, params, *args, **kwargs):
        try:
            return await self._request(method, params, *args, **kwargs)
        except Exception as error:
            if self.on_exception is not None:
                try:
                    phase = {"thread/start": "request_thread_start", "thread/resume": "request_thread_resume",
                             "turn/start": "request_turn_start", "turn/steer": "request_turn_steer"}.get(
                                 getattr(method, "value", method), "request_other")
                    self.on_exception(phase, error)
                except Exception:
                    pass
            raise

    async def _request(self, method, params, *args, **kwargs):
        name = getattr(method, "value", method)
        if name == "thread/start":
            if self.expected_thread:
                raise FactoryError("Continuation refuses a fresh thread for an existing seat")
            result = await self.client.request(method, params, *args, **kwargs)
            thread = (result.get("thread") or {}).get("id") if isinstance(result, dict) else None
            try:
                _uuid(thread)
            except (TypeError, ValueError, AttributeError) as exc:
                raise FactoryError("Thread start returned no valid identity") from exc
            # Durable identity must be recorded before any turn can start.
            self.record_thread(thread)
            self.expected_thread = self.ready_thread = thread
            return result
        if name == "thread/resume":
            self.ready_thread = None
            if not self.expected_thread or params.get("threadId") != self.expected_thread:
                raise FactoryError("Continuation refuses an unbound thread resume")
            try:
                result = await self.client.request(method, params, *args, **kwargs)
            except Exception as exc:
                raise FactoryError("Existing Codex thread could not be resumed; fresh-thread fallback blocked") from exc
            returned = (result.get("thread") or {}).get("id") if isinstance(result, dict) else None
            if returned != self.expected_thread:
                raise FactoryError("Resumed Codex thread identity differs from the approved binding")
            self.ready_thread = returned
            return result
        if name in {"turn/start", "turn/steer"}:
            if not self.ready_thread or params.get("threadId") != self.ready_thread:
                raise FactoryError("Model work requires the exact verified continuation thread")
        return await self.client.request(method, params, *args, **kwargs)


class EventAdmissionJournal:
    """Persist claims before model work; completed and ambiguous triggers never replay.

    Caller must hold the factory's existing exclusive supervisor ownership and
    one-seat semaphore. This journal is additional to platform processed state.
    Historical pending events must be explicitly reconciled, never guessed from
    a missing receipt. New events must postdate the platform audit cutoff.
    """

    def __init__(self, path: Path, *, room_id: str, cutoff_utc: str,
                 completed: list[tuple[str, str]], blocked: list[tuple[str, str]],
                 pending: list[tuple[str, str]], seats: list[str], save_json):
        self.path, self.save_json = Path(path), save_json
        self.room_id = _uuid(room_id)
        self.seats = set(seats)
        if not self.seats or len(self.seats) != len(seats):
            raise FactoryError("Continuation requires distinct configured seat IDs")
        self.cutoff = self._time(cutoff_utc)
        self.scope = {"room_id": room_id, "cutoff_utc": cutoff_utc, "seats": sorted(seats)}
        expected = {}
        for state, entries in [("completed", completed), ("blocked", blocked), ("pending", pending)]:
            for seat, event in entries:
                key = self._key(seat, event)
                if key in expected:
                    raise FactoryError("Overlapping historical event classifications")
                expected[key] = state
        self.baseline = expected
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if (not isinstance(data, dict) or set(data) != {"schema_version", "scope", "baseline", "events"}
                    or type(data.get("schema_version")) is not int or data["schema_version"] != 1
                    or data.get("scope") != self.scope or data.get("baseline") != expected):
                raise FactoryError("Continuation journal scope or baseline changed")
            if not isinstance(data.get("events"), dict):
                raise FactoryError("Continuation journal is malformed")
            for key, state in expected.items():
                actual = data["events"].get(key)
                allowed = {state} if state != "pending" else {"pending", "claimed", "completed", "blocked"}
                if actual not in allowed:
                    raise FactoryError("Continuation journal rewrote historical event status")
            if any(value not in {"pending", "claimed", "completed", "blocked"} for value in data["events"].values()):
                raise FactoryError("Continuation journal contains an invalid event state")
            for key, state in data["events"].items():
                if not isinstance(key, str) or ":" not in key:
                    raise FactoryError("Continuation journal contains an invalid event identity")
                seat, event = key.rsplit(":", 1)
                if self._key(seat, event) != key or (key not in expected and state == "pending"):
                    raise FactoryError("New journal events cannot be restored as historical pending work")
            self.data = data
        else:
            self.data = {"schema_version": 1, "scope": self.scope, "baseline": expected, "events": dict(expected)}
            self.save_json(self.path, self.data)

    @staticmethod
    def _time(value):
        instant = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(instant, datetime) or instant.tzinfo is None:
            raise FactoryError("Event continuity requires an explicit UTC timestamp")
        return instant.astimezone(timezone.utc)

    def _key(self, seat, event):
        if seat not in self.seats:
            raise FactoryError("Event seat is outside the frozen roster")
        return seat + ":" + _uuid(event)

    def claim(self, seat: str, event: str, *, room_id: str, created_at) -> bool:
        if room_id != self.room_id:
            raise FactoryError("Event room differs from the approved continuation")
        key = self._key(seat, event)
        state = self.data["events"].get(key)
        if state == "completed":
            return False
        if state in {"claimed", "blocked"}:
            raise FactoryError("Ambiguous or failed event cannot be replayed automatically")
        if state is None and self._time(created_at) <= self.cutoff:
            raise FactoryError("Historical event lacks explicit continuation reconciliation")
        self.data["events"][key] = "claimed"
        self.save_json(self.path, self.data)
        return True

    def finish(self, seat: str, event: str, *, completed: bool):
        key = self._key(seat, event)
        if self.data["events"].get(key) != "claimed":
            raise FactoryError("Only an admitted event may finish")
        self.data["events"][key] = "completed" if completed else "blocked"
        self.save_json(self.path, self.data)
