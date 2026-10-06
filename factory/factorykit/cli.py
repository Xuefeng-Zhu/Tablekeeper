"""Command-line interface. No command sends the judged task."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import yaml

from .common import FactoryError, load_config, redact, write_json
from .operations import dispatch_record, freeze, harness, launch_prepare, submission_check
from .tasks import generate, verify_tasks
from .validation import doctor, validate
from .workitems import validate_item


def emit(value):
    print(redact(json.dumps(value, indent=2, ensure_ascii=False)))


def execute(args):
    config = args.loaded_config
    if args.command == "doctor":
        result = doctor(config)
        emit(result)
        return int(result["status"] != "PASS")
    if args.command == "validate":
        result = validate(config)
        emit(result)
        return int(bool(result["errors"]) or (args.ready and bool(result["launch_blockers"])))
    if args.command == "generate-tasks":
        result = generate(config)
        emit({"status": "PASS", "tasks": {name: value["sha256"] for name, value in result["tasks"].items()}})
        return 0
    if args.command == "verify-tasks":
        result = verify_tasks(config)
        emit({"status": "PASS", "packet_count": len(result["tasks"])})
        return 0
    if args.command == "harness":
        code, result = harness(config, args.track, args.stage, args.all, args.mode)
        emit(result)
        return code
    if args.command == "freeze":
        result = freeze(config)
        emit({key: result[key] for key in ("status", "blockers", "dispatch_performed", "usage")})
        return int(result["status"] != "READY_TO_LAUNCH")
    if args.command == "launch-prepare":
        result = launch_prepare(config, args.mode, args.stage)
        emit(result)
        return int(result["status"] == "BLOCKED_WITH_ACTIONS")
    if args.command == "dispatch-record":
        emit(dispatch_record(config, args.preparation_id, args.room_event or "", args.acceptance))
        return 0
    if args.command == "submission-check":
        code, report = submission_check(config, final=args.final)
        emit(report)
        return code
    if args.command == "work-item":
        path = Path(args.file).resolve()
        try:
            item = yaml.safe_load(path.read_text()) if path.suffix in (".yaml", ".yml") else json.loads(path.read_text())
        except (OSError, ValueError, yaml.YAMLError):
            raise FactoryError("Work item is unreadable or malformed JSON/YAML") from None
        updated = validate_item(item, config, args.transition)
        if args.transition:
            if path.suffix in (".yaml", ".yml"):
                path.write_text(yaml.safe_dump(updated, sort_keys=False))
            else:
                write_json(path, updated)
        emit({"status": "PASS", "id": updated["id"], "state": updated["state"]})
        return 0
    raise FactoryError("Unknown command")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Prepare a reproducible seven-seat factory; never auto-dispatch judged work.")
    root.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "config/factory.yaml"), help="absolute configuration file path")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="observe local prerequisites and retain redacted evidence").set_defaults(func=execute)
    p = sub.add_parser("validate", help="check config, sources and generic mandate metadata")
    p.add_argument("--ready", action="store_true", help="also fail on unresolved launch prerequisites")
    p.set_defaults(func=execute)
    for name in ("generate-tasks", "verify-tasks", "freeze"):
        sub.add_parser(name).set_defaults(func=execute)
    p = sub.add_parser("harness", help="retain unique official harness evidence and propagate its exit code")
    p.add_argument("--track", choices=("toy", "tablekeeper"), required=True)
    scope = p.add_mutually_exclusive_group(required=True)
    scope.add_argument("--stage", type=int, choices=range(1, 5))
    scope.add_argument("--all", action="store_true")
    p.add_argument("--mode", choices=("host", "isolated"), default="isolated")
    p.set_defaults(func=execute)
    p = sub.add_parser("launch-prepare", help="write guarded human dispatch instructions; no messages sent")
    p.add_argument("--mode", choices=("all", "separate"), default="all")
    p.add_argument("--stage", type=int, choices=range(1, 5))
    p.set_defaults(func=execute)
    p = sub.add_parser("dispatch-record", help="record a real human dispatch/acceptance event; no messages sent")
    p.add_argument("--preparation-id", required=True)
    p.add_argument("--room-event")
    p.add_argument("--acceptance", help="absolute independent acceptance evidence file")
    p.set_defaults(func=execute)
    p = sub.add_parser("submission-check", help="official offline layout check; expected missing during preparation")
    p.add_argument("--final", action="store_true")
    p.set_defaults(func=execute)
    p = sub.add_parser("work-item", help="validate or transition a self-contained JSON/YAML handoff record")
    p.add_argument("file")
    p.add_argument("--transition", choices=("READY", "IN_PROGRESS", "REVIEW", "REJECTED", "ACCEPTED", "BLOCKED"))
    p.set_defaults(func=execute)
    try:
        from .runtime import register_commands
    except ImportError:
        pass
    else:
        register_commands(sub)
    return root


def main(argv=None) -> int:
    try:
        args = parser().parse_args(argv)
        args.config = str(Path(args.config).resolve())
        args.loaded_config = load_config(args.config)
        code = args.func(args)
        return code or 0
    except FactoryError as exc:
        emit({"status": "FAIL", "error": str(exc)})
        return 2
    except KeyboardInterrupt:
        emit({"status": "INTERRUPTED"})
        return 130
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Never echo config contents, subprocess environment or credential values.
        emit({"status": "FAIL", "error": f"{type(exc).__name__}: inspect local configuration or evidence files"})
        return 2
    except Exception as exc:
        from .runtime import GateError
        detail = str(exc) if isinstance(exc, GateError) else f"{type(exc).__name__}: operation failed; raw provider details withheld"
        emit({"status": "BLOCKED_WITH_ACTIONS", "error": detail})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
