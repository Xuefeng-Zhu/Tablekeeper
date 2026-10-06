"""Single-owner request reservations for the approved Featherless attempt.

The only public HTTP route is authenticated POST /v1/chat/completions. Every
request, including harness auxiliary calls, must pass this guard. Prices and
provider token ceilings are pinned from authenticated metadata. Prompt charges
use the full uncached price, even if the provider reports cached input.

Unknown or interrupted requests retain their entire reservation and stop the
ledger. No prompts, responses, provider IDs, headers, or credentials are saved.
This enforces the pinned rate bounds; it cannot control unrelated account use
or an upstream price change outside those pinned bounds.
"""

import asyncio
from copy import deepcopy
from collections.abc import Mapping
from decimal import Decimal, ROUND_CEILING, localcontext
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time
import uuid

import httpx

from .featherless import API_ORIGIN, APPROVED_CREDIT_NANO_USD, MODEL_IDS, _price


MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 1024 * 1024
_REASONS = frozenset({
    "money reservation would exceed the approved cap",
    "token reservation would exceed the approved cap",
    "overall time budget exhausted", "provider request usage is unknown",
    "unfinished provider request found on restart", "provider usage exceeded its reservation",
    "provider request failed or was interrupted", "ledger persistence failed",
})
_MONEY_HALT = "money reservation would exceed the approved cap"
_FACTORY_MONEY_HALTS = frozenset({
    "Request budget guard blocked: Featherless request guard is persistently stopped; reconciliation is required.",
    "Request budget guard blocked: Featherless request guard is persistently stopped; reconciliation is required.; Featherless request guard conservative money budget exhausted.",
})


class GuardError(ValueError):
    """Fixed, safe error text without request data."""


def _int(value, label, minimum=1):
    if type(value) is not int or value < minimum:
        raise GuardError(f"invalid {label}")
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _ceil_charge(price, tokens=1):
    with localcontext() as context:
        context.prec = 128
        return int((Decimal(price) * tokens * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING))


def _charge(model, prompt, completion):
    prices = model["pricing"]
    # Round each billed dimension up separately, never down or through floats.
    return (_ceil_charge(prices["prompt"], prompt) +
            _ceil_charge(prices["completion"], completion) + _ceil_charge(prices["request"]))


def _policy(models, approved_credit_nano_usd, max_total_tokens, overall_timeout_seconds, *, time_renewal=None, balance_only=False):
    if not isinstance(models, Mapping) or set(models) != set(MODEL_IDS):
        raise GuardError("exactly the two approved model identities are required")
    cap = _int(approved_credit_nano_usd, "credit cap")
    tokens = _int(max_total_tokens, "token cap")
    duration = _int(overall_timeout_seconds, "time cap")
    renewal = None
    if type(balance_only) is not bool:
        raise GuardError("invalid balance-only mode")
    if time_renewal is not None:
        from .allowance_renewal import load_time_renewal, renewal_durations
        try:
            renewal = load_time_renewal(time_renewal)
            if duration != renewal_durations(renewal)["guard"]:
                raise GuardError("renewed time cap differs from its approved deadline")
        except ValueError:
            raise GuardError("invalid explicit time renewal") from None
    if balance_only != (renewal is not None and renewal.get("balance_only") is True):
        raise GuardError("balance-only mode requires its explicit cumulative approval")
    if cap > APPROVED_CREDIT_NANO_USD or tokens > 2_000_000 or (duration > 21600 and renewal is None):
        raise GuardError("guard limits exceed the approved allowance")
    pinned = {}
    for model_id in MODEL_IDS:
        source = models[model_id]
        if not isinstance(source, Mapping) or source.get("id") != model_id or \
                source.get("status") != "active" or source.get("tool_use") is not True or \
                source.get("available_on_current_plan") is not True or source.get("is_gated") is not False:
            raise GuardError("authenticated active model evidence is required")
        context = _int(source.get("effective_context_length"), "model context")
        output = _int(source.get("effective_max_completion_tokens"), "model output ceiling")
        if context < 2 or output >= context:
            raise GuardError("model output ceiling must leave room for a prompt")
        prices = source.get("pricing")
        if not isinstance(prices, Mapping):
            raise GuardError("model prices are required")
        try:
            normalized = {key: _price(prices.get(key), "model price")
                          for key in ("prompt", "completion", "image", "request")}
            if any(Decimal(_price(value, "model price")) != 0
                   for key, value in prices.items() if key not in normalized):
                raise GuardError("unsupported nonzero billing dimension")
        except ValueError:
            raise GuardError("invalid or unsupported model pricing") from None
        pinned[model_id] = {"input_ceiling": context, "output_ceiling": output, "pricing": normalized}
    result = {"approved_credit_nano_usd": cap, "max_total_tokens": tokens,
              "overall_timeout_seconds": duration, "models": pinned}
    if balance_only:
        result["balance_only"] = True
    if renewal is not None:
        from .allowance_renewal import retained_request_bindings
        result["time_renewal"] = {"sha256": time_renewal["sha256"],
                                  "original_started_epoch": renewal["original_guard_started_epoch"],
                                  "renewal_started_epoch": renewal["renewal_started_epoch"],
                                  "renewal_deadline_epoch": renewal["renewal_deadline_epoch"],
                                  "retained_requests_sha256": retained_request_bindings(renewal)}
    return result


class _Ledger:
    def __init__(self, path, policy):
        self.path, self.policy, self._lock_fd = Path(path), policy, None
        self.data = None

    def open(self, *, checkpoint_only=False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise GuardError("guard ledger must not use symbolic links")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        self._lock_fd = os.open(self.path.with_suffix(self.path.suffix + ".lock"), flags, 0o600)
        try:
            info = os.fstat(self._lock_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise GuardError("unsafe guard lock file")
            os.fchmod(self._lock_fd, 0o600)
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.exists():
                info = self.path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or \
                        stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16 * 1024 * 1024:
                    raise GuardError("unsafe guard ledger file")
                self.data = json.loads(self.path.read_text())
                self._validate()
                if any(item["status"] != "settled" for item in self.data["requests"].values()):
                    if checkpoint_only:
                        raise GuardError("billing checkpoint requires settled requests")
                    self.halt("unfinished provider request found on restart")
            else:
                if checkpoint_only:
                    raise GuardError("billing checkpoint requires its existing ledger")
                if self.policy.get("time_renewal") is not None:
                    raise GuardError("renewed guard requires its original request ledger")
                self.data = {"schema_version": 1, "policy": self.policy, "started_epoch": None,
                             "stopped_reason": None, "requests": {}}
                self._save()
        except BaseException:
            self.close()
            raise GuardError("guard ledger is unavailable, inconsistent, or already owned") from None

    def close(self):
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _validate(self):
        data = self.data
        if not isinstance(data, dict) or not {"schema_version", "policy", "started_epoch", "stopped_reason", "requests"}.issubset(data) or \
                data.get("schema_version") != 1 or data.get("policy") != self.policy or \
                data.get("stopped_reason") not in _REASONS | {None} or not isinstance(data.get("requests"), dict):
            raise GuardError("invalid guard ledger")
        started = data.get("started_epoch")
        renewal = self.policy.get("time_renewal")
        if renewal is not None and started != renewal["original_started_epoch"]:
            raise GuardError("renewed guard accounting origin changed")
        if renewal is not None and any(identity not in data["requests"] or
                hashlib.sha256(_canonical(data["requests"][identity]).encode()).hexdigest() != expected
                for identity, expected in renewal["retained_requests_sha256"].items()):
            raise GuardError("renewed guard lost or changed a retained request")
        if started is not None and (type(started) not in (int, float) or not 0 < started <= time.time() + 1):
            raise GuardError("invalid guard start time")
        if data["requests"] and started is None:
            raise GuardError("missing guard start time")
        for key, record in data["requests"].items():
            if not isinstance(key, str) or len(key) != 32 or not isinstance(record, dict):
                raise GuardError("invalid guard request record")
            model = self.policy["models"].get(record.get("model"))
            if model is None or record.get("status") not in ("in_flight", "settled", "unknown"):
                raise GuardError("invalid guard request state")
            output = _int(record.get("output_ceiling"), "request output ceiling")
            if output > model["output_ceiling"] or \
                    record.get("reserved_tokens") != model["input_ceiling"] + output or \
                    record.get("reserved_nano_usd") != _charge(model, model["input_ceiling"], output):
                raise GuardError("invalid guard reservation")
            if record["status"] == "settled":
                prompt = _int(record.get("prompt_tokens"), "prompt usage", 0)
                completion = _int(record.get("completion_tokens"), "completion usage", 0)
                if prompt > model["input_ceiling"] or completion > output or \
                        record.get("charged_nano_usd") != _charge(model, prompt, completion):
                    raise GuardError("invalid settled guard usage")
        totals = self.totals()
        if (not self.policy.get("balance_only") and totals["observed_tokens"] + totals["held_tokens"] > self.policy["max_total_tokens"]) or \
                totals["charged_nano_usd"] + totals["held_nano_usd"] > self.policy["approved_credit_nano_usd"]:
            raise GuardError("guard ledger exceeds its approved policy")

    def _save(self):
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, prefix=".guard-", delete=False) as stream:
                name = stream.name
                os.chmod(name, 0o600)
                stream.write(_canonical(self.data))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            folder = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(folder)
            finally:
                os.close(folder)
        except Exception:
            self.data["stopped_reason"] = "ledger persistence failed"
            raise GuardError("guard ledger persistence failed") from None
        finally:
            if name is not None and os.path.exists(name):
                os.unlink(name)

    def totals(self):
        totals = {"observed_tokens": 0, "held_tokens": 0, "charged_nano_usd": 0, "held_nano_usd": 0}
        for record in self.data["requests"].values():
            if record["status"] == "settled":
                totals["observed_tokens"] += record["prompt_tokens"] + record["completion_tokens"]
                totals["charged_nano_usd"] += record["charged_nano_usd"]
            else:
                totals["held_tokens"] += record["reserved_tokens"]
                totals["held_nano_usd"] += record["reserved_nano_usd"]
        totals["total_conservative_charged_nano_usd"] = totals["charged_nano_usd"]
        checkpoint = self.latest_billing_checkpoint()
        if checkpoint is not None:
            totals["charged_nano_usd"] += (checkpoint["actual_charged_nano_usd"] -
                                            checkpoint["conservative_charged_nano_usd"])
        return totals

    def latest_billing_checkpoint(self):
        """Validate the retained cumulative chain; never sum overlapping costs."""
        checkpoint = self.data.get("billing_checkpoint")
        history = self.data.get("billing_checkpoint_history", [])
        if ("billing_checkpoint_history" in self.data and
                (not isinstance(history, list) or not history or checkpoint is None)):
            raise GuardError("invalid billing checkpoint history")
        if checkpoint is None:
            if "billing_money_halt_reconciliations" in self.data:
                raise GuardError("monetary halt reconciliation requires retained billing")
            return None
        if not isinstance(checkpoint, dict):
            raise GuardError("invalid billing checkpoint")
        expected = self.billing_checkpoint(checkpoint.get("provider_report"), checkpoint.get("covered_request_ids"))
        if checkpoint != expected:
            raise GuardError("billing checkpoint differs from its immutable evidence")
        for item in history:
            if not isinstance(item, dict):
                raise GuardError("invalid billing checkpoint history entry")
            expected = self.billing_checkpoint(item.get("provider_report"), item.get("covered_request_ids"))
            expected["parent_checkpoint_sha256"] = hashlib.sha256(_canonical(checkpoint).encode()).hexdigest()
            if item != expected:
                raise GuardError("billing checkpoint history differs from its immutable evidence or parent")
            self._billing_checkpoint_extension(checkpoint, item)
            checkpoint = item
        self._validate_money_halt_reconciliations([self.data["billing_checkpoint"], *history])
        return checkpoint

    def _validate_money_halt_reconciliations(self, checkpoints):
        history = self.data.get("billing_money_halt_reconciliations", [])
        if "billing_money_halt_reconciliations" in self.data and (not isinstance(history, list) or not history):
            raise GuardError("invalid monetary halt reconciliation history")
        by_hash = {hashlib.sha256(_canonical(item).encode()).hexdigest(): item for item in checkpoints}
        from .allowance_renewal import _read
        for index, row in enumerate(history):
            if (not isinstance(row, dict) or set(row) != {"original_stopped_reason", "before_guard_ledger",
                    "checkpoint_sha256", "provider_report", "reconciled_at", "parent_reconciliation_sha256"}
                    or row["original_stopped_reason"] != _MONEY_HALT or
                    not isinstance(row["checkpoint_sha256"], str) or
                    type(row["reconciled_at"]) not in (int, float) or not 0 < row["reconciled_at"] <= time.time() + 1 or
                    row["parent_reconciliation_sha256"] != (hashlib.sha256(_canonical(history[index - 1]).encode()).hexdigest() if index else None)):
                raise GuardError("invalid monetary halt reconciliation")
            checkpoint = by_hash.get(row["checkpoint_sha256"])
            if (checkpoint is None or row["provider_report"] != checkpoint["provider_report"] or
                    checkpoint["actual_charged_nano_usd"] >= APPROVED_CREDIT_NANO_USD):
                raise GuardError("monetary halt reconciliation lacks its retained billing checkpoint")
            try:
                before = _read(row["before_guard_ledger"])
            except (ValueError, TypeError, KeyError):
                raise GuardError("monetary halt before-image is unavailable or changed") from None
            if (not isinstance(before, dict) or not isinstance(before.get("requests"), dict) or
                    before.get("stopped_reason") != _MONEY_HALT or before.get("started_epoch") != self.data["started_epoch"] or
                    before.get("policy", {}).get("models") != self.policy["models"] or
                    before.get("policy", {}).get("approved_credit_nano_usd") != APPROVED_CREDIT_NANO_USD or
                    before.get("billing_money_halt_reconciliations", []) != history[:index] or
                    any(self.data["requests"].get(key) != value or value.get("status") != "settled"
                        for key, value in before.get("requests", {}).items()) or
                    set(before.get("requests", {})) != set(checkpoint["covered_request_ids"])):
                raise GuardError("monetary halt reconciliation changed retained accounting")

    def _billing_checkpoint_extension(self, previous, candidate):
        previous_ids, current_ids = set(previous["covered_request_ids"]), set(candidate["covered_request_ids"])
        if not previous_ids < current_ids:
            raise GuardError("subsequent billing checkpoint must extend retained settled coverage")
        increment = candidate["actual_charged_nano_usd"] - previous["actual_charged_nano_usd"]
        bound = sum(self.data["requests"][key]["charged_nano_usd"] for key in current_ids - previous_ids)
        if not 0 <= increment <= bound:
            raise GuardError("subsequent billing cost changed retained costs or exceeded new charge bounds")

    def billing_checkpoint(self, reference, covered_ids):
        """Validate a settled aggregate; never infer missing usage or prices.

        The authenticated report is an owner-private immutable preflight file.
        Its account-wide counters must cover exactly these unchanged requests.
        All later requests retain their full conservative reservation/charge.
        """
        if self.policy.get("balance_only") is not True or \
                self.policy.get("approved_credit_nano_usd") != APPROVED_CREDIT_NANO_USD:
            raise GuardError("billing checkpoint requires the existing $25-only policy")
        if (not isinstance(covered_ids, list) or not covered_ids or
                any(not isinstance(identity, str) for identity in covered_ids) or
                covered_ids != sorted(set(covered_ids))):
            raise GuardError("invalid covered billing request set")
        covered = {}
        for identity in covered_ids:
            record = self.data["requests"].get(identity)
            if not isinstance(record, dict) or record.get("status") != "settled":
                raise GuardError("billing checkpoint requires unchanged settled requests")
            covered[identity] = record
        from .allowance_renewal import _read
        try:
            report = _read(reference)
        except (ValueError, TypeError, KeyError):
            raise GuardError("billing report is unavailable, unsafe or changed") from None
        if not isinstance(report, dict):
            raise GuardError("invalid authenticated billing report")
        evidence = report.get("evidence", report)
        if (not isinstance(evidence, dict) or evidence.get("status") != "PASS" or
                evidence.get("blockers") != [] or evidence.get("api_origin") != API_ORIGIN or
                evidence.get("billing_attestation_verified_by_caller") is not True):
            raise GuardError("passing authenticated billing evidence is required")
        credits, usage = evidence.get("credits"), evidence.get("usage")
        if not isinstance(credits, dict) or not isinstance(usage, dict) or not isinstance(usage.get("totals"), dict):
            raise GuardError("complete billing counters and credits are required")
        billed = usage["totals"]
        count = _int(billed.get("request_count"), "billed request count")
        prompt = _int(billed.get("input_tokens"), "billed prompt usage", 0)
        completion = _int(billed.get("output_tokens"), "billed completion usage", 0)
        tokens = _int(billed.get("total_tokens"), "billed total usage", 0)
        actual = _int(billed.get("total_cost_nano_usd"), "actual billed cost", 0)
        balance = _int(credits.get("balance_nano_usd"), "billed credit balance", 0)
        available = _int(credits.get("available_nano_usd"), "billed available credit", 0)
        reserved = _int(credits.get("reserved_nano_usd"), "billed reserved credit", 0)
        conservative = sum(record["charged_nano_usd"] for record in covered.values())
        if (count != len(covered) or prompt != sum(record["prompt_tokens"] for record in covered.values()) or
                completion != sum(record["completion_tokens"] for record in covered.values()) or
                tokens != prompt + completion or actual > conservative):
            raise GuardError("billing report does not exactly cover retained usage and charge bounds")
        if (credits.get("currency") != "usd" or reserved != 0 or available != balance or
                balance + actual != APPROVED_CREDIT_NANO_USD or balance > APPROVED_CREDIT_NANO_USD):
            raise GuardError("billing report changed the original credit or has unresolved liabilities")
        return {"schema_version": 1, "provider_report": deepcopy(reference),
                "covered_request_ids": covered_ids[:],
                "covered_requests_sha256": hashlib.sha256(_canonical(covered).encode()).hexdigest(),
                "request_count": count, "input_tokens": prompt, "output_tokens": completion,
                "actual_charged_nano_usd": actual, "conservative_charged_nano_usd": conservative,
                "original_credit_nano_usd": APPROVED_CREDIT_NANO_USD, "balance_nano_usd": balance}

    def halt(self, reason):
        if reason not in _REASONS:
            raise GuardError("invalid guard stop reason")
        self.data["stopped_reason"] = self.data["stopped_reason"] or reason
        self._save()

    def remaining_seconds(self):
        if self.policy.get("balance_only") is True:
            return float("inf")
        start = self.data["started_epoch"]
        return self.policy["overall_timeout_seconds"] if start is None else (
            start + self.policy["overall_timeout_seconds"] - time.time())

    def reserve(self, model_id, output):
        if self.data["stopped_reason"]:
            raise GuardError("guard is persistently stopped")
        if self.remaining_seconds() <= 0:
            self.halt("overall time budget exhausted")
            raise GuardError("guard time budget exhausted")
        model = self.policy["models"][model_id]
        tokens = model["input_ceiling"] + output
        price = _charge(model, model["input_ceiling"], output)
        totals = self.totals()
        if totals["charged_nano_usd"] + totals["held_nano_usd"] + price > self.policy["approved_credit_nano_usd"]:
            self.halt("money reservation would exceed the approved cap")
            raise GuardError("guard money budget exhausted")
        if not self.policy.get("balance_only") and totals["observed_tokens"] + totals["held_tokens"] + tokens > self.policy["max_total_tokens"]:
            self.halt("token reservation would exceed the approved cap")
            raise GuardError("guard token budget exhausted")
        key = uuid.uuid4().hex
        if self.data["started_epoch"] is None:
            self.data["started_epoch"] = time.time()
        self.data["requests"][key] = {"model": model_id, "status": "in_flight", "output_ceiling": output,
                                      "reserved_tokens": tokens, "reserved_nano_usd": price}
        self._save()  # Durable before the caller may forward any network bytes.
        return key

    def settle(self, key, prompt, completion):
        record = self.data["requests"][key]
        if record["status"] != "in_flight":
            raise GuardError("guard request cannot settle twice")
        model = self.policy["models"][record["model"]]
        _int(prompt, "prompt usage", 0)
        _int(completion, "completion usage", 0)
        if prompt > model["input_ceiling"] or completion > record["output_ceiling"]:
            self.unknown(key, "provider usage exceeded its reservation")
            raise GuardError("provider usage exceeded its reservation")
        record.update(status="settled", prompt_tokens=prompt, completion_tokens=completion,
                      charged_nano_usd=_charge(model, prompt, completion))
        self._save()

    def unknown(self, key, reason="provider request usage is unknown"):
        if self.data["requests"][key]["status"] != "settled":
            self.data["requests"][key]["status"] = "unknown"
            self.halt(reason)


def apply_billing_checkpoint(ledger_path, policy, provider_report, *, apply=False, expected_ledger_sha256=None,
                             reconcile_stale_money_halt=False, halt_before_image=None):
    """Offline, single-owner preflight/apply; preserve every original liability.

    Callers supply the production-validated policy. The default only returns a
    review. Apply requires that review's original ledger digest, takes the same
    exclusive lock as the request server, and accepts only fully settled usage.
    The original checkpoint stays immutable. Subsequent cumulative checkpoints
    append parent-bound coverage; only the latest aggregate changes the total.
    """
    if type(apply) is not bool or type(reconcile_stale_money_halt) is not bool:
        raise GuardError("invalid billing checkpoint operation")
    ledger = _Ledger(ledger_path, policy)
    try:
        ledger.open(checkpoint_only=True)
        before = ledger.path.read_bytes()
        before_sha256 = hashlib.sha256(before).hexdigest()
        if apply and expected_ledger_sha256 != before_sha256:
            raise GuardError("billing checkpoint ledger changed after preflight")
        releasing_halt = ledger.data["stopped_reason"] == _MONEY_HALT and reconcile_stale_money_halt
        if ledger.data["stopped_reason"] is not None and not releasing_halt:
            raise GuardError("billing checkpoint cannot clear a persistent guard stop")
        original = deepcopy(ledger.data)
        candidate = ledger.billing_checkpoint(provider_report, sorted(original["requests"]))
        previous = ledger.latest_billing_checkpoint()
        changed = previous is None or candidate != {key: value for key, value in previous.items()
                                                    if key != "parent_checkpoint_sha256"}
        if previous is None:
            ledger.data["billing_checkpoint"] = candidate
        elif changed:
            ledger._billing_checkpoint_extension(previous, candidate)
            candidate["parent_checkpoint_sha256"] = hashlib.sha256(_canonical(previous).encode()).hexdigest()
            ledger.data.setdefault("billing_checkpoint_history", []).append(candidate)
        if releasing_halt:
            from .allowance_renewal import _read
            try:
                retained_before = _read(halt_before_image)
            except (ValueError, TypeError, KeyError):
                raise GuardError("stale monetary halt before-image is unavailable or changed") from None
            if (halt_before_image.get("sha256") != before_sha256 or retained_before != original or
                    candidate["actual_charged_nano_usd"] >= APPROVED_CREDIT_NANO_USD):
                raise GuardError("stale monetary halt requires exact before-image and available original credit")
            history = ledger.data.setdefault("billing_money_halt_reconciliations", [])
            retained_checkpoint = ledger.data.get("billing_checkpoint_history", [])[-1] if ledger.data.get("billing_checkpoint_history") else ledger.data["billing_checkpoint"]
            history.append(dict(original_stopped_reason=_MONEY_HALT, before_guard_ledger=deepcopy(halt_before_image),
                checkpoint_sha256=hashlib.sha256(_canonical(retained_checkpoint).encode()).hexdigest(),
                provider_report=deepcopy(retained_checkpoint["provider_report"]), reconciled_at=time.time(),
                parent_reconciliation_sha256=hashlib.sha256(_canonical(history[-1]).encode()).hexdigest() if history else None))
            ledger.data["stopped_reason"] = None
            changed = True
        ledger._validate()
        totals = ledger.totals()
        if ledger.path.read_bytes() != before:
            raise GuardError("billing checkpoint ledger changed while locked")
        # Revalidate the immutable report immediately before the durable save.
        fresh_candidate = ledger.billing_checkpoint(provider_report, sorted(original["requests"]))
        if {key: value for key, value in candidate.items() if key != "parent_checkpoint_sha256"} != fresh_candidate:
            raise GuardError("billing report changed during preflight")
        ledger.latest_billing_checkpoint()  # Revalidate every immutable parent report before saving.
        checkpoint_fields = {"billing_checkpoint", "billing_checkpoint_history", "billing_money_halt_reconciliations"}
        if releasing_halt:
            checkpoint_fields.add("stopped_reason")
        if {key: value for key, value in ledger.data.items() if key not in checkpoint_fields} != \
                {key: value for key, value in original.items() if key not in checkpoint_fields}:
            raise GuardError("billing checkpoint changed original accounting")
        if previous is not None and (ledger.data["billing_checkpoint"] != original["billing_checkpoint"] or
                ledger.data.get("billing_checkpoint_history", [])[:len(original.get("billing_checkpoint_history", []))]
                != original.get("billing_checkpoint_history", [])):
            raise GuardError("billing checkpoint changed its immutable history")
        if ledger.data.get("billing_money_halt_reconciliations", [])[:len(original.get("billing_money_halt_reconciliations", []))] != original.get("billing_money_halt_reconciliations", []):
            raise GuardError("billing checkpoint changed its historical monetary halts")
        if apply and changed:
            ledger._save()
        return {"status": "PASS" if apply else "REVIEW_ONLY", "ledger_before_sha256": before_sha256,
                "checkpoint": candidate, "totals": totals, "ledger_write": apply and changed,
                "checkpoint_count": 1 + len(ledger.data.get("billing_checkpoint_history", [])),
                "stale_money_halt_reconciled": releasing_halt,
                "halt_reconciliation": ledger.data.get("billing_money_halt_reconciliations", [])[-1] if releasing_halt else None,
                "original_request_count": len(original["requests"]),
                "original_requests_sha256": hashlib.sha256(_canonical(original["requests"]).encode()).hexdigest(),
                "original_started_epoch": original["started_epoch"],
                "policy_sha256": hashlib.sha256(_canonical(original["policy"]).encode()).hexdigest(),
                "network_calls": 0, "inference_calls": 0}
    finally:
        ledger.close()


def reconcile_factory_money_halt(factory_data, guard_data, *, before_image):
    """Pure matching factory-stop amendment; the caller owns durable writes."""
    if factory_data.get("stopped_reason") is None:
        return deepcopy(factory_data)
    if factory_data.get("stopped_reason") not in _FACTORY_MONEY_HALTS:
        raise GuardError("factory stop is not the sole stale request-money halt")
    checked = _Ledger(Path("unused"), guard_data["policy"])
    checked.data = guard_data
    checked._validate()
    checkpoint = checked.latest_billing_checkpoint()
    history = guard_data.get("billing_money_halt_reconciliations", [])
    if (guard_data["stopped_reason"] is not None or not history or checkpoint is None or
            any(item["status"] != "settled" for item in guard_data["requests"].values()) or
            set(checkpoint["covered_request_ids"]) != set(guard_data["requests"]) or
            checkpoint["actual_charged_nano_usd"] >= APPROVED_CREDIT_NANO_USD or
            history[-1]["checkpoint_sha256"] != hashlib.sha256(_canonical(checkpoint).encode()).hexdigest()):
        raise GuardError("factory monetary halt lacks exact settled billing reconciliation")
    from .allowance_renewal import _read
    try:
        retained_before = _read(before_image)
    except (ValueError, TypeError, KeyError):
        raise GuardError("factory monetary halt before-image is unavailable or changed") from None
    if retained_before != factory_data:
        raise GuardError("factory monetary halt before-image changed")
    result = deepcopy(factory_data)
    amendments = result.setdefault("billing_money_halt_reconciliations", [])
    if not isinstance(amendments, list):
        raise GuardError("invalid factory monetary halt history")
    amendments.append(dict(original_stopped_reason=factory_data["stopped_reason"],
        before_factory_ledger=deepcopy(before_image), guard_reconciliation=deepcopy(history[-1]),
        parent_reconciliation_sha256=hashlib.sha256(_canonical(amendments[-1]).encode()).hexdigest() if amendments else None))
    result["stopped_reason"] = None
    return result


def billing_checkpoint_main(argv=None):
    """Minimal local CLI; never reads a provider credential or opens a socket."""
    import argparse
    from .common import load_config
    from .harnesses import _featherless_metadata

    parser = argparse.ArgumentParser(description="Preflight or append a settled Featherless billing checkpoint")
    parser.add_argument("--config", required=True)
    parser.add_argument("--provider-report", required=True)
    parser.add_argument("--provider-report-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--ledger-before-sha256")
    parser.add_argument("--reconcile-stale-money-halt", action="store_true")
    parser.add_argument("--halt-before-image")
    parser.add_argument("--halt-before-image-sha256")
    args = parser.parse_args(argv)
    config = load_config(Path(args.config))
    options = config["runtime"]["featherless_budget_guard"]
    policy = _policy(_featherless_metadata(config), options["approved_credit_nano_usd"],
                     options["max_total_tokens"], options["overall_timeout_seconds"],
                     time_renewal=options.get("time_renewal"), balance_only=options.get("balance_only", False))
    result = apply_billing_checkpoint(options["ledger"], policy,
        {"path": args.provider_report, "sha256": args.provider_report_sha256},
        apply=args.apply, expected_ledger_sha256=args.ledger_before_sha256,
        reconcile_stale_money_halt=args.reconcile_stale_money_halt,
        halt_before_image={"path": args.halt_before_image, "sha256": args.halt_before_image_sha256} if args.halt_before_image else None)
    print(json.dumps(result, sort_keys=True))
    return 0


class _Usage:
    """One request's cumulative usage; repeated final SSE events are not added."""
    def __init__(self):
        self.prompt = self.completion = None
        self.done = False
        self.buffer = b""

    def observe(self, payload):
        if not isinstance(payload, dict) or payload.get("error") is not None:
            raise GuardError("invalid provider completion")
        usage = payload.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise GuardError("invalid provider usage")
            prompt = _int(usage.get("prompt_tokens"), "provider prompt usage", 0)
            completion = _int(usage.get("completion_tokens"), "provider completion usage", 0)
            total = _int(usage.get("total_tokens"), "provider total usage", 0)
            if total != prompt + completion:
                raise GuardError("inconsistent provider usage")
            self.prompt = max(prompt, self.prompt or 0)
            self.completion = max(completion, self.completion or 0)

    def feed(self, chunk):
        self.buffer += chunk
        if len(self.buffer) > MAX_EVENT_BYTES:
            raise GuardError("provider event exceeded the permitted size")
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if line.startswith(b"data:"):
                data = line[5:].strip()
                if data == b"[DONE]":
                    self.done = True
                else:
                    if self.done:
                        raise GuardError("provider data followed the final marker")
                    try:
                        self.observe(json.loads(data))
                    except (ValueError, UnicodeError):
                        raise GuardError("invalid provider stream event") from None

    def finish(self, *, streaming):
        if streaming and self.buffer.strip():
            raise GuardError("provider stream ended mid-event")
        if self.prompt is None or self.completion is None or not self.done:
            raise GuardError("provider completion did not include final usage")
        return self.prompt, self.completion


def _request(payload, models):
    if not isinstance(payload, dict) or payload.get("model") not in models:
        raise GuardError("unapproved model")
    allowed = {"model", "messages", "tools", "tool_choice", "temperature", "top_p",
               "presence_penalty", "frequency_penalty", "seed", "stop", "stream", "stream_options",
               "max_tokens", "max_completion_tokens", "n", "parallel_tool_calls", "response_format",
               "reasoning_effort", "chat_template_kwargs", "verbosity", "user", "metadata",
               "logprobs", "top_logprobs", "store"}
    if set(payload) - allowed:
        raise GuardError("unsupported completion parameter")
    if payload.get("n", 1) != 1 or type(payload.get("n", 1)) is not int or \
            any(key in payload for key in ("best_of", "audio", "modalities", "prediction", "image", "images")):
        raise GuardError("unsupported completion or billing mode")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise GuardError("text messages are required")
    for message in messages:
        if not isinstance(message, dict):
            raise GuardError("invalid text message")
        if set(message) - {"role", "content", "name", "tool_calls", "tool_call_id", "function_call", "refusal", "reasoning_content"}:
            raise GuardError("unsupported message parameter")
        # GLM/OpenCode preserves prior assistant thinking as text between tool
        # calls. It consumes the same bounded input context as other history.
        if message.get("reasoning_content") is not None and not isinstance(message["reasoning_content"], str):
            raise GuardError("assistant reasoning history must be text")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            if not isinstance(content, list) or any(
                not isinstance(part, dict) or part.get("type") != "text" or not isinstance(part.get("text"), str)
                or set(part) != {"type", "text"} for part in content
            ):
                raise GuardError("multimodal input is not supported by this budget guard")
        if any(key in message for key in ("audio", "images", "image_url", "video", "input_audio")):
            raise GuardError("multimodal input is not supported by this budget guard")
    model = models[payload["model"]]
    limits = [model["output_ceiling"]]
    for name in ("max_tokens", "max_completion_tokens"):
        if name in payload:
            limits.append(_int(payload[name], "request output bound"))
    output = min(limits)
    body = dict(payload)
    # Featherless documents max_tokens; normalize the alternative spelling into
    # that one field so precedence between two fields cannot bypass the clamp.
    body.pop("max_completion_tokens", None)
    body["max_tokens"] = output
    if type(body.get("stream", False)) is not bool:
        raise GuardError("invalid stream mode")
    if body.get("stream", False):
        options = body.get("stream_options", {})
        if not isinstance(options, dict):
            raise GuardError("invalid stream options")
        body["stream_options"] = {**options, "include_usage": True}
    return body, output


class FeatherlessGuard:
    """Async context manager owning one loopback server and persistent ledger.

    Keep one context around all seat servers. Reopen the same ledger for later
    phases, never create a new ledger to bypass a stop. ``transport`` is solely
    an injectable httpx transport for offline tests. Real keys stay in this
    parent; child servers receive only ``url`` and the random ``token``.
    """
    def __init__(self, *, api_key, models, ledger_path, approved_credit_nano_usd=APPROVED_CREDIT_NANO_USD,
                 max_total_tokens=2_000_000, overall_timeout_seconds=21600,
                 request_timeout_seconds=180, transport=None, time_renewal=None, balance_only=False):
        if not isinstance(api_key, str) or not api_key or any(c.isspace() for c in api_key):
            raise GuardError("invalid provider credential")
        self._api_key = api_key
        self._policy = _policy(models, approved_credit_nano_usd, max_total_tokens, overall_timeout_seconds,
                               time_renewal=time_renewal, balance_only=balance_only)
        self._ledger = _Ledger(ledger_path, self._policy)
        self._request_timeout = None if balance_only else _int(request_timeout_seconds, "request timeout")
        self._transport = transport
        self._server = self._client = None
        self._tasks = set()
        self._url = None
        self._token = secrets.token_urlsafe(32)

    @property
    def url(self):
        if self._url is None:
            raise GuardError("guard is not running")
        return self._url

    @property
    def token(self):
        return self._token

    def verification(self):
        if self._ledger.data is None:
            raise GuardError("guard ledger is not open")
        data = self._ledger.data
        return {"schema_version": 1, "enforcement": "request_reservations",
                "policy_sha256": hashlib.sha256(_canonical(self._policy).encode()).hexdigest(),
                "limits": {key: value for key, value in self._policy.items() if key != "models"},
                "started_epoch": data["started_epoch"], "stopped_reason": data["stopped_reason"],
                "request_count": len(data["requests"]), **self._ledger.totals()}

    async def __aenter__(self):
        if self._server is not None:
            raise GuardError("guard is already running")
        self._ledger.open()
        try:
            self._client = httpx.AsyncClient(
                base_url=API_ORIGIN, headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._request_timeout, follow_redirects=False, trust_env=False,
                transport=self._transport,
            )
            self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=65536)
            port = self._server.sockets[0].getsockname()[1]
            self._url = f"http://127.0.0.1:{port}/v1"
            return self
        except BaseException:
            if self._client is not None:
                await self._client.aclose()
            self._ledger.close()
            raise GuardError("guard could not start") from None

    async def __aexit__(self, exc_type, exc, traceback):
        try:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
            tasks = list(self._tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self._client is not None:
                await self._client.aclose()
        finally:
            self._server, self._url = None, None
            self._ledger.close()

    async def _error(self, writer, status, code):
        data = _canonical({"error": {"message": "provider budget guard blocked request",
                                     "type": "budget_guard", "code": code}}).encode()
        writer.write(f"HTTP/1.1 {status} Error\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
        await writer.drain()

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        self._tasks.add(task)
        key, forward, disconnect = None, None, None
        try:
            async with asyncio.timeout(10):
                header = await reader.readuntil(b"\r\n\r\n")
                lines = header.decode("ascii").split("\r\n")
                if lines[0] != "POST /v1/chat/completions HTTP/1.1":
                    await self._error(writer, 404, "unsupported_route")
                    return
                headers = {}
                for line in lines[1:]:
                    if not line:
                        continue
                    name, value = line.split(":", 1)
                    name, value = name.lower(), value.strip()
                    if name in headers:
                        raise GuardError("duplicate request header")
                    headers[name] = value
                if not hmac.compare_digest(headers.get("authorization", ""), "Bearer " + self._token):
                    await self._error(writer, 401, "unauthorized")
                    return
                length = headers.get("content-length", "")
                if "transfer-encoding" in headers or not length.isascii() or not length.isdecimal() or \
                        not 0 < int(length) <= MAX_BODY_BYTES:
                    raise GuardError("invalid request body length")
                payload = json.loads(await reader.readexactly(int(length)))
                body, output = _request(payload, self._policy["models"])
            # No await occurs between policy check and durable reservation.
            key = self._ledger.reserve(body["model"], output)
            forward = asyncio.create_task(self._forward(body, key, writer))
            disconnect = asyncio.create_task(reader.read(1))
            done, _ = await asyncio.wait((forward, disconnect), return_when=asyncio.FIRST_COMPLETED)
            if forward not in done:
                forward.cancel()
                await asyncio.gather(forward, return_exceptions=True)
                raise GuardError("client disconnected during provider request")
            await forward
        except asyncio.CancelledError:
            if key is not None:
                self._ledger.unknown(key, "provider request failed or was interrupted")
            raise
        except Exception:
            if key is not None:
                self._ledger.unknown(key, "provider request failed or was interrupted")
            # Never return raw upstream errors or exception text. If streaming
            # has begun, close the partial response instead of appending JSON.
            if forward is None:
                try:
                    await self._error(writer, 429 if key is None and self._ledger.data["stopped_reason"] else 400,
                                      "request_rejected")
                except Exception:
                    pass
        finally:
            for pending in (forward, disconnect):
                if pending is not None and not pending.done():
                    pending.cancel()
            await asyncio.gather(*(pending for pending in (forward, disconnect) if pending is not None), return_exceptions=True)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            self._tasks.discard(task)

    async def _forward(self, body, key, writer):
        usage = _Usage()
        streaming = body.get("stream", False)
        final_chunks = []
        received_bytes = 0
        deadline = None if self._policy.get("balance_only") else max(min(self._request_timeout, self._ledger.remaining_seconds()), 0)
        try:
            async with asyncio.timeout(deadline):
                async with self._client.stream("POST", "/v1/chat/completions", json=body) as response:
                    if response.status_code != 200:
                        await self._error(writer, 502, "upstream_failed")
                        raise GuardError("upstream request failed")
                    if streaming:
                        if not response.headers.get("content-type", "").startswith("text/event-stream"):
                            raise GuardError("invalid upstream stream type")
                        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
                        async for chunk in response.aiter_bytes():
                            if not chunk:
                                continue
                            received_bytes += len(chunk)
                            if received_bytes > MAX_BODY_BYTES:
                                raise GuardError("upstream completion exceeded the permitted size")
                            usage.feed(chunk)
                            # OpenAI clients may close as soon as [DONE] arrives.
                            # Release that marker only after durable settlement.
                            if usage.done:
                                final_chunks.append(chunk)
                                continue
                            writer.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                            await writer.drain()
                    else:
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > MAX_BODY_BYTES:
                                raise GuardError("upstream completion exceeded the permitted size")
                        payload = json.loads(content)
                        usage.observe(payload)
                        choices = payload.get("choices")
                        usage.done = isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) and \
                            choices[0].get("finish_reason") in {"stop", "length", "tool_calls", "function_call", "content_filter"}
                    prompt, completion = usage.finish(streaming=streaming)
                    self._ledger.settle(key, prompt, completion)
                    if streaming:
                        for chunk in final_chunks:
                            writer.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                        writer.write(b"0\r\n\r\n")
                    else:
                        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(content)}\r\nConnection: close\r\n\r\n".encode() + content)
                    await writer.drain()
        except BaseException:
            self._ledger.unknown(key, "provider request failed or was interrupted")
            raise


if __name__ == "__main__":
    try:
        raise SystemExit(billing_checkpoint_main())
    except GuardError:
        print('{"status":"BLOCKED","reason":"billing checkpoint validation failed","network_calls":0,"inference_calls":0}')
        raise SystemExit(1)
