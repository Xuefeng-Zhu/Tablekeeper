"""Offline, non-destructive harness selection with optional per-seat models."""
from __future__ import annotations

import copy
import ctypes
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile

import yaml

from .common import FactoryError, artifact_path, canonical, contains_secret, digest, source_lock, utc_now, write_json
from .tasks import render_packet, verify_tasks

HARNESSES = ("codex", "claude-code", "opencode")
EXECUTABLES = {"codex": "codex", "claude-code": "claude", "opencode": "opencode"}


def runtime_options(config: dict) -> dict:
    from .harnesses import command_key, harness_label
    return {"status": "PASS", "inference_started": False, "harnesses": [
        {"id": name, "label": harness_label(name),
         "command": config["runtime"].get(command_key(name)) or shutil.which(EXECUTABLES[name]),
         "model_format": "provider/model" if name == "opencode" else "exact model identifier",
         "selection_option": "--variant" if name == "opencode" else "--reasoning-effort"}
        for name in HARNESSES]}


def runtime_show(config: dict) -> dict:
    from .harnesses import command_key, selected_harness, validate_selection
    harness = selected_harness(config)
    return {"status": "PASS", "harness": harness, "model": config["runtime"].get("model"),
            "command": config["runtime"].get(command_key(harness)),
            "runs": config["paths"]["runs"], "budgets_approved": config["budgets"].get("approved") is True,
            "seats": [{key: seat.get(key) for key in ("id", "harness", "model", "reasoning_effort")}
                      for seat in config["seats"]],
            "selection_errors": validate_selection(config), "inference_started": False}


def _literal(value: str, label: str) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(character.isspace() or ord(character) < 32 for character in value)
            or any(character in value for character in "`$;|&<>\\\"'")
            or contains_secret(value)):
        raise FactoryError(f"{label} must be a nonempty literal identifier without whitespace or shell syntax")
    return value


def _seat_model_overrides(assignments: list[str]) -> dict[str, str]:
    overrides = {}
    for assignment in assignments:
        if assignment.count("=") != 1:
            raise FactoryError("--seat-model must use ROLE=MODEL")
        role, model = assignment.split("=", 1)
        _literal(role, "--seat-model role")
        _literal(model, "--seat-model model")
        if role in overrides:
            raise FactoryError(f"Duplicate --seat-model override for {role}")
        overrides[role] = model
    return overrides


def _output_path(config: dict, output: str | Path) -> Path:
    path = Path(output)
    if not path.is_absolute():
        raise FactoryError("runtime-select --output must be a new absolute directory")
    if path.exists() or path.is_symlink():
        raise FactoryError("runtime-select output already exists; choose a new directory (nothing is overwritten)")
    target = path.resolve()
    roots = [Path(value).resolve() for value in config["paths"].values()]
    for key in ("challenge", "factory", "result", "rehearsal"):
        root = Path(config["paths"][key]).resolve()
        if target == root or root in target.parents or target in root.parents:
            raise FactoryError(f"runtime-select output must not overlap paths.{key}")
    runs = Path(config["paths"]["runs"]).resolve()
    if target == runs or any(target in root.parents for root in roots):
        raise FactoryError("runtime-select output must not replace or contain an existing workspace")
    protected = [runs / name for name in ("runtime", "launch", "freeze", "readiness", "authorization")]
    protected += [Path(seat["mandate"]).resolve().parent for seat in config["seats"]]
    protected += [artifact_path(config, "tasks").resolve()]
    for root in protected:
        if target == root or root in target.parents or target in root.parents:
            raise FactoryError("runtime-select output must not overlap existing mandates, tasks or control evidence")
    credentials = Path(config["band"]["credentials_file"]).resolve()
    if target == credentials or target in credentials.parents:
        raise FactoryError("runtime-select output must not contain the credentials file")
    return target


def _assert_stopped(config: dict) -> None:
    from .runtime import is_owned
    owner = Path(config["paths"]["runs"]) / "runtime/owner.json"
    if not owner.exists() and not owner.is_symlink():
        return
    record = _json_record(owner, "source supervisor ownership")
    if (is_owned(record.get("parent", {}), record.get("token"))
            or any(is_owned(child) for child in record.get("children", []))):
        raise FactoryError("The source factory still owns live processes; stop it before taking a runtime profile snapshot")


def _json_record(path: Path, label: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise FactoryError(f"Cannot snapshot {label}: expected a regular file without symlinks")
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        raise FactoryError(f"Cannot snapshot {label}: inspect its malformed JSON locally") from None
    if not isinstance(value, dict):
        raise FactoryError(f"Cannot snapshot {label}: expected a JSON object")
    return value


def _guard_snapshot(config: dict) -> dict[str, tuple[Path, bytes]]:
    """Copy only a proven-unused guard; used credit cannot be forked safely."""
    guard = config.get("runtime", {}).get("featherless_budget_guard")
    if guard is None:
        return {}
    from .harnesses import _featherless_metadata
    from .featherless_guard import GuardError, _Ledger, _policy
    models = _featherless_metadata(config)
    root = Path(config["paths"]["runs"]).resolve()
    ledger_path = Path(guard["ledger"])
    for path in (ledger_path, Path(guard["model_metadata"])):
        if any(item.is_symlink() for item in (path, *path.parents)
               if item.resolve() == root or root in item.resolve().parents):
            raise FactoryError("Cannot snapshot Featherless guard files through symlinks")
    lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
    descriptor = None
    try:
        if lock_path.exists() or lock_path.is_symlink():
            if lock_path.is_symlink() or not lock_path.is_file():
                raise FactoryError("Cannot snapshot an unsafe Featherless guard ownership lock")
            descriptor = os.open(lock_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        data = _json_record(ledger_path, "Featherless request ledger")
        policy = _policy(models, guard["approved_credit_nano_usd"], guard["max_total_tokens"],
                         guard["overall_timeout_seconds"])
        snapshot = _Ledger(ledger_path, policy)
        snapshot.data = data
        snapshot._validate()  # Pure validation: never open/save/reset the source ledger.
        if data["requests"] or data.get("started_epoch") is not None or data.get("stopped_reason") is not None:
            raise FactoryError("A used or uncertain Featherless guard ledger cannot be cloned by runtime-select; "
                               "preserve it for separately reviewed transfer/reconciliation")
        metadata = Path(guard["model_metadata"])
        raw = metadata.read_bytes()
        if digest(raw) != guard["model_metadata_sha256"]:
            raise FactoryError("Featherless model metadata changed during profile preparation")
        return {"runtime/featherless-requests.json": (ledger_path, ledger_path.read_bytes()),
                "readiness/featherless-model-metadata.json": (metadata, raw)}
    except (OSError, GuardError):
        raise FactoryError("Cannot snapshot Featherless guard accounting: ledger is invalid, unavailable or owned") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _control_snapshot(config: dict) -> tuple[dict[str, tuple[Path, bytes]], bool]:
    """Carry accounting and duplicate-dispatch gates forward without approvals."""
    runs = Path(config["paths"]["runs"])
    copied = _guard_snapshot(config)
    guard_paths = {path.resolve() for path, _ in copied.values()}
    consumed = config.get("runtime_profile", {}).get("source_session_consumed", False) is True
    for directory in (runs / "runtime", runs / "launch", runs / "inherited-runtime"):
        if directory.is_symlink():
            raise FactoryError("Source runtime/launch evidence must not be a symlink")
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise FactoryError("Source runtime/launch evidence must be a directory")
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise FactoryError("Source runtime/launch evidence contains a symlink; preserve and inspect it before selection")
            if not path.is_file() or path.name.endswith((".lock", ".log")):
                continue
            if path.resolve() in guard_paths:
                continue
            relative = path.relative_to(runs)
            if directory.name == "runtime":
                if path.name == "owner.json" or path.name.startswith(("models", "registration")):
                    continue
                if path.parent == directory and path.name.startswith("budget-") and path.suffix == ".json":
                    budget = _json_record(path, "source budget ledger")
                    if not isinstance(budget.get("turns", {}), dict):
                        raise FactoryError("Source budget ledger turns must be a mapping; inspect it locally")
                    consumed |= (budget.get("started_epoch") is not None or bool(budget.get("tokens"))
                                 or any(budget.get("turns", {}).values()) or bool(budget.get("stopped_reason")))
                    destination = str(relative)
                else:
                    destination = str(Path("inherited-runtime") / path.relative_to(directory))
                    consumed |= path.name.endswith(".claim.json")
            elif directory.name == "inherited-runtime":
                destination = str(Path("inherited-runtime/previous") / path.relative_to(directory))
            else:
                destination = str(relative)
                if path.name == "ledger.json":
                    ledger = _json_record(path, "source dispatch ledger")
                    if not isinstance(ledger.get("entries"), list):
                        raise FactoryError("Source dispatch ledger must contain an entries list")
                    consumed |= bool(ledger["entries"])
            copied[destination] = (path, path.read_bytes())
    return copied, consumed


def _mandate(raw: str, harness: str, model: str) -> str:
    if contains_secret(raw):
        raise FactoryError("A source mandate may contain a credential; inspect it locally before selection")
    for field, value in (("Harness", harness), ("Model", model)):
        pattern = rf"(?im)^[-*_ \t]*{field}[*_ \t]*:[*_ \t]*[^\r\n]+$"
        raw, count = re.subn(pattern, lambda match: f"{field}: {value}", raw)
        if count != 1:
            raise FactoryError(f"Each source mandate must have exactly one anchored {field}: metadata line")
    return raw


def _rename_exclusive(source: Path, destination: Path) -> None:
    """Publish one complete directory atomically without replacing a raced target."""
    library = ctypes.CDLL(None, use_errno=True)
    if hasattr(library, "renamex_np"):
        rename = library.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(source), os.fsencode(destination), 4)  # Darwin RENAME_EXCL
    elif hasattr(library, "renameat2"):
        rename = library.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # RENAME_NOREPLACE
    else:
        raise FactoryError("This platform lacks atomic directory publication without overwriting; no profile was published")
    if result:
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise FactoryError("Runtime profile output appeared during preparation; nothing was overwritten")
        raise OSError(error, os.strerror(error))


def select_runtime(config: dict, *, harness: str, model: str, output: str | Path,
                   command: str | None = None, reasoning_effort: str | None = None,
                   variant: str | None = None, source_config: str | Path | None = None,
                   seat_models: dict[str, str] | None = None) -> dict:
    from .harnesses import command_key, harness_label, selected_harness, validate_selection
    if harness not in HARNESSES:
        raise FactoryError("Choose codex, claude-code or opencode")
    model = _literal(model, "--model")
    if seat_models is not None and not isinstance(seat_models, dict):
        raise FactoryError("Per-seat models must be a role-to-model mapping")
    overrides = {}
    roles = {seat["id"] for seat in config["seats"]}
    for role, override in (seat_models or {}).items():
        _literal(role, "--seat-model role")
        if role not in roles:
            raise FactoryError(f"Unknown --seat-model role {role}; choose one of {', '.join(sorted(roles))}")
        overrides[role] = _literal(override, "--seat-model model")
    if harness == "opencode" and any("/" not in value or not all(value.split("/", 1))
                                     for value in (model, *overrides.values())):
        raise FactoryError("OpenCode models must use provider/model")
    if variant is not None and harness != "opencode":
        raise FactoryError("--variant applies only to OpenCode; use --reasoning-effort for this harness")
    if reasoning_effort is not None and harness == "opencode":
        raise FactoryError("OpenCode uses --variant, not --reasoning-effort")
    effort = variant if harness == "opencode" else reasoning_effort
    if effort is not None:
        _literal(effort, "--variant" if harness == "opencode" else "--reasoning-effort")
    if contains_secret(yaml.safe_dump(config)):
        raise FactoryError("Source configuration may contain credentials; keep credentials outside repositories")
    target = _output_path(config, output)
    _assert_stopped(config)
    source_path = Path(source_config).resolve() if source_config is not None else None
    if source_path is not None and (not source_path.is_file() or source_path.is_symlink()):
        raise FactoryError("Source configuration must be an existing regular file")
    source_bytes = source_path.read_bytes() if source_path else None
    if source_bytes is not None:
        try:
            if yaml.safe_load(source_bytes) != config:
                raise FactoryError("Source configuration changed or differs from the loaded configuration; reload before selection")
        except yaml.YAMLError:
            raise FactoryError("Source configuration contains malformed YAML; inspect it locally") from None
    original_harness = selected_harness(config)
    runtime = config["runtime"]
    executable = command or runtime.get(command_key(harness)) or shutil.which(EXECUTABLES[harness])
    if not executable:
        raise FactoryError(f"{harness_label(harness)} is not installed on PATH; pass --command /absolute/path/to/{EXECUTABLES[harness]} for the intended installation")
    if not isinstance(executable, str) or not Path(executable).is_absolute():
        raise FactoryError("--command must be an absolute executable path")
    if any(character in executable for character in ("\n", "\r", "\0")):
        raise FactoryError("--command contains an invalid path character")
    locked = source_lock(config)
    lock_path = artifact_path(config, "source_lock")
    lock_bytes = lock_path.read_bytes()
    controls, consumed = _control_snapshot(config)
    prepared = copy.deepcopy(config)
    rt = prepared["runtime"]
    rt.update(harness=harness, model=model)
    rt[command_key(harness)] = executable
    if harness == "opencode" and "opencode_state_root" in rt:
        rt["opencode_state_root"] = str(target / "runtime/opencode")
    if "featherless_budget_guard" in rt:
        rt["featherless_budget_guard"].update(
            ledger=str(target / "runtime/featherless-requests.json"),
            model_metadata=str(target / "readiness/featherless-model-metadata.json"))
    if harness != original_harness:
        rt.pop("version", None)
    if harness == "codex":
        rt.pop("native_permissions", None)
        if harness != original_harness:
            rt["sandbox"] = "workspace-write"
        else:
            rt.setdefault("sandbox", "workspace-write")
        rt.update(approval_policy="never", approval_mode="auto_decline", allow_network=False)
    else:
        for field in ("sandbox", "sandbox_policy", "permission_profile", "allow_network", "docker_host"):
            rt.pop(field, None)
        rt["sandbox"] = "native-policy"
        rt["native_permissions"] = {"read": True, "write": False, "bash": False, "network": False}
    prepared["paths"]["runs"] = str(target)
    prepared["artifacts"] = {"source_lock": str(target / "source-lock.json"),
                             "tasks": str(target / "tasks"), "mandates": str(target / "mandates")}
    prepared["budgets"]["approved"] = False
    prepared["launch"].update(registration_verified=False, submission_open_verified=False)
    prepared["runtime_profile"] = {"source_configuration_sha256": digest(canonical(config)),
                                   "source_runs": config["paths"]["runs"], "selected_at": utc_now(),
                                   "source_rooms": {mode: config["band"].get(f"{mode}_room_id") for mode in ("rehearsal", "judged")},
                                   "source_session_consumed": bool(consumed)}
    mandate_contents = {}
    original_mandates = {}
    for seat in prepared["seats"]:
        old = Path(seat["mandate"])
        if old.is_symlink() or not old.is_file():
            raise FactoryError(f"Source mandate for {seat['id']} must be an existing regular file")
        if old.name in mandate_contents:
            raise FactoryError("Source mandates must use distinct filenames")
        original_mandates[old] = old.read_bytes()
        selected_model = overrides.get(seat["id"], model)
        changed_override = seat["id"] in overrides and selected_model != seat.get("model")
        mandate_contents[old.name] = _mandate(original_mandates[old].decode(), harness_label(harness), selected_model)
        seat.update(harness=harness_label(harness), model=selected_model, mandate=str(target / "mandates" / old.name), registration_verified=False)
        seat["reasoning_effort"] = (effort if effort is not None else seat.get("reasoning_effort")
                                    if harness == original_harness and not changed_override
                                    else "medium" if harness == "codex" else None)
        if harness != original_harness:
            seat.pop("cli_version", None)
    if errors := validate_selection(prepared):
        raise FactoryError("; ".join(errors))
    resolved_models = {seat["id"]: seat["model"] for seat in prepared["seats"]}
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.preparing-", dir=target.parent))
    try:
        (staging / "mandates").mkdir()
        (staging / "tasks").mkdir()
        (staging / "source-lock.json").write_bytes(lock_bytes)
        (staging / "factory.yaml").write_text(yaml.safe_dump(prepared, sort_keys=False))
        for name, raw in mandate_contents.items():
            (staging / "mandates" / name).write_text(raw)
        for name, (_, raw) in controls.items():
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            if name == "runtime/featherless-requests.json":
                destination.chmod(0o600)
        specs = [("rehearsal-toy.md", "toy", [1, 2, 3, 4], "practice-all"),
                 ("judged-all-stages.md", "tablekeeper", [1, 2, 3, 4], "all")]
        specs += [(f"judged-stage-{stage}.md", "tablekeeper", [stage], "separate") for stage in range(1, 5)]
        manifest = {"schema_version": 1, "configuration_sha256": digest(canonical(prepared)), "tasks": {}}
        for name, track, stages, mode in specs:
            raw, payloads = render_packet(prepared, track, stages, mode, locked_sources=locked)
            (staging / "tasks" / name).write_bytes(raw)
            manifest["tasks"][name] = {"sha256": digest(raw), "track": track, "stages": stages, "mode": mode, "spec_payloads": payloads}
        write_json(staging / "tasks/task-manifest.json", manifest)
        write_json(staging / "selection.json", {"status": "PREPARED_NOT_APPROVED", "harness": harness,
                   "model": model, "seat_models": resolved_models,
                   "source_configuration": str(source_path) if source_path else None,
                   "source_configuration_sha256": digest(canonical(config)), "source_lock_sha256": digest(lock_bytes),
                   "inherited_files": {name: {"source": str(path), "sha256": digest(raw)} for name, (path, raw) in controls.items()},
                   "source_session_consumed": bool(consumed), "inference_started": False, "dispatch_performed": False})
        _assert_stopped(config)
        refreshed, current_consumed = _control_snapshot(config)
        if ({name: raw for name, (_, raw) in refreshed.items()} != {name: raw for name, (_, raw) in controls.items()}
                or current_consumed != consumed or lock_path.read_bytes() != lock_bytes
                or any(path.read_bytes() != raw for path, raw in original_mandates.items())
                or (source_path is not None and source_path.read_bytes() != source_bytes)):
            raise FactoryError("Source configuration, mandates, accounting, dispatch history or source lock changed during selection; no profile was published")
        _rename_exclusive(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    verify_tasks(prepared)
    prefix = shlex.join([str(Path(config["paths"]["factory"]) / "scripts/factory"), "--config", str(target / "factory.yaml")])
    return {"status": "PREPARED_NOT_APPROVED", "config": str(target / "factory.yaml"), "harness": harness,
            "model": model, "seat_models": resolved_models,
            "packet_count": len(manifest["tasks"]), "source_session_consumed": bool(consumed),
            "inference_started": False, "dispatch_performed": False,
            "next_commands": [f"{prefix} runtime-show", f"{prefix} discover-models", f"{prefix} validate"],
            "actions": ["Review the selected command, native permissions, authentication and finite budgets.",
                        "Refresh matching model, registration and permission evidence before launch.",
                        "Inherited room scope, accounting and dispatch history remain binding; selection grants no new attempt or dispatch."]}


def _execute(args) -> int:
    from .cli import emit
    from .runtime import get_config
    config = get_config(args)
    if args.command == "runtime-options":
        result = runtime_options(config)
    elif args.command == "runtime-show":
        result = runtime_show(config)
    else:
        result = select_runtime(config, harness=args.harness, model=args.model,
                                output=args.output, command=args.executable, reasoning_effort=args.reasoning_effort,
                                variant=args.variant, source_config=args.config,
                                seat_models=_seat_model_overrides(args.seat_model))
    emit(result)
    return 0


def register_commands(subparsers) -> None:
    for name, help_text in (("runtime-options", "list agent harness choices without launching a provider"),
                            ("runtime-show", "show the configured agent harness and all seat models")):
        subparsers.add_parser(name, help=help_text).set_defaults(func=_execute)
    parser = subparsers.add_parser("runtime-select", help="prepare a new isolated harness/model configuration; never dispatch")
    parser.add_argument("--harness", choices=HARNESSES, required=True)
    parser.add_argument("--model", required=True, help="default model for seats without an override")
    parser.add_argument("--seat-model", action="append", default=[], metavar="ROLE=MODEL",
                        help="override one seat's model; repeat for additional roles")
    parser.add_argument("--output", required=True, help="new absolute directory for configuration, mandates and task packets")
    parser.add_argument("--command", dest="executable", help="absolute path to the selected harness executable")
    parser.add_argument("--reasoning-effort", help="Codex reasoning effort or Claude Code effort")
    parser.add_argument("--variant", help="OpenCode provider model variant")
    parser.set_defaults(func=_execute)
