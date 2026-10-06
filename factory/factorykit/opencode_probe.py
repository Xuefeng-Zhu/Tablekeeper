"""No-inference compatibility evidence for a pinned OpenCode executable.

Only empty sessions are created on the disposable local server. Prompt,
permission, question and MCP request serialization is exercised exclusively
through httpx.MockTransport, then checked against the live server's /doc.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
from importlib.metadata import version
import inspect
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator

from . import harnesses
from .common import FactoryError, utc_now

SESSION = "ses_factoryprobe"
MODEL = "factory-probe/probe-model"
PROBE_ENV = "FACTORY_PROTOCOL_PROBE_KEY"


def require(condition, check):
    if not condition:
        raise FactoryError(f"OpenCode compatibility check failed: {check}")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_schema(doc, schema, value, check):
    # Resolve only the server's local schema references, never remote URLs.
    def local(node):
        if isinstance(node, dict):
            require("$ref" not in node or str(node["$ref"]).startswith("#/"), "external schema reference")
            for child in node.values():
                local(child)
        elif isinstance(node, list):
            for child in node:
                local(child)
    combined = {**schema, "components": doc.get("components", {})}
    local(combined)
    require(Draft202012Validator(combined).is_valid(value), check)


def operation(doc, method, path):
    for template, item in doc.get("paths", {}).items():
        parts, actual = template.split("/"), path.split("/")
        if len(parts) != len(actual):
            continue
        values = {}
        for expected, observed in zip(parts, actual):
            if expected.startswith("{") and expected.endswith("}"):
                values[expected[1:-1]] = observed
            elif expected != observed:
                break
        else:
            if method.lower() in item:
                return template, item[method.lower()], values
    raise FactoryError(f"OpenCode compatibility check failed: missing {method} route")


def request_contract(doc, request):
    template, item, path_values = operation(doc, request.method, request.url.path)
    parameters = {("path", key): value for key, value in path_values.items()}
    parameters.update({("query", key): value for key, value in request.url.params.items()})
    for parameter in item.get("parameters", []):
        key = (parameter["in"], parameter["name"])
        if parameter.get("required"):
            require(key in parameters, "required SDK request parameter")
        if key in parameters:
            validate_schema(doc, parameter.get("schema", {}), parameters[key], "SDK parameter schema")
    if request.content:
        schema = item.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        require(schema is not None, "SDK request body documented")
        validate_schema(doc, schema, json.loads(request.content), "SDK request body schema")
    return {"method": request.method, "path": template, "operation_id": item.get("operationId"),
            "deprecated": item.get("deprecated", False)}


def event_fixtures():
    def event(kind, props):
        return {"id": "evt_factoryprobe", "type": kind, "properties": props}
    info = {"id": "msg_factoryprobe", "sessionID": SESSION, "role": "assistant", "time": {"created": 1},
            "parentID": "msg_parent", "modelID": "probe-model", "providerID": "factory-probe", "mode": "factory",
            "agent": "factory", "path": {"cwd": "/probe", "root": "/probe"}, "cost": 0,
            "tokens": {"input": 11, "output": 3, "reasoning": 2, "cache": {"read": 5, "write": 7}}}
    part = {"id": "prt_factoryprobe", "sessionID": SESSION, "messageID": "msg_factoryprobe", "type": "tool",
            "callID": "call_probe", "tool": "band_get_participants", "state": {"status": "completed", "input": {},
            "output": "synthetic", "title": "Protocol fixture", "metadata": {}, "time": {"start": 1, "end": 2}}}
    return [event("message.updated", {"sessionID": SESSION, "info": info}),
            event("message.part.updated", {"sessionID": SESSION, "part": part, "time": 2}),
            event("message.part.delta", {"sessionID": SESSION, "messageID": "msg_factoryprobe",
                                          "partID": "prt_factoryprobe", "field": "text", "delta": "synthetic"}),
            event("permission.asked", {"id": "per_factoryprobe", "sessionID": SESSION, "permission": "bash",
                                        "patterns": ["*"], "metadata": {}, "always": []}),
            event("question.asked", {"id": "que_factoryprobe", "sessionID": SESSION,
                                      "questions": [{"question": "Synthetic?", "header": "Probe", "options": []}]}),
            event("session.error", {"sessionID": SESSION, "error": {"name": "UnknownError",
                                                                      "data": {"message": "synthetic"}}}),
            event("session.idle", {"sessionID": SESSION})]


async def sdk_contract(doc, directory):
    from band.integrations.opencode.client import HttpOpencodeClient
    from band.integrations.opencode.events import UnknownOpencodeEvent, parse_opencode_event
    fixtures, requests = event_fixtures(), []
    event_schema = operation(doc, "GET", "/event")[1]["responses"]["200"]["content"]["text/event-stream"]["schema"]
    for fixture in fixtures:
        validate_schema(doc, event_schema, fixture, "published SSE event schema")
    # Exercise event-name fallback and event IDs through the real SDK SSE decoder.
    frames = "".join(f"id: fixture-{index}\nevent: {fixture['type']}\ndata: " +
                     json.dumps({key: value for key, value in fixture.items() if key != "type"}) + "\n\n"
                     for index, fixture in enumerate(fixtures))
    async def respond(request):
        requests.append(request)
        if request.url.path == "/event":
            return httpx.Response(200, text=frames, headers={"Content-Type": "text/event-stream"})
        return httpx.Response(200, json={"id": SESSION})
    client = HttpOpencodeClient(base_url="http://factory-probe.invalid", directory=directory,
                                transport=httpx.MockTransport(respond))
    try:
        await client.health()
        await client.create_session(title="Protocol fixture")
        await client.get_session(SESSION)
        await client.prompt_async(SESSION, parts=[{"type": "text", "text": "Serialization fixture only"}],
                                  system="fixture", model={"providerID": "factory-probe", "modelID": "probe-model"},
                                  agent="factory", variant="fixture", tools={"*": False})
        await client.reply_permission(SESSION, "per_factoryprobe", response="reject")
        await client.reply_question("que_factoryprobe", answers=[["fixture"]])
        await client.reject_question("que_factoryprobe")
        await client.abort_session(SESSION)
        await client.register_mcp_server(name="factory_probe", url="http://127.0.0.1:9/mcp")
        await client.disconnect_mcp_server("factory_probe")
        decoded = [event async for event in client.iter_events()]
        require(decoded == fixtures and client._last_event_id == "fixture-6", "SDK SSE frame and event-ID decoding")
        typed = [parse_opencode_event(event) for event in decoded]
        require(all(not isinstance(event, UnknownOpencodeEvent) and event.session_id == SESSION for event in typed),
                "SDK typed event session binding")
        usage = typed[0].properties.info.tokens.to_turn_usage()
        require((usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (11, 5, 5, 7),
                "SDK usage counter interpretation")
        require(typed[1].properties.part.state.reported_output == "synthetic" and
                typed[2].properties.delta == "synthetic" and typed[3].properties.id == "per_factoryprobe" and
                typed[4].properties.id == "que_factoryprobe" and
                typed[5].properties.error.describe() == "UnknownError: synthetic", "SDK event payload interpretation")
        checked = [request_contract(doc, request) for request in requests]
        require(all(request.url.params.get("directory") == directory and
                    request.headers.get("x-opencode-directory") == directory
                    for request in requests if request.url.path != "/global/health"), "SDK directory binding")
        return {"requests": checked, "event_types": [event["type"] for event in fixtures],
                "fixtures_sha256": digest(fixtures), "prompt_serialization_only": True,
                "live_prompt_requests": 0}
    finally:
        await client.close()


def synthetic_config(root, command, expected_version, expected_hash):
    seat = {"id": "probe", "harness": "OpenCode", "model": MODEL,
            "git_name": "Factory Protocol Probe", "git_email": "probe@factory.invalid"}
    return {"paths": {"runs": str(root), "rehearsal": str(root), "result": str(root)}, "seats": [seat],
            "runtime": {"harness": "opencode", "opencode_command": str(command), "opencode_version": expected_version,
                        "opencode_sha256": expected_hash, "opencode_state_root": str(root / "state"), "model": MODEL,
                        "native_permissions": dict.fromkeys(("read", "write", "bash", "network"), False),
                        "opencode_provider": {"id": "factory-probe", "npm": "@ai-sdk/openai-compatible",
                            "base_url": "https://factory-probe.invalid/v1", "api_key_env": PROBE_ENV,
                            "models": {"probe-model": {"limit": {"context": 32768, "output": 1024}}}}},
            "budgets": {"turn_timeout_seconds": 10, "billing_mode": "spend_cap"}}


def live_request_guard(requests):
    async def permit(request):
        path = request.url.path
        allowed = request.method == "GET" and (path in ("/doc", "/event", "/global/health") or
                                                bool(re.fullmatch(r"/session/[^/]+", path)))
        allowed |= request.method == "POST" and (path == "/session" or bool(re.fullmatch(r"/session/[^/]+/abort", path)))
        allowed |= request.method == "DELETE" and bool(re.fullmatch(r"/session/[^/]+", path))
        require(allowed and request.url.host == "127.0.0.1", "live request allowlist")
        requests.append((request.method, path))
    return permit


@contextmanager
def synthetic_credentials():
    previous = os.environ.get(PROBE_ENV)
    os.environ[PROBE_ENV] = "synthetic-no-provider-access"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(PROBE_ENV, None)
        else:
            os.environ[PROBE_ENV] = previous


async def probe(command, expected_version, expected_hash):
    from band.integrations.opencode.client import HttpOpencodeClient
    require(version("band-sdk") == "4.0.0", "expected BAND SDK 4.0.0")
    with tempfile.TemporaryDirectory(prefix="factory-opencode-probe-") as directory, synthetic_credentials():
        root = Path(directory).resolve()
        directory = str(root)
        config = synthetic_config(root, command, expected_version, expected_hash)
        requests = []
        async with harnesses.start_runtime(config, "rehearsal", config["seats"]) as live:
            endpoint = live["runtime"]["_opencode_endpoints"]["probe"]
            require(urlsplit(endpoint["url"]).hostname == "127.0.0.1", "loopback server")
            adapter_config = harnesses.alternate_adapter_config(live, config["seats"][0], "rehearsal", root, "", {})
            adapter = harnesses.adapter_class(live)(config=adapter_config, **harnesses.adapter_options(live))
            client = adapter._default_client_factory(adapter_config)
            client._client.event_hooks["request"] = [live_request_guard(requests)]
            stream = client.iter_events()
            try:
                response = await client._client.get("/doc")
                response.raise_for_status()
                doc = response.json()
                require(doc.get("openapi", "").startswith("3.1"), "OpenAPI 3.1 schema")
                contract = await sdk_contract(doc, directory)
                await client.health()
                connected = await asyncio.wait_for(anext(stream), 5)
                require(connected.get("type") == "server.connected", "live SSE connected event")
                session = await client.create_session(title="Factory protocol probe; no prompt")
                session_id = session.get("id")
                require(isinstance(session_id, str) and session_id.startswith("ses"), "empty session identity")
                require(session.get("directory") == directory, "live session directory")
                observed = await client.get_session(session_id)
                require(observed.get("id") == session_id and observed.get("directory") == directory, "SDK session readback")
                event_schema = operation(doc, "GET", "/event")[1]["responses"]["200"]["content"]["text/event-stream"]["schema"]
                validate_schema(doc, event_schema, connected, "live connected event schema")
                async with asyncio.timeout(5):
                    while True:
                        created = await anext(stream)
                        if created.get("type") == "session.created":
                            require(created.get("properties", {}).get("info", {}).get("id") == session_id,
                                    "live SSE created-session binding")
                            validate_schema(doc, event_schema, created, "live session event schema")
                            break
                await client.abort_session(session_id)
                response = await client._client.delete(f"/session/{session_id}", params={"directory": directory})
                response.raise_for_status()
                evidence = {"status": "PASS", "observed_at": utc_now(), "band_sdk": version("band-sdk"),
                            "binding": endpoint["verification"], "openapi_sha256": digest(doc),
                            "sdk_client_sha256": hashlib.sha256(Path(inspect.getsourcefile(HttpOpencodeClient)).read_bytes()).hexdigest(),
                            "contract": contract, "live": {"authenticated_sdk_transport": True, "empty_session_round_trip": True,
                            "event_types": [connected["type"], created["type"]], "session_aborted_and_deleted": True,
                            "request_count": len(requests)}, "inference_started": False,
                            "provider_authentication_verified": False, "provider_tool_calls_verified": False}
            finally:
                await stream.aclose()
                await client.close()
        async with httpx.AsyncClient(timeout=1, trust_env=False) as closed:
            try:
                await closed.get(endpoint["url"] + "/global/health")
            except httpx.ConnectError:
                evidence["owned_server_stopped"] = True
            else:
                raise FactoryError("OpenCode compatibility check failed: owned server cleanup")
    evidence["temporary_state_removed"] = not root.exists()
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", required=True, type=Path, help="absolute pinned OpenCode executable")
    parser.add_argument("--version", required=True, help="exact approved CLI/server version")
    parser.add_argument("--sha256", required=True, help="approved executable SHA-256, not the release archive hash")
    parser.add_argument("--output", required=True, type=Path, help="new absolute evidence JSON path; never overwritten")
    args = parser.parse_args(argv)
    require(args.command.is_absolute(), "absolute executable path")
    require(args.output.is_absolute() and args.output.parent.is_dir(), "existing absolute output parent")
    require(not args.output.exists() and not args.output.is_symlink(), "new output path")
    try:
        async def bounded():
            async with asyncio.timeout(60):
                return await probe(args.command, args.version, args.sha256)
        report = asyncio.run(bounded())
    except Exception as error:
        report = {"status": "FAIL", "observed_at": utc_now(), "error_type": type(error).__name__,
                  "error": str(error) if isinstance(error, FactoryError) else "Protocol probe failed; no raw provider response logged.",
                  "inference_started": False}
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1
