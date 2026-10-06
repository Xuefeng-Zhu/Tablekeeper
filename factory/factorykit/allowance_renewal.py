"""Hash-bound authorization for one fresh attempt without resetting accounting.

The caller records direct human approval and immutable before-images while the
runtime and request locks are held. This module reads bounded files only: it
does not create rooms, clear failures, reserve requests, or save either ledger.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from uuid import UUID

from .common import FactoryError

RENEWED_SECONDS = 21600
_KEYS = {"schema_version", "status", "authorization_source", "user_answer",
         "renewal_started_epoch", "renewal_deadline_epoch", "renewed_seconds",
         "original_factory_started_epoch", "original_guard_started_epoch",
         "original_configuration", "original_factory_budget", "original_request_ledger",
         "active_room_ids", "cumulative_room_ids", "role_models"}
_ROLES = {"pm", "architect", "designer", "backend", "frontend", "qa", "reviewer"}


def _need(condition, message):
    if not condition:
        raise FactoryError(message)


def _epoch(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _read(reference):
    _need(isinstance(reference, dict) and set(reference) == {"path", "sha256"},
          "Invalid renewal evidence reference")
    path, expected = Path(reference["path"]), reference["sha256"]
    _need(path.is_absolute() and isinstance(expected, str)
          and re.fullmatch(r"[a-f0-9]{64}", expected), "Invalid renewal evidence binding")
    try:
        _need(not any(item.is_symlink() for item in (path, *path.parents)),
              "Renewal evidence must not use symbolic links")
        info = path.stat()
        _need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
              and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
              and info.st_size <= 16 * 1024 * 1024, "Unsafe renewal evidence")
        raw = path.read_bytes()
        _need(hashlib.sha256(raw).hexdigest() == expected, "Renewal evidence changed")
        def unique(pairs):
            value = {}
            for key, item in pairs:
                _need(key not in value, "Duplicate renewal evidence field")
                value[key] = item
            return value
        parsed = json.loads(raw, object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        return parsed
    except (OSError, ValueError, TypeError):
        raise FactoryError("Renewal evidence is unavailable or malformed") from None


def load_time_renewal(reference, *, now=None):
    """Validate retained liabilities and exact six-hour fresh authorization."""
    record = _read(reference)
    if isinstance(record, dict) and record.get("kind") == "BALANCE_ONLY_AMENDMENT":
        from .allowance_scope import load_balance_amendment
        return load_balance_amendment(record, now=now)
    _need(isinstance(record, dict) and set(record) == _KEYS,
          "Invalid time renewal fields")
    _need(record["schema_version"] == 1 and record["status"] == "APPROVED"
          and record["authorization_source"] == "direct_user_chat"
          and record["user_answer"] == "approve and continue"
          and record["renewed_seconds"] == RENEWED_SECONDS,
          "Direct approval for exactly six renewed hours is required")
    instant = time.time() if now is None else now
    start, end = record["renewal_started_epoch"], record["renewal_deadline_epoch"]
    _need(all(_epoch(v) for v in (instant, start, end,
                                  record["original_factory_started_epoch"],
                                  record["original_guard_started_epoch"]))
          and type(start) is int and type(end) is int and end == start + RENEWED_SECONDS
          and max(record["original_factory_started_epoch"],
                  record["original_guard_started_epoch"]) < start <= instant,
          "Invalid renewal accounting epochs")
    active, rooms = record["active_room_ids"], record["cumulative_room_ids"]
    def room_ids(values):
        try:
            return (isinstance(values, list) and len(set(values)) == len(values)
                    and all(isinstance(v, str) and str(UUID(v)) == v for v in values))
        except (ValueError, TypeError):
            return False
    _need(room_ids(active) and len(active) == 2 and room_ids(rooms)
          and rooms == sorted(rooms) and set(active).issubset(rooms),
          "Invalid renewal room scope")
    before = _read(record["original_factory_budget"])
    requests = _read(record["original_request_ledger"])
    # Original configuration is preserved JSON, not the mutable effective YAML.
    base = _read(record["original_configuration"])
    from .budgets import room_scope, session_accounting
    original_active, old_rooms = room_scope(base)
    _need(session_accounting(base["budgets"])
          and before.get("room_id") is None and before.get("room_ids") == old_rooms
          and not set(active).intersection(old_rooms)
          and rooms == sorted(old_rooms + active)
          and before.get("started_epoch") == record["original_factory_started_epoch"]
          and requests.get("started_epoch") == record["original_guard_started_epoch"],
          "Renewal must archive all original rooms and retain accounting origins")
    _need(base["budgets"].get("overall_timeout_seconds") == RENEWED_SECONDS
          and requests.get("policy", {}).get("overall_timeout_seconds") == RENEWED_SECONDS
          and max(record["original_factory_started_epoch"], record["original_guard_started_epoch"])
              + RENEWED_SECONDS <= start,
          "Only the original six-hour allowance can be renewed once")
    _need(before.get("stopped_reason") in (None, "overall time budget exhausted")
          and requests.get("stopped_reason") in (None, "overall time budget exhausted")
          and isinstance(requests.get("requests"), dict)
          and all(value.get("status") == "settled" for value in requests["requests"].values()),
          "Unresolved requests or non-time stops cannot be renewed")
    roles = {seat["id"]: seat.get("model") or base["runtime"]["model"] for seat in base["seats"]}
    _need(set(roles) == _ROLES and record["role_models"] == roles,
          "Renewal must retain all fixed role models")
    def counts(value, allowed=None):
        return (isinstance(value, dict) and (allowed is None or set(value).issubset(allowed))
                and all(isinstance(key, str) and type(count) is int and count >= 0
                        for key, count in value.items()))
    origins, turns, stops = (before.get(name, {}) for name in
                             ("room_started_epochs", "room_turns", "room_stopped_reasons"))
    _need(type(before.get("tokens")) is int and 0 <= before["tokens"] < 2_000_000
          and counts(before.get("turns"), _ROLES) and counts(before.get("token_threads"))
          and all(isinstance(value, dict) and set(value).issubset(old_rooms)
                  for value in (origins, turns, stops))
          and all(_epoch(value) and record["original_factory_started_epoch"] <= value <= instant
                  for value in origins.values())
          and all(counts(value, _ROLES) for value in turns.values())
          and all(isinstance(value, str) and value for value in stops.values()),
          "Malformed retained factory accounting cannot be renewed")
    return record


def renewal_durations(record):
    # Never move either original epoch or round a renewed deadline up.
    end = record["renewal_deadline_epoch"]
    return {"factory": math.floor(end - record["original_factory_started_epoch"]),
            "guard": math.floor(end - record["original_guard_started_epoch"])}


def retained_request_bindings(record):
    """Stable hashes pin every carried settled request through all restarts."""
    before = _read(record["original_request_ledger"])
    return {identity: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            for identity, value in before["requests"].items()}


def validate_retained_factory_accounting(config, data, *, now=None):
    record = validate_renewal_config(config, now=now)
    _need(record is not None, "Missing retained accounting authority")
    before = _read(record["original_factory_budget"])
    def monotonic(current, previous):
        return (isinstance(current, dict) and all(type(current.get(key)) is int
                and current[key] >= value for key, value in previous.items()))
    _need(data.get("started_epoch") == before["started_epoch"]
          and type(data.get("tokens")) is int and data["tokens"] >= before["tokens"]
          and monotonic(data.get("turns"), before["turns"])
          and monotonic(data.get("token_threads"), before["token_threads"])
          and all(data.get("room_started_epochs", {}).get(room) == value
                  for room, value in before.get("room_started_epochs", {}).items())
          and all(monotonic(data.get("room_turns", {}).get(room), value)
                  for room, value in before.get("room_turns", {}).items())
          and all(data.get("room_stopped_reasons", {}).get(room) == value
                  for room, value in before.get("room_stopped_reasons", {}).items()),
          "Renewed factory accounting decreased or changed retained history")
    return record


def validate_renewal_config(config, *, now=None):
    options = config.get("runtime", {}).get("featherless_budget_guard", {})
    reference = options.get("time_renewal")
    if reference is None:
        return None
    record = load_time_renewal(reference, now=now)
    if record.get("balance_only") is True:
        from .allowance_scope import validate_balance_config
        return validate_balance_config(config, record)
    base = _read(record["original_configuration"])
    from .budgets import room_scope
    active, rooms = room_scope(config)
    durations = renewal_durations(record)
    _need(active == record["active_room_ids"] and rooms == record["cumulative_room_ids"]
          and config["paths"]["runs"] == base["paths"]["runs"]
          and options["ledger"] == base["runtime"]["featherless_budget_guard"]["ledger"],
          "Renewal requires exact rooms and the existing ledger paths")
    budgets = copy.deepcopy(config["budgets"])
    _need(budgets.pop("overall_timeout_seconds") == durations["factory"],
          "Renewed factory timeout differs from its approved deadline")
    original_budgets = copy.deepcopy(base["budgets"])
    original_budgets.pop("overall_timeout_seconds")
    _need(budgets == original_budgets and budgets.get("max_active_seats") == 1
          and budgets.get("max_total_tokens") == 2_000_000
          and budgets.get("turn_timeout_seconds") == 900 and budgets.get("spend_cap_usd") == 25,
          "Renewal cannot increase token, dollar, turn or active-role limits")
    guard = copy.deepcopy(options)
    guard.pop("time_renewal")
    _need(guard.pop("overall_timeout_seconds") == durations["guard"],
          "Renewed request timeout differs from its approved deadline")
    original_guard = copy.deepcopy(base["runtime"]["featherless_budget_guard"])
    original_guard.pop("overall_timeout_seconds")
    _need(guard == original_guard, "Renewal cannot change request ledger, metadata or monetary limits")
    _need(len(config["seats"]) == 7 and
          {seat["id"]: seat.get("model") or config["runtime"]["model"] for seat in config["seats"]}
          == record["role_models"], "Renewal role models changed")
    _need(config["runtime"].get("harness") == base["runtime"].get("harness") == "opencode",
          "Renewal must retain the OpenCode harness")
    return record


def reconcile_accounting(config, factory_data, request_data, models, *, now=None):
    """Return exact changed ledgers; mutation, locks and before-images are external."""
    record = validate_renewal_config(config, now=now)
    _need(record is not None, "Missing explicit renewal")
    before = _read(record["original_factory_budget"])
    old_requests = _read(record["original_request_ledger"])
    _need(factory_data == before and request_data == old_requests,
          "Accounting changed since renewal approval was bound")
    from .featherless_guard import _Ledger, _policy
    old_policy = _policy(models, 25_000_000_000, 2_000_000, RENEWED_SECONDS)
    checked = _Ledger(config["runtime"]["featherless_budget_guard"]["ledger"], old_policy)
    checked.data = copy.deepcopy(request_data)
    checked._validate()
    totals = checked.totals()
    _need(totals["observed_tokens"] < 2_000_000 and totals["charged_nano_usd"] < 25_000_000_000
          and type(factory_data.get("tokens")) is int and 0 <= factory_data["tokens"] < 2_000_000,
          "Exhausted token or dollar allowance cannot be renewed")
    new_factory = copy.deepcopy(factory_data)
    new_factory["room_ids"] = record["cumulative_room_ids"]
    if new_factory.get("stopped_reason") == "overall time budget exhausted":
        new_factory["stopped_reason"] = None
    new_requests = copy.deepcopy(request_data)
    if new_requests.get("stopped_reason") == "overall time budget exhausted":
        new_requests["stopped_reason"] = None
    options = config["runtime"]["featherless_budget_guard"]
    new_requests["policy"] = _policy(models, options["approved_credit_nano_usd"],
                                     options["max_total_tokens"], options["overall_timeout_seconds"],
                                     time_renewal=options["time_renewal"])
    checked = _Ledger(options["ledger"], new_requests["policy"])
    checked.data = new_requests
    checked._validate()
    # Validate retained factory values with the same production preflight inspector.
    _need(new_factory["tokens"] == before["tokens"] and new_factory["turns"] == before["turns"]
          and new_factory["token_threads"] == before["token_threads"]
          and new_requests["requests"] == old_requests["requests"], "Retained accounting changed")
    return new_factory, new_requests
