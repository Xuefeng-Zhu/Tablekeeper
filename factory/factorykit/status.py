"""Read-only status from persisted evidence, with no README or transcript parsing."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from .common import FactoryError, canonical, digest, redact, run_command
from .budgets import budget_ledger_path
from .source_snapshot import frozen_source_errors, verify_source_snapshot


def _read(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, (dict, list)) else None
    except (OSError, ValueError):
        return None


def current_status(config, *, mode="judged", now=None, runner=run_command):
    """No supervisor creation, command verification, network traffic or file writes.

    Owner status is explicitly persisted status, not a claim of process liveness.
    Queue acceptance is reported as a claim; this read does not re-run review.
    """
    now = now or datetime.now(timezone.utc)
    root = Path(config["paths"]["runs"])
    runtime = root / "runtime"
    owner = _read(runtime / "owner.json")
    owner = owner if isinstance(owner, dict) else {}
    budget_path = budget_ledger_path(config, mode)
    budget = _read(budget_path)
    budget = budget if isinstance(budget, dict) else {}
    workflow = owner.get("workflow", {})
    workflow = workflow if isinstance(workflow, dict) else {}
    recorded = owner.get("updated_at")
    try:
        age = max(0, (now - datetime.fromisoformat(recorded.replace("Z", "+00:00"))).total_seconds())
    except (TypeError, ValueError, AttributeError):
        age = None
    run_state = owner.get("status", "unknown")
    if run_state not in {"running", "stopped", "starting", "failed", "stopping"}:
        run_state = "unknown"
    stop_reason = budget.get("stopped_reason")
    room = config.get("band", {}).get(f"{mode}_room_id")
    room_reason = budget.get("room_stopped_reasons", {}).get(room) if isinstance(budget.get("room_stopped_reasons"), dict) else None
    snapshot = {
        "observed_at": now.isoformat(),
        "runtime": {"persisted_status": run_state, "updated_at": recorded, "snapshot_age_seconds": age,
                    "process_liveness": "not_checked", "workflow_state": workflow.get("state", "unknown"),
                    "active_turn_count": len(workflow.get("active_turn_ids", [])),
                    "unresolved_incident_count": len(workflow.get("unresolved_incident_ids", [])),
                    "stop_reason": redact(str(owner["stop_reason"])) if owner.get("stop_reason") else None,
                    "budget_stop_reason": redact(str(stop_reason)) if stop_reason else None,
                    "room_stop_reason": redact(str(room_reason)) if room_reason else None},
        "accounting": {"ledger_path": str(budget_path), "reported_tokens": budget.get("tokens"), "updated_at": budget.get("updated_at")},
    }
    freeze = _read(root / "freeze/latest.json")
    if isinstance(freeze, dict) and isinstance(freeze.get("files"), dict):
        errors = frozen_source_errors(config, freeze)
        changed = [s.split(": ", 1)[1] for s in errors if s.startswith(("Frozen input changed: ", "Frozen input added: "))]
        snapshot["source"] = {"state": "disk_drift" if errors else "disk_matches_freeze",
                              "changed_files": changed, "errors": errors, "checked_files": len(freeze["files"]),
                              "configuration_matches_freeze": freeze.get("configuration_sha256") == digest(canonical(config)),
                              "loaded_process_bytes": "not_established"}
    else:
        snapshot["source"] = {"state": "freeze_missing_or_invalid", "loaded_process_bytes": "not_established"}
    descriptor = owner.get("source_snapshot")
    if isinstance(descriptor, dict):
        try:
            result = verify_source_snapshot(config, Path(descriptor["root"]),
                                            expected_manifest_sha256=descriptor["manifest_sha256"])
            snapshot["source"]["recorded_snapshot"] = {"state": "verified_on_disk", "root": result["root"],
                                                        "manifest_sha256": result["manifest_sha256"]}
        except (FactoryError, KeyError, TypeError):
            snapshot["source"]["recorded_snapshot"] = {"state": "invalid_or_missing"}
    else:
        snapshot["source"]["recorded_snapshot"] = {"state": "not_recorded"}
    repository = Path(config["paths"]["result" if mode == "judged" else "rehearsal"])
    head = runner(["/usr/bin/git", "rev-parse", "HEAD"], repository, timeout=10)
    queue = _read(repository / "planning/queue.json")
    items = [r for r in queue if isinstance(r, dict)] if isinstance(queue, list) else []
    snapshot["product"] = {"candidate_commit": head["stdout"].strip() if head.get("exit_code") == 0 else None,
                           "queue_available": isinstance(queue, list),
                           "items": [{"id": r.get("id"), "state": r.get("state"),
                                      "candidate_commit": r.get("candidate_commit")} for r in items],
                           "acceptance": "not_independently_verified_by_status"}
    observation = _read(root / "readiness/observations.json")
    rows = observation.get("observations", []) if isinstance(observation, dict) else []
    historical = [r for r in rows if isinstance(r, dict) and "historical_provenance" in r]
    snapshot["rehearsal"] = {"observations_available": isinstance(observation, dict),
                             "historical_observation_count": len(historical),
                             "historical_observed_at": sorted({r["observed_at"] for r in historical if isinstance(r.get("observed_at"), str)}),
                             "configuration_matches_observation": observation.get("configuration_sha256") == digest(canonical(config)) if isinstance(observation, dict) else False,
                             "live_current_rehearsal": "not_established_by_observation_metadata"}
    progress_path = runtime / (f"progress-{room}.json" if room else "progress.json")
    progress = _read(progress_path)
    if isinstance(progress, dict):
        snapshot["progress"] = {"state_path": str(progress_path), "pending": bool(progress.get("pending")),
                                "blocked_reason": progress.get("blocked_reason"),
                                "completed_milestones": [r.get("id") for r in progress.get("completed", []) if isinstance(r, dict)],
                                "product_acceptance": "not_established"}
    else:
        snapshot["progress"] = {"state": "not_recorded"}
    return snapshot


def status_report(config, mode="judged"):
    return current_status(config, mode=mode)
