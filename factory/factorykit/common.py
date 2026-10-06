"""Small shared primitives. Commands always use argv, never a shell."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any

import yaml


class FactoryError(ValueError):
    """An actionable validation error that is safe to show to the operator."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: bytes | str | Path) -> str:
    if isinstance(value, Path):
        value = value.read_bytes()
    elif isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def write_json(path: str | Path, value: Any) -> None:
    """Atomic replacement; callers must separately lock read-modify-write operations."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value))
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


SECRET_PATTERNS = [
    re.compile(r"\bband_[au]_[A-Za-z0-9._-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{16,}"),
    re.compile(r"(?im)\b(?:[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET|PASSWORD))\s*[:=]\s*[\"']?[^\s\"']{8,}"),
    re.compile(r"(?i)(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),
]


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def run_command(argv: list[str], cwd: str | Path, timeout: float = 30,
                env: dict[str, str] | None = None, *, graceful_interrupt: bool = False) -> dict:
    """Retain failure and timeout evidence with bounded, redacted output."""
    command = [str(item) for item in argv]
    started = utc_now()
    try:
        if graceful_interrupt:
            code, stdout, stderr = _run_with_cleanup(command, cwd, timeout, env)
        else:
            result = subprocess.run(command, cwd=str(cwd), env=env, capture_output=True,
                                    text=True, timeout=timeout, check=False)
            code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        code, stdout, stderr = 127, "", "Executable or working directory not found"
    except PermissionError:
        code, stdout, stderr = 126, "", "Permission denied"
    except subprocess.TimeoutExpired as exc:
        def decoded(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        code, stdout, stderr = 124, decoded(exc.stdout), decoded(exc.stderr) + "\nTimed out"
    return {"argv": [redact(v) for v in command], "cwd": str(Path(cwd).absolute()),
            "started_at": started, "finished_at": utc_now(), "exit_code": code,
            "stdout": redact(stdout), "stderr": redact(stderr)}



def _run_with_cleanup(command: list[str], cwd: str | Path, timeout: float,
                      env: dict[str, str] | None) -> tuple[int, str, str]:
    """Own one isolated process group; give official harness cleanup a SIGINT grace.

    This never searches by process name and never touches unrelated Docker resources.
    A hard-killed harness can still leave named Docker resources; retained invocation
    evidence must guide explicit cleanup. Deadline exit 124 is preserved after cleanup.
    """
    process = subprocess.Popen(command, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)

    def signal_owned_group(sig):
        # New session makes the launched PID the sole initial process-group leader.
        if process.poll() is None:
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass

    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return process.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        signal_owned_group(signal.SIGINT)
        try:
            stdout, stderr = process.communicate(timeout=30)
            cleanup = "SIGINT delivered; official harness received 30 seconds for resource cleanup"
        except subprocess.TimeoutExpired:
            signal_owned_group(signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=10)
                cleanup = "SIGINT cleanup grace expired; owned process group terminated; inspect retained Docker resource names before any cleanup"
            except subprocess.TimeoutExpired:
                signal_owned_group(signal.SIGKILL)
                stdout, stderr = process.communicate(timeout=5)
                cleanup = "Owned process group force-stopped after cleanup grace; Docker resource cleanup unverified"
        return 124, stdout, stderr + "\nTimed out. " + cleanup
    except KeyboardInterrupt:
        signal_owned_group(signal.SIGINT)
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            signal_owned_group(signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=5)
        return 130, stdout, stderr + "\nInterrupted; inspect official cleanup evidence"


def load_config(path: str | Path) -> dict:
    """Fail without echoing malformed input, which can contain credentials."""
    try:
        raw = Path(path).read_text()
        config = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise FactoryError(f"Cannot read configuration ({type(exc).__name__}); inspect it locally") from None
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise FactoryError("Configuration must be a mapping with schema_version: 1")
    if contains_secret(raw):
        raise FactoryError("Possible credential found in configuration; move credentials outside repositories")
    for key in ("paths", "runtime", "band", "budgets", "launch"):
        if not isinstance(config.get(key), dict):
            raise FactoryError(f"Configuration requires a {key} mapping")
    for mapping, flag in (("budgets", "balance_only"), ("runtime", "strict_membership_recovery")):
        if flag in config[mapping] and type(config[mapping][flag]) is not bool:
            raise FactoryError(f"{mapping}.{flag} must be an explicit boolean")
    if config["budgets"].get("balance_only") is True and config["runtime"].get("strict_membership_recovery") is not False:
        raise FactoryError("balance_only requires runtime.strict_membership_recovery=false")
    for key in ("challenge", "factory", "rehearsal", "runs", "result"):
        value = config["paths"].get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise FactoryError(f"paths.{key} must be an absolute path")
    paths = [Path(config["paths"][key]).resolve() for key in ("challenge", "factory", "rehearsal", "runs", "result")]
    if len(set(paths)) != len(paths) or any(a in b.parents for a in paths for b in paths if a != b):
        raise FactoryError("Operational workspace paths must be distinct, non-nested directories")
    product_repository(config)
    for name in ("source_lock", "tasks", "mandates"):
        artifact_path(config, name)
    from .harnesses import command_key, selected_harness, validate_selection
    for key in ("python", "harness_python", command_key(selected_harness(config)), "browser_path"):
        value = config["runtime"].get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise FactoryError(f"runtime.{key} must be an absolute path")
    credentials = config["band"].get("credentials_file")
    if not isinstance(credentials, str) or not Path(credentials).is_absolute():
        raise FactoryError("band.credentials_file must be an absolute path outside repositories")
    cred = Path(credentials).resolve()
    if any(root == cred or root in cred.parents for root in paths):
        raise FactoryError("Credentials file must be outside all workspace directories")
    seats = config.get("seats")
    expected = {"pm", "architect", "designer", "backend", "frontend", "qa", "reviewer"}
    if not isinstance(seats, list) or len(seats) != 7 or any(not isinstance(s, dict) for s in seats):
        raise FactoryError("Exactly seven seat mappings are required")
    if {seat.get("id") for seat in seats} != expected:
        raise FactoryError("Seat IDs must be pm, architect, designer, backend, frontend, qa, reviewer exactly once")
    for field in ("handle", "agent_id", "display_name"):
        values = [str(s[field]).lower().lstrip("@") for s in seats if s.get(field)]
        if len(values) != len(set(values)):
            raise FactoryError(f"Duplicate seat {field}")
    for seat in seats:
        for field in ("display_name", "mandate", "harness", "git_name", "git_email"):
            if not isinstance(seat.get(field), str) or not seat[field].strip():
                raise FactoryError(f"Seat {seat['id']} requires {field}")
        if not Path(seat["mandate"]).is_absolute():
            raise FactoryError(f"Seat {seat['id']} mandate path must be absolute")
        if Path(seat["mandate"]).resolve().parent != artifact_path(config, "mandates").resolve():
            raise FactoryError("Mandates must be direct children of the configured mandates directory")
    if errors := validate_selection(config):
        raise FactoryError("; ".join(errors))
    from .budgets import budget_errors, room_scope
    if "archived_room_ids" in config["band"]:
        try:
            room_scope(config)
        except ValueError as error:
            raise FactoryError(str(error)) from None
    if errors := budget_errors(config["budgets"]):
        raise FactoryError("; ".join(errors))
    return config


def product_repository(config: dict) -> dict | None:
    """Optional exact publication target for a fresh, independent attempt."""
    if "product" not in config:
        return None
    product = config["product"]
    if not isinstance(product, dict) or set(product) != {"repository_url", "branch"}:
        raise FactoryError("product requires exactly repository_url and branch")
    origin, branch = product["repository_url"], product["branch"]
    # Keep credentials and executable/prompt syntax out of dispatch metadata.
    remote = r"(?:https://[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?/|git@[A-Za-z0-9][A-Za-z0-9.-]*:)[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+"
    if not isinstance(origin, str) or not re.fullmatch(remote, origin):
        raise FactoryError("product.repository_url must be a credential-free HTTPS or git@host:path repository URL")
    if (not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch)
            or branch == "HEAD" or ".." in branch
            or any(not part or part.startswith(".") or part.endswith((".", ".lock")) for part in branch.split("/"))):
        raise FactoryError("product.branch must be a literal Git branch name using letters, digits, dots, underscores, slashes or hyphens")
    return product


def artifact_path(config: dict, name: str) -> Path:
    """Resolve optional run-owned inputs without overwriting historical defaults."""
    from .source_snapshot import snapshot_artifact_path
    snapshot = snapshot_artifact_path(config, name)
    if snapshot is not None:
        return snapshot
    defaults = {"source_lock": "config/source-lock.json", "tasks": "tasks", "mandates": "mandates"}
    if name not in defaults:
        raise FactoryError("Unknown factory artifact")
    artifacts = config.get("artifacts", {})
    if not isinstance(artifacts, dict) or set(artifacts) - defaults.keys():
        raise FactoryError("artifacts permits only source_lock, tasks and mandates paths")
    scoped = {}
    runs = Path(config["paths"]["runs"]).resolve()
    for key, value in artifacts.items():
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise FactoryError(f"artifacts.{key} must be an absolute path under paths.runs")
        path = Path(value).resolve()
        if runs not in path.parents:
            raise FactoryError(f"artifacts.{key} must remain under paths.runs, including symlink targets")
        if (key == "source_lock" and path.is_dir()) or (key in ("tasks", "mandates") and path.exists() and not path.is_dir()):
            raise FactoryError(f"artifacts.{key} has the wrong file type")
        if key == "mandates" and path.name != "mandates":
            raise FactoryError("artifacts.mandates must name a mandates directory for the official vocabulary check")
        if key in ("tasks", "mandates") and path.exists() and any(child.is_symlink() for child in path.rglob("*")):
            raise FactoryError(f"artifacts.{key} must not contain symlinks")
        scoped[key] = path
    from itertools import combinations
    for left, right in combinations(scoped.values(), 2):
        if left == right or left in right.parents or right in left.parents:
            raise FactoryError("Artifact paths must not overlap")
    return scoped.get(name, Path(config["paths"]["factory"]) / defaults[name])


def scoped_mandate_errors(config: dict, frozen: dict) -> list[str]:
    """Bind profile-owned mandates without changing historical factory snapshots."""
    if "mandates" not in config.get("artifacts", {}):
        return []
    directory = artifact_path(config, "mandates").resolve()
    paths = {str(Path(seat["mandate"]).resolve()) for seat in config["seats"]}
    recorded = frozen.get("mandate_files")
    if not isinstance(recorded, dict) or set(recorded) != paths:
        return ["Frozen profile mandate inventory differs from the configured roster"]
    errors = []
    for name, expected in recorded.items():
        from .source_snapshot import snapshot_input_path
        path = snapshot_input_path(config, name)
        if (path.parent != directory or not path.is_file() or path.is_symlink()
                or digest(path) != expected):
            errors.append(f"Frozen profile mandate changed: {path.name}")
    return errors


def source_lock(config: dict) -> dict:
    path = artifact_path(config, "source_lock")
    try:
        value = json.loads(path.read_text())
        challenge = value["challenge"]
        if not isinstance(challenge["commit"], str) or not re.fullmatch(r"[a-f0-9]{40}", challenge["commit"]):
            raise ValueError()
        if not isinstance(challenge["files"], dict) or not challenge["files"]:
            raise ValueError()
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise FactoryError("Missing or malformed configured source lock") from None


def verify_sources(config: dict) -> list[str]:
    lock = source_lock(config)
    root = Path(config["paths"]["challenge"])
    problems = []
    result = run_command(["git", "rev-parse", "HEAD"], root)
    if result["exit_code"] or result["stdout"].strip() != lock["challenge"]["commit"]:
        problems.append("Pinned challenge commit does not match checkout HEAD")
    for name, expected in lock["challenge"]["files"].items():
        path = (root / name).resolve()
        if root.resolve() not in path.parents or not path.is_file() or digest(path) != expected:
            problems.append(f"Pinned source changed or missing: {name}")
    for record in lock.get("documents", []) + lock.get("instruction_inputs", []):
        path = Path(record.get("path", ""))
        if not path.is_absolute() or not path.is_file() or digest(path) != record.get("sha256"):
            problems.append(f"Locked reference document changed or missing: {path.name}")
    dirty = run_command(["git", "status", "--porcelain", "--untracked-files=normal"], root)
    if dirty["exit_code"] or dirty["stdout"].strip():
        problems.append("Challenge checkout is dirty or cannot be inspected")
    return problems
