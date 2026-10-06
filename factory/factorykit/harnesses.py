"""Maintained BAND harness adapters and owned, no-shell runtime processes.

Native permissions are harness policies, not an OS sandbox. In particular an
explicitly enabled shell can access its host. Nothing here dispatches a prompt.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile
from urllib.parse import urlsplit

from .common import FactoryError

_NAMES = {"codex": "Codex", "claude-code": "Claude Code", "opencode": "OpenCode"}
_ALIASES = {"codex": "codex", "claude": "claude-code", "claude-code": "claude-code",
            "claude code": "claude-code", "claude_sdk": "claude-code", "opencode": "opencode"}
_PERMISSIONS = frozenset({"read", "write", "bash", "network"})
_READ_TOOLS = {"Read", "Glob", "Grep"}
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_NETWORK_TOOLS = {"WebFetch", "WebSearch"}
_OPEN_READ = {"read", "glob", "grep", "list"}
_OPEN_WRITE = {"edit", "write", "patch", "apply_patch", "multiedit"}
_OPEN_NETWORK = {"webfetch", "websearch"}
OPENCODE_READY_TIMEOUT_SECONDS = 15.0
DISCOVERY_TIMEOUT_SECONDS = 35.0


def _canonical(name):
    try:
        return _ALIASES[str(name).strip().lower()]
    except KeyError:
        raise FactoryError("Supported harnesses: codex, claude-code, opencode") from None


def selected_harness(config):
    return _canonical(config.get("runtime", {}).get("harness", "codex"))


def harness_label(name):
    return _NAMES[_canonical(name)]


def command_key(name):
    return {"codex": "codex_command", "claude-code": "claude_command", "opencode": "opencode_command"}[_canonical(name)]


def validate_selection(config):
    errors = []
    try:
        harness = selected_harness(config)
    except FactoryError as exc:
        return [str(exc)]
    for seat in config.get("seats", []):
        try:
            if "harness" in config.get("runtime", {}) and _canonical(seat.get("harness", "codex")) != harness:
                errors.append(f"Seat {seat.get('id')} harness differs from runtime.harness; select a uniform profile")
        except FactoryError as exc:
            errors.append(f"Seat {seat.get('id')}: {exc}")
        model = seat.get("model") or config.get("runtime", {}).get("model")
        if not isinstance(model, str) or not model.strip() or any(c.isspace() for c in model):
            errors.append(f"Seat {seat.get('id')} requires an explicit model ID")
        elif harness == "opencode" and ("/" not in model or not all(model.split("/", 1))):
            errors.append(f"Seat {seat.get('id')} OpenCode model must be provider/model")
    if harness != "codex":
        command = config.get("runtime", {}).get(command_key(harness))
        if not isinstance(command, str) or not command or not Path(command).is_absolute():
            errors.append(f"runtime.{command_key(harness)} must name an absolute executable")
    if harness == "opencode":
        errors.extend(opencode_runtime_errors(config))
    elif config.get("budgets", {}).get("balance_only") is True:
        errors.append("Balance-only accounting requires the guarded OpenCode runtime")
    return errors


def opencode_runtime_errors(config):
    """Validate optional binary, provider and private-state bindings without I/O."""
    runtime, errors = config["runtime"], []
    version = runtime.get("opencode_version")
    if version is not None and (not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version)):
        errors.append("runtime.opencode_version must be an exact CLI/server version")
    expected_hash = runtime.get("opencode_sha256")
    if expected_hash is not None and (not isinstance(expected_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)):
        errors.append("runtime.opencode_sha256 must be a lowercase SHA-256 digest of the selected executable")
    state = runtime.get("opencode_state_root")
    if state is not None:
        runs = config.get("paths", {}).get("runs")
        if (not isinstance(state, str) or not Path(state).is_absolute() or not isinstance(runs, str)
                or Path(runs).resolve() not in Path(state).resolve().parents):
            errors.append("runtime.opencode_state_root must be an absolute private directory under paths.runs")
    errors.extend(featherless_guard_errors(config))
    provider = runtime.get("opencode_provider")
    if provider is None:
        return errors
    required = {"id", "npm", "base_url", "api_key_env", "models"}
    if not isinstance(provider, dict) or set(provider) != required:
        return errors + ["runtime.opencode_provider requires exactly id, npm, base_url, api_key_env and models"]
    if not isinstance(provider["id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", provider["id"]):
        errors.append("OpenCode provider id must be a literal identifier")
    if not isinstance(provider["npm"], str) or not re.fullmatch(r"(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9][A-Za-z0-9._-]*", provider["npm"]):
        errors.append("OpenCode provider npm must be a literal package name")
    try:
        url = urlsplit(provider["base_url"])
        if (url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment
                or any(character.isspace() for character in provider["base_url"])):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        errors.append("OpenCode provider base_url must be a credential-free HTTPS endpoint without a query or fragment")
    if not isinstance(provider["api_key_env"], str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", provider["api_key_env"]):
        errors.append("OpenCode provider api_key_env must name an environment variable, never contain its value")
    models = provider["models"]
    if not isinstance(models, dict) or not models:
        return errors + ["OpenCode provider models must declare exact upstream IDs and positive context/output limits"]
    forbidden = {"apikey", "api_key", "authorization", "headers", "baseurl", "base_url", "npm", "password", "secret"}
    def unsafe_metadata(value):
        if isinstance(value, dict):
            return any(str(key).lower() in forbidden or unsafe_metadata(item) for key, item in value.items())
        if isinstance(value, list):
            return any(unsafe_metadata(item) for item in value)
        return False
    for model_id, metadata in models.items():
        if (not isinstance(model_id, str) or not model_id or any(c.isspace() for c in model_id)
                or not isinstance(metadata, dict) or metadata.get("id", model_id) != model_id):
            errors.append("OpenCode provider model entries must retain their exact upstream model IDs")
            continue
        limits = metadata.get("limit")
        if (not isinstance(limits, dict) or any(type(limits.get(key)) is not int or limits[key] <= 0 for key in ("context", "output"))
                or limits["output"] > limits["context"]):
            errors.append(f"OpenCode provider model {model_id!r} needs positive context/output limits with output <= context")
        if unsafe_metadata(metadata):
            errors.append("OpenCode model metadata must not contain credentials or override provider routing")
    configured_models = [("Runtime default", runtime.get("model", ""))]
    configured_models += [(f"Seat {seat.get('id')}", seat.get("model") or runtime.get("model", ""))
                          for seat in config.get("seats", [])]
    for label, selected in configured_models:
        prefix, separator, upstream = selected.partition("/") if isinstance(selected, str) else ("", "", "")
        if not separator or prefix != provider["id"] or upstream not in models:
            errors.append(f"{label} model is outside the explicitly allowed provider/model inventory")
    return errors


def featherless_guard_errors(config):
    """Validate the optional request guard binding, without loading evidence."""
    runtime, budgets = config.get("runtime", {}), config.get("budgets", {})
    guard = runtime.get("featherless_budget_guard")
    if guard is None:
        return (["Balance-only accounting requires the approved Featherless request guard"]
                if budgets.get("balance_only") is True else [])
    fields = {"ledger", "model_metadata", "model_metadata_sha256", "approved_credit_nano_usd",
              "max_total_tokens", "overall_timeout_seconds", "request_timeout_seconds"}
    if (not isinstance(guard, dict) or not fields.issubset(guard)
            or set(guard) - fields - {"time_renewal", "balance_only"}):
        return ["runtime.featherless_budget_guard requires the exact ledger, model_metadata, model_metadata_sha256 and four limit fields"]
    errors = []
    balance_only = guard.get("balance_only", False)
    if (type(balance_only) is not bool or balance_only != budgets.get("balance_only", False)
            or (balance_only and "time_renewal" not in guard)):
        errors.append("Balance-only accounting requires matching flags and its explicit approved amendment")
    if balance_only and selected_harness(config) != "opencode":
        errors.append("Balance-only accounting requires the guarded OpenCode runtime")
    pin = runtime.get("opencode_provider")
    if (not isinstance(pin, dict) or pin.get("id") != "featherless"
            or pin.get("npm") != "@ai-sdk/openai-compatible"
            or pin.get("base_url") != "https://api.featherless.ai/v1"):
        errors.append("Featherless request guard requires the exact approved Featherless provider/package/HTTPS binding")
    if not runtime.get("opencode_state_root"):
        errors.append("Featherless request guard requires isolated OpenCode state")
    runs = config.get("paths", {}).get("runs")
    for key in ("ledger", "model_metadata"):
        path = guard[key]
        required_root = Path(runs).resolve() if isinstance(runs, str) else None
        if key == "ledger" and required_root is not None:
            required_root = required_root / "runtime"
        if (not isinstance(path, str) or not Path(path).is_absolute() or not isinstance(runs, str)
                or required_root not in Path(path).resolve().parents):
            errors.append(f"Featherless request guard {key} must be an absolute path under paths.runs" + ("/runtime" if key == "ledger" else ""))
    if not isinstance(guard["model_metadata_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", guard["model_metadata_sha256"]):
        errors.append("Featherless model metadata requires an exact lowercase SHA-256 binding")
    limits = ("approved_credit_nano_usd", "max_total_tokens", "overall_timeout_seconds", "request_timeout_seconds")
    if any(type(guard[key]) is not int or guard[key] <= 0 for key in limits):
        return errors + ["Featherless request guard limits must be positive integers"]
    try:
        approved_cap = Decimal(str(budgets.get("spend_cap_usd"))) * 1_000_000_000
        if (budgets.get("billing_mode") != "spend_cap" or not approved_cap.is_finite()
                or guard["approved_credit_nano_usd"] > min(25_000_000_000, approved_cap)):
            raise ValueError
    except (InvalidOperation, ValueError):
        errors.append("Featherless request credit limit must not exceed approved spend_cap_usd or the existing $25")
    for key, budget_key in (("max_total_tokens", "max_total_tokens"),
                            ("overall_timeout_seconds", "overall_timeout_seconds"),
                            ("request_timeout_seconds", "turn_timeout_seconds")):
        if not balance_only and (type(budgets.get(budget_key)) is not int or guard[key] > budgets[budget_key]):
            errors.append(f"Featherless request guard {key} must not exceed budgets.{budget_key}")
    if "time_renewal" in guard:
        try:
            from .allowance_renewal import validate_renewal_config
            validate_renewal_config(config)
        except Exception:
            errors.append("Featherless explicit time renewal is invalid or changed")
    return errors


def _featherless_metadata(config):
    guard = config["runtime"]["featherless_budget_guard"]
    if errors := featherless_guard_errors(config):
        raise FactoryError("; ".join(errors))
    path = Path(guard["model_metadata"])
    root = Path(config["paths"]["runs"]).resolve()
    if any(item.is_symlink() for item in (path, *path.parents)
           if item.resolve() == root or root in item.resolve().parents):
        raise FactoryError("Featherless metadata must not use symlinks")
    try:
        raw = path.read_bytes()
    except OSError:
        raise FactoryError("Featherless authenticated model metadata is missing or invalid JSON") from None
    if hashlib.sha256(raw).hexdigest() != guard["model_metadata_sha256"]:
        raise FactoryError("Featherless authenticated model metadata differs from its approved SHA-256")
    try:
        evidence = json.loads(raw)
    except ValueError:
        raise FactoryError("Featherless authenticated model metadata is missing or invalid JSON") from None
    if (not isinstance(evidence, dict) or evidence.get("status") != "PASS" or evidence.get("blockers") != []
            or evidence.get("api_origin") != "https://api.featherless.ai"
            or evidence.get("billing_attestation_verified_by_caller") is not True
            or not isinstance(evidence.get("plan"), dict) or not evidence["plan"].get("id")):
        raise FactoryError("Featherless metadata must be passing authenticated evidence with reviewed billing and plan binding")
    credits = evidence.get("credits")
    if (not isinstance(credits, dict) or credits.get("currency") != "usd"
            or any(type(credits.get(key)) is not int or credits[key] < 0
                   for key in ("balance_nano_usd", "reserved_nano_usd", "available_nano_usd"))
            or credits["available_nano_usd"] != credits["balance_nano_usd"] - credits["reserved_nano_usd"]
            or credits["balance_nano_usd"] > 25_000_000_000
            or guard["approved_credit_nano_usd"] > credits["available_nano_usd"]):
        raise FactoryError("Featherless request credit limit exceeds authenticated available credit")
    models = evidence.get("models")
    pin = config["runtime"]["opencode_provider"]
    if not isinstance(models, dict) or set(models) != set(pin["models"]):
        raise FactoryError("Featherless authenticated model inventory differs from the approved OpenCode provider")
    for name, metadata in models.items():
        if (not isinstance(metadata, dict) or metadata.get("id") != name or metadata.get("status") != "active"
                or metadata.get("tool_use") is not True or metadata.get("available_on_current_plan") is not True
                or metadata.get("is_gated") is not False):
            raise FactoryError("Featherless model access/tool support is not verified")
        limits = pin["models"][name]["limit"]
        if (limits["context"] != metadata.get("effective_context_length")
                or limits["output"] != metadata.get("effective_max_completion_tokens")):
            raise FactoryError("Featherless authenticated model limits differ from the approved OpenCode provider")
    return models


class _OwnedFeatherlessRoute:
    """Opaque per-context authority; never loaded from user configuration."""
    __slots__ = ("guard",)

    def __init__(self, guard):
        self.guard = guard

    def __repr__(self):
        return "<owned Featherless request guard>"


def _guard_endpoint(config, route):
    configured = config["runtime"].get("featherless_budget_guard") is not None
    if not configured and route is None:
        return None
    if not configured or not isinstance(route, _OwnedFeatherlessRoute):
        raise FactoryError("Featherless requests require this runtime's owned budget guard; direct routing is forbidden")
    endpoint = urlsplit(route.guard.url)
    if (endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or not endpoint.port
            or endpoint.path != "/v1" or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password):
        raise FactoryError("Featherless request guard must own a credential-free loopback endpoint")
    return route.guard.url


@asynccontextmanager
async def _featherless_runtime(config):
    options = config["runtime"].get("featherless_budget_guard")
    if options is None:
        yield None
        return
    from .featherless_guard import FeatherlessGuard, GuardError
    models = _featherless_metadata(config)
    ledger = Path(options["ledger"])
    runs = Path(config["paths"]["runs"]).resolve()
    if any(item.is_symlink() for item in (ledger, *ledger.parents)
           if item.resolve() == runs or runs in item.resolve().parents):
        raise FactoryError("Featherless request ledger must not use symlinks")
    key = os.environ.get(config["runtime"]["opencode_provider"]["api_key_env"])
    if not key:
        raise FactoryError("The configured Featherless provider credential environment variable is unset")
    kwargs = {name: options[name] for name in ("approved_credit_nano_usd", "max_total_tokens",
                                             "overall_timeout_seconds", "request_timeout_seconds")}
    if "time_renewal" in options:
        kwargs["time_renewal"] = options["time_renewal"]
    if "balance_only" in options:
        kwargs["balance_only"] = options["balance_only"]
    try:
        async with FeatherlessGuard(api_key=key, models=models, ledger_path=ledger, **kwargs) as guard:
            if guard.verification().get("stopped_reason") is not None:
                raise FactoryError("Featherless request guard is persistently stopped; reconcile its preserved ledger before starting seats")
            route = _OwnedFeatherlessRoute(guard)
            _guard_endpoint(config, route)
            yield route
    except GuardError as exc:
        raise FactoryError(f"Featherless request guard: {exc}") from None


def permission_errors(config):
    if selected_harness(config) == "codex":
        return []
    runtime = config["runtime"]
    policy = runtime.get("native_permissions")
    errors = []
    if not isinstance(policy, dict) or set(policy) != _PERMISSIONS or any(type(v) is not bool for v in policy.values()):
        errors.append("Alternate harnesses require runtime.native_permissions with explicit read/write/bash/network booleans")
    for key in ("permission_profile", "sandbox_policy", "docker_host"):
        if runtime.get(key) is not None:
            errors.append(f"runtime.{key} is Codex-specific; it cannot grant alternate harness permissions")
    if runtime.get("sandbox") not in (None, "native-policy"):
        errors.append("Alternate harnesses require sandbox: native-policy; Codex sandbox settings do not transfer")
    return errors


def _policy(config):
    errors = permission_errors(config)
    if errors:
        raise FactoryError("; ".join(errors))
    return dict(config["runtime"]["native_permissions"])


def _inside(workspace, raw):
    if not isinstance(raw, str) or not raw:
        return False
    path = Path(raw).expanduser()
    path = (path if path.is_absolute() else workspace / path).resolve()
    return path.is_relative_to(workspace)


def native_tool_allowed(policy, workspace, name, arguments):
    """Constrain direct filesystem tools. Shell permission is explicit trust."""
    workspace = Path(workspace).resolve()
    if name in _READ_TOOLS:
        if name == "Glob":
            pattern = arguments.get("pattern", "")
            if not isinstance(pattern, str) or Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                return False
        key = "file_path" if name == "Read" else "path"
        return policy["read"] and _inside(workspace, arguments.get(key, str(workspace)))
    if name in _WRITE_TOOLS:
        key = "notebook_path" if name == "NotebookEdit" else "file_path"
        return policy["write"] and _inside(workspace, arguments.get(key))
    if name == "Bash":
        return policy["bash"]
    if name in _NETWORK_TOOLS:
        return policy["network"]
    return False


@lru_cache(maxsize=1)
def _claude_types():
    from band.adapters import ClaudeSDKAdapter, ClaudeSDKAdapterConfig
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    from pydantic import Field

    class FactoryClaudeConfig(ClaudeSDKAdapterConfig):
        factory_native_permissions: dict[str, bool] = Field(exclude=True)

    class FactoryClaudeAdapter(ClaudeSDKAdapter):
        async def _resolve_tool_permission(self, room_id, tool_name, tool_input, context):
            # The maintained adapter's PreToolUse hook routes every native tool
            # here. Its BAND MCP calls still go through audited room tools.
            if native_tool_allowed(self.config.factory_native_permissions, self.config.cwd,
                                   tool_name, tool_input):
                return PermissionResultAllow(updated_input=tool_input)
            return PermissionResultDeny(message="Tool denied by the factory's explicit native permission policy")

    return FactoryClaudeConfig, FactoryClaudeAdapter


def _opencode_worktree(workspace):
    workspace = Path(workspace).resolve()
    # Pinned OpenCode 1.18.34 uses the Git checkout root, or '/' for its
    # non-VCS global project. The owned server readback verifies this assumption.
    return next((path for path in (workspace, *workspace.parents) if (path / ".git").exists()), Path("/"))


def _opencode_input_roots(config):
    """Public input directories only; never grant the factory/config/runs root."""
    paths = config.get("paths", {})
    candidates = []
    if paths.get("factory"):
        candidates.extend(Path(paths["factory"]) / name for name in ("mandates", "protocols", "docs", "templates"))
    if paths.get("challenge"):
        candidates.extend(Path(paths["challenge"]) / name for name in ("toy/spec", "tablekeeper/spec", "harness"))
    candidates.extend(Path(seat["mandate"]).parent for seat in config.get("seats", [])
                      if seat.get("mandate") and Path(seat["mandate"]).parent.name == "mandates")
    roots = []
    for root in sorted(set(candidates)):
        if not root.exists():
            continue
        if (not root.is_absolute() or not root.is_dir() or any(char in str(root) for char in "*?")
                or any(path.is_symlink() for path in (root, *root.parents))):
            raise FactoryError("OpenCode public input directory must be absolute and free of symbolic links")
        for path in root.rglob("*"):
            if (path.is_symlink() or path.name == ".git" or path.name.startswith(".env")
                    or path.name in ("auth.json", "credentials.json", "credentials.yaml")
                    or path.suffix in (".pem", ".key")):
                raise FactoryError("OpenCode public input directory contains a private file or symbolic link")
        roots.append(root)
    return roots


def opencode_permissions(policy, workspace=None, read_roots=()):
    permissions = {"*": "deny", "external_directory": "deny", "doom_loop": "deny",
                   "task": "deny", "skill": "deny", "question": "deny"}
    for key in _OPEN_READ:
        permissions[key] = "allow" if policy["read"] else "deny"
    permissions["edit"] = "allow" if policy["write"] else "deny"
    permissions["bash"] = "allow" if policy["bash"] else "deny"
    for key in _OPEN_NETWORK:
        permissions[key] = "allow" if policy["network"] else "deny"
    permissions["band_*"] = "allow"
    if workspace is not None and read_roots:
        workspace = Path(workspace).resolve()
        if any(char in str(workspace) for char in "*?"):
            raise FactoryError("OpenCode workspace must not contain permission wildcard characters")
        worktree = _opencode_worktree(workspace)

        def patterns(path):
            relative = os.path.relpath(path, worktree).replace(os.sep, "/")
            return ["*"] if relative == "." else [relative, relative.rstrip("/") + "/*"]

        read = {"*": "deny"}
        edit = {"*": "deny"}
        if policy["read"]:
            read.update(dict.fromkeys(patterns(workspace), "allow"))
        if policy["write"]:
            edit.update(dict.fromkeys(patterns(workspace), "allow"))
        # A workspace equal to its Git root uses '*'; outside paths still deny.
        for mapping in (read, edit):
            mapping.update({"../*": "deny", "/*": "deny", "*/../*": "deny"})
        external = {"*": "deny"}
        for root in read_roots:
            if policy["read"]:
                external[str(root / "*")] = "allow"
                read.update(dict.fromkeys(patterns(root), "allow"))
            edit.update(dict.fromkeys(patterns(root), "deny"))
        permissions.update(read=read, edit=edit, external_directory=external)
    return permissions


@lru_cache(maxsize=1)
def _opencode_types():
    from dataclasses import replace
    from band.adapters import OpencodeAdapter, OpencodeAdapterConfig
    from band.core.tool_filter import filter_tool_schemas
    from band.integrations.opencode.client import HttpOpencodeClient
    from band.runtime.custom_tools import custom_tool_effects, get_custom_tool_name, invoke_validated_custom_tool
    from band.runtime.tools.registry import TOOL_DEFINITIONS, get_band_tool_category
    from pydantic import Field, SecretStr
    import httpx

    class FactoryAddParticipantInput(TOOL_DEFINITIONS["band_add_participant"].input_model):
        """Restore an absent, already configured factory teammate to this room.

        PM only: first confirm absence with band_get_participants, then pass the
        teammate's exact UUID from the configured roster and role="member".
        Do not search for or recruit other identities. After an uncertain add,
        read the roster again instead of blindly repeating the mutation.
        """
        identifier: str = Field(description="Exact UUID of the absent teammate from the configured factory roster.")

    class FilteredRoomTools:
        """Keep maintained MCP's direct method dispatch inside the current policy."""
        def __init__(self, adapter, tools, room_id):
            self._adapter = adapter
            self._tools = tools
            self._room_id = room_id

        def __getattr__(self, name):
            # These attributes are used only to enrich send-message errors.
            if name in {"participants", "agent_id"}:
                return getattr(self._tools, name)

            async def invoke(**arguments):
                tool_name = self._adapter._factory_allowed_methods.get(name)
                if tool_name is None:
                    raise ValueError("Factory tool policy denies this MCP operation")
                authorize = getattr(self._tools, "authorize_mcp_tool", None)
                if authorize is not None:
                    authorize(tool_name, arguments)
                turn = (self._adapter._no_reply_turn(self._room_id, self._tools)
                        if tool_name == "band_no_reply" else None)
                result = await getattr(self._tools, name)(**arguments)
                if turn is not None:
                    await self._adapter._end_no_reply(self._room_id, self._tools, turn, result)
                return result
            return invoke

    class FactoryOpencodeConfig(OpencodeAdapterConfig):
        turn_timeout_s: float | None = 300.0
        factory_native_permissions: dict[str, bool] = Field(exclude=True)
        factory_http_password: SecretStr = Field(exclude=True)
        factory_request_ledger_path: Path | None = Field(default=None, exclude=True)

    class FactoryOpencodeAdapter(OpencodeAdapter):
        async def _await_turn(self, turn):
            if self.config.turn_timeout_s is None:
                # Preserve native completion/cancellation without an artificial
                # execution clock. The owned dollar guard remains authoritative.
                await asyncio.shield(turn.turn_future)
            else:
                await super()._await_turn(turn)
            # Native idle remains the completion authority. If it raced the
            # abort HTTP response, do not release the callback before that
            # response confirms termination of this exact native prompt.
            quiet_task = getattr(turn, "_factory_no_reply_task", None)
            if quiet_task is not None:
                try:
                    await asyncio.shield(quiet_task)
                except Exception:
                    # The MCP invocation still raises. Its captured failure is
                    # already attached to this turn; allow the normal watcher
                    # to report that failure and all captured paid usage.
                    if not turn.last_error_message or turn.tools.terminal_status != "failed":
                        raise

        def _no_reply_turn(self, room_id, tools):
            state = self._rooms.get(room_id)
            turn = state.turn if state is not None else None
            if (turn is None or state.tools is not tools or turn.tools is not tools
                    or turn.turn_future.done() or turn.client is not self._client):
                raise ValueError("No reply requires the exact active room turn")
            return turn

        async def _wait_no_reply_settlement(self, room_id, tools, turn):
            path = self.config.factory_request_ledger_path
            if path is None:
                return  # Other providers have no factory request ledger.
            import stat
            from .featherless_guard import _Ledger
            if not path.is_absolute() or any(parent.is_symlink() for parent in (path, *path.parents)):
                raise ValueError("No reply requires the configured private request ledger")
            while True:
                if self._no_reply_turn(room_id, tools) is not turn:
                    raise ValueError("No reply turn changed while awaiting provider usage")
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "r") as stream:
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                            or info.st_size > 16 * 1024 * 1024):
                        raise ValueError("No reply request ledger is unsafe")
                    data = json.load(stream)
                checked = _Ledger(path, data["policy"])
                checked.data = data
                checked._validate()  # Read only; never lock, save, settle, or fabricate usage.
                if any(item["status"] == "unknown" for item in data["requests"].values()):
                    raise ValueError("No reply cannot conceal unknown provider usage")
                if not any(item["status"] == "in_flight" for item in data["requests"].values()):
                    return
                # A streamed tool call can precede final usage. Withhold its
                # result until the existing guard has durably settled the stream;
                # aborting earlier would convert paid usage into an unknown hold.
                await asyncio.sleep(0.01)

        async def _end_no_reply(self, room_id, tools, turn, result):
            if result != {"status": "no_reply"}:
                raise ValueError("No reply did not complete successfully")
            if self._no_reply_turn(room_id, tools) is not turn:
                raise ValueError("No reply turn changed before termination")
            if turn.last_error_message:
                raise ValueError("No reply cannot conceal a native failure")
            quiet_task = getattr(turn, "_factory_no_reply_task", None)
            if quiet_task is None:
                async def terminate():
                    try:
                        # The MCP result is still withheld: OpenCode cannot
                        # continue its model/tool loop from this successful
                        # no_reply until its active prompt has been aborted.
                        await self._wait_no_reply_settlement(room_id, tools, turn)
                        if self._no_reply_turn(room_id, tools) is not turn:
                            raise ValueError("No reply turn changed before native abort")
                        turn._factory_no_reply_abort_requested = True
                        await turn.client.abort_session(turn.session_id)
                    except Exception:
                        turn.tools.terminal_status = "failed"
                        ledger = getattr(turn.tools, "ledger", None)
                        if ledger is not None:
                            ledger.stop.set()  # Native termination failed; do not admit a successor.
                        self._fail_turn_for_owner(lambda: turn, turn.last_error_message or "OpenCode no_reply termination failed")
                        raise
                    turn._factory_no_reply_confirmed = True
                quiet_task = asyncio.create_task(terminate())
                turn._factory_no_reply_task = quiet_task
            # A native abort can cancel its outstanding MCP HTTP request.
            # Preserve the captured abort task through that transport cancel;
            # the normal watcher still requires real idle and reports usage.
            await asyncio.shield(quiet_task)

        def _apply_message_update(self, room_state, info):
            turn = room_state.turn
            if (turn is not None and getattr(turn, "_factory_no_reply_abort_requested", False)
                    and info is not None and info.role == "assistant"
                    and info.error is not None and info.error.name == "MessageAbortedError"):
                info = info.model_copy(update={"error": None})
            super()._apply_message_update(room_state, info)

        async def _handle_event(self, event):
            from band.integrations.opencode import SessionErrorEvent
            if isinstance(event, SessionErrorEvent):
                state = await self._room_state_for_session(event.session_id)
                turn = state.turn if state is not None else None
                if (turn is not None and getattr(turn, "_factory_no_reply_abort_requested", False)
                        and event.properties.error is not None
                        and event.properties.error.name == "MessageAbortedError"):
                    # This exact self-requested native interruption is quiet.
                    # It is not idle and must not resolve the native future.
                    return
            await super()._handle_event(event)

        def _refresh_tool_definitions(self):
            # BAND 4.0.0 filters capabilities here, but omits feature include,
            # exclude and category filters. Reuse its shared filter for both
            # built-in and custom definitions before creating the MCP backend.
            if not hasattr(self, "_factory_custom_tools"):
                self._factory_custom_tools = tuple(self._custom_tools)
            super()._refresh_tool_definitions()
            entries = [(definition.name, definition) for definition in self._tool_definitions]
            entries.extend((get_custom_tool_name(model), (model, handler))
                           for model, handler in self._factory_custom_tools)
            selected = filter_tool_schemas(entries, self.features, get_name=lambda entry: entry[0],
                                           get_category=lambda entry: get_band_tool_category(entry[0]))
            self._own_tool_names = frozenset(name for name, _ in selected)
            self._tool_definitions = []
            self._custom_tools = []
            for name, definition in selected:
                if isinstance(definition, tuple):
                    self._custom_tools.append(self._guard_custom_tool(name, definition))
                else:
                    if name == "band_add_participant":
                        definition = replace(definition, input_model=FactoryAddParticipantInput)
                    self._tool_definitions.append(definition)
            self._factory_allowed_methods = {d.method_name: d.name for d in self._tool_definitions}
            self._custom_effects = custom_tool_effects(
                tool for tool in self._factory_custom_tools if get_custom_tool_name(tool[0]) in self._own_tool_names)

        def _guard_custom_tool(self, name, definition):
            async def invoke(validated):
                if name not in self._own_tool_names:
                    raise ValueError("Factory tool policy denies this MCP operation")
                return await invoke_validated_custom_tool(definition, validated)
            return definition[0], invoke

        def _get_room_tools(self, room_id):
            tools = super()._get_room_tools(room_id)
            return FilteredRoomTools(self, tools, room_id) if tools is not None else None

        async def _deliver_fallback_text(self, room_id, turn):
            # BAND 4.0.0 can prioritize text over a simultaneous provider error.
            # Preserve failure locally before any best-effort room reporting.
            if turn.last_error_message:
                turn.tools.terminal_status = "failed"
                from band_sdk_core import AgentFailure
                await turn.tools.send_failure(AgentFailure("opencode", turn.last_error_message))
                return
            if getattr(turn, "_factory_no_reply_confirmed", False):
                # Deliberate silence ends this callback, not a work item or
                # stage. Keep native text private and let the SDK report usage.
                turn.replied_via_room_tool = True
                return
            if not turn.replied_via_room_tool and not any(text.strip() for text in turn.text_parts.values()):
                turn.tools.terminal_status = "failed"
                from band_sdk_core import AgentFailure
                await turn.tools.send_failure(AgentFailure("opencode", "OpenCode completed without a reply"))
                return
            await super()._deliver_fallback_text(room_id, turn)

        async def _report_delivery_failure(self, room_id, turn):
            turn.tools.terminal_status = "failed"
            try:
                await turn.tools.send_event("OpenCode result delivery failed", "error", metadata={
                    "factory_event_type": "turn_lifecycle", "factory_turn_status": "failed"})
            except Exception:
                pass  # The local failure marker survives a failed audit send.

        def _default_client_factory(self, config):
            client = HttpOpencodeClient(base_url=config.base_url, directory=config.directory,
                                       workspace=config.workspace, timeout_s=config.turn_timeout_s)
            # BAND 4.0.0's transport has no public auth argument. Keep the small
            # bridge here, tested against its real HTTP request construction.
            client._client.auth = httpx.BasicAuth("opencode", config.factory_http_password.get_secret_value())
            return client

        def _mcp_tool_visibility(self):
            # OpenCode converts per-turn tool booleans into late permission
            # rules. Keep this map MCP-only: a generic deny would override
            # external-directory grants, while native allows would bypass
            # the frozen server's path-scoped read/edit policy.
            visibility = {f"{self.config.mcp_server_name}_*": False,
                          f"{self._mcp_server_name}_*": False}
            visibility.update({f"{self._mcp_server_name}_{name}": True for name in sorted(self._own_tool_names)})
            return visibility

    return FactoryOpencodeConfig, FactoryOpencodeAdapter


def adapter_class(config):
    harness = selected_harness(config)
    if harness == "codex":
        from band.adapters import CodexAdapter
        return CodexAdapter
    return (_claude_types() if harness == "claude-code" else _opencode_types())[1]


def adapter_options(config):
    from band.core.types import Emit
    emit = [Emit.TOOL_CALLS, Emit.USAGE]
    if selected_harness(config) != "claude-code":
        emit.insert(1, Emit.TASK_EVENTS)
    return {"emit": emit}


def alternate_adapter_config(config, seat, mode, workspace, instructions, env):
    harness = selected_harness(config)
    policy = _policy(config)
    model = seat.get("model") or config["runtime"]["model"]
    timeout = None if config["budgets"].get("balance_only") is True else config["budgets"]["turn_timeout_seconds"]
    effort = seat.get("reasoning_effort") or None
    if harness == "claude-code":
        from band.adapters import ClaudeCLIOptions, ClaudeApprovalOptions
        cls, _ = _claude_types()
        return cls(model=model, effort=effort, cwd=Path(workspace), custom_section=instructions,
                   setting_sources=(), permission_mode="acceptEdits", turn_timeout_s=timeout,
                   approvals=ClaudeApprovalOptions(mode="auto_decline", text_notifications=False,
                                                   wait_timeout_s=30 if timeout is None else min(30, timeout / 2)),
                   cli=ClaudeCLIOptions(cli_path=config["runtime"]["claude_command"], env=env),
                   factory_native_permissions=policy)
    if harness != "opencode":
        raise FactoryError("alternate_adapter_config only accepts Claude Code or OpenCode")
    endpoint = config["runtime"].get("_opencode_endpoints", {}).get(seat["id"])
    if endpoint is None:
        raise FactoryError("OpenCode requires start_runtime's owned local server for this seat")
    provider, model_id = model.split("/", 1)
    cls, _ = _opencode_types()
    defaults = {name: field.get_default(call_default_factory=True) for name, field in cls.model_fields.items()
                if not field.is_required()}
    return cls(**(defaults | dict(base_url=endpoint["url"], directory=str(workspace), workspace=None,
               provider_id=provider, model_id=model_id, variant=effort, agent="factory",
               custom_section=instructions, approval_mode="auto_decline", question_mode="auto_reject",
               turn_timeout_s=timeout, factory_native_permissions=policy,
               factory_http_password=endpoint["password"],
               factory_request_ledger_path=config["runtime"].get("featherless_budget_guard", {}).get("ledger"))))


def _command(config):
    return config["runtime"][command_key(selected_harness(config))]


def auth_errors(config):
    """Check local authentication evidence without exposing account data."""
    harness = selected_harness(config)
    if harness == "codex":
        return []
    subscription = config.get("budgets", {}).get("billing_mode") == "subscription_only"
    if harness == "opencode":
        if subscription:
            return ["OpenCode cannot verify subscription-only billing; explicitly approve a provider-enforced spend cap"]
        # /provider.connected is checked at discovery/startup. Do not inspect,
        # copy, or infer validity from a token's presence on disk.
        return []
    try:
        result = subprocess.run([_command(config), "auth", "status", "--json"],
                                capture_output=True, text=True, timeout=15, check=False)
        status = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ["Claude Code local auth status could not be verified; run the selected CLI's auth login"]
    if result.returncode or status.get("loggedIn") is not True:
        return ["Claude Code is not logged in according to the selected CLI's auth status"]
    if subscription:
        overrides = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                     "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
        if any(os.environ.get(key) for key in overrides):
            return ["Claude subscription-only mode rejects API/provider environment overrides"]
        if status.get("authMethod") != "claude.ai" or not status.get("subscriptionType"):
            return ["Claude subscription-only mode requires verified claude.ai OAuth and subscription type"]
    return []


def model_errors(config, catalog):
    if catalog.get("harness") != selected_harness(config):
        return ["Model catalog belongs to a different or unknown harness; run discover-models for this profile"]
    rows = {row["id"]: row for row in catalog.get("models", []) if isinstance(row, dict) and row.get("id")}
    errors = []
    harness = selected_harness(config)
    for seat in config.get("seats", []):
        model = seat.get("model") or config["runtime"].get("model")
        row = rows.get(model)
        if row is None:
            errors.append(f"Seat {seat['id']} model {model!r} is absent from the selected harness catalog")
            continue
        effort = seat.get("reasoning_effort")
        supported = row.get("supportedReasoningEfforts", [])
        values = [entry.get("reasoningEffort") if isinstance(entry, dict) else entry for entry in supported]
        if effort and effort not in values:
            errors.append(f"Seat {seat['id']} effort/variant {effort!r} is not advertised for {model}")
        if harness == "opencode" and row.get("connected") is not True:
            errors.append(f"Seat {seat['id']} OpenCode provider is not connected; use opencode auth login")
    return errors


def _opencode_environment(config, policy, model, extra_env, state_paths=None, *, guarded_route=None, workspace=None):
    roots = _opencode_input_roots(config) if workspace is not None else ()
    permissions = opencode_permissions(policy, workspace, roots)
    guarded_endpoint = _guard_endpoint(config, guarded_route)
    provider_id, _, _ = model.partition("/")
    generated = {"permission": permissions, "share": "disabled", "autoupdate": False,
                 "model": model, "small_model": model, "default_agent": "factory",
                 "enabled_providers": [provider_id],
                 "agent": {"factory": {"description": "Factory seat", "mode": "primary",
                                         "model": model, "permission": permissions}}, "plugin": []}
    for helper in ("title", "summary", "compaction"):
        generated["agent"][helper] = {"model": model}
    provider = config["runtime"].get("opencode_provider")
    if provider:
        key_env = "FACTORY_FEATHERLESS_GUARD_TOKEN" if guarded_endpoint else provider["api_key_env"]
        generated["provider"] = {provider["id"]: {
            "npm": provider["npm"], "options": {"baseURL": guarded_endpoint or provider["base_url"],
            "apiKey": "{env:" + key_env + "}"}, "models": deepcopy(provider["models"])}}
    # No ambient OpenCode configuration flags may replace a factory binding.
    # In isolated mode the only provider credential inherited is the explicit
    # named variable. Values never enter configuration, reports or argv.
    inherited = {key: value for key, value in os.environ.items() if not key.startswith("OPENCODE_")}
    if state_paths is not None:
        if provider:
            inherited = {key: value for key, value in inherited.items()
                         if not re.search(r"(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET|PASSWORD)$", key, re.I)
                         or key == provider["api_key_env"]}
        inherited.update({f"XDG_{name.upper()}_HOME": str(state_paths[name]) for name in ("config", "data", "cache", "state")})
        inherited["XDG_CONFIG_DIRS"] = str(state_paths["config"])
        inherited["OPENCODE_CONFIG_DIR"] = str(state_paths["config"] / "opencode")
        inherited["OPENCODE_CONFIG"] = str(state_paths["config"] / "opencode" / "factory.json")
        inherited["OPENCODE_DISABLE_PROJECT_CONFIG"] = "true"
    if provider and provider["api_key_env"] in os.environ:
        inherited[provider["api_key_env"]] = os.environ[provider["api_key_env"]]
    environment = {**inherited, **extra_env, "OPENCODE_CONFIG_CONTENT": json.dumps(generated),
                   "OPENCODE_PERMISSION": json.dumps(permissions), "OPENCODE_DISABLE_AUTOUPDATE": "true",
                   "OPENCODE_DISABLE_MODELS_FETCH": "true", "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
                   "OPENCODE_DISABLE_CLAUDE_CODE": "true", "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true",
                   "OPENCODE_DISABLE_PRUNE": "true"}
    if guarded_endpoint:
        # The real provider key belongs only to the proxy in this process.
        # Remove aliases holding the same value as well as the declared name.
        real_key = os.environ.get(provider["api_key_env"])
        environment = {key: value for key, value in environment.items()
                       if key != provider["api_key_env"] and (not real_key or value != real_key)}
        environment["FACTORY_FEATHERLESS_GUARD_TOKEN"] = guarded_route.guard.token
    if state_paths is not None:
        # This private fixed file prevents fallback to an ambient config path.
        # The provider key remains an environment placeholder on disk.
        destination = Path(environment["OPENCODE_CONFIG"])
        if destination.is_symlink():
            raise FactoryError("OpenCode isolated config file must not be a symlink")
        with destination.open("w") as stream:
            os.chmod(destination, 0o600)
            json.dump(generated, stream)
    return environment, permissions


def _opencode_state(config, state_id):
    root = config["runtime"].get("opencode_state_root")
    if root is None:
        return None
    if errors := opencode_runtime_errors(config):
        raise FactoryError("; ".join(errors))
    root = Path(root)
    if root.is_symlink():
        raise FactoryError("OpenCode state root must not be a symlink")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root = root.resolve()
    def private(path):
        if path.is_symlink():
            raise FactoryError("OpenCode isolated state must not contain directory symlinks")
        path.mkdir(mode=0o700, exist_ok=True)
        status = path.stat()
        if not path.is_dir() or status.st_uid != os.getuid() or status.st_mode & 0o077:
            raise FactoryError("OpenCode isolated state directories must be owner-only (0700)")
    private(root)
    if state_id is None:
        directory = Path(tempfile.mkdtemp(prefix="discovery-", dir=root))
    else:
        if not re.fullmatch(r"(?:rehearsal|judged)-[a-z][a-z0-9_-]*", state_id):
            raise FactoryError("OpenCode seat state requires an exact mode-seat identifier")
        directory = root / state_id
        private(directory)
    paths = {name: directory / name for name in ("config", "data", "cache", "state")}
    for path in paths.values():
        private(path)
    private(paths["config"] / "opencode")
    return paths


def _verified_opencode_command(config, environment):
    """Read a pinned executable and --version; never initialize a model turn."""
    from .startup_telemetry import record_startup
    record_startup("opencode_binary_check_begin")
    runtime = config["runtime"]
    expected_hash, expected_version = runtime.get("opencode_sha256"), runtime.get("opencode_version")
    if expected_hash is None and expected_version is None:
        return _command(config), {}
    if errors := opencode_runtime_errors(config):
        raise FactoryError("; ".join(errors))
    path = Path(_command(config)).resolve()
    try:
        if not path.is_file() or not os.access(path, os.X_OK):
            raise OSError
        with path.open("rb") as stream:
            observed_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError:
        raise FactoryError("Pinned OpenCode executable cannot be read or executed") from None
    if expected_hash is not None and observed_hash != expected_hash:
        raise FactoryError("OpenCode executable SHA-256 differs from the approved pin")
    if expected_version is not None:
        try:
            result = subprocess.run([str(path), "--version"], env=environment, capture_output=True,
                                    text=True, timeout=10, check=False)
            if result.returncode or result.stdout.strip() != expected_version:
                raise FactoryError("OpenCode CLI version differs from the approved pin")
        except (OSError, subprocess.SubprocessError):
            raise FactoryError("Pinned OpenCode CLI version could not be verified") from None
        record_startup("opencode_version_verified")
    # A CLI update during verification must not launch under the previous hash.
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != observed_hash:
            raise FactoryError("OpenCode executable changed during startup verification")
    record_startup("opencode_binary_verified")
    return str(path), {"executable_sha256": observed_hash, "cli_version": expected_version}


async def _terminate(process):
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), 5)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()


def _verify_opencode_routing(config, model, effective, agents, catalog, *, guarded_route=None):
    """Inspect only route metadata. Never return or interpolate provider options."""
    provider_id, _, model_id = model.partition("/")
    factory = effective.get("agent", {}).get("factory", {})
    if (effective.get("model") != model or effective.get("small_model") != model
            or effective.get("default_agent") != "factory" or factory.get("model") != model):
        raise FactoryError("OpenCode effective main/small/factory-agent model routing differs from the selected seat")
    if effective.get("enabled_providers") != [provider_id]:
        raise FactoryError("OpenCode enabled providers differ from the single approved provider; fallback is forbidden")
    selected = [agent for agent in agents if isinstance(agent, dict) and agent.get("name") == "factory"] if isinstance(agents, list) else []
    if len(selected) != 1 or selected[0].get("model") != {"providerID": provider_id, "modelID": model_id}:
        raise FactoryError("OpenCode resolved factory-agent model differs from the selected seat")
    if any(agent.get("model") not in (None, {"providerID": provider_id, "modelID": model_id})
           for agent in agents if isinstance(agent, dict)):
        raise FactoryError("OpenCode resolved helper-agent model permits an alternate route or fallback")
    providers = catalog.get("all", [])
    if (not isinstance(providers, list) or len(providers) != 1 or not isinstance(providers[0], dict)
            or providers[0].get("id") != provider_id):
        raise FactoryError("OpenCode resolved provider inventory permits an unexpected provider or fallback")
    provider = providers[0]
    connected = catalog.get("connected", [])
    if not isinstance(connected, list) or set(connected) != {provider_id}:
        raise FactoryError("OpenCode selected provider authentication is not verified; no fallback is allowed")
    resolved = provider.get("models", {}).get(model_id)
    if (not isinstance(resolved, dict) or resolved.get("id") != model_id
            or resolved.get("providerID") != provider_id or resolved.get("api", {}).get("id") != model_id):
        raise FactoryError("OpenCode resolved model does not retain the selected upstream provider/model ID")
    pin = config["runtime"].get("opencode_provider")
    guarded_endpoint = _guard_endpoint(config, guarded_route)
    verified = {"main_model": model, "small_model": model, "provider": provider_id, "upstream_model": model_id}
    if pin is not None:
        actual = effective.get("provider", {}).get(provider_id, {})
        options = actual.get("options", {})
        api = resolved.get("api", {})
        endpoint = guarded_endpoint or pin["base_url"]
        if (actual.get("npm") != pin["npm"] or options.get("baseURL") != endpoint
                or provider.get("options", {}).get("baseURL") != endpoint
                or api.get("npm") != pin["npm"] or api.get("url") not in ("", endpoint)):
            raise FactoryError("OpenCode effective provider endpoint/package routing differs from the approved binding")
        if guarded_endpoint and any(value.get("options", {}).get("apiKey") != guarded_route.guard.token
                                    for value in (actual, provider)):
            raise FactoryError("OpenCode effective provider credential must belong to the owned request guard")
        expected = pin["models"][model_id]["limit"]
        configured = actual.get("models", {}).get(model_id, {}).get("limit", {})
        observed = resolved.get("limit", {})
        if any(configured.get(key) != expected[key] or observed.get(key) != expected[key] for key in ("context", "output")):
            raise FactoryError("OpenCode configured/resolved model limits differ from the authenticated metadata")
        verified.update(base_url=pin["base_url"], npm=pin["npm"],
                        limits={key: observed[key] for key in ("context", "output")})
        if guarded_endpoint:
            verified["request_guard"] = guarded_route.guard.verification()
    return verified


async def _verify_server(client, process, permissions, config, model, *, guarded_route=None, workspace=None):
    import httpx
    from .startup_telemetry import record_startup
    try:
        async with asyncio.timeout(OPENCODE_READY_TIMEOUT_SECONDS):
            while True:
                if process.returncode is not None:
                    raise FactoryError("Owned OpenCode server exited before readiness; inspect CLI/config compatibility")
                try:
                    response = await client.get("/global/health")
                    response.raise_for_status()
                    version = config["runtime"].get("opencode_version")
                    if version is not None:
                        health = response.json()
                        if not isinstance(health, dict) or health.get("healthy") is not True or health.get("version") != version:
                            raise FactoryError("OpenCode server health/version differs from the approved CLI pin")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.1)
            response = await client.get("/config")
            response.raise_for_status()
            effective = response.json()
            if effective.get("agent", {}).get("factory", {}).get("permission") != permissions:
                raise FactoryError("OpenCode effective factory-agent permission policy differs from the approved profile")
            if effective.get("share") != "disabled":
                raise FactoryError("OpenCode effective configuration did not disable sharing")
            if effective.get("permission") != permissions:
                raise FactoryError("OpenCode global permission policy differs from the approved profile")
            if workspace is not None and isinstance(permissions.get("external_directory"), dict):
                response = await client.get("/path")
                response.raise_for_status()
                native_paths = response.json()
                if (native_paths.get("worktree") != str(_opencode_worktree(workspace))
                        or native_paths.get("directory") != str(Path(workspace).resolve())):
                    raise FactoryError("OpenCode native paths differ from the scoped input permission policy")
            response = await client.get("/agent")
            response.raise_for_status()
            agents = response.json()
            response = await client.get("/provider")
            response.raise_for_status()
            verified = _verify_opencode_routing(config, model, effective, agents, response.json(), guarded_route=guarded_route)
            if config["runtime"].get("opencode_version") is not None:
                verified["server_version"] = config["runtime"]["opencode_version"]
            record_startup("opencode_route_verified")
            return verified
    except TimeoutError:
        raise FactoryError(f"Owned OpenCode server readiness exceeded {OPENCODE_READY_TIMEOUT_SECONDS:g} seconds") from None


@asynccontextmanager
async def _opencode_server(config, workspace, model, policy, extra_env, *, state_id=None, guarded_route=None):
    import httpx
    from .startup_telemetry import record_startup
    record_startup("opencode_server_begin")
    from pydantic import SecretStr
    if errors := opencode_runtime_errors(config):
        raise FactoryError("; ".join(errors))
    _guard_endpoint(config, guarded_route)
    provider = config["runtime"].get("opencode_provider")
    if provider and not os.environ.get(provider["api_key_env"]):
        raise FactoryError("The configured OpenCode provider credential environment variable is unset")
    state_paths = _opencode_state(config, state_id)
    environment, permissions = _opencode_environment(config, policy, model, extra_env, state_paths,
                                                     guarded_route=guarded_route, workspace=workspace)
    command, binary = _verified_opencode_command(config, environment)
    password = secrets.token_urlsafe(32)
    environment.update(OPENCODE_SERVER_PASSWORD=password, OPENCODE_SERVER_USERNAME="opencode")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    endpoint = {"url": f"http://127.0.0.1:{port}", "password": SecretStr(password)}
    process = await asyncio.create_subprocess_exec(command, "serve", "--pure", "--hostname", "127.0.0.1",
                  "--port", str(port), cwd=str(workspace), env=environment,
                  stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        # Logging is inside process ownership so a storage failure still stops it.
        record_startup("opencode_process_started")
        async with httpx.AsyncClient(base_url=endpoint["url"], auth=("opencode", password), timeout=5) as client:
            routing = await _verify_server(client, process, permissions, config, model,
                                           guarded_route=guarded_route, workspace=workspace)
            endpoint["verification"] = {**binary, **routing}
            yield endpoint, client
    finally:
        await _terminate(process)


@asynccontextmanager
async def start_runtime(config, mode, seats):
    """Own only servers created here; never attach to/stop an ambient server."""
    if selected_harness(config) != "opencode":
        yield config
        return
    from contextlib import AsyncExitStack
    from .runtime import room_workspace
    from .startup_telemetry import record_startup, startup_seat
    policy = _policy(config)
    live_config = deepcopy(config)
    endpoints = live_config["runtime"]["_opencode_endpoints"] = {}
    async with AsyncExitStack() as stack:
        guarded_route = await stack.enter_async_context(_featherless_runtime(config))
        for seat in seats:
            workspace = room_workspace(config, seat, mode)
            if not workspace.is_dir():
                raise FactoryError(f"Seat {seat['id']} workspace is missing")
            environment = {"GIT_AUTHOR_NAME": seat["git_name"], "GIT_COMMITTER_NAME": seat["git_name"],
                           "GIT_AUTHOR_EMAIL": seat["git_email"], "GIT_COMMITTER_EMAIL": seat["git_email"]}
            with startup_seat(seat["id"]):
                endpoint, client = await stack.enter_async_context(_opencode_server(config, workspace,
                        seat.get("model") or config["runtime"]["model"], policy, environment,
                        state_id=f"{mode}-{seat['id']}", guarded_route=guarded_route))
                catalog = await _opencode_catalog(client)
                errors = model_errors({**config, "seats": [seat]}, catalog)
                if errors:
                    raise FactoryError("; ".join(errors))
                endpoints[seat["id"]] = endpoint
                record_startup("opencode_seat_ready")
        record_startup("opencode_all_servers_ready")
        yield live_config


async def _opencode_catalog(client):
    response = await client.get("/provider")
    response.raise_for_status()
    payload = response.json()
    connected = set(payload.get("connected", []))
    rows = []
    for provider in payload.get("all", []):
        for model_id, model in provider.get("models", {}).items():
            rows.append({"id": provider["id"] + "/" + model_id,
                         "supportedReasoningEfforts": [{"reasoningEffort": v} for v in model.get("variants", {})],
                         "connected": provider["id"] in connected})
    return {"harness": "opencode", "models": rows, "source": "owned OpenCode /provider catalog", "inference": False}


async def discover_models(config):
    """Bound no-inference catalog initialization; contexts still own cleanup."""
    try:
        async with asyncio.timeout(DISCOVERY_TIMEOUT_SECONDS):
            return await _discover_models(config)
    except TimeoutError:
        raise FactoryError(f"{harness_label(selected_harness(config))} model discovery exceeded {DISCOVERY_TIMEOUT_SECONDS:g} seconds") from None


async def _discover_models(config):
    harness = selected_harness(config)
    if harness == "opencode":
        # A private empty cwd avoids initializing project plugins/config while
        # reading the existing provider catalog. No sessions or prompts.
        with tempfile.TemporaryDirectory(prefix="factory-opencode-catalog-") as directory:
            policy = dict.fromkeys(_PERMISSIONS, False)
            async with _featherless_runtime(config) as guarded_route:
                async with _opencode_server(config, directory, config["runtime"].get("model", ""), policy, {},
                                            guarded_route=guarded_route) as (_, client):
                    return await _opencode_catalog(client)
    if harness != "claude-code":
        raise FactoryError("Use the Codex app-server model/list discovery for Codex")
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    options = ClaudeAgentOptions(cli_path=_command(config), setting_sources=[], tools=[],
                                permission_mode="dontAsk", extra_args={"disable-slash-commands": None})
    async with ClaudeSDKClient(options=options) as client:
        # Initialization only. Never call query() while discovering a catalog.
        info = await client.get_server_info() or {}
    rows = []
    for model in info.get("models", []):
        model_id = model.get("value") or model.get("id")
        if not model_id:
            continue
        levels = model.get("supportedEffortLevels", [])
        rows.append({"id": model_id, "supportedReasoningEfforts": [{"reasoningEffort": v} for v in levels]})
    if not rows:
        raise FactoryError("Claude Code initialization did not expose a model catalog; update the selected CLI/SDK")
    return {"harness": harness, "models": rows, "source": "Claude Code initialization catalog", "inference": False}
