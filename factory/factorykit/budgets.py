"""Finite consumption policy; importing this module never checks credentials."""
from __future__ import annotations

import json
import math
from pathlib import Path
import time
from uuid import UUID


FINITE_LIMITS = (
    "max_active_seats", "max_repairs", "turn_timeout_seconds",
    "stage_timeout_seconds", "overall_timeout_seconds",
    "max_turns_per_seat", "max_total_tokens",
)


def balance_only(limits: dict) -> bool:
    """Only the explicitly approved cumulative dollar ceiling limits work."""
    return limits.get("balance_only") is True


def subscription_only(limits: dict) -> bool:
    return limits.get("billing_mode", "spend_cap") == "subscription_only"


def session_accounting(limits: dict) -> bool:
    """Accounting scope is independent of how the provider bills the work."""
    return subscription_only(limits) or limits.get("accounting_scope") == "session"


def accounting_scope_errors(limits: dict) -> list[str]:
    scope = limits.get("accounting_scope", "session" if subscription_only(limits) else "mode")
    if not isinstance(scope, str) or scope not in ("mode", "session"):
        return ["budgets.accounting_scope must be mode or session"]
    if subscription_only(limits) and scope != "session":
        return ["subscription_only requires session accounting; existing cumulative consumption cannot become per-mode"]
    return []


def budget_ledger_path(config: dict, mode: str | None = None) -> Path:
    """Keep historical filenames stable; spend-cap sessions use their own ledger."""
    limits = config["budgets"]
    if errors := accounting_scope_errors(limits):
        raise ValueError("; ".join(errors))
    if subscription_only(limits):
        name = "budget-subscription.json"
    elif session_accounting(limits):
        name = "budget-session.json"
    elif mode in ("rehearsal", "judged"):
        name = f"budget-{mode}.json"
    else:
        raise ValueError("Per-mode accounting requires rehearsal or judged mode")
    return Path(config["paths"]["runs"]) / "runtime" / name


def accounting_ledger_conflicts(config: dict) -> list[str]:
    """Never silently renew consumption by selecting another ledger filename."""
    if errors := accounting_scope_errors(config["budgets"]):
        return errors
    directory = Path(config["paths"]["runs"]) / "runtime"
    names = {"budget-subscription.json", "budget-session.json"}
    if session_accounting(config["budgets"]):
        names |= {"budget-rehearsal.json", "budget-judged.json"}
        names.remove(budget_ledger_path(config).name)
    if any((directory / name).exists() or (directory / name).is_symlink() for name in names):
        return ["Existing ledgers use a different accounting or billing scope; reconcile retained consumption before selecting another ledger"]
    return []


def budget_errors(limits: dict) -> list[str]:
    errors = accounting_scope_errors(limits)
    if "balance_only" in limits and type(limits["balance_only"]) is not bool:
        errors.append("budgets.balance_only must be an explicit boolean")
    if balance_only(limits) and (limits.get("billing_mode") != "spend_cap"
            or limits.get("accounting_scope") != "session" or limits.get("spend_cap_usd") != 25):
        errors.append("balance_only requires the existing $25 cumulative spend cap")
    for name in FINITE_LIMITS + (("ack_timeout_seconds",) if "ack_timeout_seconds" in limits else ()):
        value = limits.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            errors.append(f"budgets.{name} requires a finite positive integer")
    if not isinstance(limits.get("approved"), bool):
        errors.append("budgets.approved must be an explicit boolean")
    mode = limits.get("billing_mode", "spend_cap")
    if mode not in ("spend_cap", "subscription_only"):
        errors.append("budgets.billing_mode must be spend_cap or subscription_only")
    cap = limits.get("spend_cap_usd")
    if subscription_only(limits):
        if cap is not None:
            errors.append("subscription_only requires spend_cap_usd: null; it is not a measured zero-dollar cost")
        for name in ("api_billing_allowed", "paid_provisioning_allowed"):
            if limits.get(name) is not False:
                errors.append(f"subscription_only requires budgets.{name}: false")
    elif cap is not None and (isinstance(cap, bool) or not isinstance(cap, (int, float)) or not 0 < cap < float("inf")):
        errors.append("spend_cap_usd must be a finite positive number or null")
    if errors:
        return errors
    if limits["max_active_seats"] > 7:
        errors.append("max_active_seats cannot exceed seven")
    if not balance_only(limits) and (limits["turn_timeout_seconds"] > limits["stage_timeout_seconds"] or limits["stage_timeout_seconds"] > limits["overall_timeout_seconds"]):
        errors.append("Timeouts must satisfy turn <= stage <= overall")
    return errors


def budget_blockers(limits: dict) -> list[str]:
    errors = budget_errors(limits)
    if limits.get("approved") is not True:
        errors.append("Approve finite active-work and consumption budgets before live seat work (budgets.approved=false)")
    if not subscription_only(limits) and limits.get("spend_cap_usd") is None:
        errors.append("Set an approved positive spend_cap_usd with provider billing enforcement, or explicitly approve the subscription_only policy")
    return errors


def room_scope(config: dict) -> tuple[list[str], list[str]]:
    """Return current messaging rooms and cumulative accounting rooms.

    Archived UUIDs retain consumption only; this helper never changes a ledger.
    Adding a room requires explicit reconciliation of its persisted exact scope.
    """
    band = config["band"]
    active = [band.get(f"{mode}_room_id") for mode in ("rehearsal", "judged")]
    archived = band.get("archived_room_ids", [])
    if (any(not isinstance(room, str) or not room.strip() or room != room.strip() for room in active)
            or len(set(active)) != 2):
        raise ValueError("Cumulative accounting requires two distinct configured active rooms.")
    if not isinstance(archived, list):
        raise ValueError("band.archived_room_ids must be a list of exact canonical room UUIDs.")
    try:
        valid = all(isinstance(room, str) and str(UUID(room)) == room for room in archived)
    except ValueError:
        valid = False
    if not valid or len(set(archived)) != len(archived) or set(active).intersection(archived):
        raise ValueError("band.archived_room_ids requires unique canonical UUIDs disjoint from active rooms.")
    return active, sorted(active + archived)


def persisted_guard_blockers(config: dict, *, now: float | None = None,
                             require_existing: bool = False, allow_in_flight: bool = False) -> list[str]:
    """Read the pinned request ledger without creating, locking or changing it.

    An active supervisor may inspect its own in-flight reservations. Preparation,
    freeze and launch must treat unfinished requests as unresolved liabilities.
    No provider requests, credential access or guard startup occur here.
    """
    guard = config.get("runtime", {}).get("featherless_budget_guard")
    if guard is None:
        return []
    invalid = ["Featherless request accounting is missing, malformed or differs from its pinned policy."]
    try:
        import os
        import stat
        from .harnesses import _featherless_metadata
        from .featherless_guard import _Ledger, _policy
        models = _featherless_metadata(config)
        path = Path(guard["ledger"])
        root = Path(config["paths"]["runs"]).resolve()
        if any(item.is_symlink() for item in (path, *path.parents)
               if item.resolve() == root or root in item.resolve().parents):
            return invalid
        if not path.exists():
            return invalid if require_existing or guard.get("time_renewal") is not None else []
        info = path.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16 * 1024 * 1024):
            return invalid
        policy = _policy(models, guard["approved_credit_nano_usd"], guard["max_total_tokens"],
                         guard["overall_timeout_seconds"], time_renewal=guard.get("time_renewal"),
                         balance_only=guard.get("balance_only", False))
        snapshot = _Ledger(path, policy)
        snapshot.data = json.loads(path.read_text())
        snapshot._validate()
        data, totals = snapshot.data, snapshot.totals()
        instant = time.time() if now is None else now
        if type(instant) not in (int, float) or not math.isfinite(instant):
            return invalid
        blockers = []
        if data["stopped_reason"]:
            blockers.append("Featherless request guard is persistently stopped; reconciliation is required.")
        unfinished = [item["status"] for item in data["requests"].values() if item["status"] != "settled"]
        if "unknown" in unfinished or (unfinished and not allow_in_flight):
            blockers.append("Featherless request guard has unfinished or unknown provider usage.")
        if not policy.get("balance_only") and data["started_epoch"] is not None and instant - data["started_epoch"] >= policy["overall_timeout_seconds"]:
            blockers.append("Featherless request guard overall time budget exhausted.")
        # In-flight holds can exactly fill a cap and later settle lower. They
        # remain protected by atomic admission, without cancelling valid work.
        if not policy.get("balance_only") and totals["observed_tokens"] >= policy["max_total_tokens"]:
            blockers.append("Featherless request guard observed token budget exhausted.")
        if totals["charged_nano_usd"] >= policy["approved_credit_nano_usd"]:
            blockers.append("Featherless request guard conservative money budget exhausted.")
        return blockers
    except Exception:
        return invalid


def persisted_budget_blockers(config: dict, *, now: float | None = None, require_existing: bool = False) -> list[str]:
    """Read both overlapping ledgers; neither can authorize resetting the other."""
    return [*_persisted_factory_budget_blockers(config, now=now, require_existing=require_existing),
            *persisted_guard_blockers(config, now=now, require_existing=require_existing)]


def _persisted_factory_budget_blockers(config: dict, *, now: float | None = None, require_existing: bool = False) -> list[str]:
    """Inspect aggregate consumption without constructing/writing a ledger.

    An absent ledger is normal before first rehearsal. Room stage halts remain
    live-start concerns: a completed rehearsal must not block a fresh judged room.
    """
    limits = config["budgets"]
    if conflicts := accounting_ledger_conflicts(config):
        return conflicts
    if not session_accounting(limits):
        return []
    invalid = ["Existing cumulative budget ledger is malformed or has changed scope; preserve it before launch."]
    try:
        active, rooms = room_scope(config)
    except (ValueError, TypeError, KeyError):
        return invalid
    path = budget_ledger_path(config)
    if not path.exists() and not path.is_symlink():
        return ["Judged readiness requires the existing cumulative budget ledger; preserve rehearsal accounting before launch."] if require_existing or len(rooms) > len(active) else []
    try:
        if path.is_symlink():
            return invalid
        data = json.loads(path.read_text())
        instant = time.time() if now is None else now
        seats = {seat["id"] for seat in config["seats"]}
        def counts(value, allowed=None):
            return (isinstance(value, dict) and (allowed is None or set(value).issubset(allowed))
                    and all(isinstance(k, str) and type(v) is int and v >= 0 for k, v in value.items()))
        def epoch(value):
            return type(value) in (int, float) and math.isfinite(value) and 0 < value <= instant
        if (not isinstance(data, dict) or not {"room_id", "room_ids", "started_epoch", "tokens", "turns", "token_threads", "stopped_reason"}.issubset(data)
                or type(instant) not in (int, float) or not math.isfinite(instant)
                or data["room_id"] is not None or data["room_ids"] != rooms
                or type(data["tokens"]) is not int or data["tokens"] < 0
                or not counts(data["turns"], seats) or not counts(data["token_threads"])
                or (data["stopped_reason"] is not None and (not isinstance(data["stopped_reason"], str) or not data["stopped_reason"]))):
            return invalid
        origins = data.get("room_started_epochs", {})
        room_turns = data.get("room_turns", {})
        room_stops = data.get("room_stopped_reasons", {})
        if (any(not isinstance(value, dict) or not set(value).issubset(rooms) for value in (origins, room_turns, room_stops))
                or any(not epoch(value) for value in origins.values())
                or any(not counts(value, seats) for value in room_turns.values())
                or any(not isinstance(value, str) or not value for value in room_stops.values())):
            return invalid
        start = data["started_epoch"]
        renewal = config.get("runtime", {}).get("featherless_budget_guard", {}).get("time_renewal")
        if renewal is not None:
            from .allowance_renewal import validate_retained_factory_accounting
            validate_retained_factory_accounting(config, data, now=instant)
        if start is None:
            if data["tokens"] or any(data["turns"].values()) or data["token_threads"] or origins or room_turns or room_stops:
                return invalid
        elif not epoch(start) or any(value < start for value in origins.values()):
            return invalid
        blockers = []
        if data["stopped_reason"]:
            blockers.append("Persisted cumulative budget has a global stopped_reason; explicit reconciliation is required.")
        if not balance_only(limits) and start is not None and instant - start >= limits["overall_timeout_seconds"]:
            blockers.append("Cumulative overall time budget exhausted.")
        if not balance_only(limits) and data["tokens"] >= limits["max_total_tokens"]:
            blockers.append("Cumulative observed token budget exhausted.")
        if not balance_only(limits):
            blockers.extend(f"Cumulative turn budget exhausted for {seat}." for seat, value in sorted(data["turns"].items()) if value >= limits["max_turns_per_seat"])
        return blockers
    except (OSError, ValueError, TypeError, KeyError):
        return invalid


# This is an authentication restriction, not a promise of zero charge or a
# replacement for provider-side plan and billing controls.
SUBSCRIPTION_CONFIG = (
    '-c', 'forced_login_method="chatgpt"',
    '-c', 'model_provider="openai"',
    '-c', 'openai_base_url=""',
)
API_ENVIRONMENT = ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "CODEX_ACCESS_TOKEN", "OPENAI_FEDERATION_RULE_ID", "OPENAI_IDENTITY_TOKEN_FILE", "OPENAI_WORKLOAD_IDENTITY_CONTEXT")


def codex_argv(config: dict, *arguments: str) -> list[str]:
    extra = SUBSCRIPTION_CONFIG if subscription_only(config["budgets"]) else ()
    # BAND merges codex_env into inherited env; empty auth values can still
    # select an auth path. env -u truly removes these variables without a shell.
    prefix = ["/usr/bin/env", *(part for name in API_ENVIRONMENT for part in ("-u", name))] if extra else []
    return [*prefix, config["runtime"]["codex_command"], *extra, *arguments]


def subscription_auth_errors(config: dict) -> list[str]:
    """Read local login status only; never create a thread/turn or echo keys."""
    from .harnesses import selected_harness, auth_errors
    if selected_harness(config) != "codex":
        return auth_errors(config)
    if not subscription_only(config["budgets"]):
        return []
    import os
    from .common import run_command
    if any(os.environ.get(name) for name in API_ENVIRONMENT):
        return ["subscription_only rejects API credential, external-token, workload-identity or endpoint environment overrides; remove them without exposing their values"]
    # Inspect existing authentication first, with no forced-login setting that
    # could invalidate an incompatible saved login at CLI startup.
    result = run_command([config["runtime"]["codex_command"], "login", "status"], config["paths"]["factory"], timeout=15)
    lines = (result.get("stdout", "") + "\n" + result.get("stderr", "")).splitlines()
    if result.get("exit_code") != 0 or "Logged in using ChatGPT" not in lines:
        return ["subscription_only requires verified ChatGPT login from the pinned CLI; API-key, unknown or unavailable authentication is blocked"]
    # preflight can run inside serve's event loop. A separate thread owns this
    # short-lived read-only SDK connection; it never starts a thread or turn.
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            verified = pool.submit(lambda: asyncio.run(subscription_auth_probe(config))).result()
    except Exception:
        return ["subscription_only could not verify effective provider/authentication without inference; no raw provider response printed"]
    if not verified:
        return ["subscription_only requires effective OpenAI provider and existing ChatGPT account; managed/provider overrides or unknown authentication are blocked"]
    return []


async def subscription_auth_probe(config: dict) -> bool:
    """Read supported account/config RPCs; discard personal and secret values."""
    import asyncio
    from band.integrations.codex.stdio_client import CodexStdioClient
    client = CodexStdioClient(command=codex_argv(config, "app-server", "--listen", "stdio://"), cwd=config["paths"]["factory"], env={name: "" for name in API_ENVIRONMENT})
    try:
        async with asyncio.timeout(20):
            await client.connect()
            await client.initialize(client_name="factory_subscription_probe", client_title="Factory subscription policy probe", client_version="1.0")
            account = await client.request("account/read", {"refreshToken": False})
            if account.get("requiresOpenaiAuth") is not True or (account.get("account") or {}).get("type") != "chatgpt":
                return False
            paths = {config["paths"][name] for name in ("factory", "rehearsal", "result")}
            paths.update(seat[key] for seat in config.get("seats", []) for key in ("cwd", "rehearsal_cwd", "judged_cwd") if seat.get(key))
            for path in sorted(paths):
                result = await client.request("config/read", {"cwd": path, "includeLayers": False})
                effective = result.get("config", {})
                if effective.get("model_provider") != "openai" or effective.get("forced_login_method") != "chatgpt":
                    return False
            return True
    finally:
        await client.close()
