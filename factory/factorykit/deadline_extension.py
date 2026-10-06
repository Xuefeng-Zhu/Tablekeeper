"""Side-effect-free validation for an approved, same-room clock amendment.

This is not a launch command or a replacement for authentication, permissions,
event-admission, thread-continuity, or product gates.  The caller must hold the
existing launch lock while claiming a validated amendment with O_EXCL, saving
the returned cleared ledger, and recording the new owner before starting it.
Original configuration, dispatch, freeze, packets and evidence remain immutable.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import stat
import time
from uuid import UUID

import psutil
import yaml

from .budgets import budget_errors, room_scope
from .common import FactoryError, artifact_path, canonical, digest, run_command

EXTENSION_SECONDS = 28800
OVERALL_STOP = "overall time budget exhausted"
RUNTIME_DELTA_PATH = "factorykit/runtime.py"
_KEYS = {"schema_version", "status", "created_at_utc", "room_id",
         "base_configuration_sha256", "effective_configuration_sha256",
         "validator_sha256", "bindings", "authorization", "stopped_status",
         "owner_registry", "trust_audit", "source_deltas", "changes", "review", "continuity", "model_authorization", "model_catalog"}
_AUTH_KEYS = {"schema_version", "recorded_at_utc", "status", "authorization_source",
              "user_answer", "scope", "room_id", "additional_seconds", "old_deadline_utc",
              "new_deadline_utc", "old_overall_timeout_seconds", "new_overall_timeout_seconds",
              "old_stage_timeout_seconds", "minimum_run6_stage_timeout_seconds",
              "max_total_tokens", "tokens_preserved", "remaining_reported_tokens",
              "original_started_epoch", "run6_started_epoch", "orbstack_cap_mib",
              "initial_dispatch_event", "original_configuration_sha256",
              "original_freeze_sha256", "budget_sha256_at_halt", "limitations"}
_CLAIM_KEYS = {"schema_version", "status", "room_id", "claimed_at_utc",
               "amendment_sha256", "owner_token_sha256", "original_budget_sha256",
               "cleared_budget_sha256", "effective_configuration_sha256"}


def _require(condition, message):
    if not condition:
        raise FactoryError(message)


def _shape(value, keys, name):
    _require(isinstance(value, dict) and set(value) == keys, f"Invalid {name} fields")


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def _bytes(path):
    path = Path(path)
    _require(path.is_absolute() and not path.is_symlink(), "Evidence requires an absolute regular-file path")
    try:
        info = path.stat()
        _require(stat.S_ISREG(info.st_mode) and info.st_size <= 16 * 1024 * 1024,
                 "Evidence is not a bounded regular file")
        return path.read_bytes()
    except OSError:
        raise FactoryError("Required amendment evidence is unavailable") from None


def _json_bytes(raw):
    import json
    try:
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError()
                value[key] = item
            return value
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError):
        raise FactoryError("Malformed amendment evidence") from None


def _read(path):
    return _json_bytes(_bytes(path))


def _reference(path):
    path = Path(path).absolute()
    return {"path": str(path), "sha256": digest(_bytes(path))}


def _referenced(value):
    _shape(value, {"path", "sha256"}, "evidence reference")
    _require(_sha(value["sha256"]), "Invalid evidence digest")
    raw = _bytes(value["path"])
    _require(digest(raw) == value["sha256"], "Referenced evidence changed")
    return _json_bytes(raw)


def _epoch(value):
    _require(type(value) in (int, float) and math.isfinite(value) and value > 0,
             "Invalid accounting epoch")
    return value


def _instant(value):
    try:
        parsed = datetime.fromisoformat(value)
        _require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0,
                 "Amendment timestamps must be UTC")
        return parsed.timestamp()
    except (TypeError, ValueError, AttributeError, OverflowError):
        raise FactoryError("Invalid amendment timestamp") from None


def _now(value):
    return time.time() if value is None else _epoch(value)


def _utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _process_alive(identity):
    """Unknown process visibility is a blocker, never proof of a stopped owner."""
    _shape(identity, {"pid", "created", "cmdline"}, "owned process identity")
    _require(type(identity["pid"]) is int and identity["pid"] > 0
             and isinstance(identity["cmdline"], list)
             and all(isinstance(v, str) for v in identity["cmdline"]), "Invalid process identity")
    _epoch(identity["created"])
    try:
        process = psutil.Process(identity["pid"])
        if abs(process.create_time() - identity["created"]) > 0.001:
            return False  # PID reuse is not this historical owner.
        _require(process.cmdline() == identity["cmdline"], "Owned PID command changed; inspect before continuation")
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        raise FactoryError("Cannot establish whether an owned process is alive") from None


def _stopped(base, amendment):
    status = _referenced(amendment["stopped_status"])
    _require(status.get("status") == "stopped" and status.get("mode") == "judged"
             and status.get("owned_parent_alive") is False
             and type(status.get("owned_children_alive")) is int
             and status["owned_children_alive"] == 0, "A verified stopped runtime is required")
    owner = _referenced(amendment["owner_registry"])
    _require(owner.get("status") == "stopped" and owner.get("mode") == "judged"
             and isinstance(owner.get("children"), list), "Owner registry is not stopped")
    _require(not _process_alive(owner.get("parent", {})), "Owned supervisor is active")
    for identity in owner["children"]:
        _require(not _process_alive(identity), "An owned child is active")
    _require(Path(amendment["owner_registry"]["path"]) == Path(base["paths"]["runs"]) / "runtime/owner.json",
             "Owner registry path changed")


def _binding_bytes(binding, *, live=True):
    _shape(binding, {"path", "preserved_path", "sha256"}, "preserved binding")
    _require(_sha(binding["sha256"]), "Invalid preserved digest")
    raw = _bytes(binding["preserved_path"])
    _require(digest(raw) == binding["sha256"], "Preserved evidence changed")
    if live:
        _require(_bytes(binding["path"]) == raw, "Original live evidence differs from its preservation")
    return raw


def _trust_input(base, record, amendment):
    """Allow only the already-audited exact new-result trust stanza, in memory."""
    raw = _bytes(record["path"])
    if digest(raw) == record["sha256"]:
        return
    _require(amendment["trust_audit"] is not None, "Inherited instruction input changed")
    audit = _referenced(amendment["trust_audit"])
    observed = audit.get("global_configuration", {})
    reconstruction = observed.get("reconstruction", {})
    expected_key = "projects." + base["paths"]["result"]
    _require(observed.get("classification") == "EXACT_NEW_RESULT_TRUST_STANZA_ONLY"
             and observed.get("path") == record["path"]
             and observed.get("locked_sha256") == record["sha256"]
             and observed.get("observed_sha256") == digest(raw)
             and reconstruction.get("matches_locked_sha256") is True
             and reconstruction.get("reconstructed_sha256") == record["sha256"]
             and reconstruction.get("removed_section_key") == expected_key
             and reconstruction.get("section_keys") == ["trust_level"]
             and reconstruction.get("sections_removed") == 1,
             "Inherited instruction drift exceeds the exact audited trust stanza")
    import tomllib
    try:
        parsed = tomllib.loads(raw.decode())
        _require(parsed["projects"][base["paths"]["result"]] == {"trust_level": "trusted"},
                 "Trusted-project stanza contains additional settings")
    except (KeyError, ValueError, UnicodeError):
        raise FactoryError("Cannot verify trusted-project stanza") from None
    header = ('[projects."' + base["paths"]["result"] + '"]\n').encode()
    block = header + b'trust_level = "trusted"\n'
    _require(raw.count(block) == 1, "Exact audited trust stanza is missing or duplicated")
    index = raw.index(block)
    candidates = [raw[:index] + raw[index + len(block):]]
    if index and raw[index - 1:index] == b"\n":
        candidates.append(raw[:index - 1] + raw[index + len(block):])
    _require(any(digest(value) == record["sha256"] for value in candidates),
             "Removing only the audited trust stanza does not recover the frozen input")


def _sources(base, frozen, lock, amendment):
    from .common import scoped_mandate_errors
    _require(not scoped_mandate_errors(base, frozen), "Frozen profile mandates changed")
    root = Path(base["paths"]["factory"]).resolve()
    deltas = amendment["source_deltas"]
    _require(isinstance(deltas, list) and len(deltas) <= 1, "Only one reviewed runtime source delta is permitted")
    allowed = {}
    for item in deltas:
        _shape(item, {"path", "before_sha256", "after_sha256"}, "source delta")
        _require(item["path"] == RUNTIME_DELTA_PATH and _sha(item["before_sha256"])
                 and _sha(item["after_sha256"]) and item["before_sha256"] != item["after_sha256"],
                 "Only an exact runtime.py source delta can be reviewed")
        _require(frozen["files"].get(item["path"]) == item["before_sha256"], "Runtime delta baseline differs from freeze")
        allowed[item["path"]] = item["after_sha256"]
    _require(isinstance(frozen.get("files"), dict) and frozen["files"], "Frozen source inventory is missing")
    for name, expected in frozen["files"].items():
        target = root / name
        _require(target.resolve().is_relative_to(root) and _sha(expected), "Invalid frozen source path")
        _require(digest(_bytes(target)) == allowed.get(name, expected), "Frozen input changed outside reviewed runtime delta")
    challenge = Path(base["paths"]["challenge"]).resolve()
    pinned = lock.get("challenge", {})
    commit = pinned.get("commit")
    _require(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit), "Pinned challenge commit is invalid")
    observed = run_command(["git", "rev-parse", "HEAD"], challenge, timeout=5)
    _require(observed["exit_code"] == 0 and observed["stdout"].strip() == commit,
             "Pinned challenge commit does not match checkout HEAD")
    dirty = run_command(["git", "status", "--porcelain", "--untracked-files=normal"], challenge, timeout=5)
    _require(dirty["exit_code"] == 0 and not dirty["stdout"].strip(),
             "Challenge checkout is dirty or cannot be inspected")
    files = pinned.get("files")
    _require(isinstance(files, dict) and files, "Pinned challenge inventory is missing")
    for name, expected in files.items():
        target = challenge / name
        _require(target.resolve().is_relative_to(challenge) and _sha(expected)
                 and digest(_bytes(target)) == expected, "Pinned challenge file changed")
    for record in lock.get("documents", []):
        _require(_sha(record.get("sha256")) and digest(_bytes(record["path"])) == record["sha256"],
                 "Locked reference document changed")
    for record in lock.get("instruction_inputs", []):
        _trust_input(base, record, amendment)


def _continuity(base, amendment):
    """Bind reviewed helper code and seed evidence; runner verifies derivation.

    This layer never infers that a platform-processed receipt executed a model
    turn. Completed, blocked, pending, and suppressed receipts stay distinct.
    """
    manifest = _referenced(amendment["continuity"])
    _shape(manifest, {"schema_version", "room_id", "cutoff_utc", "platform_audit", "helpers",
                      "completed", "blocked", "pending", "processed_without_admission",
                      "expected_thread_ids"}, "continuity manifest")
    _require(manifest["schema_version"] == 1 and manifest["room_id"] == amendment["room_id"],
             "Continuity manifest room differs from the amendment")
    _require(_instant(manifest["cutoff_utc"]) <= _instant(amendment["created_at_utc"]),
             "Continuity cutoff is later than the proposal")
    _referenced(manifest["platform_audit"])
    root = Path(base["paths"]["factory"]).resolve()
    expected = {str(root / ("factorykit/" + name + ".py")) for name in
                ("continuation_guard", "continuation_platform", "continuation_runner")}
    helpers = manifest["helpers"]
    _require(isinstance(helpers, list) and len(helpers) == len(expected), "Missing continuation helper inventory")
    actual = []
    for helper in helpers:
        _shape(helper, {"path", "sha256"}, "continuation helper")
        _require(helper["path"] in expected and _sha(helper["sha256"])
                 and digest(_bytes(helper["path"])) == helper["sha256"], "Continuation helper changed or is outside the allowed inventory")
        actual.append(helper["path"])
    _require(set(actual) == expected, "Duplicate or missing continuation helper")
    seats = {seat["id"] for seat in base["seats"]}
    threads = manifest["expected_thread_ids"]
    _require(isinstance(threads, dict) and set(threads) == seats
             and all(value is None or _uuid(value) for value in threads.values()), "Invalid expected thread inventory")
    seen = set()
    for field in ("completed", "blocked", "pending", "processed_without_admission"):
        pairs = manifest[field]
        _require(isinstance(pairs, list), "Invalid continuity event inventory")
        for pair in pairs:
            _require(isinstance(pair, list) and len(pair) == 2 and pair[0] in seats and _uuid(pair[1]),
                     "Invalid continuity event identity")
            identity = tuple(pair)
            _require(identity not in seen, "Continuity event categories overlap or contain duplicates")
            seen.add(identity)
    return manifest


def _effective_model(base, amendment, effective, *, now):
    """Apply only the separately approved, catalog-verified all-seat model swap."""
    reference, catalog_reference = amendment["model_authorization"], amendment["model_catalog"]
    if reference is None and catalog_reference is None:
        return
    _require(reference is not None and catalog_reference is not None, "Model amendment requires both authorization and catalog")
    approval = _referenced(reference)
    _shape(approval, {"schema_version", "status", "authorization_source", "user_answer", "room_id",
                     "resolved_model", "seat_ids", "catalog_sha256", "recorded_at_utc",
                     "base_configuration_sha256"}, "model authorization")
    _require(approval["schema_version"] == 1 and approval["status"] == "APPROVED_NOT_APPLIED"
             and approval["authorization_source"] == "Direct user answer in current Codex chat"
             and approval["user_answer"] == "Can you change the factory agent model to sol6.1"
             and approval["room_id"] == amendment["room_id"]
             and approval["resolved_model"] == "gpt-6.1-sol"
             and approval["seat_ids"] == sorted(seat["id"] for seat in base["seats"])
             and approval["catalog_sha256"] == catalog_reference["sha256"]
             and approval["base_configuration_sha256"] == digest(canonical(base))
             and _instant(approval["recorded_at_utc"]) <= now,
             "Model authorization differs from the exact approved all-seat switch")
    catalog = _referenced(catalog_reference)
    _require(catalog.get("method") == "initialize + model/list"
             and catalog.get("inference_started") is False and catalog.get("sdk_version") == "4.0.0"
             and catalog.get("codex_version") == base["runtime"]["version"]
             and _instant(catalog.get("checked_at")) <= now and isinstance(catalog.get("models"), list),
             "Model catalog is not the bound no-inference discovery from the pinned runtime")
    matches = [model for model in catalog["models"] if isinstance(model, dict)
               and model.get("model", model.get("id")) == "gpt-6.1-sol"]
    _require(len(matches) == 1, "Requested model is not uniquely advertised")
    levels = matches[0].get("supportedReasoningEfforts")
    _require(isinstance(levels, list) and all(isinstance(item, dict) and isinstance(item.get("reasoningEffort"), str) for item in levels),
             "Requested model reasoning support is malformed")
    available = {item["reasoningEffort"] for item in levels}
    _require(all(seat.get("reasoning_effort") in available for seat in base["seats"]),
             "Requested model does not advertise every preserved seat reasoning effort")
    effective["runtime"]["model"] = "gpt-6.1-sol"
    for seat in effective["seats"]:
        seat["model"] = "gpt-6.1-sol"


def _review(amendment, *, proposed=False):
    if proposed:
        _require(amendment["status"] == "PROPOSED" and amendment["review"] is None, "Expected an unapproved proposal")
        return
    _require(amendment["status"] == "APPROVED_NOT_APPLIED", "Amendment is not approved")
    record = _referenced(amendment["review"])
    _shape(record, {"schema_version", "status", "scope", "independent", "reviewer",
                    "reviewed_at_utc", "proposal_sha256", "source_deltas"}, "independent review")
    proposal = copy.deepcopy(amendment)
    proposal.update(status="PROPOSED", review=None)
    _require(record["schema_version"] == 1 and record["status"] == "PASS"
             and record["scope"] == "same-room deadline extension" and record["independent"] is True
             and isinstance(record["reviewer"], str) and bool(record["reviewer"].strip())
             and record["proposal_sha256"] == digest(canonical(proposal))
             and record["source_deltas"] == amendment["source_deltas"], "Independent review does not bind this exact proposal")
    _instant(record["reviewed_at_utc"])


def _static(base, amendment, *, now=None, proposed=False):
    """Verify immutable inputs; never asserts stopped/current-child admission."""
    _shape(amendment, _KEYS, "amendment")
    _require(amendment["schema_version"] == 1 and _uuid(amendment["room_id"]), "Invalid amendment identity")
    _require(digest(canonical(base)) == amendment["base_configuration_sha256"], "Base configuration changed")
    _require(digest(_bytes(Path(__file__).absolute())) == amendment["validator_sha256"], "Amendment validator changed after proposal")
    instant = _now(now)
    _require(_instant(amendment["created_at_utc"]) <= instant, "Proposal timestamp is in the future")
    authorization = _referenced(amendment["authorization"])
    _shape(authorization, _AUTH_KEYS, "authorization")
    _require(authorization["schema_version"] == 1 and authorization["status"] == "APPROVED_NOT_APPLIED"
             and authorization["authorization_source"] == "Direct user answer in current Codex chat"
             and authorization["user_answer"] == "Add 8 hours", "Explicit eight-hour authorization is missing")
    _review(amendment, proposed=proposed)
    _continuity(base, amendment)
    bindings = amendment["bindings"]
    _shape(bindings, {"configuration", "freeze", "launch_ledger", "source_lock", "budget", "workflow"}, "binding inventory")
    raw = {key: _binding_bytes(value, live=key not in {"budget", "workflow"}) for key, value in bindings.items()}
    try:
        _require(yaml.safe_load(raw["configuration"]) == base, "Preserved configuration differs from base")
    except yaml.YAMLError:
        raise FactoryError("Malformed preserved configuration") from None
    frozen, launch, lock, ledger = (_json_bytes(raw[key]) for key in ("freeze", "launch_ledger", "source_lock", "budget"))
    runs = Path(base["paths"]["runs"])
    paths = {"freeze": runs / "freeze/latest.json", "launch_ledger": runs / "launch/ledger.json",
             "source_lock": artifact_path(base, "source_lock"), "budget": runs / "runtime/budget-subscription.json",
             "workflow": runs / f"runtime/workflow-{amendment['room_id']}.json"}
    _require(all(Path(bindings[name]["path"]) == path for name, path in paths.items()), "Binding path does not match the original run")
    _require(frozen.get("status") == "READY_TO_LAUNCH" and frozen.get("configuration_sha256") == digest(canonical(base))
             and frozen.get("budgets") == base["budgets"] and frozen.get("seats") == base["seats"]
             and frozen.get("source_lock_sha256") == digest(raw["source_lock"]), "Original READY freeze binding failed")
    room = base["band"]["judged_room_id"]
    _require(room == amendment["room_id"] == authorization["room_id"], "Amendment is restricted to its original judged room")
    _require(launch.get("mode") == "all" and len(launch.get("entries", [])) == 1, "Exactly one original all-stage dispatch is required")
    entry = launch["entries"][0]
    _require(entry.get("state") == "DISPATCHED" and entry.get("stages") == [1, 2, 3, 4]
             and entry.get("room_event") == authorization["initial_dispatch_event"] and _uuid(entry.get("room_event"))
             and entry.get("freeze_sha256") == digest(raw["freeze"]), "Original dispatch binding failed")
    tasks = frozen.get("tasks", {})
    _require(isinstance(tasks, dict) and tasks, "Frozen task inventory is missing")
    for name, task in tasks.items():
        _require(Path(name).name == name and _sha(task.get("sha256"))
                 and digest(_bytes(runs / "tasks" / name)) == task["sha256"], "Original task packet changed")
    _require(Path(entry.get("task", "")) == runs / "tasks/judged-all-stages.md"
             and entry.get("task_sha256") == tasks["judged-all-stages.md"]["sha256"], "Dispatched task binding failed")
    _sources(base, frozen, lock, amendment)
    limits = base["budgets"]
    _require(not budget_errors(limits) and limits.get("approved") is True
             and limits.get("billing_mode") == "subscription_only", "Original subscription budget is invalid")
    active, rooms = room_scope(base)
    _require(ledger.get("room_id") is None and ledger.get("room_ids") == rooms, "Accounting room scope changed")
    _require(ledger.get("stopped_reason") == OVERALL_STOP, "Only the exact overall-time halt is eligible")
    _require(ledger.get("room_stopped_reasons", {}).get(room) is None, "Current room has a separate stop reason")
    start = _epoch(ledger.get("started_epoch")); room_start = _epoch(ledger.get("room_started_epochs", {}).get(room))
    _require(start <= room_start <= instant, "Original accounting epochs are invalid")
    seats = {s["id"] for s in base["seats"]}
    def counts(value, allowed=None):
        return (isinstance(value, dict) and (allowed is None or set(value).issubset(allowed))
                and all(isinstance(k, str) and type(v) is int and v >= 0 for k, v in value.items()))
    _require(type(ledger.get("tokens")) is int and 0 <= ledger["tokens"] < limits["max_total_tokens"]
             and counts(ledger.get("turns"), seats) and counts(ledger.get("token_threads"))
             and all(v < limits["max_turns_per_seat"] for v in ledger["turns"].values()), "Accounting is malformed or exhausted")
    for field in ("room_started_epochs", "room_turns", "room_stopped_reasons"):
        _require(isinstance(ledger.get(field), dict) and set(ledger[field]).issubset(rooms), "Closed-room accounting is malformed")
    _require(all(start <= _epoch(v) <= instant for v in ledger["room_started_epochs"].values())
             and all(counts(v, seats) for v in ledger["room_turns"].values())
             and all(isinstance(v, str) and v for v in ledger["room_stopped_reasons"].values()), "Closed-room history is malformed")
    old_end = start + limits["overall_timeout_seconds"]; new_end = old_end + EXTENSION_SECONDS
    new_stage = max(limits["stage_timeout_seconds"], math.ceil(new_end - room_start))
    changes = {"additional_seconds": EXTENSION_SECONDS, "old_deadline_utc": _utc(old_end),
               "new_deadline_utc": _utc(new_end), "old_overall_timeout_seconds": limits["overall_timeout_seconds"],
               "new_overall_timeout_seconds": limits["overall_timeout_seconds"] + EXTENSION_SECONDS,
               "old_stage_timeout_seconds": limits["stage_timeout_seconds"], "new_stage_timeout_seconds": new_stage}
    _require(amendment["changes"] == changes and old_end <= instant < new_end, "Amendment clock differs from the authorized eight-hour extension or is expired")
    expected_auth = {"additional_seconds": EXTENSION_SECONDS, "old_overall_timeout_seconds": limits["overall_timeout_seconds"],
                     "new_overall_timeout_seconds": changes["new_overall_timeout_seconds"], "old_stage_timeout_seconds": limits["stage_timeout_seconds"],
                     "minimum_run6_stage_timeout_seconds": new_stage, "max_total_tokens": limits["max_total_tokens"],
                     "tokens_preserved": ledger["tokens"], "remaining_reported_tokens": limits["max_total_tokens"] - ledger["tokens"],
                     "original_started_epoch": start, "run6_started_epoch": room_start,
                     "original_configuration_sha256": digest(raw["configuration"]), "original_freeze_sha256": digest(raw["freeze"]),
                     "budget_sha256_at_halt": digest(raw["budget"])}
    _require(all(type(authorization[k]) is type(v) and authorization[k] == v for k, v in expected_auth.items()),
             "Authorization does not match original clocks, counters or immutable evidence")
    _require(_instant(authorization["old_deadline_utc"]) == old_end and _instant(authorization["new_deadline_utc"]) == new_end
             and type(authorization["orbstack_cap_mib"]) is int and authorization["orbstack_cap_mib"] > 0,
             "Authorization deadline or preserved resource cap is invalid")
    effective = copy.deepcopy(base)
    effective["budgets"].update(overall_timeout_seconds=changes["new_overall_timeout_seconds"], stage_timeout_seconds=new_stage)
    _effective_model(base, amendment, effective, now=instant)
    _require(digest(canonical(effective)) == amendment["effective_configuration_sha256"], "Effective configuration changes exceed the approved clocks and model")
    return effective, ledger


def prepare_amendment(base_config, *, authorization_path, stopped_status_path, continuity_path,
                      preservation_manifest_path=None, trust_audit_path=None,
                      source_deltas=None, model_authorization_path=None, model_catalog_path=None, now=None):
    """Return a proposal; no file, ledger, process, room or setting is changed."""
    authorization_path = Path(authorization_path).absolute()
    preserved = _read(preservation_manifest_path or authorization_path.parent / "before-manifest.json")
    entries = preserved.get("files", [])
    runs = Path(base_config["paths"]["runs"]); room = base_config["band"]["judged_room_id"]
    targets = {"configuration": runs / "factory.yaml", "freeze": runs / "freeze/latest.json",
               "launch_ledger": runs / "launch/ledger.json", "source_lock": artifact_path(base_config, "source_lock"),
               "budget": runs / "runtime/budget-subscription.json", "workflow": runs / f"runtime/workflow-{room}.json"}
    bindings = {}
    for key, path in targets.items():
        matches = [entry for entry in entries if entry.get("source") == str(path)]
        _require(len(matches) == 1, "Preservation manifest is missing an exact unique original")
        value = matches[0]
        bindings[key] = {"path": str(path), "preserved_path": value["preserved"], "sha256": value["sha256"]}
    ledger = _json_bytes(_binding_bytes(bindings["budget"]))
    limits = base_config["budgets"]; old_end = ledger["started_epoch"] + limits["overall_timeout_seconds"]
    new_end = old_end + EXTENSION_SECONDS
    new_stage = max(limits["stage_timeout_seconds"], math.ceil(new_end - ledger["room_started_epochs"][room]))
    effective = copy.deepcopy(base_config)
    effective["budgets"].update(overall_timeout_seconds=limits["overall_timeout_seconds"] + EXTENSION_SECONDS, stage_timeout_seconds=new_stage)
    proposal = {"schema_version": 1, "status": "PROPOSED", "created_at_utc": _utc(_now(now)), "room_id": room,
                "base_configuration_sha256": digest(canonical(base_config)), "effective_configuration_sha256": digest(canonical(effective)),
                "validator_sha256": digest(_bytes(Path(__file__).absolute())), "bindings": bindings,
                "authorization": _reference(authorization_path), "stopped_status": _reference(stopped_status_path),
                "owner_registry": _reference(runs / "runtime/owner.json"),
                "trust_audit": _reference(trust_audit_path) if trust_audit_path else None,
                "source_deltas": copy.deepcopy(source_deltas or []), "review": None,
                "continuity": _reference(continuity_path),
                "model_authorization": _reference(model_authorization_path) if model_authorization_path else None,
                "model_catalog": _reference(model_catalog_path) if model_catalog_path else None,
                "changes": {"additional_seconds": EXTENSION_SECONDS, "old_deadline_utc": _utc(old_end), "new_deadline_utc": _utc(new_end),
                            "old_overall_timeout_seconds": limits["overall_timeout_seconds"], "new_overall_timeout_seconds": limits["overall_timeout_seconds"] + EXTENSION_SECONDS,
                            "old_stage_timeout_seconds": limits["stage_timeout_seconds"], "new_stage_timeout_seconds": new_stage}}
    _effective_model(base_config, proposal, effective, now=_now(now))
    proposal["effective_configuration_sha256"] = digest(canonical(effective))
    _static(base_config, proposal, now=now, proposed=True)
    _stopped(base_config, proposal)
    return proposal


def approve_amendment(base_config, proposal, *, review_path, now=None):
    """Attach already-observed independent review, returning data only."""
    _static(base_config, proposal, now=now, proposed=True)
    _stopped(base_config, proposal)
    approved = copy.deepcopy(proposal)
    approved.update(status="APPROVED_NOT_APPLIED", review=_reference(review_path))
    _static(base_config, approved, now=now)
    return approved


def validate_amendment(base_config, amendment_path, *, now=None):
    """Stopped-parent phase. Return effective config or raise safe FactoryError."""
    try:
        amendment = _read(amendment_path)
        effective, _ = _static(base_config, amendment, now=now)
        _binding_bytes(amendment["bindings"]["budget"])
        _binding_bytes(amendment["bindings"]["workflow"])
        _stopped(base_config, amendment)
        return effective
    except FactoryError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise FactoryError("Malformed or inconsistent deadline amendment") from None


def amendment_errors(base_config, amendment_path, *, now=None):
    try:
        validate_amendment(base_config, amendment_path, now=now)
        return []
    except FactoryError as error:
        return [str(error)]


def prepare_claim(base_config, amendment_path, *, owner_token, now=None):
    """Caller writes claim with O_EXCL and cleared ledger under launch lock.

    The only semantic ledger delta returned is stopped_reason -> None.  Claim
    validation never grants dispatch or automatically retries a crashed claim.
    """
    effective = validate_amendment(base_config, amendment_path, now=now)
    _require(isinstance(owner_token, str) and re.fullmatch(r"[0-9a-f]{48}", owner_token), "Invalid continuation owner token")
    amendment = _read(amendment_path)
    old = _json_bytes(_binding_bytes(amendment["bindings"]["budget"]))
    cleared = copy.deepcopy(old); cleared["stopped_reason"] = None
    claim = {"schema_version": 1, "status": "CLAIMED", "room_id": amendment["room_id"],
             "claimed_at_utc": _utc(_now(now)), "amendment_sha256": digest(_bytes(amendment_path)),
             "owner_token_sha256": digest(owner_token), "original_budget_sha256": amendment["bindings"]["budget"]["sha256"],
             "cleared_budget_sha256": digest(canonical(cleared)), "effective_configuration_sha256": digest(canonical(effective))}
    return {"claim": claim, "cleared_budget": cleared}


def validate_claimed_amendment(base_config, amendment_path, claim_path, *, owner_token, now=None):
    """Child phase: require the exact claim, cleared ledger and current owner PID."""
    try:
        amendment = _read(amendment_path)
        effective, original = _static(base_config, amendment, now=now)
        claim = _read(claim_path)
        _shape(claim, _CLAIM_KEYS, "continuation claim")
        cleared = copy.deepcopy(original); cleared["stopped_reason"] = None
        _require(claim["schema_version"] == 1 and claim["status"] == "CLAIMED"
                 and claim["room_id"] == amendment["room_id"]
                 and claim["amendment_sha256"] == digest(_bytes(amendment_path))
                 and claim["owner_token_sha256"] == digest(owner_token)
                 and claim["original_budget_sha256"] == amendment["bindings"]["budget"]["sha256"]
                 and claim["cleared_budget_sha256"] == digest(canonical(cleared))
                 and claim["effective_configuration_sha256"] == digest(canonical(effective)), "Continuation claim binding failed")
        _require(_instant(amendment["created_at_utc"]) <= _instant(claim["claimed_at_utc"]) <= _now(now), "Invalid continuation claim time")
        live = _bytes(amendment["bindings"]["budget"]["path"])
        _require(digest(live) == claim["cleared_budget_sha256"] and _json_bytes(live) == cleared, "Claimed ledger changed beyond clearing its deadline halt")
        _binding_bytes(amendment["bindings"]["workflow"])
        owner = _read(amendment["owner_registry"]["path"])
        _require(owner.get("mode") == "judged" and owner.get("status") in {"starting", "running"}
                 and owner.get("token") == owner_token and owner.get("parent", {}).get("pid") == os.getpid()
                 and _process_alive(owner["parent"]), "Claim is not owned by this continuation child")
        return effective
    except FactoryError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise FactoryError("Malformed or inconsistent continuation claim") from None
