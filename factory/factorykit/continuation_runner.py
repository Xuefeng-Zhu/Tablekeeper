"""Explicit same-room continuation entrypoint; importing this module is inert.

The reviewed amendment never replaces the original config or dispatch. Historical
receipt suppression is separate from model completion. No command sends a task.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile
import threading
import time
from uuid import UUID

import psutil

from . import deadline_extension as de
from .common import FactoryError, canonical, digest, load_config
from .continuation_guard import EventAdmissionJournal

GLOBAL_HALT_ERROR = "Persisted cumulative budget has a global stopped_reason; explicit reconciliation is required."


def _require(value, message):
    if not value:
        raise FactoryError(message)


def _uuid(value):
    try:
        _require(isinstance(value, str) and str(UUID(value)) == value,
                 "Continuity requires canonical event and thread identities")
    except (ValueError, TypeError, AttributeError):
        raise FactoryError("Continuity requires canonical event and thread identities") from None
    return value


def _time(value):
    try:
        value = datetime.fromisoformat(value) if isinstance(value, str) else value
        _require(isinstance(value, datetime) and value.tzinfo is not None
                 and value.utcoffset() is not None, "Continuity timestamp is missing its timezone")
        return value.timestamp()
    except (TypeError, ValueError, OverflowError):
        raise FactoryError("Invalid continuity timestamp") from None


def validate_continuity(base, amendment):
    """Derive reviewed seeds from original workflow and complete receipt audit.

    Pure file reads only. The validator binds helper bytes, room, roster and audit
    reference first. This function does not trust summarized event classifications.
    """
    manifest = de._continuity(base, amendment)
    audit = de._referenced(manifest["platform_audit"])
    workflow_raw = de._binding_bytes(amendment["bindings"]["workflow"], live=False)
    workflow = de._json_bytes(workflow_raw)
    room = manifest["room_id"]
    cutoff = _time(manifest["cutoff_utc"])
    _require(audit.get("schema_version") == 1 and audit.get("room_id") == room
             and audit.get("read_only") is True and audit.get("model_calls") is False
             and audit.get("mutations") is False and audit.get("input_hashes_unchanged") is True
             and audit.get("before_hashes") == audit.get("after_hashes")
             and audit.get("before_hashes", {}).get("workflow_sha256") == digest(workflow_raw)
             and _time(audit.get("created_at")) == cutoff,
             "Continuity audit is not bound to the original workflow and cutoff")
    seats = {s["id"]: s["agent_id"] for s in base["seats"]}
    _require(len(set(seats.values())) == len(seats), "Duplicate platform seat identity")
    scope = workflow.get("scope", {})
    _require(scope.get("room_id") == room and set(scope.get("participant_ids", [])) == set(seats.values()),
             "Original workflow room or roster differs from continuation")
    turns = workflow.get("turns")
    _require(isinstance(turns, dict), "Original workflow has no turn inventory")
    by_agent = {agent: seat for seat, agent in seats.items()}
    original = {seat: {} for seat in seats}
    groups = {name: set() for name in ("completed", "blocked", "pending", "processed_without_admission")}
    for turn_id, turn in turns.items():
        _require(isinstance(turn, dict) and turn.get("agent_id") in by_agent,
                 "Original admission has an unknown seat")
        seat = by_agent[turn["agent_id"]]
        event = _uuid(turn.get("trigger_event_id"))
        _require(event not in original[seat], "Duplicate original admission requires manual reconciliation")
        _require(turn.get("status") in {"completed", "failed", "running", "active", "interrupted"},
                 "Original admission state is unknown")
        original[seat][event] = (turn_id, turn)
        groups["completed" if turn["status"] == "completed" else "blocked"].add((seat, event))
    rows = audit.get("seats")
    _require(isinstance(rows, list) and len(rows) == len(seats)
             and {r.get("seat") for r in rows} == set(seats), "Incomplete platform seat audit")
    threads = {}
    for row in rows:
        seat, agent = row["seat"], seats[row["seat"]]
        _require(row.get("agent_id") == agent, "Audited seat identity changed")
        receipt = de._referenced({"path": row["file"], "sha256": row["sha256"]})
        _require(receipt.get("seat") == seat and receipt.get("agent_id") == agent
                 and receipt.get("room_id") == room and receipt.get("complete_cursor_pagination") is True
                 and _time(receipt.get("observed_start")) <= _time(receipt.get("observed_end")) <= cutoff,
                 "Receipt audit is incomplete or outside its cutoff")
        def events(name):
            values = receipt.get(name)
            _require(isinstance(values, list), "Receipt event inventory is missing")
            result = {}
            for item in values:
                event = _uuid(item.get("event_id"))
                _require(event not in result and _time(item.get("inserted_at")) <= cutoff,
                         "Duplicate or future receipt in continuity audit")
                result[event] = item
            return result
        all_events, actionable = events("all_receipts"), events("actionable_receipts")
        _require(all(all_events.get(event) == item for event, item in actionable.items()),
                 "Actionable receipts differ from complete receipt inventory")
        comparisons = receipt.get("admission_comparisons")
        _require(isinstance(comparisons, list) and len(comparisons) == len(original[seat]),
                 "Receipt audit omitted original admissions")
        seen = set()
        for item in comparisons:
            event = _uuid(item.get("trigger_event_id"))
            _require(event in original[seat] and event not in seen, "Receipt admission was duplicated or invented")
            seen.add(event)
            turn_id, turn = original[seat][event]
            _require(item.get("turn_id") == turn_id
                     and all(item.get(k) == turn.get(k) for k in ("status", "started_at", "ended_at", "reason_code"))
                     and item.get("server_event_found") is True and event in all_events
                     and item.get("server_actionable") is (event in actionable)
                     and item.get("server_delivery_status") == (all_events[event].get("own_delivery") or {}).get("status"),
                     "Local admissions and platform receipts disagree")
        suppressed = {event for event, item in all_events.items()
                      if event not in original[seat] and (item.get("own_delivery") or {}).get("status") == "processed"}
        _require(set(events("processed_without_recorded_admission")) == suppressed,
                 "Processed receipts were not reconciled exactly")
        admitted_actionable = set(actionable) & set(original[seat])
        _require(set(events("actionable_with_recorded_admission")) == admitted_actionable,
                 "Actionable admission reconciliation differs")
        _require(set(row.get("actionable_event_ids", [])) == set(actionable)
                 and set(row.get("admitted_actionable_event_ids", [])) == admitted_actionable
                 and set(row.get("processed_without_admission_event_ids", [])) == suppressed
                 and row.get("admissions") == len(original[seat]) and row.get("all_receipts") == len(all_events),
                 "Platform audit summary disagrees with its original receipts")
        pending = set(actionable) - set(original[seat]) - suppressed
        groups["pending"].update((seat, event) for event in pending)
        groups["processed_without_admission"].update((seat, event) for event in suppressed)
        local, public = receipt.get("latest_local_thread_binding"), receipt.get("latest_public_thread_binding")
        if local is None or public is None:
            _require(local is None and public is None and not original[seat]
                     and not receipt.get("thread_task_metadata"), "A used seat has no verified thread binding")
            threads[seat] = None
        else:
            metadata = public.get("metadata", {})
            thread = _uuid(metadata.get("codex_thread_id"))
            _require(local.get("codex_thread_id") == thread and local.get("codex_room_id") == room
                     and metadata.get("codex_room_id") == room and public.get("sender_id") == agent
                     and receipt.get("public_matches_latest_local_thread") is True
                     and public in receipt.get("thread_task_metadata", [])
                     and _time(public.get("inserted_at")) <= cutoff,
                     "Public and local Codex thread bindings disagree")
            threads[seat] = thread
        _require(row.get("latest_public_thread_binding") == public, "Summary thread differs from its source")
    _require(audit.get("local_admissions") == sum(len(v) for v in original.values()),
             "Original admission count differs from audit")
    for category, expected in groups.items():
        _require({tuple(v) for v in manifest[category]} == expected,
                 "Reviewed continuity event classifications differ from original evidence")
    _require(manifest["expected_thread_ids"] == threads, "Reviewed Codex threads differ from original evidence")
    return manifest


def _no_symlinks(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        _require(not part.is_symlink(), "Continuation paths may not traverse symlinks")
    return path


def _private_directory(path):
    path = _no_symlinks(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == 0o700, "Continuation directory must be owner-only")


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_json(path, value, *, exclusive=False):
    """Owner-only, fsynced JSON; exclusive claims never get replaced or retried."""
    path = _no_symlinks(path)
    _require(path.parent.is_dir(), "Continuation destination directory is missing")
    if path.exists():
        info = path.stat()
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), "Unsafe continuation state file")
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
        _fsync_directory(path.parent)
        return
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
        _fsync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def continuation_paths(base, amendment_path):
    root = _no_symlinks(Path(base["paths"]["runs"]) / "runtime/continuation")
    identity = digest(de._bytes(amendment_path))
    return root, root / (identity + ".claim.json"), root / identity


def _policy_module(policy):
    if policy == "deadline":
        return de
    if policy == "token":
        from . import token_extension
        return token_extension
    raise FactoryError("Unknown continuation policy; no fallback is allowed")


class ContinuityContext:
    _policy_name = "deadline"

    def __init__(self, base_config, amendment_path, claim_path, owner_token):
        self.base_config = copy.deepcopy(base_config)
        self.amendment_path, self.claim_path = Path(amendment_path), Path(claim_path)
        self.effective_config = _policy_module(self._policy_name).validate_claimed_amendment(
            base_config, amendment_path, claim_path, owner_token=owner_token)
        self.amendment = de._read(amendment_path)
        self.manifest = validate_continuity(base_config, self.amendment)
        self.room_id = self.manifest["room_id"]
        self.owner_token = owner_token
        root, expected_claim, self.directory = continuation_paths(base_config, amendment_path)
        _require(self.claim_path.absolute() == expected_claim, "Continuation claim path differs from its amendment")
        self.binding = {"room_id": self.room_id, "amendment_sha256": digest(de._bytes(amendment_path)),
                        "claim_sha256": digest(de._bytes(claim_path)),
                        "continuity_sha256": self.amendment["continuity"]["sha256"]}
        self.lock = threading.RLock()
        _private_directory(root); _private_directory(self.directory)
        binding_path = self.directory / "binding.json"
        if binding_path.exists():
            _require(de._read(binding_path) == self.binding, "Continuation state belongs to another claim")
        else:
            durable_json(binding_path, self.binding, exclusive=True)
        self.thread_path = self.directory / "threads.json"
        self.threads = copy.deepcopy(self.manifest["expected_thread_ids"])
        if self.thread_path.exists():
            stored = de._read(self.thread_path)
            _require(set(stored) == {"schema_version", "binding", "threads"} and stored["schema_version"] == 1
                     and stored["binding"] == self.binding and set(stored["threads"]) == set(self.threads),
                     "Continuation thread state binding changed")
            for seat, expected in self.threads.items():
                observed = stored["threads"][seat]
                _require(expected is None or observed == expected, "Existing thread history was replaced")
                if observed is not None:
                    _uuid(observed)
            self.threads = stored["threads"]
        else:
            self._save_threads()
        self.journal = EventAdmissionJournal(
            self.directory / "events.json", room_id=self.room_id,
            cutoff_utc=self.manifest["cutoff_utc"], completed=self.manifest["completed"],
            blocked=self.manifest["blocked"] + self.manifest["processed_without_admission"],
            pending=self.manifest["pending"], seats=list(self.threads), save_json=self._save)

    def _assert_binding(self):
        _require(digest(de._bytes(self.amendment_path)) == self.binding["amendment_sha256"]
                 and digest(de._bytes(self.claim_path)) == self.binding["claim_sha256"],
                 "Continuation amendment or claim changed")
        de._continuity(self.base_config, self.amendment)
        owner = de._read(Path(self.base_config["paths"]["runs"]) / "runtime/owner.json")
        _require(owner.get("token") == self.owner_token and owner.get("parent", {}).get("pid") == os.getpid()
                 and owner.get("mode") == "judged" and owner.get("status") in {"starting", "running"},
                 "Continuation no longer owns the supervisor")

    def _save(self, path, value):
        self._assert_binding()
        _require(Path(path).parent == self.directory, "Continuation write escaped its bound state directory")
        durable_json(path, value)

    def _save_threads(self):
        self._save(self.thread_path, {"schema_version": 1, "binding": self.binding, "threads": self.threads})

    def expected_thread(self, seat):
        _require(seat in self.threads, "Unknown continuation seat")
        return self.threads[seat]

    def record_thread(self, seat, thread):
        with self.lock:
            expected = self.expected_thread(seat)
            _uuid(thread)
            _require(expected is None or expected == thread, "Refusing to replace an existing Codex thread")
            self.threads[seat] = thread
            self._save_threads()

    def claim_event(self, seat, msg):
        with self.lock:
            self._assert_binding()
            return self.journal.claim(seat, msg.id, room_id=msg.room_id, created_at=msg.created_at)

    def finish_event(self, seat, msg_id, completed: bool):
        _require(type(completed) is bool, "Completion status must be explicit")
        with self.lock:
            self._assert_binding()
            self.journal.finish(seat, msg_id, completed=completed)

    def excluded_event_ids(self, seat):
        self.expected_thread(seat)
        return {event for category in ("completed", "blocked", "processed_without_admission")
                for owner, event in self.manifest[category] if owner == seat}

    def pending_events_unsettled(self):
        """Defer notices until every reviewed pending seed has completed successfully."""
        with self.lock:
            return any(self.journal.data["events"].get(seat + ":" + event) != "completed"
                       for seat, event in self.manifest["pending"])

    def metadata(self):
        return {"amendment_sha256": self.binding["amendment_sha256"], "room_id": self.room_id,
                "additional_seconds": self.amendment["changes"]["additional_seconds"],
                "old_deadline_utc": self.amendment["changes"]["old_deadline_utc"],
                "new_deadline_utc": self.amendment["changes"]["new_deadline_utc"],
                "original_dispatch_preserved": True, "tokens_and_turns_reset": False,
                "effective_models": {s["id"]: s.get("model") for s in self.effective_config["seats"]},
                "model_authorization": self.amendment.get("model_authorization"),
                "model_catalog": self.amendment.get("model_catalog"),
                "processed_without_model_admission_suppressed": len(self.manifest["processed_without_admission"])}


class TokenContinuityContext(ContinuityContext):
    """Canonical runtime-compatible context validated only by token policy."""
    _policy_name = "token"

    def metadata(self):
        changes = self.amendment["changes"]
        return {"kind": "token_amendment", "amendment_sha256": self.binding["amendment_sha256"],
                "room_id": self.room_id, "new_deadline_utc": changes["new_deadline_utc"],
                "deadline_preserved": True, "original_dispatch_preserved": True,
                "tokens_and_turns_reset": False,
                "old_max_total_tokens": changes["old_max_total_tokens"],
                "new_max_total_tokens": changes["new_max_total_tokens"],
                "additional_tokens": changes["additional_tokens"],
                "effective_models": {s["id"]: s.get("model") for s in self.effective_config["seats"]},
                "model_catalog": self.amendment.get("model_catalog"),
                "processed_without_model_admission_suppressed": len(self.manifest["processed_without_admission"])}


def check_resume(base, amendment_path, *, policy="deadline"):
    """Read-only admission, including live preflight; never claims or starts."""
    from . import runtime
    from .validation import docker_resource_check
    validator = _policy_module(policy)
    effective = validator.validate_amendment(base, amendment_path)
    amendment = de._read(amendment_path)
    validate_continuity(base, amendment)
    errors = runtime.preflight_runtime(
        base, "judged", effective_budgets=effective["budgets"],
        effective_models={s["id"]: s["model"] for s in effective["seats"]},
        model_catalog=de._referenced(amendment["model_catalog"]) if amendment.get("model_catalog") else None)
    # The selected validator proves the exact authorized halt reconciliation.
    # Every other gate must pass under its strictly bounded effective budgets.
    _require(errors == [GLOBAL_HALT_ERROR], "Continuation preflight blockers: " + "; ".join(errors))
    resource_errors = docker_resource_check(effective)["errors"]
    _require(not resource_errors, "Continuation resource blockers: " + "; ".join(resource_errors))
    # External preflight may take time. Revalidate immutable sources, owner and
    # clocks immediately before returning to the under-lock claiming caller.
    effective = validator.validate_amendment(base, amendment_path)
    validate_continuity(base, de._read(amendment_path))
    _, claim_path, _ = continuation_paths(base, amendment_path)
    _require(not claim_path.exists() and not claim_path.is_symlink(),
             "Continuation amendment already claimed; automatic retry is forbidden")
    return effective


def _running_handshake(record, base, token):
    expected = {s["agent_id"] for s in base["seats"]}
    contexts = record.get("workflow", {}).get("sdk_execution_contexts", [])
    return (record.get("token") == token and record.get("status") == "running"
            and set(record.get("seats", [])) == {s["id"] for s in base["seats"]}
            and len(contexts) == len(expected)
            and {c.get("agent_id") for c in contexts} == expected
            and all(c.get("running") is True for c in contexts))


def resume(base, config_path, amendment_path, *, policy="deadline"):
    """One exclusive claim and one spawn. Any uncertainty consumes the claim."""
    from . import runtime
    validator = _policy_module(policy)
    with runtime.launch_lock(base):
        effective = check_resume(base, amendment_path, policy=policy)
        token = secrets.token_hex(24)
        prepared = validator.prepare_claim(base, amendment_path, owner_token=token)
        amendment = de._read(amendment_path)
        validate_continuity(base, amendment)
        root, claim_path, _ = continuation_paths(base, amendment_path)
        _private_directory(root)
        # Claim precedes every consequential mutation. Do not remove it if a
        # later write or spawn fails; a human must reconcile that uncertainty.
        durable_json(claim_path, prepared["claim"], exclusive=True)
        durable_json(Path(amendment["bindings"]["budget"]["path"]), prepared["cleared_budget"])
        command = [base["runtime"]["python"], "-m", "factorykit.continuation_runner",
                   "--config", str(Path(config_path).absolute()), "--amendment", str(Path(amendment_path).absolute()),
                   *(["--policy", "token"] if policy == "token" else []),
                   "_serve", "--claim", str(claim_path), "--owner-token", token]
        logfile = root / (digest(de._bytes(amendment_path)) + ".supervisor.log")
        fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "ab") as output:
            child = subprocess.Popen(command, cwd=base["paths"]["factory"], stdout=output,
                                     stderr=output, start_new_session=True)
        record = {"token": token, "parent": runtime.process_identity(psutil.Process(child.pid)),
                  "children": [], "mode": "judged", "status": "starting", "started_at": runtime.timestamp(),
                  "config_sha256": runtime.fingerprint(base), "log": str(logfile),
                  "continuation": {"amendment_sha256": prepared["claim"]["amendment_sha256"],
                                   "claim_path": str(claim_path)}}
        durable_json(runtime.registry_path(base), record)
    new_end = _time(amendment["changes"]["new_deadline_utc"])
    deadline = time.monotonic() + min(45 * len(base["seats"]) + 10, max(0, new_end - time.time()))
    while time.monotonic() < deadline:
        _require(runtime.is_owned(record["parent"], token),
                 "Continuation exited before all seats connected; claim remains consumed")
        current = runtime.read_registry(base)
        if _running_handshake(current, base, token):
            return {"status": "running", "pid": child.pid, "room_id": amendment["room_id"],
                    "seats": current["seats"], "claim_path": str(claim_path),
                    "active_turn_limit": effective["budgets"]["max_active_seats"]}
        time.sleep(0.2)
    raise FactoryError("Continuation startup timed out; claim remains consumed; inspect owned workers before any action")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--amendment", required=True)
    parser.add_argument("--policy", choices=("deadline", "token"), default="deadline")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("resume")
    child = sub.add_parser("_serve", help=argparse.SUPPRESS)
    child.add_argument("--claim", required=True)
    child.add_argument("--owner-token", required=True)
    args = parser.parse_args(argv)
    base = load_config(args.config)
    try:
        if args.command == "check":
            from .runtime import launch_lock
            with launch_lock(base):
                effective = check_resume(base, args.amendment, policy=args.policy)
            print(json.dumps({"status": "READY_TO_RESUME", "room_id": base["band"]["judged_room_id"],
                              "effective_budgets": effective["budgets"], "dispatch_performed": False}))
        elif args.command == "resume":
            print(json.dumps(resume(base, args.config, args.amendment, policy=args.policy)))
        else:
            from . import runtime
            logging.disable(logging.CRITICAL)
            # Parent ownership must be visible before validating the child; the
            # worker has made no SDK connection or model request at this point.
            for _ in range(50):
                if runtime.read_registry(base).get("token") == args.owner_token:
                    break
                time.sleep(0.1)
            else:
                raise FactoryError("Continuation parent never registered ownership")
            # python -m executes this file as __main__; use the canonical class
            # identity also checked by runtime.serve, without weakening its gate.
            from .continuation_runner import ContinuityContext as VerifiedContinuityContext, TokenContinuityContext
            context_type = TokenContinuityContext if args.policy == "token" else VerifiedContinuityContext
            context = context_type(base, args.amendment, args.claim, args.owner_token)
            asyncio.run(runtime.serve(base, "judged", args.owner_token, continuation=context))
        return 0
    except Exception as error:
        from .runtime import GateError
        # Provider exceptions must not expose credentials or response bodies.
        print(json.dumps({"status": "blocked", "error_type": type(error).__name__,
                          "detail": str(error) if isinstance(error, (FactoryError, GateError)) else "Continuation failed; inspect owned status without retrying."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
