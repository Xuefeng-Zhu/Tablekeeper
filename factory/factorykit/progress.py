"""Optional, persisted limits on time spent without machine-observed progress.

A checkpoint proves only that a configured command passed against an unchanged
candidate. It never establishes independent product acceptance.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid

from .common import FactoryError, canonical, contains_secret, digest, redact, run_command, utc_now, write_json


def _checkpoint_command(argv, cwd, timeout, cancelled):
    """Own and reap this command's process group on timeout or cancellation."""
    started = utc_now()
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, stdout=out, stderr=err, start_new_session=True)
        except (OSError, ValueError):
            return {"exit_code": 127, "started_at": started, "finished_at": utc_now(),
                    "stdout": "", "stderr": "Checkpoint executable unavailable"}
        reason = None
        while proc.poll() is None:
            if cancelled.is_set() or time.monotonic() >= deadline:
                reason = "cancelled" if cancelled.is_set() else "timeout"
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=2)
                break
            cancelled.wait(min(0.05, max(0, deadline - time.monotonic())))
        def output(stream):
            stream.seek(0, 2); size = stream.tell(); stream.seek(max(0, size - 65536))
            return redact(stream.read().decode(errors="replace"))
        return {"exit_code": 130 if reason == "cancelled" else 124 if reason else proc.returncode,
                "started_at": started, "finished_at": utc_now(),
                "stdout": output(out), "stderr": output(err), "termination": reason,
                "output_limit_bytes_per_stream": 65536}


def progress_policy(config: dict) -> dict | None:
    policy = config.get("progress")
    if policy is None:
        return None  # Historical configurations retain their original semantics.
    if not isinstance(policy, dict) or set(policy) != {"milestones"}:
        raise FactoryError("progress requires exactly a milestones list")
    rows = policy["milestones"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise FactoryError("progress requires 1..32 ordered milestones")
    ids = set()
    required = {"id", "max_turns", "max_seconds", "command", "timeout_seconds", "max_attempts"}
    for row in rows:
        if not isinstance(row, dict) or set(row) != required:
            raise FactoryError("Each progress milestone requires id, max_turns, max_seconds, command, timeout_seconds and max_attempts")
        name = row["id"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) or name in ids:
            raise FactoryError("Progress milestone IDs must be unique simple identifiers")
        ids.add(name)
        for key in ("max_turns", "max_seconds", "timeout_seconds", "max_attempts"):
            if type(row[key]) is not int or row[key] < 1:
                raise FactoryError(f"Progress {key} must be a positive integer")
        if row["timeout_seconds"] > row["max_seconds"] or row["max_attempts"] > 10:
            raise FactoryError("Progress timeout exceeds milestone limit or max_attempts exceeds 10")
        command = row["command"]
        if (not isinstance(command, list) or not command or len(command) > 128
                or any(not isinstance(s, str) or not s or "\x00" in s for s in command)
                or not Path(command[0]).is_absolute() or contains_secret(json.dumps(command))):
            raise FactoryError("Progress command must be credential-free argv with an absolute executable")
    return json.loads(json.dumps(policy))


class ProgressGuard:
    def __init__(self, path, room_id, repository, policy, *, clock=time.time, runner=run_command):
        self.path, self.repository = Path(path), Path(repository).resolve()
        self.policy = progress_policy({"progress": policy})
        if self.policy is None or not isinstance(room_id, str) or not room_id:
            raise FactoryError("Progress guard requires a policy and room identity")
        self.clock, self.runner = clock, runner
        self._active_claim = None
        self._cancelled = threading.Event()
        self._verification_lock = threading.Lock()
        self._verification_scheduled = False
        self._verification_running = False
        self.scope = {"room_id": room_id, "repository": str(self.repository),
                      "policy_sha256": digest(canonical(self.policy))}
        with self._state(create=True):
            pass

    def _now(self):
        now = self.clock()
        if not isinstance(now, (float, int)) or not math.isfinite(now):
            raise FactoryError("Progress clock is invalid")
        return float(now)

    @contextmanager
    def _state(self, create=False):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if self.path.is_symlink():
                raise FactoryError("Progress state cannot be a symlink")
            if not self.path.exists() and create:
                now = self._now()
                state = {"version": 1, "scope": self.scope, "started_at": None,
                         "milestone_started_at": None, "last_observed_at": now,
                         "turns": [], "milestone_start_turn": 0, "completed": [],
                         "attempts": [], "pending": None, "blocked_reason": None}
                write_json(self.path, state)
            try:
                state = json.loads(self.path.read_text())
                valid = (state["version"] == 1 and state["scope"] == self.scope
                         and isinstance(state["turns"], list) and len(state["turns"]) == len(set(state["turns"]))
                         and isinstance(state["completed"], list) and isinstance(state["attempts"], list)
                         and [v["id"] for v in state["completed"]] == [r["id"] for r in self.policy["milestones"][:len(state["completed"])]]
                         and len(state["completed"]) <= len(self.policy["milestones"])
                         and type(state["milestone_start_turn"]) is int
                         and 0 <= state["milestone_start_turn"] <= len(state["turns"]))
                for key in ("started_at", "milestone_started_at", "last_observed_at"):
                    valid = valid and (state[key] is None and key != "last_observed_at" or type(state[key]) in (float, int) and math.isfinite(state[key]))
                if not valid:
                    raise ValueError()
                for record in state["completed"]:
                    if record not in state["attempts"] or record.get("outcome") != "passed":
                        raise ValueError()
                    evidence = Path(record["evidence_path"])
                    expected_parent = self.path.parent / (self.path.stem + "-evidence")
                    if evidence.parent != expected_parent or evidence.is_symlink() or digest(evidence) != record["evidence_sha256"]:
                        raise ValueError()
            except (OSError, ValueError, TypeError, KeyError):
                raise FactoryError("Progress state is invalid or belongs to different frozen inputs") from None
            yield state
            write_json(self.path, state)
        finally:
            os.close(fd)

    def _health(self, state, execution_busy=False):
        now = self._now()
        if now < state["last_observed_at"]:
            state["blocked_reason"] = "clock_regressed"
        state["last_observed_at"] = max(now, state["last_observed_at"])
        index = len(state["completed"])
        row = self.policy["milestones"][index] if index < len(self.policy["milestones"]) else None
        reason = state["blocked_reason"]
        verifying = state["pending"] and state["pending"].get("claim") == self._active_claim
        if state["pending"] and not verifying:
            reason = reason or "checkpoint_execution_unknown"
        if row:
            if state["milestone_started_at"] is not None and now - state["milestone_started_at"] >= row["max_seconds"]:
                reason = reason or "checkpoint_time_exhausted"
            elif not execution_busy and not verifying and len(state["turns"]) - state["milestone_start_turn"] >= row["max_turns"]:
                reason = reason or "checkpoint_turns_exhausted"
            elif sum(a["id"] == row["id"] for a in state["attempts"]) >= row["max_attempts"]:
                reason = reason or "checkpoint_attempts_exhausted"
        turns_used = len(state["turns"]) - state["milestone_start_turn"]
        seconds_used = max(0, now - state["milestone_started_at"]) if state["milestone_started_at"] is not None else 0
        attempts_used = (sum(a["id"] == row["id"] for a in state["attempts"])
                         + int(bool(state["pending"]) and state["pending"].get("id") == row["id"])) if row else 0
        next_checkpoint = {**row, "command": [redact(arg) for arg in row["command"]]} if row else None
        return {"state": "blocked" if reason else "verifying" if verifying else "complete" if row is None else "waiting",
                "reason": reason, "next_milestone": row["id"] if row else None,
                "next_checkpoint": next_checkpoint,
                "remaining_turns": max(0, row["max_turns"] - turns_used) if row else None,
                "remaining_seconds": max(0, row["max_seconds"] - seconds_used) if row else None,
                "remaining_attempts": max(0, row["max_attempts"] - attempts_used) if row else None,
                "completed_milestones": [x["id"] for x in state["completed"]],
                "admitted_turns": len(state["turns"]),
                "turns_since_checkpoint": turns_used,
                "seconds_since_checkpoint": seconds_used,
                "product_acceptance": "not_established"}

    def health(self, *, execution_busy=False):
        with self._state() as state:
            return self._health(state, execution_busy)

    def admit_turn(self, turn_id):
        if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 512:
            raise FactoryError("Progress admission requires a bounded turn identity")
        with self._state() as state:
            if turn_id in state["turns"]:
                return {"admitted": True, "duplicate": True, **self._health(state)}
            health = self._health(state)
            if health["state"] == "blocked":
                return {"admitted": False, **health}
            if health["state"] == "verifying":
                return {"admitted": False, **health}
            if state["started_at"] is None:
                state["started_at"] = state["milestone_started_at"] = self._now()
            state["turns"].append(turn_id)
            return {"admitted": True, "duplicate": False, **self._health(state, execution_busy=True)}

    def _candidate(self, candidate, deadline):
        if not isinstance(candidate, str) or not re.fullmatch(r"[0-9a-f]{40}", candidate):
            raise FactoryError("Checkpoint requires an exact full Git commit")
        def git(argv):
            left = deadline - self._now()
            if left <= 0:
                raise FactoryError("Checkpoint execution deadline exhausted")
            return self.runner(["/usr/bin/git", *argv], self.repository, timeout=min(10, left))
        head = git(["rev-parse", "HEAD"])
        clean = git(["status", "--porcelain", "--untracked-files=all"])
        if head["exit_code"] != 0 or head["stdout"].strip() != candidate or clean["exit_code"] != 0 or clean["stdout"].strip():
            raise FactoryError("Checkpoint requires an unchanged clean checkout at the exact candidate")

    def cancel_verification(self):
        self._cancelled.set()

    def begin_verification(self):
        """Reset cancellation synchronously BEFORE scheduling an owned worker."""
        with self._verification_lock:
            if self._verification_scheduled or self._verification_running or self._active_claim:
                raise FactoryError("A checkpoint worker is already scheduled or running")
            self._cancelled.clear()
            self._verification_scheduled = True

    def verify_next(self, candidate, *, remaining_seconds):
        """Called only inside an admitted team turn; never accept supplied results."""
        with self._verification_lock:
            if self._verification_running:
                raise FactoryError("A checkpoint worker is already running")
            self._verification_running = True
        try:
            if self._cancelled.is_set():
                raise FactoryError("Checkpoint was cancelled before execution")
            return self._verify_next(candidate, remaining_seconds=remaining_seconds)
        finally:
            with self._verification_lock:
                self._verification_running = False
                self._verification_scheduled = False

    def _verify_next(self, candidate, *, remaining_seconds):
        if not isinstance(candidate, str) or not re.fullmatch(r"[0-9a-f]{40}", candidate):
            raise FactoryError("Checkpoint requires an exact full Git commit")
        if type(remaining_seconds) not in (int, float) or not math.isfinite(remaining_seconds) or remaining_seconds <= 0:
            raise FactoryError("Checkpoint requires positive remaining authorized execution time")
        deadline = self._now() + remaining_seconds
        with self._state() as state:
            health = self._health(state, execution_busy=True)
            if health["state"] != "waiting" or state["started_at"] is None:
                raise FactoryError("Checkpoint requires an admitted turn within current progress limits")
            row = self.policy["milestones"][len(state["completed"])]
            deadline = min(deadline, state["milestone_started_at"] + row["max_seconds"])
            attempt = {"id": row["id"], "candidate_commit": candidate, "claim": uuid.uuid4().hex,
                       "started_at": self._now(), "attempt": 1 + sum(a["id"] == row["id"] for a in state["attempts"])}
            self._active_claim = attempt["claim"]
            state["pending"] = attempt
        # No state lock is held during Git or command execution. Supervisor reads
        # remain responsive; a reconstructed guard treats the pending claim as unknown.
        try:
            try:
                self._candidate(candidate, deadline)
            except FactoryError:
                result = {"exit_code": 125, "stdout": "", "stderr": "Candidate precondition failed"}
                outcome = "candidate_rejected"
            else:
                remaining = deadline - self._now()
                if remaining <= 0 or self._cancelled.is_set():
                    result = {"exit_code": 124, "stdout": "", "stderr": "Checkpoint authorization expired or cancelled"}
                elif self.runner is run_command:
                    result = _checkpoint_command(row["command"], self.repository, min(row["timeout_seconds"], remaining), self._cancelled)
                else:
                    result = self.runner(row["command"], self.repository, timeout=min(row["timeout_seconds"], remaining))
                outcome = "passed" if result.get("exit_code") == 0 else "failed"
                try:
                    self._candidate(candidate, deadline)
                except FactoryError:
                    outcome = "candidate_changed"
            if self._now() >= deadline:
                outcome = "deadline_exceeded"
            if self._cancelled.is_set():
                outcome = "cancelled"
            receipt = {**attempt, "finished_at": self._now(), "outcome": outcome,
                       "command_sha256": digest(canonical(row["command"])), "execution": result,
                       "scope": "Configured checkpoint command only; not product acceptance"}
            with self._state() as state:
                if state["pending"] != attempt:
                    raise FactoryError("Checkpoint claim changed during execution")
                if self._now() < state["last_observed_at"] or state["blocked_reason"]:
                    outcome = "clock_or_state_blocked"
                    receipt["outcome"] = outcome
                evidence = self.path.parent / (self.path.stem + "-evidence") / f"{row['id']}-{attempt['attempt']}.json"
                write_json(evidence, receipt)
                record = {k: v for k, v in receipt.items() if k != "execution"}
                record.update(evidence_path=str(evidence), evidence_sha256=digest(evidence))
                state["attempts"].append(record)
                state["pending"] = None
                if outcome == "passed":
                    state["completed"].append(record)
                    state["milestone_started_at"] = self._now()
                    state["milestone_start_turn"] = len(state["turns"])
                return {**record, "progress": self._health(state)}
        finally:
            self._active_claim = None
