"""Exact source inventories and sealed, run-owned supervisor source snapshots.

These are source snapshots, not relocated Python virtual environments. The
configured absolute interpreters and adapter installations remain external
dependencies; their entrypoints are recorded and checked at activation. Existing
configuration bytes and therefore readiness/accounting fingerprints are retained.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile

from .common import FactoryError, canonical, digest


SOURCE_FOLDERS = ("mandates", "protocols", "agents", "factorykit", "scripts")
SOURCE_FILES = ("AGENTS.md", "pyproject.toml", "uv.lock", "config/source-lock.json",
                "config/harness-requirements.lock", "tooling/codex/package.json",
                "tooling/codex/package-lock.json")
_active_snapshot: tuple[str, Path, dict] | None = None


def _relative(name: str) -> Path:
    if (not isinstance(name, str) or not name or "\\" in name
            or PurePosixPath(name).is_absolute()
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise FactoryError("Source inventory contains an unsafe relative path")
    return Path(name)


def _regular(path: Path, root: Path) -> None:
    if not path.is_relative_to(root):
        raise FactoryError("Source input escapes its recorded root")
    for item in (path, *path.parents):
        if item.is_symlink():
            raise FactoryError(f"Source input must not contain symlinks: {path.name}")
        if item == root:
            break
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise FactoryError(f"Source input must be a regular file: {path.name}")


def source_inventory(config: dict, *, root: Path | None = None) -> tuple[dict[str, str], list[str]]:
    """Enumerate the entire freeze scope, including newly added source files."""
    root = Path(root) if root is not None else factory_source_root(config)
    files: dict[str, str] = {}
    errors: list[str] = []
    for folder in SOURCE_FOLDERS:
        if folder == "mandates" and "mandates" in config.get("artifacts", {}):
            continue
        directory = root / folder
        if directory.is_symlink():
            errors.append(f"Source input must not contain symlinks: {folder}")
            continue
        for path in sorted(directory.rglob("*")):
            name = path.relative_to(root).as_posix()
            if path.is_symlink():
                errors.append(f"Source input must not contain symlinks: {name}")
                continue
            if "__pycache__" in path.relative_to(root).parts or path.suffix == ".pyc":
                continue
            if path.is_dir():
                continue
            try:
                _regular(path, root)
                files[name] = digest(path)
            except (OSError, FactoryError) as exc:
                errors.append(str(exc))
    for name in SOURCE_FILES:
        if name == "config/source-lock.json" and "source_lock" in config.get("artifacts", {}):
            continue
        path = root / name
        if not path.is_file():
            errors.append(f"Freeze input missing: {name}")
            continue
        try:
            _regular(path, root)
            files[name] = digest(path)
        except (OSError, FactoryError) as exc:
            errors.append(str(exc))
    return files, sorted(set(errors))


def frozen_source_errors(config: dict, frozen: dict) -> list[str]:
    """Validate additions as well as removals/edits for old and new freezes."""
    recorded = frozen.get("files")
    if not isinstance(recorded, dict) or not recorded:
        return ["Frozen source inventory is missing or malformed"]
    try:
        for name, value in recorded.items():
            _relative(name)
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise FactoryError("Frozen source inventory contains an invalid digest")
        current, errors = source_inventory(config)
    except (OSError, FactoryError) as exc:
        return [str(exc)]
    for name in sorted(set(current) - set(recorded)):
        errors.append(f"Frozen input added: {name}")
    for name, expected in recorded.items():
        if current.get(name) != expected:
            errors.append(f"Frozen input changed: {name}")
    return sorted(set(errors))


def source_fingerprint(config: dict) -> str:
    files, errors = source_inventory(config)
    if errors:
        raise FactoryError("; ".join(errors))
    mandates = {}
    for seat in config.get("seats", []):
        path = snapshot_input_path(config, seat["mandate"])
        _regular(path, path.parent)
        # Original configured identities remain stable when snapshot paths move.
        mandates[str(Path(seat["mandate"]).resolve())] = digest(path)
    return digest(canonical({"version": 1, "files": files, "selected_mandates": mandates}))


def _tree_files(directory: Path) -> dict[str, str]:
    if directory.is_symlink() or not directory.is_dir():
        raise FactoryError("Snapshot input directory is missing or symlinked")
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise FactoryError("Snapshot inputs must not contain symlinks")
        if path.is_dir():
            continue
        _regular(path, directory)
        result[path.relative_to(directory).as_posix()] = digest(path)
    return result


def _dependency_entries(config: dict) -> dict[str, dict]:
    result = {}
    for name in ("python", "harness_python", "codex_command", "claude_command", "opencode_command"):
        value = config["runtime"].get(name)
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute() or not path.is_file():
            raise FactoryError(f"Snapshot external runtime entrypoint is unavailable: runtime.{name}")
        result[name] = {"path": str(path), "resolved_path": str(path.resolve()), "sha256": digest(path)}
    return result


def materialize_source_snapshot(config: dict, frozen: dict) -> dict:
    """Copy validated inputs into a new content-addressed, read-only directory.

    No freeze, ledger, configuration or existing snapshot is rewritten. The
    caller must complete its normal admission gates before launching anything.
    """
    from .common import artifact_path, scoped_mandate_errors
    errors = frozen_source_errors(config, frozen)
    if frozen.get("configuration_sha256") != digest(canonical(config)):
        errors.append("Configuration changed after freeze")
    errors.extend(scoped_mandate_errors(config, frozen))
    if errors:
        raise FactoryError("; ".join(errors))
    source_root = factory_source_root(config)
    copies: dict[str, tuple[Path, str]] = {
        f"source/{name}": (source_root / _relative(name), expected)
        for name, expected in frozen["files"].items()
    }
    input_paths: dict[str, str] = {}
    artifact_paths: dict[str, str] = {}
    trees: dict[Path, dict] = {}
    for kind in ("source_lock", "tasks", "mandates"):
        original = artifact_path(config, kind)
        if kind == "source_lock":
            _regular(original, original.parent)
            expected = frozen.get("source_lock_sha256")
            if digest(original) != expected:
                raise FactoryError("Configured source lock changed after freeze")
            destination = "inputs/source-lock.json"
            copies[destination] = (original, expected)
            input_paths[str(original)] = destination
            input_paths[str(original.resolve())] = destination
        else:
            destination = f"inputs/{kind}"
            tree = _tree_files(original)
            trees[original] = tree
            for name, expected in tree.items():
                copies[f"{destination}/{name}"] = (original / name, expected)
                input_paths[str(original / name)] = f"{destination}/{name}"
                input_paths[str((original / name).resolve())] = f"{destination}/{name}"
            if kind == "tasks":
                for name, record in frozen.get("tasks", {}).items():
                    if tree.get(name) != record.get("sha256"):
                        raise FactoryError("Generated task packet changed after freeze")
        artifact_paths[kind] = destination
    payload = canonical(config)
    # Rendering historical packets iterates configured path order. Preserve that
    # order in the child JSON while retaining the canonical fingerprint binding.
    config_bytes = (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode()
    files = {name: expected for name, (_, expected) in copies.items()}
    files["inputs/config.json"] = digest(config_bytes)
    executable_files = sorted(name for name, (path, _) in copies.items() if path.stat().st_mode & 0o111)
    manifest = {"schema_version": 1, "configuration_sha256": digest(payload),
                "frozen_manifest_sha256": digest(canonical(frozen)), "source_files": frozen["files"],
                "files": files, "executable_files": executable_files,
                "input_paths": input_paths, "artifact_paths": artifact_paths,
                "external_runtime_entrypoints": _dependency_entries(config),
                "dependency_boundary": "Source and input bytes are sealed. Absolute Python/adapter installations remain external; entrypoint digests are checked at activation, not an immutable copy of their entire dependency trees. No virtual environment relocation or credential copying."}
    manifest_bytes = canonical(manifest)
    identifier = digest(manifest_bytes)
    parent = Path(config["paths"]["runs"]) / "source-snapshots"
    if parent.is_symlink():
        raise FactoryError("Source snapshot directory must not be a symlink")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = parent / identifier
    if target.exists() or target.is_symlink():
        return verify_source_snapshot(config, target, expected_manifest_sha256=identifier)
    pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=parent))
    try:
        for name, (original, expected) in copies.items():
            _regular(original, original.parent)
            raw = original.read_bytes()
            if digest(raw) != expected:
                raise FactoryError("Source snapshot input changed during copying")
            copied = pending / _relative(name)
            copied.parent.mkdir(parents=True, exist_ok=True)
            copied.write_bytes(raw)
            copied.chmod(0o555 if name in executable_files else 0o444)
        (pending / "inputs/config.json").write_bytes(config_bytes)
        (pending / "inputs/config.json").chmod(0o444)
        (pending / "manifest.json").write_bytes(manifest_bytes)
        (pending / "manifest.json").chmod(0o444)
        # Reject source additions/deletions and external input edits during copy.
        errors = frozen_source_errors(config, frozen)
        if errors or any(_tree_files(directory) != tree for directory, tree in trees.items()):
            raise FactoryError("Source snapshot inputs changed while copying")
        if _dependency_entries(config) != manifest["external_runtime_entrypoints"]:
            raise FactoryError("External runtime entrypoint changed while copying")
        for directory in sorted((p for p in pending.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            directory.chmod(0o555)
        pending.chmod(0o555)
        try:
            pending.rename(target)
        except FileExistsError:
            # Another identical creation is safe only after exact verification.
            pass
        return verify_source_snapshot(config, target, expected_manifest_sha256=identifier)
    finally:
        if pending.exists():
            for directory in (pending, *(p for p in pending.rglob("*") if p.is_dir())):
                directory.chmod(0o700)
            shutil.rmtree(pending)


def verify_source_snapshot(config: dict, root: Path, *, expected_manifest_sha256: str | None = None) -> dict:
    root = Path(root)
    parent = Path(config["paths"]["runs"]) / "source-snapshots"
    if root.parent != parent or root.is_symlink() or parent.is_symlink() or not root.is_dir():
        raise FactoryError("Source snapshot must be a run-owned content-addressed directory")
    manifest_path = root / "manifest.json"
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
        if digest(raw) != root.name or (expected_manifest_sha256 and digest(raw) != expected_manifest_sha256):
            raise FactoryError("Source snapshot manifest digest differs")
        if manifest.get("schema_version") != 1 or manifest.get("configuration_sha256") != digest(canonical(config)):
            raise FactoryError("Source snapshot configuration binding differs")
        files = manifest["files"]
        if not isinstance(files, dict) or not files:
            raise FactoryError("Source snapshot inventory is missing")
        for name in files:
            _relative(name)
        actual = _tree_files(root)
        if set(actual) != set(files) | {"manifest.json"}:
            raise FactoryError("Source snapshot file inventory differs")
        for name, expected in files.items():
            if actual[name] != expected:
                raise FactoryError(f"Source snapshot input changed: {name}")
        executables = set(manifest["executable_files"])
        for path in (root, *root.rglob("*")):
            if path.is_symlink() or path.stat().st_mode & 0o222:
                raise FactoryError("Source snapshot must remain read-only and free of symlinks")
            if path.is_file() and bool(path.stat().st_mode & 0o111) != (path.relative_to(root).as_posix() in executables):
                raise FactoryError("Source snapshot executable permissions differ")
        if json.loads((root / "inputs/config.json").read_bytes()) != config:
            raise FactoryError("Source snapshot configuration content differs")
        if _dependency_entries(config) != manifest["external_runtime_entrypoints"]:
            raise FactoryError("External runtime entrypoint changed after source snapshot")
    except FactoryError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise FactoryError("Source snapshot is missing or malformed") from None
    return {"root": str(root), "source_root": str(root / "source"),
            "config_path": str(root / "inputs/config.json"), "manifest_sha256": digest(raw), "manifest": manifest}


def activate_source_snapshot(config: dict, root: Path) -> dict:
    """Activate only in the newly launched child, preserving canonical config."""
    global _active_snapshot
    descriptor = verify_source_snapshot(config, root)
    _active_snapshot = (digest(canonical(config)), Path(root), descriptor["manifest"])
    return descriptor


def _active(config: dict) -> tuple[Path, dict] | None:
    if _active_snapshot is None:
        return None
    fingerprint, root, manifest = _active_snapshot
    if fingerprint != digest(canonical(config)):
        raise FactoryError("Active source snapshot belongs to another configuration")
    return root, manifest


def factory_source_root(config: dict) -> Path:
    active = _active(config)
    return active[0] / "source" if active else Path(config["paths"]["factory"])


def snapshot_artifact_path(config: dict, name: str) -> Path | None:
    active = _active(config)
    return active[0] / active[1]["artifact_paths"][name] if active else None


def snapshot_input_path(config: dict, path: Path | str) -> Path:
    active = _active(config)
    if not active:
        return Path(path)
    name = active[1]["input_paths"].get(str(path)) or active[1]["input_paths"].get(str(Path(path).resolve()))
    if name is None:
        raise FactoryError("Requested input is absent from active source snapshot")
    return (active[0] / name).resolve()
