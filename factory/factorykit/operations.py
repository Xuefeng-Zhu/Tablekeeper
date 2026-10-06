"""Evidence-retaining checks and no-dispatch launch preparation."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shlex
import uuid
from datetime import datetime, timezone

from .common import FactoryError, artifact_path, canonical, digest, product_repository, run_command, source_lock, utc_now, verify_sources, write_json
from .budgets import persisted_budget_blockers
from .tasks import verify_tasks
from .validation import docker_resource_check, observations, validate, frozen_readiness_authorization, frozen_readiness_errors
from .source_snapshot import frozen_source_errors, source_fingerprint, source_inventory


def evidence_directory(config: dict, prefix: str) -> Path:
    root = Path(config["paths"]["runs"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"{prefix}-{stamp}-{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def harness(config: dict, track: str, stage: int | None, all_stages: bool, mode: str) -> tuple[int, dict]:
    errors = verify_sources(config)
    if errors:
        raise FactoryError("; ".join(errors))
    if (stage is None) == (not all_stages):
        raise FactoryError("Choose exactly one --stage N or --all")
    directory = evidence_directory(config, f"harness-{track}-{mode}")
    repo = config["paths"]["rehearsal" if track == "toy" else "result"]
    argv = [config["runtime"]["harness_python"], "-m", "harness", "run", "--track", track,
            "--repo", repo, "--mode", mode, "--out", str(directory / "official")]
    argv += ["--all"] if all_stages else ["--stage", str(stage)]
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = config["runtime"]["browser_path"]
    result = run_command(argv, config["paths"]["challenge"],
                         timeout=config["budgets"]["overall_timeout_seconds"] if all_stages else config["budgets"]["stage_timeout_seconds"], env=env, graceful_interrupt=True)
    result.update({"source_commit": source_lock(config)["challenge"]["commit"], "track": track, "mode": mode,
                   "stages": [1, 2, 3, 4] if all_stages else [stage], "evidence_directory": str(directory)})
    write_json(directory / "invocation.json", result)
    (directory / "stdout.log").write_text(result["stdout"])
    (directory / "stderr.log").write_text(result["stderr"])
    return result["exit_code"], result


def pristine_result(config: dict) -> list[str]:
    root = Path(config["paths"]["result"])
    if not root.is_dir():
        return ["Result repository directory is missing"]
    if any(item.name != ".git" for item in root.iterdir()):
        return ["Result is not pristine: entries other than .git exist"]
    if not (root / ".git").is_dir():
        return ["Result must be a freshly initialized independent Git repository"]
    head = run_command(["git", "rev-parse", "--verify", "HEAD"], root)
    if head["exit_code"] == 0:
        return ["Result already has a commit; first dispatch requires a pristine repository"]
    tracked = run_command(["git", "ls-files"], root)
    if tracked["exit_code"] or tracked["stdout"].strip():
        return ["Result has indexed files or Git state could not be verified"]
    try:
        product = product_repository(config)
    except FactoryError as error:
        return [str(error)]
    blockers = []
    if product:
        branch = run_command(["git", "symbolic-ref", "--quiet", "HEAD"], root)
        if branch["exit_code"] or branch["stdout"].strip() != f"refs/heads/{product['branch']}":
            blockers.append("Result branch does not match product.branch")
        for mode in ([], ["--push"]):
            origin = run_command(["git", "remote", "get-url", *mode, "--all", "origin"], root)
            if origin["exit_code"] or origin["stdout"].splitlines() != [product["repository_url"]]:
                blockers.append(f"Result origin {'push' if mode else 'fetch'} URL does not match product.repository_url")
    return blockers


def freeze(config: dict) -> dict:
    report = validate(config)
    blockers = report["errors"] + report["launch_blockers"]
    try:
        tasks = verify_tasks(config)
    except FactoryError as exc:
        blockers.append(str(exc))
        tasks = {"tasks": {}}
    evidence, missing = observations(config)
    blockers.extend(missing)
    blockers.extend(pristine_result(config))
    deadline = source_lock(config).get("deadline", {}).get("time")
    if not config["launch"].get("practice_mode"):
        try:
            if not deadline or datetime.now(timezone.utc) >= datetime.fromisoformat(deadline):
                blockers.append("Submission deadline has passed or is unavailable; set practice_mode true")
        except ValueError:
            blockers.append("Cannot verify source-locked submission deadline; use practice mode")
    doctor_path = Path(config["paths"]["runs"]) / "doctor-latest.json"
    try:
        doctor = json.loads(doctor_path.read_text())
        if doctor.get("status") != "PASS":
            blockers.append("Doctor reports failed environment checks")
        if doctor.get("configuration_sha256") != digest(canonical(config)):
            blockers.append("Doctor evidence belongs to a different configuration")
        timestamp = datetime.fromisoformat(doctor["created_at"])
        if (datetime.now(timezone.utc) - timestamp).total_seconds() > 86400:
            blockers.append("Doctor evidence is older than 24 hours")
    except (OSError, ValueError, KeyError):
        blockers.append("Run doctor and retain its observed report")
    try:
        from .runtime import preflight_runtime
        blockers.extend(preflight_runtime(config, mode="rehearsal"))
        blockers.extend(preflight_runtime(config, mode="judged"))
    except ImportError:
        blockers.append("Runtime adapter preflight is unavailable")
    files, inventory_errors = source_inventory(config)
    blockers.extend(inventory_errors)
    try:
        factory_source_sha256 = source_fingerprint(config)
    except (OSError, FactoryError) as exc:
        blockers.append(f"Cannot bind frozen factory source: {exc}")
        factory_source_sha256 = None
    lock_path = artifact_path(config, "source_lock")
    lock_sha256 = digest(lock_path) if lock_path.is_file() else None
    if lock_sha256 is None:
        blockers.append("Configured source lock is missing")
    blockers.extend(persisted_budget_blockers(config, require_existing=True))
    blockers = sorted(set(blockers))
    manifest = {"schema_version": 1, "created_at": utc_now(),
                "status": "BLOCKED_WITH_ACTIONS" if blockers else "READY_TO_LAUNCH",
                "configuration_sha256": digest(canonical(config)), "source_lock_sha256": lock_sha256,
                "files": files, "source_inventory_version": 1,
                "factory_source_sha256": factory_source_sha256,
                "tasks": tasks["tasks"], "seats": config["seats"], "budgets": config["budgets"],
                "observed_checks": evidence, "blockers": blockers,
                "usage": {"measured_preparation_cost_usd": None, "measured_rehearsal_cost_usd": None, "status": "UNAVAILABLE"},
                "dispatch_performed": False}
    if "source_lock" in config.get("artifacts", {}):
        manifest["source_lock_path"] = str(lock_path)
    if "mandates" in config.get("artifacts", {}):
        manifest["mandate_files"] = {str(Path(seat["mandate"]).resolve()): digest(Path(seat["mandate"]))
                                    for seat in config["seats"] if Path(seat["mandate"]).is_file()}
    authorization = frozen_readiness_authorization(config)
    if authorization is not None:
        manifest["operator_readiness_authorization"] = authorization
    path = Path(config["paths"]["runs"]) / "freeze/latest.json"
    write_json(path, manifest)
    archive = evidence_directory(config, "freeze")
    write_json(archive / "manifest.json", manifest)
    return manifest


@contextmanager
def ledger_lock(config: dict):
    directory = Path(config["paths"]["runs"]) / "launch"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "ledger.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield directory
        fcntl.flock(stream, fcntl.LOCK_UN)


def validate_launch_request(ledger: dict, mode: str, stage: int | None) -> list[int]:
    if mode == "all" and stage is not None or mode == "separate" and stage not in (1, 2, 3, 4):
        raise FactoryError("Use --mode all without --stage, or --mode separate --stage N")
    if ledger.get("mode") not in (None, mode):
        raise FactoryError("Dispatch mode is already fixed; mixing all/separate is forbidden")
    stages = [1, 2, 3, 4] if mode == "all" else [stage]
    reserved = {s for entry in ledger.get("entries", []) for s in entry["stages"]}
    if reserved.intersection(stages):
        raise FactoryError("This stage is already prepared or dispatched; duplicate dispatch is forbidden")
    if mode == "separate" and stage > 1:
        previous = [e for e in ledger.get("entries", []) if stage - 1 in e["stages"] and e.get("state") == "ACCEPTED"]
        if len(previous) != 1:
            raise FactoryError("The preceding separate stage requires recorded independent acceptance")
    return stages


def launch_prepare(config: dict, mode: str, stage: int | None) -> dict:
    with ledger_lock(config) as directory:
        path = directory / "ledger.json"
        ledger = json.loads(path.read_text()) if path.exists() else {"schema_version": 1, "mode": None, "entries": []}
        stages = validate_launch_request(ledger, mode, stage)
        freeze_path = Path(config["paths"]["runs"]) / "freeze/latest.json"
        try:
            frozen = json.loads(freeze_path.read_text())
        except (OSError, ValueError):
            raise FactoryError("Run freeze before preparing a dispatch") from None
        blockers = list(frozen.get("blockers", []))
        if frozen.get("status") != "READY_TO_LAUNCH":
            blockers.append("Freeze is not READY_TO_LAUNCH")
        if frozen.get("configuration_sha256") != digest(canonical(config)):
            blockers.append("Configuration changed after freeze")
        blockers.extend(frozen_source_errors(config, frozen))
        from .common import scoped_mandate_errors
        blockers.extend(scoped_mandate_errors(config, frozen))
        blockers.extend(frozen_readiness_errors(config, frozen))
        lock_path = artifact_path(config, "source_lock")
        if not lock_path.is_file() or digest(lock_path) != frozen.get("source_lock_sha256"):
            blockers.append("Configured source lock changed after freeze")
        blockers.extend(verify_sources(config))
        verify_tasks(config)
        if not ledger["entries"]:
            blockers.extend(pristine_result(config))
        _, evidence_blockers = observations(config)
        blockers.extend(evidence_blockers)
        task = artifact_path(config, "tasks") / ("judged-all-stages.md" if mode == "all" else f"judged-stage-{stage}.md")
        frozen_task = frozen.get("tasks", {}).get(task.name, {}).get("sha256")
        if frozen_task != digest(task):
            blockers.append("Dispatch task differs from frozen packet")
        blockers.extend(persisted_budget_blockers(config, require_existing=True))
        # Do not prepare an exact-once dispatch using stale daemon capacity.
        # Recheck elapsed budgets after the bounded query as well.
        docker = None
        if not blockers:
            docker = docker_resource_check(config)
            blockers.extend(docker["errors"])
            blockers.extend(persisted_budget_blockers(config, require_existing=True))
        pm = next(seat for seat in config["seats"] if seat["id"] == "pm")
        instructions = ["# Launch preparation — no message has been sent", "",
                        f"Status: {'BLOCKED_WITH_ACTIONS' if blockers else 'PREPARED_NOT_DISPATCHED'}",
                        f"Mode: {mode}; stages: {stages}", f"Task: {task}", f"Task SHA-256: {digest(task)}", "",
                        "Resolve every blocker, rerun doctor, generate-tasks and freeze before proceeding.",
                        "Start the configured factory seats in judged mode only when READY.",
                        f"In BAND Desktop, open judged room {config['band'].get('judged_room_id') or 'UNRESOLVED'}.",
                        f"Address PM handle {pm.get('handle') or 'UNRESOLVED'} and dispatch the full task file exactly once.",
                        "For a multi-part task, send numbered parts and require complete receipt before work.",
                        "Record the real room event ID with dispatch-record immediately; do not resend on uncertainty.",
                        "No human steering, approvals, debugging hints or reruns during a judged stage.", ""]
        if blockers:
            instructions.extend(["## Actions remaining", *(f"- {item}" for item in sorted(set(blockers)))])
        (directory / "launch-instructions.md").write_text("\n".join(instructions) + "\n")
        result = {"status": "BLOCKED_WITH_ACTIONS" if blockers else "PREPARED_NOT_DISPATCHED", "task": str(task),
                  "instructions": str(directory / "launch-instructions.md"), "blockers": sorted(set(blockers)), "dispatch_performed": False}
        if docker is not None:
            result["docker_resources"] = docker
        if not blockers:
            entry = {"id": uuid.uuid4().hex, "prepared_at": utc_now(), "state": "PREPARED", "stages": stages,
                     "task": str(task), "task_sha256": digest(task), "freeze_sha256": digest(freeze_path)}
            ledger["mode"] = mode
            ledger["entries"].append(entry)
            write_json(path, ledger)
            result["preparation_id"] = entry["id"]
        write_json(directory / "preparation-latest.json", result)
        return result


def dispatch_record(config: dict, preparation_id: str, event: str, acceptance: str | None = None) -> dict:
    """Record evidence of a human action. This command cannot send any BAND message."""
    with ledger_lock(config) as directory:
        path = directory / "ledger.json"
        if not path.exists():
            raise FactoryError("No prepared dispatch exists")
        ledger = json.loads(path.read_text())
        entries = [item for item in ledger["entries"] if item["id"] == preparation_id]
        if len(entries) != 1:
            raise FactoryError("Unknown preparation ID")
        entry = entries[0]
        if acceptance:
            if entry["state"] != "DISPATCHED":
                raise FactoryError("Only a dispatched stage can have acceptance recorded")
            proof = Path(acceptance)
            if not proof.is_absolute() or not proof.is_file() or proof.stat().st_size == 0:
                raise FactoryError("Acceptance requires a nonempty absolute evidence file")
            entry.update(state="ACCEPTED", accepted_at=utc_now(), acceptance={"path": str(proof), "sha256": digest(proof)})
        else:
            if entry["state"] != "PREPARED":
                raise FactoryError("Dispatch was already recorded; do not dispatch again")
            if not event.strip():
                raise FactoryError("A real room event reference is required")
            entry.update(state="DISPATCHED", dispatched_at=utc_now(), room_event=event)
        write_json(path, ledger)
        return entry


def submission_check(config: dict, final: bool = False) -> tuple[int, dict]:
    directory = evidence_directory(config, "submission-check")
    root = Path(config["paths"]["result"])
    expected = ["README.md", "FACTORY.md", "room.json", "mandates", "stage-1/Dockerfile", "stage-1/RUN.md"]
    missing = [name for name in expected if not (root / name).exists()]
    result = run_command([config["runtime"]["harness_python"], "-m", "harness", "check", str(root), "--track", "tablekeeper"], config["paths"]["challenge"], timeout=60)
    write_json(directory / "official-check.json", result)
    nested = [str(path) for path in root.glob("stage-*/**/.git")]
    state = "FAIL" if final and (missing or result["exit_code"] or nested) else "EXPECTED_MISSING" if missing and not final else "PASS" if result["exit_code"] == 0 and not nested else "FAIL"
    report = {"created_at": utc_now(), "status": state, "preparation_mode": not final,
              "missing": missing, "nested_repositories": nested, "official_exit_code": result["exit_code"],
              "official_check_evidence": str(directory / "official-check.json"),
              "not_verified": ["stage independence and genuine incremental history", "fixed-candidate provenance and full-room references",
                               "isolated container checks for every claimed stage", "real room video and public-release human review"],
              "ready_to_submit": False}
    if final:
        report["status"] = "HUMAN_REVIEW_REQUIRED" if report["status"] == "PASS" else "FAIL"
        report["actions"] = "Review provenance, incremental stage history, full-room export, real video, and isolated harness evidence; final approval remains human."
    write_json(directory / "report.json", report)
    return (0 if report["status"] == "EXPECTED_MISSING" else result["exit_code"] or (1 if report["status"] in ("FAIL", "HUMAN_REVIEW_REQUIRED") else 0)), report
