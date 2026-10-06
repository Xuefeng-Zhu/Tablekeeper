"""Offline, separately authorized token-only continuation after a retained stop.

Preparation does not grant execution. Caller owns the exclusive launch lock,
claim persistence, and continuity derivation. This module never writes state.
"""
from __future__ import annotations
import copy
import os
from pathlib import Path
import re
import tomllib
import yaml
from . import deadline_extension as de
from .common import FactoryError, canonical, digest
from .budgets import budget_errors, room_scope

OLD_LIMIT = 286011577
ADDITIONAL_TOKENS = 500000000
NEW_LIMIT = OLD_LIMIT + ADDITIONAL_TOKENS
CLEANUP_STOP = "continuation turn did not complete; preserve before retry"
_KEYS = {"schema_version", "kind", "status", "created_at_utc", "room_id", "base_configuration_sha256",
         "effective_configuration_sha256", "validator_sha256", "deadline_validator_sha256", "bindings",
         "parent_amendment", "parent_claim", "stop_summary", "authorization", "stopped_status",
         "owner_registry", "trust_audit", "source_deltas", "continuity", "model_authorization", "model_catalog",
         "changes", "review", "inherited_defaults_reconciliation"}
_AUTH_KEYS = {"schema_version", "status", "authorization_source", "user_answer", "room_id", "additional_tokens",
              "old_max_total_tokens", "new_max_total_tokens", "deadline_utc", "current_budget_sha256",
              "parent_amendment_sha256", "base_configuration_sha256", "recorded_at_utc"}
_CLAIM_KEYS = de._CLAIM_KEYS | {"kind"}

def _require(condition, message):
    de._require(condition, message)


def _review(amendment, proposed):
    if proposed:
        _require(amendment["status"] == "PROPOSED" and amendment["review"] is None, "Expected an unapproved token proposal")
        return
    _require(amendment["status"] == "APPROVED_NOT_APPLIED", "Token amendment is not approved")
    review = de._referenced(amendment["review"])
    de._shape(review, {"schema_version", "status", "scope", "independent", "reviewer", "reviewed_at_utc",
                       "proposal_sha256", "source_deltas"}, "token independent review")
    proposal = copy.deepcopy(amendment); proposal.update(status="PROPOSED", review=None)
    _require(review["schema_version"] == 1 and review["status"] == "PASS" and review["independent"] is True
             and review["scope"] == "same-room token extension" and isinstance(review["reviewer"], str)
             and bool(review["reviewer"].strip()) and review["proposal_sha256"] == digest(canonical(proposal))
             and review["source_deltas"] == amendment["source_deltas"], "Independent review does not bind this token proposal")
    de._instant(review["reviewed_at_utc"])


def _reconciled_sources(base, effective, lock, amendment):
    """Prove the exact ambient-default delta without changing any locked bytes."""
    reference = amendment["inherited_defaults_reconciliation"]
    if reference is None:
        return lock
    proof = de._referenced(reference)
    de._shape(proof, {"schema_version", "status", "path", "observed_sha256", "prior_audited_sha256",
                     "locked_sha256", "trust_audit", "observed_defaults", "prior_audited_defaults",
                     "seat_overrides"}, "inherited defaults reconciliation")
    observed_defaults = {"model": "gpt-6-astra", "model_reasoning_effort": "xhigh"}
    prior_defaults = {"model": "gpt-6.1-sol", "model_reasoning_effort": "high"}
    _require(proof["schema_version"] == 1 and proof["status"] == "PASS_EXACT_INHERITED_DEFAULTS_RECONSTRUCTION"
             and proof["observed_defaults"] == observed_defaults and proof["prior_audited_defaults"] == prior_defaults
             and proof["trust_audit"] == amendment["trust_audit"]
             and all(de._sha(proof[k]) for k in ("observed_sha256", "prior_audited_sha256", "locked_sha256")),
             "Inherited defaults proof does not describe the exact reviewed transition")
    records = [r for r in lock.get("instruction_inputs", []) if r.get("path") == proof["path"]]
    _require(len(records) == 1 and records[0]["sha256"] == proof["locked_sha256"],
             "Inherited defaults proof is not bound to one original instruction input")
    seats = effective["seats"]
    _require(effective["runtime"].get("model") == "gpt-6.1-sol" and seats
             and all(s.get("model") == "gpt-6.1-sol" and s.get("reasoning_effort") in {"medium", "high"} for s in seats),
             "Every seat must explicitly override the inherited model and reasoning effort")
    expected_seats = [{"seat_id": s["id"], "model": s["model"], "reasoning_effort": s["reasoning_effort"]} for s in seats]
    _require(proof["seat_overrides"] == expected_seats, "Inherited defaults proof seat overrides changed")
    raw = de._bytes(proof["path"])
    try:
        parsed = tomllib.loads(raw.decode())
    except (ValueError, UnicodeError):
        raise FactoryError("Inherited defaults TOML is malformed or has duplicate keys") from None
    current_defaults = {key: parsed.get(key) for key in prior_defaults}
    _require(isinstance(current_defaults["model"], str)
             and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", current_defaults["model"])
             and isinstance(current_defaults["model_reasoning_effort"], str)
             and current_defaults["model_reasoning_effort"] in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"},
             "Inherited defaults must be simple top-level model and reasoning values")
    # Preserve every unrelated byte. Conservative bare, unique top-level lines only;
    # TOML serialization would hide unrelated formatting or instruction changes.
    reconstructed = raw
    for key, value in current_defaults.items():
        pattern = rb"(?m)^[ \t]*" + key.encode() + rb"[ \t]*=[^\n]*(?:\n|$)"
        matches = list(re.finditer(pattern, raw))
        expected_line = (key + ' = "' + value + '"\n').encode()
        _require(len(matches) == 1 and matches[0].group() == expected_line
                 and not re.search(rb"(?m)^[ \t]*\[", raw[:matches[0].start()]),
                 "Inherited defaults require exact unique top-level assignment lines")
        reconstructed = reconstructed.replace(expected_line, (key + ' = "' + prior_defaults[key] + '"\n').encode(), 1)
    _require(digest(reconstructed) == proof["prior_audited_sha256"],
             "Replacing only the inherited defaults does not recover the prior audited input")
    # App defaults may change while the independently configured factory waits.
    # Re-prove the recorded snapshot from the same immutable prior bytes as well
    # as the current projection; no other byte or field is ignored.
    recorded = reconstructed
    for key, value in observed_defaults.items():
        recorded = recorded.replace((key + ' = "' + prior_defaults[key] + '"\n').encode(),
                                    (key + ' = "' + value + '"\n').encode(), 1)
    _require(digest(recorded) == proof["observed_sha256"], "Recorded inherited defaults snapshot is not reconstructible")
    audit = de._referenced(proof["trust_audit"])
    observed = audit.get("global_configuration", {}); reconstruction = observed.get("reconstruction", {})
    _require(observed.get("classification") == "EXACT_NEW_RESULT_TRUST_STANZA_ONLY"
             and observed.get("path") == proof["path"] and observed.get("locked_sha256") == proof["locked_sha256"]
             and observed.get("observed_sha256") == proof["prior_audited_sha256"]
             and reconstruction.get("matches_locked_sha256") is True
             and reconstruction.get("reconstructed_sha256") == proof["locked_sha256"]
             and reconstruction.get("removed_section_key") == "projects." + base["paths"]["result"]
             and reconstruction.get("section_keys") == ["trust_level"] and reconstruction.get("sections_removed") == 1,
             "Inherited defaults proof differs from the immutable prior trust audit")
    try:
        prior = tomllib.loads(reconstructed.decode())
        _require(prior["projects"][base["paths"]["result"]] == {"trust_level": "trusted"},
                 "Prior trusted-project stanza contains additional settings")
    except (KeyError, ValueError, UnicodeError):
        raise FactoryError("Cannot verify prior trusted-project stanza") from None
    block = ('[projects."' + base["paths"]["result"] + '"]\ntrust_level = "trusted"\n').encode()
    _require(reconstructed.count(block) == 1, "Exact prior trusted-project stanza is missing or duplicated")
    index = reconstructed.index(block)
    candidates = [reconstructed[:index] + reconstructed[index + len(block):]]
    if index and reconstructed[index - 1:index] == b"\n":
        candidates.append(reconstructed[:index - 1] + reconstructed[index + len(block):])
    _require(any(digest(value) == proof["locked_sha256"] for value in candidates),
             "Removing only the prior trust stanza does not recover the original locked input")
    _require(de._bytes(proof["path"]) == raw, "Inherited defaults changed during reconciliation")
    checked = copy.deepcopy(lock)
    next(r for r in checked["instruction_inputs"] if r["path"] == proof["path"])["sha256"] = digest(raw)
    return checked


def _parent(base, amendment, now):
    """Revalidate immutable prior approval without its now-obsolete live state."""
    _require(not budget_errors(base["budgets"]) and base["budgets"].get("approved") is True
             and base["budgets"].get("billing_mode") == "subscription_only", "Prior budget must be approved and subscription-only")
    parent = de._referenced(amendment["parent_amendment"])
    de._shape(parent, de._KEYS, "prior deadline amendment")
    de._review(parent)
    _require(parent["room_id"] == amendment["room_id"] == base["band"]["judged_room_id"]
             and parent["base_configuration_sha256"] == digest(canonical(base))
             and parent["validator_sha256"] == amendment["deadline_validator_sha256"], "Prior deadline amendment identity changed")
    original = {k: de._binding_bytes(v, live=False) for k,v in parent["bindings"].items()}
    _require(yaml.safe_load(original["configuration"]) == base, "Prior original configuration differs")
    frozen, launch, lock, original_budget = (de._json_bytes(original[k]) for k in ("freeze", "launch_ledger", "source_lock", "budget"))
    _require(frozen.get("status") == "READY_TO_LAUNCH" and frozen.get("configuration_sha256") == digest(canonical(base))
             and frozen.get("budgets") == base["budgets"] and frozen.get("seats") == base["seats"]
             and frozen.get("source_lock_sha256") == digest(original["source_lock"]), "Original READY freeze binding failed")
    authorization = de._referenced(parent["authorization"])
    de._shape(authorization, de._AUTH_KEYS, "prior deadline authorization")
    _require(authorization["status"] == "APPROVED_NOT_APPLIED" and authorization["authorization_source"] == "Direct user answer in current Codex chat"
             and authorization["user_answer"] == "Add 8 hours" and authorization["room_id"] == parent["room_id"], "Prior explicit deadline authorization is missing")
    _require(original_budget.get("stopped_reason") == de.OVERALL_STOP
             and original_budget.get("room_stopped_reasons",{}).get(parent["room_id"]) is None,
             "Prior deadline approval has an ineligible original halt")
    start = de._epoch(original_budget["started_epoch"])
    room_start = de._epoch(original_budget["room_started_epochs"][parent["room_id"]])
    import math
    old_end = start + base["budgets"]["overall_timeout_seconds"]
    new_end = old_end + de.EXTENSION_SECONDS
    new_stage = max(base["budgets"]["stage_timeout_seconds"], math.ceil(new_end - room_start))
    expected = {"additional_seconds": de.EXTENSION_SECONDS, "old_deadline_utc": de._utc(old_end), "new_deadline_utc": de._utc(new_end),
                "old_overall_timeout_seconds": base["budgets"]["overall_timeout_seconds"],
                "new_overall_timeout_seconds": base["budgets"]["overall_timeout_seconds"] + de.EXTENSION_SECONDS,
                "old_stage_timeout_seconds": base["budgets"]["stage_timeout_seconds"], "new_stage_timeout_seconds": new_stage}
    _require(parent["changes"] == expected and now < new_end, "Prior approved deadline changed or expired")
    _require(authorization["additional_seconds"] == de.EXTENSION_SECONDS
             and authorization["max_total_tokens"] == OLD_LIMIT
             and de._instant(authorization["new_deadline_utc"]) == new_end
             and authorization["original_started_epoch"] == start and authorization["run6_started_epoch"] == room_start
             and authorization["original_configuration_sha256"] == digest(original["configuration"])
             and authorization["original_freeze_sha256"] == digest(original["freeze"])
             and authorization["budget_sha256_at_halt"] == digest(original["budget"]), "Prior approval evidence differs from its original inputs")
    _require(launch.get("mode") == "all" and len(launch.get("entries", [])) == 1, "Exactly one original dispatch is required")
    entry = launch["entries"][0]
    runs = Path(base["paths"]["runs"])
    tasks = frozen.get("tasks", {})
    _require(entry.get("state") == "DISPATCHED" and entry.get("stages") == [1,2,3,4]
             and entry.get("room_event") == authorization["initial_dispatch_event"]
             and entry.get("freeze_sha256") == digest(original["freeze"]), "Original consumed dispatch changed")
    _require(isinstance(tasks, dict) and tasks, "Original task inventory is missing")
    for name, task in tasks.items():
        _require(Path(name).name == name and digest(de._bytes(runs / "tasks" / name)) == task["sha256"], "Original task packet changed")
    _require(entry.get("task") == str(runs / "tasks/judged-all-stages.md")
             and entry.get("task_sha256") == tasks["judged-all-stages.md"]["sha256"], "Original dispatched packet changed")
    for name in ("configuration", "freeze", "launch_ledger", "source_lock"):
        _require(amendment["bindings"][name] == parent["bindings"][name], "Token proposal cannot replace original bindings")
    _require(amendment["trust_audit"] == parent["trust_audit"]
             and amendment["model_authorization"] == parent["model_authorization"]
             and amendment["model_catalog"] == parent["model_catalog"], "Token amendment cannot alter prior model or source exceptions")
    effective = copy.deepcopy(base)
    effective["budgets"].update(overall_timeout_seconds=expected["new_overall_timeout_seconds"], stage_timeout_seconds=new_stage)
    de._effective_model(base, parent, effective, now=now)
    _require(effective["budgets"]["max_total_tokens"] == OLD_LIMIT and effective["runtime"]["model"] == "gpt-6.1-sol"
             and all(s["model"] == "gpt-6.1-sol" for s in effective["seats"])
             and digest(canonical(effective)) == parent["effective_configuration_sha256"], "Prior effective Sol configuration changed")
    de._sources(base, frozen, _reconciled_sources(base, effective, lock, amendment), amendment)
    return parent, original_budget, effective


def _counts(value, allowed=None):
    return isinstance(value, dict) and (allowed is None or set(value).issubset(allowed)) and all(isinstance(k,str) and type(v) is int and v >= 0 for k,v in value.items())


def _current(base, amendment, old, effective, raw, now):
    ledger, workflow, owner = (de._json_bytes(raw[k]) for k in ("budget", "workflow", "owner"))
    room = amendment["room_id"]; _, rooms = room_scope(base); seats = {s["id"] for s in base["seats"]}
    _require(ledger.get("room_id") is None and ledger.get("room_ids") == rooms and ledger.get("started_epoch") == old["started_epoch"]
             and ledger.get("room_started_epochs") == old.get("room_started_epochs")
             and ledger.get("room_stopped_reasons") == old.get("room_stopped_reasons"), "Original accounting epochs or closed-room scope changed")
    _require(ledger.get("room_stopped_reasons",{}).get(room) is None, "Current room has a separate stop reason")
    _require(type(ledger.get("tokens")) is int and OLD_LIMIT <= ledger["tokens"] < NEW_LIMIT
             and ledger.get("stopped_reason") == CLEANUP_STOP and _counts(ledger.get("turns"), seats)
             and _counts(ledger.get("token_threads")) and isinstance(ledger.get("room_turns"),dict), "Current stopped token accounting is invalid")
    _require(all(ledger["turns"].get(k,0) >= v for k,v in old["turns"].items())
             and all(v < effective["budgets"]["max_turns_per_seat"] for v in ledger["turns"].values())
             and all(ledger["token_threads"].get(k,0) >= v for k,v in old["token_threads"].items()), "Counters regressed or a turn ceiling is exhausted")
    _require(set(ledger["room_turns"]).issubset(rooms) and all(_counts(v,seats) for v in ledger["room_turns"].values())
             and all(ledger["room_turns"].get(k) == v for k,v in old.get("room_turns",{}).items() if k != room)
             and all(ledger["room_turns"].get(room,{}).get(k,0) >= v for k,v in old.get("room_turns",{}).get(room,{}).items()), "Room accounting changed outside the continued room")
    _require(owner.get("mode") == "judged" and owner.get("status") == "stopped"
             and amendment["owner_registry"] == {"path":amendment["bindings"]["owner"]["path"], "sha256":digest(raw["owner"])}, "Stopped ownership binding changed")
    claim = de._referenced(amendment["parent_claim"])
    de._shape(claim, de._CLAIM_KEYS, "prior consumed claim")
    parent = de._referenced(amendment["parent_amendment"])
    cleared_original = copy.deepcopy(old); cleared_original["stopped_reason"] = None
    _require(claim["schema_version"] == 1 and claim["status"] == "CLAIMED" and claim["room_id"] == room
             and claim["original_budget_sha256"] == parent["bindings"]["budget"]["sha256"]
             and claim["cleared_budget_sha256"] == digest(canonical(cleared_original))
             and claim["effective_configuration_sha256"] == digest(canonical(effective))
             and de._instant(parent["created_at_utc"]) <= de._instant(claim["claimed_at_utc"]) <= now
             and claim["amendment_sha256"] == amendment["parent_amendment"]["sha256"]
             and claim["owner_token_sha256"] == digest(owner["token"])
             and owner.get("continuation",{}).get("claim_path") == amendment["parent_claim"]["path"]
             and owner["continuation"]["amendment_sha256"] == claim["amendment_sha256"], "Prior consumed claim is not linked to the stopped owner")
    summary = de._referenced(amendment["stop_summary"])
    _require(summary.get("schema_version") == 1 and summary.get("status") == "STOPPED_AT_TOKEN_CEILING"
             and summary.get("room_id") == room and summary.get("all_recorded_owned_processes_absent") is True
             and summary.get("reported_tokens") == ledger["tokens"] and summary.get("max_total_tokens") == OLD_LIMIT
             and summary.get("reported_overage") == ledger["tokens"]-OLD_LIMIT
             and summary.get("persisted_stop_reason") == ledger["stopped_reason"]
             and de._instant(summary.get("observed_at")) <= now, "Token-exhaustion stop evidence does not match the preserved ledger")
    expected_refs = [{"path":amendment["bindings"][k]["path"], "sha256":digest(raw[k])} for k in ("budget","workflow","execution")]
    _require(summary.get("runtime_evidence") == expected_refs, "Token stop report is not bound to exact runtime evidence")
    last_id, last = summary.get("last_turn_id"), summary.get("last_turn")
    _require(isinstance(last_id,str) and isinstance(last,dict) and workflow.get("turns",{}).get(last_id) == last
             and last.get("status") == "interrupted" and last.get("reason_code") == "interrupted", "Interrupted trigger stop evidence changed")
    manifest = de._continuity(base, amendment)
    _require([last_id.split(':',1)[0], last.get("trigger_event_id")] in manifest["blocked"], "Interrupted trigger must remain blocked, never replayed")
    return ledger


def _authorization(amendment, ledger, now, proposed):
    reference = amendment["authorization"]
    if reference is None:
        _require(proposed, "Direct user approval for additional tokens is absent")
        return
    approval = de._referenced(reference); de._shape(approval, _AUTH_KEYS, "token authorization")
    expected = {"schema_version":1,"status":"APPROVED_NOT_APPLIED", "authorization_source":"Direct user answer in current Codex chat",
                "room_id":amendment["room_id"], "additional_tokens":ADDITIONAL_TOKENS,"old_max_total_tokens":OLD_LIMIT,"new_max_total_tokens":NEW_LIMIT,
                "deadline_utc":amendment["changes"]["new_deadline_utc"],"current_budget_sha256":amendment["bindings"]["budget"]["sha256"],
                "parent_amendment_sha256":amendment["parent_amendment"]["sha256"],"base_configuration_sha256":amendment["base_configuration_sha256"]}
    _require(all(type(approval[k]) is type(v) and approval[k] == v for k,v in expected.items())
             and isinstance(approval["user_answer"],str) and bool(approval["user_answer"].strip())
             and de._instant(approval["recorded_at_utc"]) <= now, "Explicit token approval does not match this exact stopped scope")


def _static(base, amendment, *, now=None, proposed=False):
    de._shape(amendment, _KEYS, "token amendment")
    instant = de._now(now)
    _require(amendment["schema_version"] == 1 and amendment["kind"] == "token_amendment" and de._uuid(amendment["room_id"])
             and amendment["base_configuration_sha256"] == digest(canonical(base))
             and amendment["validator_sha256"] == digest(de._bytes(Path(__file__).absolute()))
             and amendment["deadline_validator_sha256"] == digest(de._bytes(Path(de.__file__).absolute()))
             and de._instant(amendment["created_at_utc"]) <= instant, "Token amendment identity, validator or base configuration changed")
    _review(amendment, proposed)
    de._shape(amendment["bindings"], {"configuration","freeze","launch_ledger","source_lock","budget","workflow","execution","owner"}, "token bindings")
    raw = {k:de._binding_bytes(v,live=k not in {"budget","workflow","owner"}) for k,v in amendment["bindings"].items()}
    runs=Path(base["paths"]["runs"]);room=amendment["room_id"]
    expected_paths={"budget":runs/'runtime/budget-subscription.json',"workflow":runs/f'runtime/workflow-{room}.json',
                    "execution":runs/'runtime/execution-events.jsonl',"owner":runs/'runtime/owner.json'}
    _require(all(Path(amendment["bindings"][k]["path"])==p for k,p in expected_paths.items()), "Current token evidence paths changed")
    parent, original, effective = _parent(base, amendment, instant)
    expected_changes={"old_max_total_tokens":OLD_LIMIT,"new_max_total_tokens":NEW_LIMIT,"additional_tokens":ADDITIONAL_TOKENS,
                      "old_deadline_utc":parent["changes"]["new_deadline_utc"],"new_deadline_utc":parent["changes"]["new_deadline_utc"]}
    _require(amendment["changes"]==expected_changes,"Token amendment changed more than the requested ceiling")
    ledger=_current(base,amendment,original,effective,raw,instant)
    _authorization(amendment,ledger,instant,proposed)
    effective["budgets"]["max_total_tokens"]=NEW_LIMIT
    _require(not budget_errors(effective["budgets"]) and digest(canonical(effective))==amendment["effective_configuration_sha256"], "Effective token configuration differs from approved scope")
    return effective,ledger


def prepare_amendment(base_config, *, parent_amendment_path, preservation_manifest_path,
                      stopped_status_path, stop_summary_path, continuity_path,
                      authorization_path=None, source_deltas=None, inherited_defaults_reconciliation_path=None, now=None):
    """Return PROPOSED data, even without user approval. Never write or launch."""
    parent=de._read(parent_amendment_path); runs=Path(base_config["paths"]["runs"]);room=base_config["band"]["judged_room_id"]
    entries=de._read(preservation_manifest_path)["files"]
    bindings={k:copy.deepcopy(parent["bindings"][k]) for k in ("configuration","freeze","launch_ledger","source_lock")}
    for name,path in {"budget":runs/'runtime/budget-subscription.json',"workflow":runs/f'runtime/workflow-{room}.json',
                      "execution":runs/'runtime/execution-events.jsonl',"owner":runs/'runtime/owner.json'}.items():
        matches=[v for v in entries if v.get('source')==str(path)]
        _require(len(matches)==1,"Fresh preservation manifest is missing an exact unique input")
        item=matches[0];bindings[name]={"path":str(path),"preserved_path":item['preserved'],"sha256":item['sha256']}
    owner=de._json_bytes(de._binding_bytes(bindings['owner']))
    proposal={"schema_version":1,"kind":"token_amendment","status":"PROPOSED","created_at_utc":de._utc(de._now(now)),"room_id":room,
              "base_configuration_sha256":digest(canonical(base_config)),"effective_configuration_sha256":"",
              "validator_sha256":digest(de._bytes(Path(__file__).absolute())),"deadline_validator_sha256":digest(de._bytes(Path(de.__file__).absolute())),
              "bindings":bindings,"parent_amendment":de._reference(parent_amendment_path),
              "parent_claim":de._reference(owner['continuation']['claim_path']),"stop_summary":de._reference(stop_summary_path),
              "authorization":de._reference(authorization_path) if authorization_path else None,
              "stopped_status":de._reference(stopped_status_path),"owner_registry":de._reference(runs/'runtime/owner.json'),
              "trust_audit":copy.deepcopy(parent['trust_audit']),"source_deltas":copy.deepcopy(source_deltas or []),
              "inherited_defaults_reconciliation":de._reference(inherited_defaults_reconciliation_path) if inherited_defaults_reconciliation_path else None,
              "continuity":de._reference(continuity_path),"model_authorization":copy.deepcopy(parent['model_authorization']),
              "model_catalog":copy.deepcopy(parent['model_catalog']),"changes":{"old_max_total_tokens":OLD_LIMIT,"new_max_total_tokens":NEW_LIMIT,
              "additional_tokens":ADDITIONAL_TOKENS,"old_deadline_utc":parent['changes']['new_deadline_utc'],"new_deadline_utc":parent['changes']['new_deadline_utc']},"review":None}
    _,_,effective=_parent(base_config,proposal,de._now(now));effective['budgets']['max_total_tokens']=NEW_LIMIT
    proposal['effective_configuration_sha256']=digest(canonical(effective))
    _static(base_config,proposal,now=now,proposed=True);de._stopped(base_config,proposal)
    return proposal


def approve_amendment(base_config, proposal, *, review_path, now=None):
    effective,ledger=_static(base_config,proposal,now=now,proposed=True)
    _authorization(proposal,ledger,de._now(now),False);de._stopped(base_config,proposal)
    result=copy.deepcopy(proposal);result.update(status='APPROVED_NOT_APPLIED',review=de._reference(review_path))
    _static(base_config,result,now=now)
    return result


def validate_amendment(base_config, amendment_path, *, now=None):
    try:
        amendment=de._read(amendment_path);effective,_=_static(base_config,amendment,now=now)
        for key in ('budget','workflow','owner'):de._binding_bytes(amendment['bindings'][key])
        de._stopped(base_config,amendment)
        return effective
    except FactoryError:raise
    except (OSError,ValueError,TypeError,KeyError,AttributeError,OverflowError):
        raise FactoryError('Malformed or inconsistent token amendment') from None


def amendment_errors(base_config, amendment_path, *, now=None):
    try:validate_amendment(base_config,amendment_path,now=now);return []
    except FactoryError as error:return [str(error)]


def prepare_claim(base_config, amendment_path, *, owner_token, now=None):
    effective=validate_amendment(base_config,amendment_path,now=now)
    _require(isinstance(owner_token,str) and re.fullmatch(r'[0-9a-f]{48}',owner_token),'Invalid continuation owner token')
    amendment=de._read(amendment_path);old=de._json_bytes(de._binding_bytes(amendment['bindings']['budget']))
    cleared=copy.deepcopy(old);cleared['stopped_reason']=None
    claim={"schema_version":1,"kind":"token_amendment","status":"CLAIMED","room_id":amendment['room_id'],
           "claimed_at_utc":de._utc(de._now(now)),"amendment_sha256":digest(de._bytes(amendment_path)),
           "owner_token_sha256":digest(owner_token),"original_budget_sha256":amendment['bindings']['budget']['sha256'],
           "cleared_budget_sha256":digest(canonical(cleared)),"effective_configuration_sha256":digest(canonical(effective))}
    return {'claim':claim,'cleared_budget':cleared}


def validate_claimed_amendment(base_config, amendment_path, claim_path, *, owner_token, now=None):
    try:
        amendment=de._read(amendment_path);effective,original=_static(base_config,amendment,now=now)
        claim=de._read(claim_path);de._shape(claim,_CLAIM_KEYS,'token continuation claim')
        cleared=copy.deepcopy(original);cleared['stopped_reason']=None
        expected={'schema_version':1,'kind':'token_amendment','status':'CLAIMED','room_id':amendment['room_id'],
                  'amendment_sha256':digest(de._bytes(amendment_path)),'owner_token_sha256':digest(owner_token),
                  'original_budget_sha256':amendment['bindings']['budget']['sha256'],'cleared_budget_sha256':digest(canonical(cleared)),
                  'effective_configuration_sha256':digest(canonical(effective))}
        _require(all(claim[k]==v for k,v in expected.items()) and de._instant(amendment['created_at_utc'])<=de._instant(claim['claimed_at_utc'])<=de._now(now), 'Token continuation claim binding failed')
        _require(de._bytes(amendment['bindings']['budget']['path'])==canonical(cleared),'Token claim changed accounting beyond clearing the retained halt')
        de._binding_bytes(amendment['bindings']['workflow'])
        owner=de._read(amendment['owner_registry']['path'])
        _require(owner.get('mode')=='judged' and owner.get('status') in {'starting','running'} and owner.get('token')==owner_token
                 and owner.get('parent',{}).get('pid')==os.getpid() and de._process_alive(owner['parent']), 'Token claim is not owned by this child')
        return effective
    except FactoryError:raise
    except (OSError,ValueError,TypeError,KeyError,AttributeError,OverflowError):
        raise FactoryError('Malformed or inconsistent token continuation claim') from None
