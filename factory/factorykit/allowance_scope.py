"""Explicit balance-only continuation with immutable cumulative before-images.

No network, inference, new allowance origin, membership change or ledger write.
The existing $25 ceiling includes every retained charge and outstanding liability.
"""
from __future__ import annotations

import copy
import math
import time
from uuid import UUID

from .allowance_renewal import _read, _need, load_time_renewal, validate_renewal_config, validate_retained_factory_accounting

FIELDS = {"schema_version", "kind", "status", "authorization_source", "user_answer",
          "approved_epoch", "prior_time_renewal", "before_configuration",
          "before_factory_budget", "before_request_ledger", "active_room_ids", "cumulative_room_ids"}


def _rooms(values):
    try:
        return (isinstance(values, list) and len(set(values)) == len(values)
                and all(isinstance(value, str) and str(UUID(value)) == value for value in values))
    except (ValueError, TypeError):
        return False


def preserve_configuration_policy(config, before):
    """Permit room/artifact relocation, preserving credentials and permissions."""
    from pathlib import Path
    ignored = {'paths', 'artifacts', 'runtime', 'band', 'budgets', 'seats'}
    _need({key: value for key, value in config.items() if key not in ignored}
          == {key: value for key, value in before.items() if key not in ignored},
          "Continuation changed unrelated configuration policy")
    _need({key: value for key, value in config['paths'].items() if key not in ('rehearsal', 'result')}
          == {key: value for key, value in before['paths'].items() if key not in ('rehearsal', 'result')},
          "Continuation changed challenge, factory or accounting roots")
    if before['budgets'].get('balance_only') is True:
        _need(config['paths'].get('result') == before['paths'].get('result'),
              "Room continuation changed the existing judged result path")
    def runtime_policy(value):
        return {key: item for key, item in value.items()
                if key not in ('featherless_budget_guard', 'opencode_state_root', 'strict_membership_recovery')}
    _need(runtime_policy(config['runtime']) == runtime_policy(before['runtime'])
          and config['runtime'].get('strict_membership_recovery') is False,
          "Continuation changed permissions, harness, model routing or execution policy")
    _need({key: value for key, value in config['band'].items() if key not in ('rehearsal_room_id', 'archived_room_ids')}
          == {key: value for key, value in before['band'].items() if key not in ('rehearsal_room_id', 'archived_room_ids')},
          "Continuation changed BAND endpoints, credentials or judged room")
    _need(len(config['seats']) == len(before['seats']) == 7, "Continuation changed configured seat count")
    for seat, old in zip(config['seats'], before['seats']):
        _need({key: value for key, value in seat.items() if key != 'mandate'}
              == {key: value for key, value in old.items() if key != 'mandate'},
              "Continuation changed frozen role identity or model")
        if 'mandate' in old:
            _need(isinstance(seat.get('mandate'), str) and Path(seat['mandate']).is_absolute()
                  and Path(seat['mandate']).name == Path(old['mandate']).name,
                  "Continuation changed mandate basename or role binding")


def load_balance_amendment(authority, *, now=None):
    """Retain origins, monetary policy and current consumption; widen room scope."""
    _need(set(authority) == FIELDS and authority["schema_version"] == 1
          and authority["kind"] == "BALANCE_ONLY_AMENDMENT" and authority["status"] == "APPROVED"
          and authority["authorization_source"] == "direct_user_chat"
          and authority["user_answer"] == "Keep only the $25 cap", "Invalid explicit balance-only amendment")
    instant = time.time() if now is None else now
    approved = authority["approved_epoch"]
    _need(type(approved) in (int, float) and math.isfinite(approved) and approved > 0
          and type(instant) in (int, float) and math.isfinite(instant) and approved <= instant,
          "Invalid balance-only approval epoch")
    prior = load_time_renewal(authority["prior_time_renewal"], now=instant)
    already_balance = prior.get("balance_only") is True
    if already_balance:
        parent = _read(authority["prior_time_renewal"])
        _need(parent.get("kind") == "BALANCE_ONLY_AMENDMENT"
              and parent.get("authorization_source") == authority["authorization_source"]
              and parent.get("user_answer") == authority["user_answer"]
              and parent.get("approved_epoch") == approved,
              "Room continuation must reuse the original balance-only approval")
    config = _read(authority["before_configuration"])
    options = config["runtime"]["featherless_budget_guard"]
    _need(options.get("time_renewal") == authority["prior_time_renewal"]
          and (config["budgets"].get("balance_only") is True) == already_balance
          and (options.get("balance_only") is True) == already_balance,
          "Previous configuration authority changed")
    validate_renewal_config(config, now=instant)
    factory, guard = (_read(authority[key]) for key in ("before_factory_budget", "before_request_ledger"))
    validate_retained_factory_accounting(config, factory, now=instant)
    from .budgets import room_scope
    active, old_rooms = room_scope(config)
    _need(factory.get("room_id") is None and factory.get("room_ids") == old_rooms
          and factory.get("stopped_reason") is None and guard.get("stopped_reason") is None
          and isinstance(guard.get("requests"), dict)
          and all(value.get("status") == "settled" for value in guard["requests"].values()),
          "Balance-only continuation requires resolved cumulative accounting")
    # Reuse the prior production guard policy to validate all carried amounts.
    # Pinned metadata is read locally; no credential or provider call occurs.
    from .harnesses import _featherless_metadata
    from .featherless_guard import _Ledger, _policy
    models = _featherless_metadata(config)
    policy = _policy(models, options["approved_credit_nano_usd"], options["max_total_tokens"],
                     options["overall_timeout_seconds"], time_renewal=options["time_renewal"],
                     balance_only=already_balance)
    checked = _Ledger(options["ledger"], policy)
    checked.data = guard
    try:
        checked._validate()
    except ValueError:
        _need(False, "Carried request accounting changed or is invalid")
    _need(checked.totals()["held_nano_usd"] == 0
          and checked.totals()["charged_nano_usd"] < 25_000_000_000
          and options["approved_credit_nano_usd"] == 25_000_000_000
          and config["budgets"]["spend_cap_usd"] == 25,
          "Balance-only continuation cannot reset or increase the existing $25 ceiling")
    fresh, cumulative = authority["active_room_ids"], authority["cumulative_room_ids"]
    _need(_rooms(fresh) and len(fresh) == 2 and _rooms(cumulative)
          and fresh[1] == active[1] and fresh[0] not in old_rooms
          and cumulative == sorted(old_rooms + [fresh[0]]),
          "Balance-only continuation must archive the previous rehearsal and retain judged room")
    # Existing epoch/deadline fields remain historical evidence. balance_only
    # explicitly removes their enforcement; it does not create a new deadline.
    effective = copy.deepcopy(prior)
    effective.update(balance_only=True, active_room_ids=fresh, cumulative_room_ids=cumulative,
                     original_factory_budget=authority["before_factory_budget"],
                     original_request_ledger=authority["before_request_ledger"],
                     balance_configuration=authority["before_configuration"])
    return effective


def validate_balance_config(config, effective):
    before = _read(effective["balance_configuration"])
    from .budgets import room_scope
    active, rooms = room_scope(config)
    _need(active == effective["active_room_ids"] and rooms == effective["cumulative_room_ids"]
          and config["paths"]["runs"] == before["paths"]["runs"], "Balance-only continuation changed rooms or accounting path")
    preserve_configuration_policy(config, before)
    expected_budgets = copy.deepcopy(before["budgets"])
    expected_budgets["balance_only"] = True
    _need(config["budgets"] == expected_budgets
          and config["budgets"].get("max_active_seats") == 1,
          "Balance-only continuation must retain $25 and historical allowance fields")
    options = config["runtime"]["featherless_budget_guard"]
    expected_guard = copy.deepcopy(before["runtime"]["featherless_budget_guard"])
    expected_guard.update(balance_only=True, time_renewal=options["time_renewal"])
    _need(options == expected_guard and config["runtime"].get("harness") == before["runtime"].get("harness")
          and config["runtime"].get("model") == before["runtime"].get("model")
          and config["runtime"].get("opencode_provider") == before["runtime"].get("opencode_provider"),
          "Balance-only continuation changed monetary limits or fixed model routing")
    return effective


def reconcile_balance_scope(config, factory, guard, models, *, now=None):
    """Return only room-scope and explicit policy binding changes, without writes."""
    effective = validate_renewal_config(config, now=now)
    _need(effective is not None and effective.get("balance_only") is True, "Missing explicit balance-only approval")
    _need(factory == _read(effective["original_factory_budget"])
          and guard == _read(effective["original_request_ledger"]), "Accounting changed after review")
    from .featherless_guard import _Ledger, _policy
    new_factory, new_guard = copy.deepcopy(factory), copy.deepcopy(guard)
    new_factory["room_ids"] = effective["cumulative_room_ids"]
    options = config["runtime"]["featherless_budget_guard"]
    new_guard["policy"] = _policy(models, options["approved_credit_nano_usd"], options["max_total_tokens"],
                                  options["overall_timeout_seconds"], time_renewal=options["time_renewal"], balance_only=True)
    checked = _Ledger(options["ledger"], new_guard["policy"])
    checked.data = new_guard
    checked._validate()
    _need({key: value for key, value in new_factory.items() if key != "room_ids"}
          == {key: value for key, value in factory.items() if key != "room_ids"}
          and {key: value for key, value in new_guard.items() if key != "policy"}
          == {key: value for key, value in guard.items() if key != "policy"}, "Cumulative consumption changed")
    return new_factory, new_guard
