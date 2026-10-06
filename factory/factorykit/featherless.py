"""Read-only Featherless metadata and billing evidence for the approved attempt.

Only the documented plan, model-detail, balance, and usage-summary GET routes
are used. A passing result verifies these observations and the caller's separate
billing attestation; it does not authorize inference or enforce a dollar cap.
Credit settlement can lag requests by five minutes. The API does not expose
auto-top-up settings or promise a strict no-overdraft ceiling.

Provider field definitions:
https://featherless.ai/docs/api-reference-plan
https://featherless.ai/docs/api-reference-models
https://featherless.ai/docs/api-reference-credits
https://featherless.ai/docs/api-reference-usage-activity
"""

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
import re
from urllib.parse import quote

import httpx


API_ORIGIN = "https://api.featherless.ai"
MODEL_IDS = ("zai-org/GLM-5.3", "zai-org/GLM-5.3-Flash")
APPROVED_CREDIT_NANO_USD = 25_000_000_000
_NANO = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_PRICE = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_PLAN_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_LIMITATIONS = [
    "API evidence does not verify auto-top-up or the account's billing contract.",
    "Organization-wide credit and usage totals can lag live requests by five minutes.",
    "This read-only snapshot neither enforces a dollar cap nor authorizes inference.",
]


class EvidenceError(ValueError):
    """A fixed, credential-free validation error; never includes provider bodies."""


def _mapping(value, label):
    if not isinstance(value, Mapping):
        raise EvidenceError(f"{label}: expected an object")
    return value


def _integer(value, label, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise EvidenceError(f"{label}: expected an integer >= {minimum}")
    return value


def nano_usd(value, label="amount"):
    """Parse the provider's exact nonnegative integer string, never a float."""
    if not isinstance(value, str) or len(value) > 40 or not _NANO.fullmatch(value):
        raise EvidenceError(f"{label}: expected a nonnegative nano-USD integer string")
    return int(value)


def _price(value, label):
    if not isinstance(value, str) or len(value) > 80 or not _PRICE.fullmatch(value):
        raise EvidenceError(f"{label}: expected a nonnegative decimal USD string")
    # Decimal construction is exact and independent of the arithmetic context.
    if not Decimal(value).is_finite():
        raise EvidenceError(f"{label}: expected a finite price")
    return value


def _model_prices(value):
    prices = _mapping(value, "model.pricing")
    canonical = {key: _price(prices.get(key), f"model.pricing.{key}")
                 for key in ("prompt", "completion", "image", "request")}
    # Authenticated model responses also contain numeric input/output values.
    # The public pricing guide lists those rates per million tokens:
    # https://featherless.ai/docs/request-pricing-and-credits
    # Treat them only as redundant aliases when that interpretation agrees
    # exactly with the documented per-token strings. Never derive a billing
    # rate from an alias or silently discard a conflicting value.
    for alias, key in (("input", "prompt"), ("output", "completion")):
        if alias not in prices:
            continue
        amount = prices[alias]
        if type(amount) not in (int, Decimal):
            raise EvidenceError(f"model.pricing.{alias}: expected an exact numeric per-million rate")
        amount = Decimal(amount)
        if not amount.is_finite() or amount < 0:
            raise EvidenceError(f"model.pricing.{alias}: expected a finite nonnegative rate")
        # Changing the exponent is exact even for prices longer than the
        # ambient Decimal arithmetic precision; multiplication may round.
        parts = Decimal(canonical[key]).as_tuple()
        per_million = Decimal((parts.sign, parts.digits, parts.exponent + 6))
        if amount != per_million:
            raise EvidenceError(f"model.pricing.{alias}: differs from the per-token rate")
    if any(Decimal(_price(amount, "model.pricing")) != 0
           for key, amount in prices.items()
           if key not in {*canonical, "input", "output"}):
        raise EvidenceError("model.pricing: unsupported nonzero billing dimension")
    return canonical


def _plan(payload):
    data = _mapping(payload, "plan")
    plan_id = data.get("id")
    if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
        raise EvidenceError("plan.id: missing or invalid")
    if "max_context_length" not in data:
        raise EvidenceError("plan.max_context_length: missing")
    context = data["max_context_length"]
    if context is not None:
        context = _integer(context, "plan.max_context_length", minimum=1)
    return {
        "id": plan_id,
        "max_context_length": context,
        "concurrency": _integer(data.get("concurrency"), "plan.concurrency", minimum=1),
    }


def _model(payload, model_id, plan):
    data = _mapping(payload, "model")
    if data.get("id") != model_id:
        raise EvidenceError("model.id: did not match the requested model")
    if data.get("status") != "active":
        raise EvidenceError("model.status: model is not active")
    if data.get("available_on_current_plan") is not True:
        raise EvidenceError("model.available_on_current_plan: authenticated access was not confirmed")
    if data.get("is_gated") is not False:
        raise EvidenceError("model.is_gated: ungated access was not confirmed")
    if _mapping(data.get("features"), "model.features").get("tool_use") is not True:
        raise EvidenceError("model.features.tool_use: tool support was not confirmed")
    context = _integer(data.get("context_length"), "model.context_length", minimum=1)
    completion = _integer(data.get("max_completion_tokens"), "model.max_completion_tokens", minimum=1)
    cost = _integer(data.get("concurrency_cost"), "model.concurrency_cost", minimum=1)
    if cost > plan["concurrency"]:
        raise EvidenceError("model.concurrency_cost: exceeds the plan's concurrency-unit limit")
    effective_context = min(context, plan["max_context_length"] or context)
    if effective_context < 2:
        raise EvidenceError("model context cannot fit both prompt and completion")
    prices = _model_prices(data.get("pricing"))
    return {
        "id": model_id, "status": "active", "tool_use": True,
        "available_on_current_plan": True, "is_gated": False,
        "context_length": context, "max_completion_tokens": completion,
        "effective_context_length": effective_context,
        # The actual prompt consumes some of this context; callers must still
        # ensure prompt tokens + requested output fit effective_context_length.
        "effective_max_completion_tokens": min(completion, effective_context - 1),
        "concurrency_cost": cost,
        "pricing": prices,
    }


def _credits(payload):
    data = _mapping(payload, "credits")
    if data.get("currency") != "usd":
        raise EvidenceError("credits.currency: expected usd")
    result = {key: nano_usd(data.get(key), f"credits.{key}") for key in (
        "balance_nano_usd", "reserved_nano_usd", "available_nano_usd",
    )}
    if result["available_nano_usd"] != result["balance_nano_usd"] - result["reserved_nano_usd"]:
        raise EvidenceError("credits: available balance does not equal balance minus reservations")
    return {**result, "currency": "usd"}


def _epoch(value, label):
    if not isinstance(value, str):
        raise EvidenceError(f"{label}: expected a UTC date")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise EvidenceError(f"{label}: expected a UTC date") from None
    if date.tzinfo is None or date.utcoffset().total_seconds() != 0:
        raise EvidenceError(f"{label}: expected a UTC date")
    return date.timestamp()


def _usage(payload, start, end):
    data = _mapping(payload, "usage")
    if _epoch(data.get("start_date"), "usage.start_date") != start or \
            _epoch(data.get("end_date"), "usage.end_date") != end:
        raise EvidenceError("usage: response date range did not match the requested range")
    totals = _mapping(data.get("totals"), "usage.totals")
    result = {key: _integer(totals.get(key), f"usage.totals.{key}") for key in (
        "request_count", "input_tokens", "output_tokens", "total_tokens",
    )}
    if result["total_tokens"] != result["input_tokens"] + result["output_tokens"]:
        raise EvidenceError("usage.totals: token counts are inconsistent")
    if "total_cost_nano_usd" not in totals:
        raise EvidenceError("usage.totals.total_cost_nano_usd: missing")
    price = totals["total_cost_nano_usd"]
    result["total_cost_nano_usd"] = None if price is None else nano_usd(price, "usage.totals.total_cost_nano_usd")
    if not result["request_count"] and (result["total_tokens"] or result["total_cost_nano_usd"]):
        raise EvidenceError("usage.totals: usage was returned without any requests")
    return {"start": start, "end": end, "totals": result}


def evaluate_evidence(
    payloads, *, usage_start, usage_end, expected_plan_id=None,
    billing_attestation=None, baseline_balance_nano_usd=None,
):
    """Validate response shapes and retain only the fields needed for preflight.

    ``expected_plan_id=None`` permits discovery but keeps the result BLOCKED.
    No plan ID implies billing semantics. The caller must independently attest
    ``request_pricing=True``, ``auto_topup_enabled=False``, and
    ``existing_credit_only=True`` after inspecting the account's billing UI.
    Keep the first observed balance unchanged when supplying a later baseline.
    """
    _integer(usage_start, "usage_start")
    _integer(usage_end, "usage_end", minimum=usage_start + 1)
    if baseline_balance_nano_usd is not None:
        _integer(baseline_balance_nano_usd, "baseline_balance_nano_usd", minimum=1)
        if baseline_balance_nano_usd > APPROVED_CREDIT_NANO_USD:
            raise EvidenceError("baseline balance exceeds the approved existing credit")
    if expected_plan_id is not None and (
        not isinstance(expected_plan_id, str) or not _PLAN_ID.fullmatch(expected_plan_id)
    ):
        raise EvidenceError("expected_plan_id: invalid")
    payloads = _mapping(payloads, "responses")
    result = {"status": "BLOCKED", "blockers": [], "api_origin": API_ORIGIN,
              "models": {}, "limitations": list(_LIMITATIONS)}
    blockers = result["blockers"]
    attestation = billing_attestation if isinstance(billing_attestation, Mapping) else {}
    attested = (attestation.get("request_pricing") is True and
                attestation.get("auto_topup_enabled") is False and
                attestation.get("existing_credit_only") is True)
    result["billing_attestation_verified_by_caller"] = attested
    if not attested:
        blockers.append("separate billing and auto-top-up attestation is required")
    if expected_plan_id is None:
        blockers.append("the observed plan ID must be reviewed and explicitly pinned")
    try:
        result["plan"] = _plan(payloads.get("plan"))
        if expected_plan_id is not None and result["plan"]["id"] != expected_plan_id:
            blockers.append("observed plan ID differs from the caller's expected plan")
    except EvidenceError as exc:
        blockers.append(str(exc))
    if "plan" in result:
        for model_id in MODEL_IDS:
            try:
                result["models"][model_id] = _model(payloads.get(model_id), model_id, result["plan"])
            except EvidenceError as exc:
                blockers.append(f"{model_id}: {exc}")
    try:
        result["credits"] = _credits(payloads.get("credits"))
        balance = result["credits"]["balance_nano_usd"]
        if balance <= 0 or result["credits"]["available_nano_usd"] <= 0:
            blockers.append("positive available prepaid credit is required")
        if balance > APPROVED_CREDIT_NANO_USD:
            blockers.append("credit balance exceeds the approved existing $25")
        if baseline_balance_nano_usd is not None and balance > baseline_balance_nano_usd:
            blockers.append("credit balance increased above the original baseline; review is required")
    except EvidenceError as exc:
        blockers.append(str(exc))
    try:
        result["usage"] = _usage(payloads.get("usage"), usage_start, usage_end)
        totals = result["usage"]["totals"]
        if totals["request_count"] and totals["total_cost_nano_usd"] is None:
            blockers.append("usage contains requests without any reported price")
    except EvidenceError as exc:
        blockers.append(str(exc))
    if not blockers:
        result["status"] = "PASS"
    return result


async def preflight(
    api_key, *, usage_start, usage_end, expected_plan_id=None,
    billing_attestation=None, baseline_balance_nano_usd=None, transport=None,
):
    """Collect five authenticated GETs, without redirects, retries, or inference.

    The optional httpx transport supports offline tests. No credentials are read
    from disk, included in URLs, returned in evidence, or printed by this helper.
    HTTP failures include only the fixed endpoint label and numeric status code.
    Cancellation propagates; malformed responses and transport failures BLOCK.
    """
    if not isinstance(api_key, str) or not api_key or any(c.isspace() for c in api_key):
        raise EvidenceError("a nonempty API key without whitespace is required")
    options = dict(usage_start=usage_start, usage_end=usage_end,
                   expected_plan_id=expected_plan_id, billing_attestation=billing_attestation,
                   baseline_balance_nano_usd=baseline_balance_nano_usd)
    # Validate caller arguments before opening a connection.
    evaluate_evidence({}, **options)
    requests = [("plan", "/v1/plan", None)] + [
        (model_id, "/v1/models/" + quote(model_id, safe=""), None) for model_id in MODEL_IDS
    ] + [("credits", "/credits/balance", None),
         ("usage", "/usage/activity/summary", {"start": usage_start, "end": usage_end})]
    payloads, failures = {}, []
    try:
        async with asyncio.timeout(45):
            async with httpx.AsyncClient(
                base_url=API_ORIGIN, headers={"Authorization": f"Bearer {api_key}"},
                timeout=10, follow_redirects=False, trust_env=False, transport=transport,
            ) as client:
                for label, path, params in requests:
                    try:
                        response = await client.get(path, params=params)
                        if response.status_code != 200:
                            failures.append(f"{label}: HTTP {response.status_code}")
                            continue
                        # Numeric price aliases must never pass through a
                        # binary float before their exact consistency check.
                        payloads[label] = response.json(parse_float=Decimal)
                    except (httpx.HTTPError, ValueError):
                        failures.append(f"{label}: response unavailable or invalid")
    except TimeoutError:
        failures.append("provider preflight exceeded its total timeout")
    result = evaluate_evidence(payloads, **options)
    result["blockers"].extend(failures)
    result["observed_at"] = datetime.now(timezone.utc).isoformat()
    if failures:
        result["status"] = "BLOCKED"
    # Even a malformed provider echo in an otherwise permitted string field
    # must not allow the credential into the returned evidence.
    if result.get("plan", {}).get("id") == api_key:
        result["plan"]["id"] = "[redacted]"
        result["blockers"].append("provider response contained an invalid plan identity")
        result["status"] = "BLOCKED"
    return result
