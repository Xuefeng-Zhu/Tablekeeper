"""Deterministic dispatch packets with complete byte-verified source payloads."""
from __future__ import annotations

import json
from pathlib import Path

from .common import FactoryError, artifact_path, canonical, digest, product_repository, source_lock, verify_sources, write_json

LAUNCHER_BOUNDARY = """## Launcher admission and seat execution
The launcher checks authentication, approved finite budgets, registered identities
and recorded permission evidence before connecting seats. For a seat connected by
this launcher, those admission gates have already been enforced by the operator;
do not repeat them inside the task sandbox.
Do not run operator-only doctor, preflight, registration/authentication probes,
freeze, launch, start-seats or stop-seats commands; do not call
subscription_auth_probe or preflight_runtime or create nested Codex app servers.
Use the injected roster, limits and workspace metadata to execute the delivered
task. Test required operations in your assigned workspace and report any actual
denied operation with evidence; do not reinterpret an operator probe's sandbox
failure as a failed task prerequisite or try to repair operator infrastructure.
Admission does not establish product correctness, seat smoke results, peer
collaboration or stage acceptance. Perform those assigned checks and retain their
actual evidence within the existing permissions and limits.
"""


def launcher_boundary(config: dict) -> str:
    if config['budgets'].get('balance_only') is True:
        return LAUNCHER_BOUNDARY.replace('approved finite budgets', 'the approved $25 balance cap')\
            .replace('roster, limits and workspace metadata', 'roster, balance policy and workspace metadata')\
            .replace('within the existing permissions and limits', 'within the existing permissions and $25 balance cap')
    return LAUNCHER_BOUNDARY

JUDGED_PREFERENCES = """## Task preferences and ownership
Product proposal: Tablekeeper, a coherent restaurant reservation experience.
Starting technical proposal: TypeScript, React/Vite, a small Node HTTP API, SQLite,
and a tested timezone adapter. The Architect may choose a better compliant option
and record why. The official specifications always take priority.
Package one self-contained container per completed stage. No hosted authentication,
database, CDN, or AI API is allowed as an application runtime dependency.
Stage 1: complete API behavior, atomic changes, retry correctness, time handling,
and portability. Stage 2: polished search/booking/lookup, approved table pairs,
delayed-response handling, and reliable uncertain-submission recovery.
Stage 3: policy selection, accepted-term history, revisions, recurring agreements,
and upgrade continuity. Stage 4: deterministic seating replanning, atomic application,
and series amendments. Preserve genuine incremental earlier stage outputs; do not
copy a final implementation backwards. Attempt all stages and label incomplete work.
Prioritize required behavior and a polished Stage 2 over optional dashboards,
integrations, or decorative assets.
The PM creates the actual product brief and requirement map from the exact specs.
The Architect records implementable architecture decisions before corresponding
implementation. The Designer creates journeys, screen hierarchy, interaction states,
responsive specifications and design reviews before corresponding UI implementation.
Use the empty templates as record formats; they contain no preselected decisions.
Designer direction: warm hospitality, clear hierarchy and human-readable labels;
inspect desktop and 375px rendered layouts, keyboard focus and detailed feedback
states. Required routes and test attributes come only from the exact official spec.
Clearly mark optional visual ideas. QA independently authors actual behavior tests
covering late search responses, conflicts, lost responses, retries and upgrades.
"""


def _header(config: dict, track: str, stages: list[int], mode: str, *, locked_sources: dict | None = None) -> str:
    balance_only = config['budgets'].get('balance_only') is True
    execution_platform = config["runtime"].get("execution_platform", "darwin")
    if execution_platform not in ("darwin", "linux"):
        raise FactoryError("runtime.execution_platform must be darwin or linux")
    unresolved = any(not s.get("handle") or not s.get("model") for s in config["seats"])
    paths = config["paths"]
    practice = track == "toy" or config["launch"].get("practice_mode", False)
    run_label = "Practice (unscored; eligibility not claimed)" if practice else "Judged"
    stage_label = (f"{stages[0]} through {stages[-1]}" if len(stages) > 1
                   and stages == list(range(stages[0], stages[-1] + 1)) else ", ".join(map(str, stages)))
    lines = [f"# {run_label} dispatch packet — {track}", "",
             f"Packet state: {'BLOCKED_UNRESOLVED_ROSTER' if unresolved else 'REQUIRES_READY_FREEZE' if track == 'tablekeeper' else 'REQUIRES_REHEARSAL_PREFLIGHT'}",
             f"Dispatch mode: {mode}; stages: {', '.join(map(str, stages))}.",
             (f"Execute only stage {stages[0]}. Earlier specifications are inherited requirements, not new dispatches. Do not execute a future stage until its own separate dispatch." if mode == "separate" else f"Execute {'stage' if len(stages) == 1 else 'stages'} {stage_label} once in increasing order, with an independent gate before advancing."),
             ("This file is preparation only. Do not dispatch before the freeze reports READY_TO_LAUNCH." if track == "tablekeeper" else
              "Operator dispatch prerequisite: rehearsal runtime preflight must pass and the $25 balance cap must be approved before the launcher connects seats. A judged freeze is not required for rehearsal. Seats do not rerun operator preflight." if balance_only else
              "Operator dispatch prerequisite: rehearsal runtime preflight must pass and finite live budgets must be approved before the launcher connects seats. A judged freeze is not required for rehearsal. Seats do not rerun operator preflight."),
             f"Pinned challenge commit: {(source_lock(config) if locked_sources is None else locked_sources)['challenge']['commit']}",
             f"Configuration SHA-256: {digest(canonical(config))}", "", "## Absolute workspace paths"]
    lines.extend(f"- {name}: `{value}`" for name, value in paths.items())
    lines.extend([f"- Assigned output checkout: `{paths['result'] if track == 'tablekeeper' else paths['rehearsal']}`", "", "## Actual roster"])
    if track == "tablekeeper" and (product := product_repository(config)):
        lines[-1:-1] = [
            "## Attempt repository and branch",
            f"- Exact origin fetch and push URL: `{product['repository_url']}`.",
            f"- Assigned branch: `{product['branch']}`; push new attributable progress commits with `git push origin HEAD:refs/heads/{product['branch']}`.",
            "- Begin from the assigned empty, unborn branch. Do not check out, merge, cherry-pick or copy implementation from another attempt or the repository's default branch.",
            "- Preserve all other branches. Do not force-push, rewrite history, change origin or create another application repository.",
            "",
        ]
    # Operational guidance belongs in dispatch metadata, not track-specific mandates.
    lines[-1] = launcher_boundary(config) + "\n## Execution environment"
    output = paths["result"] if track == "tablekeeper" else paths["rehearsal"]
    lines.extend([
        f"- Writable product checkout and Git metadata: `{output}`. One active writer is enforced.",
        f"- Store team test logs, screenshots and results under `{output}/.evidence` in unique run directories; keep generated caches out of commits.",
        "- Use the platform temporary directory for clean exact-candidate review clones outside stage folders and for dependency caches. Read-only access to sources and factory tools does not grant write access to their directories.",
        "- The runs directory contains operator-owned control records. Do not alter launcher, usage, dispatch or readiness files or attempt to broaden permissions.",
        f"- Pinned harness interpreter: `{config['runtime']['harness_python']}`. Run the official `-m harness run` from the challenge directory, with `--track {track}`, an absolute `--repo` and `--out` inside your writable evidence directory. Use `--mode isolated` for acceptance; inspect its `--help` for the exact stage options.",
        "- The factory harness wrapper writes operator evidence under runs; use the official harness directly for seat-owned checks.",
        ("- Docker uses the configured Linux daemon. Keep containers, networks and build state scoped to this attempt. Do not prune unrelated resources, mount host credentials, broaden host access, or run privileged containers."
         if execution_platform == "linux" else "- Docker uses the pinned local socket and private temporary buildx state supplied by the launcher. Do not override them or mount host credentials, broaden host access, or run privileged containers."),
        ("- This execution host is Linux; build images for its native architecture. Use the installed Playwright Chromium or the official harness Docker browser for rendered checks. Keep evidence in the assigned checkout and use an isolated test network; the final service must satisfy the official no-outbound-network harness."
         if execution_platform == "linux" else "- Browser execution on this Mac uses Chromium inside the official harness Docker image. Native macOS Chromium is blocked by the seat sandbox. For rendered checks, use an isolated test network and copy screenshots back into your evidence directory; the final service must still satisfy the official no-outbound-network harness."),
        "- Put npm/pip/uv dependency caches inside the writable checkout or temporary directory. No global package or system configuration changes are needed.",
        "", "## Required repository packaging",
        "The submission repository root contains README.md, FACTORY.md, mandates/, room.json, and only genuinely completed stage-1/ through stage-4/ directories.",
        "Each completed stage is an independent full service containing its own Dockerfile, RUN.md and source. Do not nest the result repository under a factory directory or copy a final implementation backwards into earlier stages.",
        "After dispatch, copy the seven final seat mandates into the output root's mandates/ directory under their existing matching filenames; preserve Harness and Model metadata.",
        "README.md and FACTORY.md are final human-authored narratives. room.json is the actual complete BAND room download after the run. Do not fabricate these artifacts or treat missing post-run metadata as a passing submission check.",
        "Keep challenge and factory inputs outside the submitted repository. The official harness check validates final packaging; isolated harness run validates each actual stage. Final metadata assembly and public release happen after the autonomous run.",
        "", "## Actual roster",
    ])
    for seat in config["seats"]:
        handle = "@" + seat["handle"].lstrip("@") if seat.get("handle") else "UNRESOLVED — do not send"
        lines.append(f"- {seat['id']}: {seat['display_name']}; handle: {handle}; identity: {seat.get('agent_id') or 'UNRESOLVED'}; harness: {seat['harness']}; model: {seat.get('model') or 'UNRESOLVED'}")
    if config["runtime"].get("featherless_budget_guard"):
        lines.extend(["", "## Provider input limits",
            "This attempt's billing guard permits text and tool messages only. Do not attach image, audio or video inputs to model requests. Use browser DOM, accessibility and layout measurements for automated inspection; retain screenshots as review artifacts and clearly label visual checks that could not be performed."])
    policy = ({key: config['budgets'][key] for key in ('approved', 'balance_only', 'spend_cap_usd', 'billing_mode', 'accounting_scope')
               if key in config['budgets']} if balance_only else config['budgets'])
    lines.extend(["", "## Balance policy" if balance_only else "## Finite work limits", "```json", canonical(policy).decode().rstrip(), "```", "",
        "Only the existing $25 balance cap limits work. There is no factory time, token, turn, repair or receipt deadline. Do not purchase credits or top up. Preserve cumulative usage and charges across all phases and attempts." if balance_only else
        "Budgets are ceilings, never permission to spend; approved must be true before live work.",
        "Work stages in increasing order with an independent release gate for each exact commit.",
        "Every later stage inherits every earlier specification included below.",
        "PM assigns material work in the BAND room using the verified actual handles. Handoffs",
        "carry requirements, owner, revision, paths, evidence, limitations and next recipient.",
        "For oversized packets, number every part and obtain complete-set receipt before execution.",
        "Keep all seven identities registered; preserve the shared checkout's single active writer." if balance_only else
        "Keep all seven identities registered; keep implementation concurrency within the limit.",
        "Use the assigned shared checkout with one active writer. Use separate worktrees only",
        "after the operator has provisioned their writable roots and enabled parallel mode;",
        "do not create worktrees under operator-owned runs. PM integrates attributable commits",
        "without rewriting history.",
        "Reviewer independently verifies a clean checkout of an exact integrated candidate.",
        "Retain failed evidence and revise the approach when a failure repeats. Reconcile any uncertain write before retrying it; do not mistake a request or acknowledgment for accepted work." if balance_only else
        "Retain failed evidence; stop/replan after the repair ceiling or repeated identical failure.",
        ("This is unscored practice; eligibility is not claimed. Simulate an autonomous run:" if practice else "Human stage dispatch is the only human input during the judged run. Do not solicit"),
        ("use stage dispatch as the only human input, and do not solicit" if practice else ""),
        "steering, approvals or debugging hints. Report a blocker if unattended permissions fail.",
        "Required outcome: genuine stage-N outputs, independent Dockerfile/RUN.md, requirement",
        "coverage, own QA checks, designer evidence where applicable, official isolated harness",
        "evidence, candidate acceptance, room event provenance, timing and available usage data.",
        "The factory must never label an unobserved check successful or fabricate room exports.", ""])
    if track == "tablekeeper":
        lines.append(JUDGED_PREFERENCES.replace("copy a final implementation backwards. Attempt all stages and label incomplete work.", "copy a final implementation backwards. Attempt only this dispatched stage and label incomplete work." if mode == "separate" else "copy a final implementation backwards. Attempt all stages and label incomplete work."))
    else:
        lines.extend(["## Toy rehearsal scope", "Build only the official toy track in the assigned rehearsal checkout.",
            "Exercise PM assignment, peer handoffs, engineer commits and fixed-candidate review.",
            "Observe ordinary message delivery. Do not remove participants or create a missing-peer fixture; record any naturally occurring membership issue as an observation." if balance_only else
            "Observe missing-peer and delayed-message handling without manufacturing a rejection.",
            "Check every seat can see its assigned checkout and a committed change; preserve",
            "directed replies for every seat and isolated final-container evidence.", ""])
    return "\n".join(lines) + "\n"


def render_packet(config: dict, track: str, stages: list[int], mode: str, *, locked_sources: dict | None = None) -> tuple[bytes, list[dict]]:
    # A profile may stage packets with final absolute paths before publishing its
    # copied lock. Supplied lock data still authenticates every embedded payload.
    locked = source_lock(config) if locked_sources is None else locked_sources
    content = _header(config, track, stages, mode, locked_sources=locked).encode()
    payloads = []
    # A stage-N packet includes stages 1..N, not only stage N's delta.
    for stage in range(1, max(stages) + 1):
        relative = f"{track}/spec/stage-{stage}.md"
        path = Path(config["paths"]["challenge"]) / relative
        raw = path.read_bytes()
        expected = locked["challenge"]["files"].get(relative)
        if not expected or digest(raw) != expected:
            raise FactoryError(f"Missing or changed locked specification: {relative}")
        label = f"## Exact official specification — {relative}\nSource: {path}\nSHA-256: {expected}\n\n<!-- BEGIN EXACT SPEC {relative} -->\n".encode()
        content += label
        start = len(content)
        content += raw
        payloads.append({"source": relative, "sha256": expected, "offset": start, "bytes": len(raw)})
        content += f"\n<!-- END EXACT SPEC {relative} -->\n\n".encode()
    return content, payloads


def generate(config: dict) -> dict:
    errors = verify_sources(config)
    if errors:
        raise FactoryError("; ".join(errors))
    directory = artifact_path(config, "tasks")
    directory.mkdir(parents=True, exist_ok=True)
    specs = [("rehearsal-toy.md", "toy", [1, 2, 3, 4], "practice-all"),
             ("judged-all-stages.md", "tablekeeper", [1, 2, 3, 4], "all")]
    specs.extend((f"judged-stage-{stage}.md", "tablekeeper", [stage], "separate") for stage in range(1, 5))
    manifest = {"schema_version": 1, "configuration_sha256": digest(canonical(config)), "tasks": {}}
    for name, track, stages, mode in specs:
        raw, payloads = render_packet(config, track, stages, mode)
        (directory / name).write_bytes(raw)
        manifest["tasks"][name] = {"sha256": digest(raw), "track": track, "stages": stages, "mode": mode, "spec_payloads": payloads}
    write_json(directory / "task-manifest.json", manifest)
    verify_tasks(config)
    return manifest


def verify_tasks(config: dict) -> dict:
    directory = artifact_path(config, "tasks")
    try:
        manifest = json.loads((directory / "task-manifest.json").read_text())
        if manifest["configuration_sha256"] != digest(canonical(config)):
            raise FactoryError("Task packets are stale after configuration changes; regenerate")
        expected_specs = {"rehearsal-toy.md": ("toy", [1, 2, 3, 4], "practice-all"),
                          "judged-all-stages.md": ("tablekeeper", [1, 2, 3, 4], "all")}
        expected_specs.update({f"judged-stage-{i}.md": ("tablekeeper", [i], "separate") for i in range(1, 5)})
        if set(manifest["tasks"]) != set(expected_specs):
            raise FactoryError("Task manifest must contain all six dispatch packets")
        for name, item in manifest["tasks"].items():
            if (item["track"], item["stages"], item["mode"]) != expected_specs[name]:
                raise FactoryError(f"Packet track, stage inheritance or dispatch mode changed: {name}")
            raw = (directory / name).read_bytes()
            expected_raw, payloads = render_packet(config, item["track"], item["stages"], item["mode"])
            if raw != expected_raw or digest(raw) != item["sha256"] or payloads != item["spec_payloads"]:
                raise FactoryError(f"Packet differs from full deterministic source rendering: {name}")
            for payload in payloads:
                piece = raw[payload["offset"]:payload["offset"] + payload["bytes"]]
                if digest(piece) != payload["sha256"]:
                    raise FactoryError(f"Incomplete specification payload: {name}")
        return manifest
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, FactoryError):
            raise
        raise FactoryError("Missing or malformed task-manifest.json; run generate-tasks") from None
