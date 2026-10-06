"""Normalize trusted adapter telemetry without guessing provider usage.

BAND 4.0.0's Claude SDK and OpenCode adapters emit one ``band_usage``
aggregate per provider turn. Those events contain no session, message, or turn
identity: the caller must supply the persisted factory turn identity. OpenCode
already deduplicates assistant-message counters and folds reasoning into output;
Claude's result already aggregates its internal tool loop. Both report cache
tokens separately from input, so all four emitted dimensions are additive.

Codex is deliberately left to the ledger's existing cumulative thread counters.
Its additional ``band_usage`` event describes the same consumption and must not
be added a second time.
"""

from collections.abc import Mapping


# Reserved for trusted adapter/wrapper events, never model-authored metadata.
TRUSTED_METADATA_KEYS = frozenset({"band_usage", "failure"})
TRUSTED_METADATA_PREFIXES = ("codex_", "claude_", "opencode_", "factory_")
TOKEN_FIELDS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
)
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})
_ALTERNATE_HARNESSES = frozenset({"claude-code", "opencode"})
_FAILURE_PROVIDERS = frozenset({"codex", "claude_sdk", "opencode"})


def is_trusted_metadata_key(key: object) -> bool:
    """Whether a model-authored event would forge adapter-owned telemetry."""
    return isinstance(key, str) and (
        key in TRUSTED_METADATA_KEYS or key.startswith(TRUSTED_METADATA_PREFIXES)
    )


def usage_record(
    metadata: Mapping, *, turn_id: str | None, harness: str | None = None,
) -> tuple[str, int] | None:
    """Return a stable aggregate identity and observed total, or no observation.

    Callers persist the maximum for this key and add only its positive delta.
    That handles duplicate delivery, a corrected aggregate, and replay after a
    restart without treating separate turns with equal counts as duplicates.
    The factory turn identity must be durable, not a per-process sequence.

    No provider identity is inferred from ``band_usage``: the installed SDK's
    shape is identical for Codex and the alternate adapters. Requiring explicit
    harness context prevents counting Codex's unified event twice. Unknown
    harnesses also require a deliberate accounting implementation first.
    """
    if not isinstance(harness, str):
        return None
    harness = "claude-code" if harness == "claude" else harness
    if (
        harness not in _ALTERNATE_HARNESSES
        or not isinstance(turn_id, str)
        or not turn_id.strip()
        or not isinstance(metadata, Mapping)
    ):
        return None
    usage = metadata.get("band_usage")
    if not isinstance(usage, Mapping) or not any(k in usage for k in TOKEN_FIELDS):
        return None
    values = [usage.get(k, 0) for k in TOKEN_FIELDS]
    # bool is an int subclass; neither it nor coerced strings/floats are counts.
    if any(type(value) is not int or value < 0 for value in values):
        return None
    return f"{harness}:turn:{turn_id}", sum(values)


def lifecycle_status(metadata: Mapping) -> str | None:
    """Read an explicit terminal status or the SDK's structured failure event.

    Alternate adapters do not emit success lifecycle metadata themselves. Their
    factory wrapper supplies ``factory_event_type``/``factory_turn_status``
    after observing the actual provider completion. A normal task/usage event
    is never evidence of success. Structured failure takes precedence if an
    event happens to contain both failure and lifecycle metadata.
    """
    if not isinstance(metadata, Mapping):
        return None
    failure = metadata.get("failure")
    if isinstance(failure, Mapping):
        provider = failure.get("provider")
        if isinstance(provider, str) and provider in _FAILURE_PROVIDERS:
            return "failed"
    for prefix in ("factory", "codex"):
        if metadata.get(f"{prefix}_event_type") == "turn_lifecycle":
            status = metadata.get(f"{prefix}_turn_status")
            if isinstance(status, str) and status in TERMINAL_STATUSES:
                return status
    return None
