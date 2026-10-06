"""Explicit, single-use paid permission probes through the maintained BAND adapter.

Importing this module is inert. Each configured model receives one synthetic
turn, charged to the existing rehearsal session ledger and request guard. The
local tools sink delivers no BAND messages. Only trusted native tool events and
the immutable helper's independently checked artifacts can produce readiness.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys

from . import harnesses, runtime
from .budgets import budget_blockers, persisted_guard_blockers, session_accounting
from .common import FactoryError, artifact_path, canonical, digest, load_config, redact, utc_now, write_json

CHECKS = ("permissions_agent_write_git", "permissions_docker_build", "permissions_browser",
          "permissions_development_network")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts/probe-opencode-live.py"
NETWORK_URL = "https://pypi.org/simple/pip/"


def require(condition, message):
    if not condition:
        raise FactoryError(message)


def _private_path(config, path):
    runs = Path(config["paths"]["runs"]).resolve()
    path = Path(path)
    require(path.is_absolute() and runs in path.resolve().parents, "Probe artifacts must stay under paths.runs")
    require(not any(p.is_symlink() for p in (path, *path.parents)
                    if p.resolve() == runs or runs in p.resolve().parents), "Probe paths must not use symlinks")
    for key in ("result", "rehearsal"):
        protected = Path(config["paths"][key]).resolve()
        require(not path.resolve().is_relative_to(protected), "Permission probes must stay outside product and rehearsal repositories")
    return path


def prerequisites(config):
    """Local gates only; neither credentials nor provider requests are printed."""
    require(harnesses.selected_harness(config) == "opencode", "This probe requires an OpenCode profile")
    errors = harnesses.validate_selection(config) + harnesses.permission_errors(config) + budget_blockers(config["budgets"])
    require(not errors, "; ".join(errors))
    require(session_accounting(config["budgets"]), "Paid verification requires the existing cumulative session accounting scope")
    require(config["runtime"].get("featherless_budget_guard") is not None, "Paid verification requires the approved Featherless request guard")
    require(all(config["runtime"]["native_permissions"].values()), "The four permission probes require the approved read/write/bash/network policy")
    require(config["runtime"].get("opencode_version") and config["runtime"].get("opencode_sha256"), "Pin the OpenCode executable version and SHA-256 before paid verification")
    require(os.environ.get(config["runtime"]["opencode_provider"]["api_key_env"]), "The configured provider credential is missing; no inference started")
    harnesses._featherless_metadata(config)
    credentials = runtime.credentials(config)
    owner = runtime.read_registry(config)
    require(not runtime.is_owned(owner.get("parent", {}), owner.get("token"))
            and not any(runtime.is_owned(child) for child in owner.get("children", [])),
            "Stop the owned factory runtime before using its accounting for permission probes")
    lock = artifact_path(config, "source_lock")
    require(lock.is_file() and not lock.is_symlink(), "A regular pinned source-lock is required before paid verification")
    require(all(Path(config["runtime"][name]).is_file() for name in ("python", "harness_python")), "The configured factory or harness Python is missing")
    require(SCRIPT.is_file(), "The immutable live-probe helper is missing")
    selected = {}
    for seat in config["seats"]:
        selected.setdefault(seat.get("model") or config["runtime"]["model"], seat)
    require(selected and len(selected) <= 2, "Permission verification admits at most the two configured approved models")
    return selected, credentials


def _claim(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with path.open("x") as stream:
            os.chmod(path, 0o600)
            stream.write(canonical(value).decode())
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except FileExistsError:
        raise FactoryError("This model's paid permission probe is already claimed; retries are prohibited") from None


@contextmanager
def _exclusive(config):
    path = _private_path(config, runtime.state_dir(config) / "opencode-live-probe.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FactoryError("Another permission probe owns this attempt") from None
        yield
    finally:
        os.close(descriptor)


class EvidenceSink:
    """Local adapter delivery only. No BAND client or credentials are retained."""
    def __init__(self, agent_id, room_id, path, secret_values=()):
        self.agent_id, self.room_id, self.path = agent_id, room_id, path
        self._ctx = None  # SDK failure reporting may inspect its execution context.
        self.events, self.replies, self.failed = [], [], False
        self.secret_values = tuple(value for value in secret_values if isinstance(value, str) and value)

    def safe(self, value):
        text = json.dumps(value, ensure_ascii=False)
        for secret in self.secret_values:
            text = text.replace(secret, "[REDACTED]")
        return json.loads(redact(text))

    async def send_event(self, content, message_type, metadata=None):
        if message_type == "thought":
            return
        event = {"type": str(message_type), "metadata": metadata or {}}
        if message_type in ("tool_call", "tool_result"):
            try:
                payload = json.loads(content)
                require(isinstance(payload, dict), "Malformed native tool evidence")
                event["tool"] = payload
            except (ValueError, TypeError):
                event["invalid_tool_evidence"] = True
        if message_type == "error" or (metadata or {}).get("failure"):
            self.failed = True
        event = self.safe(event)
        self.events.append(event)
        with self.path.open("a") as stream:
            os.chmod(self.path, 0o600)
            stream.write(json.dumps(event) + "\n")

    async def send_message(self, content, mentions=None):
        # Assistant prose is retained as non-authoritative summary only.
        self.replies.append(self.safe(str(content)[:8192]))

    async def send_failure(self, failure):
        self.failed = True

    async def execute_tool_call_structured(self, *args, **kwargs):
        raise FactoryError("BAND platform actions are unavailable in this local permission probe")

    async def get_participants(self):
        return []


def _command(argv, cwd, timeout=20):
    environment = dict(os.environ)
    if argv[0] == "git":
        # A caller's GIT_DIR or hooks must never redirect synthetic Git writes.
        environment = {key: value for key, value in environment.items() if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        argv = ["git", "-c", "core.hooksPath=" + os.devnull, *argv[1:]]
    result = subprocess.run(argv, cwd=cwd, env=environment, capture_output=True, text=True, timeout=timeout, check=False)
    require(result.returncode == 0, "Synthetic permission operation failed")
    return result.stdout.strip()


def _inspect_image(manifest):
    template = '{"id":{{json .Id}},"nonce":{{json (index .Config.Labels "factory.probe")}}}'
    data = json.loads(_command(["docker", "image", "inspect", manifest["image_tag"], "--format", template], manifest["workspace"]))
    require(isinstance(data, dict) and data.get("nonce") == manifest["nonce"]
            and re.fullmatch(r"sha256:[a-f0-9]{64}", data.get("id", "")), "Synthetic Docker image identity/label did not match")
    return data["id"]


def verify_evidence(manifest, expected_command, sink):
    """Require an actual SDK-native call/result pair, then inspect its effects."""
    calls = [event["tool"] for event in sink.events if event.get("type") == "tool_call" and "tool" in event]
    results = [event["tool"] for event in sink.events if event.get("type") == "tool_result" and "tool" in event]
    require(not sink.failed and len(calls) == len(results) == 1, "Permission proof needs exactly one successful native helper call; model claims do not count")
    call, result = calls[0], results[0]
    args = call.get("args", {})
    require(call.get("name") == result.get("name") == "bash" and args.get("command") == expected_command
            and args.get("workdir", manifest["workspace"]) == manifest["workspace"]
            and call.get("tool_call_id") and call["tool_call_id"] == result.get("tool_call_id")
            and result.get("is_error") is False, "Native tool evidence does not match the one authorized helper command")
    try:
        evidence = json.loads(result["output"])
    except (TypeError, ValueError, KeyError):
        raise FactoryError("Native helper output is missing or malformed") from None
    require(isinstance(evidence, dict) and evidence.get("nonce") == manifest["nonce"]
            and evidence.get("status") == "PASS", "Native helper did not prove every permission check")
    workspace = Path(manifest["workspace"])
    marker = (manifest["nonce"] + "\n").encode()
    require((workspace / "marker.txt").is_file() and not (workspace / "marker.txt").is_symlink()
            and (workspace / "marker.txt").read_bytes() == marker, "Agent workspace-write artifact is missing")
    commit = _command(["git", "rev-parse", "HEAD"], workspace)
    require(re.fullmatch(r"[a-f0-9]{40,64}", commit)
            and _command(["git", "show", "HEAD:marker.txt"], workspace) == manifest["nonce"]
            and evidence.get("git_commit") == commit, "Agent Git commit was not independently verified")
    image_id = _inspect_image(manifest)
    require(evidence.get("docker_image") == image_id, "Agent Docker build output differs from the observed image")
    screenshot = workspace / "browser.png"
    require(screenshot.is_file() and not screenshot.is_symlink() and screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
            and digest(screenshot) == evidence.get("browser", {}).get("screenshot_sha256")
            and evidence.get("browser", {}).get("observed_nonce") == manifest["nonce"], "Agent browser interaction artifact is missing or changed")
    network = evidence.get("development_network", {})
    require(network.get("url") == NETWORK_URL and network.get("http_status") == 200
            and re.fullmatch(r"[a-f0-9]{64}", network.get("sample_sha256", "")), "Agent development-network result is not verified")
    return {"git_commit": commit, "docker_image": image_id, "browser_screenshot_sha256": digest(screenshot),
            "development_network": network, "native_tool_call_id": call["tool_call_id"]}


def cleanup_image(manifest):
    try:
        identity = _inspect_image(manifest)
        _command(["docker", "image", "rm", "--no-prune", manifest["image_tag"]], manifest["workspace"])
        return {"status": "PASS", "image_id": identity, "removed_tag": manifest["image_tag"]}
    except (FactoryError, OSError, subprocess.SubprocessError, ValueError):
        return {"status": "NOT_VERIFIED", "tag": manifest["image_tag"]}


def publish_observations(config, report_path):
    from .source_snapshot import source_fingerprint
    report = json.loads(report_path.read_text())
    require(report.get("status") == "PASS" and report.get("configuration_sha256") == digest(canonical(config)),
            "Only completed probes bound to the original config can publish permission observations")
    path = _private_path(config, Path(config["paths"]["runs"]) / "readiness/observations.json")
    lock_hash = digest(artifact_path(config, "source_lock"))
    factory_hash = source_fingerprint(config)
    require(report.get("factory_source_sha256") == factory_hash,
            "Factory source changed since the permission probe; preserve evidence and rehearse the current source")
    expected_models = {seat.get("model") or config["runtime"]["model"] for seat in config["seats"]}
    require(report.get("source_lock_sha256") == lock_hash
            and {row.get("model") for row in report.get("models", [])} == expected_models
            and len(report.get("models", [])) == len(expected_models)
            and all(row.get("status") == "PASS" and row.get("verified") and row.get("cleanup", {}).get("status") == "PASS"
                    for row in report.get("models", [])), "Incomplete or drifted per-model proof cannot publish permission observations")
    if path.exists():
        saved = json.loads(path.read_text())
        require(saved.get("configuration_sha256") == report["configuration_sha256"]
                and saved.get("source_lock_sha256") == lock_hash
                and saved.get("factory_source_sha256") == factory_hash, "Existing readiness evidence belongs to another configuration or source; preserve and reconcile it")
    else:
        saved = {"configuration_sha256": report["configuration_sha256"], "source_lock_sha256": lock_hash,
                 "factory_source_sha256": factory_hash, "observations": []}
    entries = [entry for entry in saved["observations"] if entry.get("id") not in CHECKS]
    reference = {"path": str(report_path), "sha256": digest(report_path)}
    entries += [{"id": check, "status": "PASS", "observed": True, "observed_at": utc_now(),
                 "factory_source_sha256": factory_hash,
                 "observer": "BAND OpenCodeAdapter native tool events + immutable synthetic helper + artifact verification",
                 "evidence": [reference]} for check in CHECKS]
    write_json(path, {**saved, "observations": entries})


async def run_probes(config):
    from .source_snapshot import source_fingerprint
    selected, credentials = prerequisites(config)
    original_hash, source_hash = digest(canonical(config)), digest(artifact_path(config, "source_lock"))
    factory_hash = source_fingerprint(config)
    root = _private_path(config, Path(config["paths"]["runs"]) / "opencode-live-probe")
    root.mkdir(mode=0o700, exist_ok=True)
    claim_root = _private_path(config, runtime.state_dir(config) / "opencode-live-probe-claims")
    models = list(selected)
    require(not any((claim_root / (digest(model) + ".json")).exists() for model in models),
            "A configured model probe is already claimed; no automatic or manual retry is permitted by this runner")
    from band.core.types import AgentInput, HistoryProvider, PlatformMessage
    report = {"status": "FAIL", "configuration_sha256": original_hash,
              "factory_source_sha256": factory_hash,
              "runtime_configuration_sha256": runtime.fingerprint(config), "source_lock_sha256": source_hash,
              "observed_at": utc_now(), "scope": "paid synthetic permission verification; no BAND messages or product acceptance",
              "models": [], "accounting_mode": "rehearsal"}
    report_path = root / "evidence.json"
    helper_hashes = {str(SCRIPT): digest(SCRIPT), str(Path(__file__).resolve()): digest(Path(__file__).resolve())}
    probe = deepcopy(config)
    probe["runtime"]["opencode_state_root"] = str(root / "opencode-state")
    seats, manifests = [], {}
    for model, source_seat in selected.items():
        directory = _private_path(config, root / digest(model)[:16])
        require(not directory.exists(), "Disposable model probe directory already exists; preserve it for review")
        directory.mkdir(mode=0o700)
        workspace = directory / "workspace"
        workspace.mkdir(mode=0o700)
        nonce = secrets.token_hex(16)
        manifest = {"nonce": nonce, "workspace": str(workspace), "image_tag": "factory-permission-probe:" + nonce,
                    "browser_path": config["runtime"]["browser_path"], "network_url": NETWORK_URL,
                    "harness_python": config["runtime"]["harness_python"],
                    "helper_sha256": helper_hashes}
        manifest_path = directory / "manifest.json"
        write_json(manifest_path, manifest)
        manifests[model] = (manifest, manifest_path, digest(manifest_path))
        seat = deepcopy(source_seat)
        seat["rehearsal_cwd"] = str(workspace)
        seats.append(seat)
    probe["seats"] = seats
    secret_values = [os.environ.get(config["runtime"]["opencode_provider"]["api_key_env"])]
    secret_values.extend(value.get("api_key") for value in credentials.values())
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with _exclusive(config):
            async with harnesses.start_runtime(probe, "rehearsal", seats) as live:
                ledger = runtime.session_ledger(config, "rehearsal")
                for seat in seats:
                    require(source_fingerprint(config) == factory_hash,
                            "Factory source changed during permission verification; preserve claimed work")
                    model = seat["model"]
                    require(not ledger.reason(seat["id"]), "Existing session budget does not admit another permission-probe turn")
                    manifest, manifest_path, manifest_hash = manifests[model]
                    command = shlex.join([config["runtime"]["python"], str(SCRIPT), "--child-manifest", str(manifest_path)])
                    binding = live["runtime"]["_opencode_endpoints"][seat["id"]]["verification"]
                    claim = {"model": model, "seat": seat["id"], "claimed_at": utc_now(), "configuration_sha256": original_hash,
                             "source_lock_sha256": source_hash, "manifest_sha256": manifest_hash, "binding": binding}
                    _claim(claim_root / (digest(model) + ".json"), claim)
                    row = {**claim, "status": "FAIL", "helper_sha256": helper_hashes}
                    report["models"].append(row)
                    write_json(report_path, report)
                    prompt = ("Perform one synthetic permission verification in your assigned disposable workspace. "
                              "Call the native bash tool exactly once with the following command unchanged. Do not run any other "
                              "command, edit the helper or manifest, retry, or use BAND tools. The helper performs all four checks "
                              "and returns JSON. After it finishes, reply briefly. Command:\n" + command)
                    msg = PlatformMessage(id="probe-" + manifest["nonce"], room_id=config["band"]["rehearsal_room_id"],
                                          content=prompt, sender_id="factory-local-probe", sender_type="user", sender_name="Factory permission probe",
                                          message_type="message", metadata={}, created_at=datetime.now(timezone.utc))
                    require(ledger.reserve_event(seat["id"], msg), "Session budget refused the claimed probe turn")
                    directory = manifest_path.parent
                    sink = EvidenceSink(seat["agent_id"], msg.room_id, directory / "native-events.jsonl", secret_values)
                    wrapped = runtime.AuditedTools(sink, ledger, seat["id"], directory / "accounting-events.jsonl",
                                                    harness="opencode", turn_id=msg.id)
                    config_adapter = harnesses.alternate_adapter_config(live, seat, "rehearsal", Path(manifest["workspace"]),
                                                                        "Only the exact synthetic helper command is authorized. No BAND actions.", {})
                    backend = harnesses.adapter_class(live)
                    class LocalProbeAdapter(backend):
                        def _mcp_tool_visibility(self):
                            visibility = super()._mcp_tool_visibility()
                            visibility[self._mcp_server_name + "_*"] = False
                            return visibility
                        def _apply_message_update(self, room_state, info):
                            if info is not None and info.role == "assistant":
                                self.live_routes.add((getattr(info, "providerID", None), getattr(info, "modelID", None)))
                            return super()._apply_message_update(room_state, info)
                    adapter = LocalProbeAdapter(config=config_adapter, **harnesses.adapter_options(live))
                    adapter.live_routes = set()
                    try:
                        await adapter.on_started("Factory permission probe", "Synthetic local verification")
                        inp = AgentInput(msg=msg, tools=wrapped, history=HistoryProvider([]), participants_msg=None,
                                         contacts_msg=None, is_session_bootstrap=True, room_id=msg.room_id)
                        async with asyncio.timeout(config["budgets"]["turn_timeout_seconds"] + 30):
                            await runtime.accounted_adapter_turn(adapter.on_event, inp, wrapped)
                        require(wrapped.usage_observed and not getattr(wrapped, "terminal_status", None) == "failed",
                                "Adapter usage or successful completion is missing")
                        require(adapter.live_routes == {tuple(model.split("/", 1))},
                                "Actual assistant message provider/model routing did not match the selected model")
                        row["actual_model_route"] = model
                        require(not persisted_guard_blockers(config, require_existing=True),
                                "Request guard is stopped, unsettled or invalid; auxiliary failures cannot pass permission verification")
                        require(digest(manifest_path) == manifest_hash and all(digest(Path(path)) == value for path, value in helper_hashes.items()),
                                "Immutable helper or manifest changed during the probe")
                        row["verified"] = verify_evidence(manifest, command, sink)
                        row["status"] = "PASS"
                    except BaseException as error:
                        row["failure_type"] = type(error).__name__
                        ledger.halt("OpenCode permission probe failed; preserve claimed turn and reconcile before further work")
                        raise
                    finally:
                        try:
                            await adapter.on_cleanup(msg.room_id)
                        finally:
                            row["cleanup"] = cleanup_image(manifest)
                            row["tool_evidence"] = {"path": str(sink.path), "sha256": digest(sink.path)} if sink.path.is_file() else None
                            row["session_tokens_after"] = ledger.data["tokens"]
                            write_json(report_path, report)
                    require(row["cleanup"]["status"] == "PASS", "Probe Docker cleanup did not complete; inspect the unique retained resource")
                require(not persisted_guard_blockers(config, require_existing=True), "Request guard must be fully settled before publishing permission observations")
                require(source_fingerprint(config) == factory_hash,
                        "Factory source changed during permission verification; a fresh proof is required")
                report["status"] = "PASS"
                write_json(report_path, report)
    finally:
        logging.disable(previous_logging)
    publish_observations(config, report_path)
    return report_path


def child_probe(manifest_path):
    """Executed only by the agent's native tool; never by runner verification."""
    import http.server
    import threading
    import urllib.request
    manifest = json.loads(Path(manifest_path).read_text())
    workspace = Path(manifest["workspace"])
    require(Path.cwd().resolve() == workspace.resolve(), "Helper must run in its assigned disposable workspace")
    require(re.fullmatch(r"[a-f0-9]{32}", manifest["nonce"]), "Invalid synthetic nonce")
    require(manifest["network_url"] == NETWORK_URL and manifest["image_tag"] == "factory-permission-probe:" + manifest["nonce"], "Unexpected synthetic fixture binding")
    _claim(workspace / ".helper-claimed", {"nonce": manifest["nonce"]})
    result = {"status": "FAIL", "nonce": manifest["nonce"]}
    try:
        (workspace / "marker.txt").write_text(manifest["nonce"] + "\n")
        _command(["git", "init", "-q", "-b", "main"], workspace)
        _command(["git", "add", "marker.txt"], workspace)
        _command(["git", "-c", "user.name=Factory Probe", "-c", "user.email=probe@factory.invalid", "commit", "-q", "-m", "Synthetic permission proof"], workspace)
        result["git_commit"] = _command(["git", "rev-parse", "HEAD"], workspace)
        (workspace / "Dockerfile").write_text("FROM scratch\nCOPY marker.txt /marker.txt\nLABEL factory.probe=" + manifest["nonce"] + "\n")
        _command(["docker", "build", "--network=none", "--pull=false", "-t", manifest["image_tag"], "."], workspace, timeout=120)
        result["docker_image"] = _inspect_image(manifest)
        (workspace / "fixture.html").write_text('<!doctype html><title>Factory permission probe</title><input id="value"><button id="go" onclick="document.querySelector(\'#result\').textContent=document.querySelector(\'#value\').value">Run</button><output id="result"></output>')
        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(workspace), **kwargs)
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = manifest["browser_path"]
        try:
            browser_script = """import sys
from playwright.sync_api import sync_playwright
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        page = browser.new_page()
        page.goto(sys.argv[1], timeout=15000)
        page.fill('#value', sys.argv[2])
        page.click('#go')
        assert page.text_content('#result') == sys.argv[2]
        page.screenshot(path=sys.argv[3])
    finally:
        browser.close()
"""
            _command([manifest["harness_python"], "-c", browser_script,
                      f"http://127.0.0.1:{server.server_port}/fixture.html", manifest["nonce"], str(workspace / "browser.png")],
                     workspace, timeout=45)
            result["browser"] = {"observed_nonce": manifest["nonce"], "screenshot_sha256": digest(workspace / "browser.png")}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        with urllib.request.build_opener(NoRedirect).open(NETWORK_URL, timeout=15) as response:
            require(response.status == 200, "Development network request failed")
            result["development_network"] = {"url": NETWORK_URL, "http_status": response.status,
                                              "sample_sha256": digest(response.read(1024))}
        result["status"] = "PASS"
    except Exception as error:
        result["failure_type"] = type(error).__name__
    print(json.dumps(result))
    return 0 if result["status"] == "PASS" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--execute-live-probes", action="store_true", help="Explicitly admit one paid synthetic turn per configured model; never retries")
    parser.add_argument("--child-manifest", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.child_manifest:
            return child_probe(args.child_manifest)
        require(args.config and args.execute_live_probes, "Supply --config and --execute-live-probes only after approving the paid verification")
        path = asyncio.run(run_probes(load_config(args.config)))
        print(json.dumps({"status": "PASS", "evidence": str(path), "inference_started": True}))
        return 0
    except (FactoryError, runtime.GateError) as error:
        print(str(error), file=sys.stderr)
        return 2
