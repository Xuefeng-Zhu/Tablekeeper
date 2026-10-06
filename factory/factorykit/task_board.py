"""Project validated work items onto BAND's shared board through the public SDK.

No inference, session attachment or chat dispatch occurs here. Writes are claimed
durably before transport, never retried, and reconciled by reading an exact task.
Only an owner's authenticated context changes that owner's assignment status.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID, uuid4

from .common import FactoryError, canonical, contains_secret, write_json
from .workitems import REQUIRED, validate_item


MUTATION_TOOLS = frozenset({"band_create_task", "band_update_task", "band_set_board"})
STATUS = {"PROPOSED": "pending", "READY": "pending", "IN_PROGRESS": "in_progress",
          "REVIEW": "in_review", "ACCEPTED": "completed", "REJECTED": "blocked", "BLOCKED": "blocked"}
SCOPE_FIELDS = ("id", "owner", "reviewer", "dependencies", "goal", "requirements", "starting_revision",
                "workspace_paths", "ownership_boundaries", "acceptance_conditions")


def _tool(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(required),
                       "additionalProperties": False}}}


_lists = {"dependencies", "requirements", "workspace_paths", "ownership_boundaries", "acceptance_conditions",
          "commands", "results", "evidence_paths", "limitations"}
_review_lists = {"review_commands", "review_results", "review_evidence_paths"}
ITEM_SCHEMA = {"type": "object", "properties": {
    key: ({"type": "array", "items": {"type": "string"}} if key in _lists | _review_lists else
          {"type": ["string", "null"]}) for key in (*REQUIRED, "reviewer", "room_event", "bounded_next_action", *sorted(_review_lists))},
    "required": list(REQUIRED), "additionalProperties": True}
TOOLS = (
    _tool("factory_work_item", "Publish a complete work item to BAND's shared board. PM creates PROPOSED/READY; "
          "the owner starts/delivers work; its nominated independent reviewer accepts/rejects the exact candidate. "
          "Use factory_task_board to read current versions. The owner must sync after acceptance before Done. "
          "This does not send the required addressed room handoff.",
          {"item": ITEM_SCHEMA, "expected_version": {"type": "integer", "minimum": 0}}, ("item", "expected_version")),
    _tool("factory_task_board", "Read a compact index of task versions, ownership, state, candidate and pending writes. "
          "Pass id for that complete authoritative work-item record, including requirements and evidence. "
          "Use band_list_tasks/band_get_task for the live BAND view.", {"id": {"type": ["string", "null"]}}),
    _tool("factory_reconcile_task", "Read an exact BAND task to confirm a previously uncertain write. "
          "Never sends or retries a mutation. For an uncertain create find its exact task_id with band_list_tasks. "
          "A mismatch remains blocked; do not create a replacement to bypass uncertainty.",
          {"id": {"type": "string"}, "task_id": {"type": "string"}}, ("id", "task_id")),
)


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def _dict(value):
    return value if isinstance(value, dict) else value.model_dump(mode="json") if hasattr(value, "model_dump") else {}


class TaskBoard:
    def __init__(self, path: Path, room_id: str, roster: list[dict]):
        self.path, self.room_id = Path(path), room_id
        self.roster = [{key: s[key] for key in ("id", "handle", "agent_id")} for s in roster]
        if not _uuid(room_id) or any(not _uuid(s["agent_id"]) for s in self.roster):
            raise FactoryError("Task board requires verified room and roster identities")
        if (len({s["agent_id"] for s in self.roster}) != len(self.roster)
                or len({s["handle"].lstrip("@").lower() for s in self.roster}) != len(self.roster)
                or sum(s["id"] == "pm" for s in self.roster) != 1):
            raise FactoryError("Task board requires an unambiguous frozen roster with one coordinator")
        self.scope = {"room_id": room_id, "roster": self.roster}
        self.lock = asyncio.Lock()

    @contextmanager
    def state(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads(self.path.read_text()) if self.path.exists() else {
                    "scope": self.scope, "items": {}, "messages": {}}
                if data.get("scope") != self.scope:
                    raise FactoryError("Task board room or roster changed; preserve the original mapping")
                yield data
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def snapshot(self):
        with self.state() as data:
            return {"room_id": self.room_id, "items": {key: self._public(record) for key, record in data["items"].items()}}

    def read(self, identity=None):
        """Compact tool read; snapshot() retains its existing full Python API."""
        if identity is not None and (not isinstance(identity, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", identity) is None):
            raise FactoryError("factory_task_board id must be an exact work-item id")
        with self.state() as data:
            if identity is not None:
                if identity not in data["items"]:
                    raise FactoryError("Work-item id is not in the factory task board")
                return {"room_id": self.room_id, "view": "item",
                        "items": {identity: self._public(data["items"][identity])}}
            items = {}
            for key, record in data["items"].items():
                pending = record.get("pending")
                item = record.get("item") or (pending or {}).get("item", {})
                brief = {"version": record["version"], "task_id": record.get("task_id"),
                         "owner": item.get("owner"), "state": record.get("item", {}).get("state"),
                         "candidate_commit": item.get("candidate_commit"),
                         "pending": ({name: pending[name] for name in
                             ("operation", "version", "write_error") if name in pending} if pending else None),
                         "owner_sync_pending": bool(record.get("item") and
                             record.get("owner_status") != STATUS[item["state"]])}
                if "sync_error" in record:
                    brief["sync_error"] = record["sync_error"]
                items[key] = brief
            return {"room_id": self.room_id, "view": "index", "items": items,
                    "complete_records": "Call factory_task_board with id for the complete authoritative work item."}

    @staticmethod
    def _public(record):
        result = deepcopy(record)
        result["owner_sync_pending"] = bool(record.get("item") and
            record.get("owner_status") != STATUS[record["item"]["state"]])
        return result

    def _seat(self, identity):
        for seat in self.roster:
            if identity == seat["agent_id"] or (isinstance(identity, str)
                    and identity.lstrip("@").lower() == seat["handle"].lstrip("@").lower()):
                return seat
        raise FactoryError("Task actor, owner or reviewer is outside the frozen roster")

    def observe_message(self, actor_id, response, content):
        """Keep only confirmed provenance needed for candidate-bound review gates."""
        raw = _dict(response)
        header = re.fullmatch(r"WORK-REVIEW ([A-Za-z0-9][A-Za-z0-9_.-]{0,95}) version ([1-9][0-9]*) "
                              r"(ACCEPTED|REJECTED) candidate ([0-9a-f]{40})",
                              content.splitlines()[0] if isinstance(content, str) and content.splitlines() else "")
        recipients = raw.get("recipients")
        if (header is None or raw.get("success") is not True or not _uuid(raw.get("id"))
                or not isinstance(recipients, list) or not recipients):
            return
        self._seat(actor_id)
        with self.state() as data:
            value = {"actor_id": actor_id, "work_item_id": header[1], "version": int(header[2]),
                     "verdict": header[3], "candidate_commit": header[4],
                     "recipients": [r.get("id") for r in recipients if isinstance(r, dict)]}
            old = data["messages"].get(raw["id"])
            if old is not None and old != value:
                raise FactoryError("Conflicting review message provenance")
            data["messages"][raw["id"]] = value
            write_json(self.path, data)

    def _validate(self, data, actor_id, item, expected_version):
        if not isinstance(item, dict) or type(expected_version) is not int or expected_version < 0:
            raise FactoryError("A complete work item and nonnegative integer version are required")
        item = deepcopy(item)
        if not isinstance(item.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", item["id"]):
            raise FactoryError("Work item id must be 1-96 letters, digits, dots, underscores or hyphens")
        if len(canonical(item)) > 65536 or contains_secret(canonical(item).decode()):
            raise FactoryError("Work item exceeds 64 KiB or contains credential-like content")
        for key in (set(REQUIRED) - _lists) | {"reviewer", "room_event", "bounded_next_action"}:
            if item.get(key) is not None and not isinstance(item[key], str):
                raise FactoryError("Work-item scalar fields must be text or null")
        for key in _lists:
            if not isinstance(item.get(key), list) or any(not isinstance(v, str) or not v.strip() for v in item[key]):
                raise FactoryError("Work-item lists must contain nonempty text")
        if item["id"] in item["dependencies"] or len(set(item["dependencies"])) != len(item["dependencies"]):
            raise FactoryError("Dependencies cannot contain the item itself or duplicate IDs")
        item = validate_item(item, {"seats": self.roster})
        owner, actor = self._seat(item["owner"]), self._seat(actor_id)
        reviewer = self._seat(item["reviewer"]) if item.get("reviewer") else None
        if item["state"] != "PROPOSED" and (reviewer is None or reviewer == owner):
            raise FactoryError("READY and later work requires a nominated independent reviewer")
        old = data["items"].get(item["id"])
        if old and old.get("pending"):
            raise FactoryError("Task write is unconfirmed; reconcile its exact BAND task before continuing")
        if expected_version != (old["version"] if old else 0):
            raise FactoryError("Stale work-item version; read factory_task_board before updating")
        if not old:
            if actor["id"] != "pm" or item["state"] not in {"PROPOSED", "READY"}:
                raise FactoryError("Only PM may create a PROPOSED or READY work item")
        else:
            previous = old["item"]
            if previous["state"] != "PROPOSED" and any(item.get(k) != previous.get(k) for k in SCOPE_FIELDS):
                raise FactoryError("Ready task scope and ownership are immutable; preserve it and create a new item")
            changed = item != previous
            if changed:
                if previous["state"] in {"ACCEPTED", "BLOCKED"}:
                    raise FactoryError("Accepted and blocked work items are terminal; preserve their evidence")
                if item["state"] == previous["state"]:
                    if previous["state"] != "PROPOSED" or actor["id"] != "pm":
                        raise FactoryError("Changed work items require a valid lifecycle transition")
                else:
                    validate_item({**item, "state": previous["state"]}, {"seats": self.roster}, item["state"])
                allowed = (actor["id"] == "pm" if item["state"] in {"PROPOSED", "READY"} else
                           actor == reviewer if item["state"] in {"ACCEPTED", "REJECTED"} else
                           actor == owner or (item["state"] == "BLOCKED" and actor["id"] == "pm"))
                if not allowed:
                    raise FactoryError("Only the responsible owner/coordinator/reviewer may make this transition")
                if item["state"] in {"ACCEPTED", "REJECTED"}:
                    if any(item.get(key) != previous.get(key) for key in
                           ("candidate_commit", "commands", "results", "evidence_paths")):
                        raise FactoryError("Review must preserve the exact delivered candidate and implementation evidence")
                    for key in _review_lists:
                        if (not isinstance(item.get(key), list) or not item[key]
                                or any(not isinstance(v, str) or not v.strip() for v in item[key])):
                            raise FactoryError("Review requires review_commands, review_results and review_evidence_paths")
                    if any(not Path(p).is_absolute() for p in item["review_evidence_paths"]):
                        raise FactoryError("Review evidence paths must be absolute")
                    message = data["messages"].get(item.get("room_event"), {})
                    if (message.get("actor_id") != actor_id or owner["agent_id"] not in message.get("recipients", [])
                            or message.get("candidate_commit") != item["candidate_commit"]
                            or message.get("work_item_id") != item["id"] or message.get("version") != old["version"]
                            or message.get("verdict") != item["state"]):
                        raise FactoryError("Review requires the reviewer's confirmed WORK-REVIEW message to the owner binding this item, version, verdict and commit")
                if (item["state"] == "REVIEW" and old.get("rejected_candidate")
                        and item["candidate_commit"] == old["rejected_candidate"]):
                    raise FactoryError("A rejected candidate requires a changed repair commit before another review")
        if item["state"] in {"REVIEW", "ACCEPTED", "REJECTED"} and not re.fullmatch(r"[0-9a-f]{40}", item.get("candidate_commit") or ""):
            raise FactoryError("Review requires a full candidate commit SHA")
        if item["state"] in {"READY", "IN_PROGRESS"}:
            for dependency in item["dependencies"]:
                record = data["items"].get(dependency, {})
                if dependency == item["id"] or record.get("pending") or record.get("item", {}).get("state") != "ACCEPTED":
                    raise FactoryError("Dependencies must be independently accepted before work is ready")
        return item, old, STATUS[item["state"]] if actor == owner else None

    def _payload(self, item, projection_id):
        # Historical projections must keep their exact serialization for readback.
        subject = f"{item['id']}: {item['goal'] or 'Proposed work'}"[:200]
        detail = (f"Factory projection: {projection_id}\nFactory work item: {item['id']}\nOwner: {item['owner']}\n"
                  f"State: {item['state']}\n\nComplete handoff record:\n```json\n"
                  + json.dumps(item, indent=2, ensure_ascii=False, sort_keys=True) + "\n```")
        return subject, detail

    def _new_payload(self, item, projection_id):
        """Fit the BAND API's 1..500 subject / <=10000 detail character limits.

        This is only a board projection. The durable item and addressed handoff
        retain their complete content, including when the projection is a pointer.
        """
        subject, detail = self._payload(item, projection_id)
        if len(detail) > 10000:
            compact = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            detail = (f"Factory projection: {projection_id}\nFactory work item: {item['id']}\n"
                      "Complete handoff record:\n```json\n" + compact + "\n```")
        if len(detail) > 10000:
            detail = (f"Factory projection: {projection_id}\nFactory work item: {item['id']}\n"
                      f"State: {item['state']}\n"
                      f"Complete item canonical SHA256: {hashlib.sha256(canonical(item)).hexdigest()}\n"
                      "The complete work item exceeds this BAND task's detail limit.\n"
                      f"Call factory_task_board with id={json.dumps(item['id'])}, then select items[{json.dumps(item['id'])}].item "
                      "(pending.item while a write is unconfirmed).\n"
                      "The complete item and addressed handoff remain authoritative; this is only a board projection.")
        return {"subject": subject, "detail": detail}

    def _record_payload(self, record):
        if "band_payload" in record:
            payload = record["band_payload"]
            if (not isinstance(payload, dict) or set(payload) != {"subject", "detail"}
                    or any(not isinstance(payload[key], str) for key in payload)):
                raise FactoryError("Stored BAND projection is invalid; preserve its record")
            return deepcopy(payload)
        subject, detail = self._payload(record["item"], record["projection_id"])
        return {"subject": subject, "detail": detail}

    @staticmethod
    def _write_error(error):
        # Never retain exception messages, bodies, headers or request objects.
        from band_rest.core.api_error import ApiError
        from band_rest.core.parse_error import ParsingError

        name = type(error).__name__
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", name) is None:
            name = "Exception"
        status = getattr(error, "status_code", None) if isinstance(error, (ApiError, ParsingError)) else None
        if type(status) is not int or not 100 <= status <= 599:
            status = None
        # A response parse failure or server/transport failure cannot establish
        # whether the mutation happened. Even explicit rejection keeps its claim.
        rejected = isinstance(error, ApiError) and status is not None and 400 <= status < 500 and status != 408
        return {"outcome": "rejected" if rejected else "unknown", "error_class": name, "http_status": status}

    async def _request(self, tools, method, window, **kwargs):
        if tools.room_id != self.room_id:
            raise FactoryError("Task transport is bound to another room")
        seconds = window()
        if seconds <= 0:
            raise FactoryError("No approved time remains for this task operation")
        async with asyncio.timeout(seconds):
            response = await getattr(tools.rest.agent_api_chat_tasks, method)(chat_id=self.room_id,
                request_options={"max_retries": 0, "timeout_in_seconds": seconds}, **kwargs)
        task = _dict(response.data)
        if not _uuid(task.get("id")) or task.get("chat_room_id") != self.room_id or task.get("state") != "active":
            raise FactoryError("Task response has no matching active room identity")
        return task

    def _matches(self, task, pending):
        payload = self._record_payload(pending)
        if any(task.get(key) != value for key, value in payload.items()):
            return False
        if pending["operation"] == "create" and task.get("created_by", {}).get("id") != pending["actor_id"]:
            return False
        status = pending["owner_status"]
        return status is None or any(a.get("assignee", {}).get("id") == pending["actor_id"]
                                    and a.get("status") == status for a in task.get("assignments", []))

    def _finish(self, data, identity, task, operation_id):
        record = data["items"][identity]
        pending = record["pending"]
        if not pending or pending["operation_id"] != operation_id:
            raise FactoryError("Pending task operation changed during confirmation; read its current state")
        if not self._matches(task, pending) or (record.get("task_id") and task["id"] != record["task_id"]):
            raise FactoryError("BAND task readback differs from the claimed write; reconciliation is required")
        record.update(item=pending["item"], version=pending["version"], task_id=task["id"],
                      number=task.get("number"), overall_status=task.get("overall_status"), pending=None)
        if "band_payload" in pending:
            record["band_payload"] = deepcopy(pending["band_payload"])
        if pending["owner_status"] is not None:
            record["owner_status"] = pending["owner_status"]
        if pending["item"]["state"] == "REJECTED":
            record["rejected_candidate"] = pending["item"]["candidate_commit"]
        record.pop("sync_error", None)
        write_json(self.path, data)
        return self._public(record)

    async def publish(self, tools, actor_id, item, expected_version, window):
        if tools.agent_id != actor_id or tools.room_id != self.room_id:
            raise FactoryError("Task transport does not match the admitted actor and room")
        async with self.lock:
            # Validate before any network operation, then claim before mutation.
            with self.state() as data:
                item, old, status = self._validate(data, actor_id, item, expected_version)
                if old and old["item"] == item and (status is None or old.get("owner_status") == status):
                    return self._public(old)
                task_id = old.get("task_id") if old else None
            if task_id:
                current = await self._request(tools, "get_chat_task", window, id=task_id)
                payload = self._record_payload(old)
                if current["id"] != task_id or any(current.get(key) != value for key, value in payload.items()):
                    raise FactoryError("BAND task was edited outside the factory; reconcile before overwriting")
            # A failed budget/room check must not leave a claimed, unsent operation.
            if tools.room_id != self.room_id or window() <= 0:
                raise FactoryError("Room binding or remaining budget prevents this task write")
            with self.state() as data:
                item, old, status = self._validate(data, actor_id, item, expected_version)
                projection_id = old["projection_id"] if old else str(uuid4())
                operation_id = str(uuid4())
                payload = (self._record_payload(old) if old and old["item"] == item
                           else self._new_payload(item, projection_id))
                pending = {"operation": "update" if task_id else "create", "item": item,
                           "actor_id": actor_id, "owner_status": status if task_id else None,
                           "version": (old["version"] if old else 0) + 1,
                           "projection_id": projection_id, "operation_id": operation_id,
                           "band_payload": payload}
                record = data["items"].setdefault(item["id"], {"version": 0, "task_id": None,
                    "projection_id": projection_id})
                record["pending"] = pending
                write_json(self.path, data)
            kwargs = deepcopy(payload)
            if task_id:
                kwargs["id"] = task_id
                if status is not None:
                    kwargs.update(status=status, active_form=f"{item['id']}: {item['state']}")
            # Any exception/cancellation leaves the durable pending claim intact.
            try:
                result = await self._request(tools, "update_chat_task" if task_id else "create_chat_task", window, **kwargs)
                with self.state() as data:
                    return self._finish(data, item["id"], result, operation_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                diagnostic = self._write_error(error)
                with self.state() as data:
                    current = data["items"][item["id"]].get("pending")
                    if current and current["operation_id"] == operation_id:
                        current["write_error"] = diagnostic
                        write_json(self.path, data)
                if isinstance(error, FactoryError):
                    raise
                if diagnostic["outcome"] == "rejected":
                    raise FactoryError(f"BAND rejected this task write ({diagnostic['error_class']}, "
                                       f"HTTP {diagnostic['http_status']}); its claim is preserved; never retry or duplicate") from None
                raise FactoryError(f"BAND write outcome is unknown ({diagnostic['error_class']}, "
                                   f"HTTP {diagnostic['http_status']}); use factory_reconcile_task, never retry or duplicate") from None

    async def reconcile(self, tools, identity, task_id, window):
        self._seat(tools.agent_id)
        if not isinstance(identity, str):
            raise FactoryError("Reconciliation requires a work-item id")
        if not _uuid(task_id):
            raise FactoryError("Reconciliation requires an exact BAND task UUID")
        async with self.lock:
            with self.state() as data:
                record = data["items"].get(identity)
                if not record or not record.get("pending"):
                    raise FactoryError("This work item has no pending write")
                if record.get("task_id") and record["task_id"] != task_id:
                    raise FactoryError("Reconciliation cannot substitute a different task")
                operation_id = record["pending"]["operation_id"]
            task = await self._request(tools, "get_chat_task", window, id=task_id)
            if task["id"] != task_id:
                raise FactoryError("Reconciliation read returned a different task identity")
            with self.state() as data:
                return self._finish(data, identity, task, operation_id)

    async def sync_owner(self, tools, actor_id, window):
        """On the owner's next admitted turn, reflect reviewer/PM decisions as self."""
        if tools.agent_id != actor_id or tools.room_id != self.room_id:
            raise FactoryError("Task transport does not match the admitted actor and room")
        with self.state() as data:
            records = [deepcopy(r) for r in data["items"].values() if not r.get("pending") and not r.get("sync_error")
                       and self._seat(r["item"]["owner"])["agent_id"] == actor_id
                       and r.get("owner_status") != STATUS[r["item"]["state"]]]
        for record in records:
            try:
                await self.publish(tools, actor_id, record["item"], record["version"], window)
            except Exception as error:
                # A display-sync failure must not prevent the owner from reading
                # the mapping, reconciling uncertainty, or reporting a blocker.
                # Cancellation still propagates; pending writes are never retried.
                with self.state() as data:
                    current = data["items"][record["item"]["id"]]
                    current["sync_error"] = (str(error) if isinstance(error, FactoryError)
                                             else "Task synchronization could not be confirmed")
                    write_json(self.path, data)
