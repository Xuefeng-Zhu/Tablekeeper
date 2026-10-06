# The Build Guild factory

**AI-assisted factual draft. The participant must author/review this document and README.md before submission. The official guide requires participant-written narratives; this draft does not claim human authorship.**

This factory uses seven distinct BAND agents, each running the Codex harness with exact model `gpt-6.1-sol`. The responsibilities and protocols are generic; track requirements arrive in the task rather than being embedded in mandates. The application history belongs to local development practice. It is useful evidence of actual collaboration and defect handling, but human interventions prevent a claim of judged-run autonomy.

## Ownership and review

| BAND seat | Reasoning | Owns | Acceptance boundary |
|---|---|---|---|
| Factory PM | low | Requirement map, dependency queue, bounded work allocation and integration | Coordinates acceptance; does not invent test outcomes |
| Factory Architect | medium | Contracts, architecture decisions and compatibility planning | Independent review of implementable decisions |
| Factory Designer | low | Journeys, hierarchy, interaction states and rendered assessments | Presentation evidence does not itself accept implementation |
| Factory Backend | medium | Scoped API/state implementation and attributable commits | Exact committed candidate reviewed independently |
| Factory Frontend | medium | Scoped browser implementation and attributable commits | Actual browser/recovery/accessibility evidence and review |
| Factory QA | medium | Independently authored behavior checks and evidence | QA planning is separate from final release validation |
| Factory Reviewer | medium | Exact-candidate review, reproduction and explicit verdicts | Rejects defects; does not repair the product under its own review |

The matching files in `mandates/` begin with `Harness: Codex` and `Model: gpt-6.1-sol`. Their names follow the room's Factory seat names. [runtime-summary.json](evidence/local-sol/runtime-summary.json) records nonsecret identities, selected models, reasoning settings and the actual finite limits; it is a sanitized summary rather than a runnable configuration or authentication proof.

## Stand up a new factory

Use Python 3.12 or later, Git, Docker with enough capacity for the isolated app/browser checks, and a supported Codex subscription login. The reusable preparation and launcher toolkit is bundled in [factory/](factory), with runtime modules, scripts, generic agent support, tests, dependency locks and a sanitized example configuration. Use this bundled source for the documented local corrections; no private factory repository is required. The original preparation-only `factory/AGENTS.md` is included unchanged because the toolkit requires it as a source-freeze input. Its scope is the factory tools; application implementation stays in the separate result workspace. Fresh setups must verify their own identities, capabilities and frozen source inventory. The dependency pin used here is `band-sdk[codex,claude-sdk,opencode]==4.0.0`, with PyYAML 6.0.3 and psutil 7.2.2. The pinned challenge input was `band-ai/dark-factory-wearedevs` commit `803560d2a678ace1414465c098eb0ab5380ffade`.

Create seven distinct BAND identities and a fresh room. Give every seat its matching generic mandate and the shared protocols. Configure separate absolute paths for challenge inputs, factory tools, practice output, result output and operator evidence; keep application Git metadata in its own repository. Configure seat-specific Git names/emails, verify the exact room membership and models, and bind every seat to its intended output workspace.

Keep SDK credentials in an owner-only file **outside every repository** (private parent directory mode 0700, credential file mode 0600). Authenticate Codex through supported local subscription tooling. Do not put provider or BAND keys in mandates, tasks, source, room messages or committed environment files. This run permitted subscription-only use, no API billing and no paid provisioning.

The bundled toolkit's bootstrap restores lockfile dependencies and a project-local Codex runtime. First copy `factory/config/factory.example.yaml` to a new ignored local `factory/config/factory.yaml` only if that file is absent, then resolve machine-specific absolute paths and configure your fresh verified identities and room. Keep the credential file external. From the configured toolkit, inspect the supported commands before launching:

```sh
cd factory
python3 scripts/bootstrap.py --install-browser
scripts/factory doctor
scripts/factory validate
scripts/runtime discover-models
scripts/runtime probe-registration --mode rehearsal
scripts/runtime seat-status
```

Copy its example configuration only if no actual configuration exists. Resolve all machine-specific paths and allocate new identities, rooms and evidence directories. Source-lock the selected challenge and instructions; render and verify the complete task packets. Perform actual selected-adapter permission checks for checkout/Git writes, Docker build, container Chromium and allowed development networking. Previous-machine attestations do not authorize a new setup. The configured narrow profile permits explicit dependency hosts, loopback binding and an explicit local Docker socket; it does not grant arbitrary host writes.

After those gates pass, a normal practice launcher uses `scripts/runtime start-seats --mode rehearsal`. Send one complete task to the verified PM and observe the actual replies. A separate judged attempt needs its prescribed freeze/readiness process and must avoid human steering after dispatch. Do not reuse this room, reset its consumption or relabel this practice history as a new autonomous run.

The actual launcher source used locally was sealed before each startup. The post-amendment manifest was `ca806b7fde74dbc6a6bedd7cd000ae524f07e8471a3631e72bc1ddfd77dc0ba9`. The generic transport patches and their baseline/validation records are under [evidence/factory-runtime](evidence/factory-runtime). The bundled toolkit and scoped records document the local runtime corrections. Machine-specific paths and private authentication are intentionally excluded from the public runnable example; fresh setup requires its own source, identity and capability verification.

## How work moves

The PM breaks the complete task into bounded items with a goal, owner, requirements, dependencies, acceptance conditions and next recipient. One active seat/writer lease serializes product changes. Architects and Designers provide accepted contracts and experience decisions; implementers report attributable commits. QA and Reviewer inspect explicit candidates, preserving failures and limitations instead of turning process exit codes into product acceptance.

Handoffs carry the full scoped requirements and source digests, starting and candidate revisions, commands, evidence paths, ownership and bounded next action. Large handoffs are numbered, digest-bound parts. A durable journal binds the original sender, room, recipient and complete payload. Incomplete parts do not start implementation. Receipt confirms delivery; it does not mean the Reviewer accepted the work. Claimed or uncertain outcomes remain retained and block blind retry/replay.

The runtime validates authoritative room envelopes, authenticated exact-roster contexts and persisted ownership before admitting work. A single supervisor owns the accounting ledger. Cumulative usage is charged once per provider thread, counters survive restarts, and completed handoff claims are never cleared to manufacture another attempt. Finite turn, overall, stage and runnable-checkpoint gates bound activity. Only identity-verified owned processes may be stopped.

## An actual bad result and recovery

At candidate `b1f7ef64db7f0dce0b3f3a913a3af280c96d70b9`, the independent Reviewer reproduced keyboard-focus loss after conflict, uncertain-response and one success completion. Focus fell to the page body in nine recorded cases; explicit user-moved/new-form controls passed. The verdict rejected UI-S2 and accepted Designer evidence only while the rendered product gate failed. See [findings](evidence/local-sol/reviewer-b1/findings.txt) and [exact-candidate record](evidence/local-sol/reviewer-b1/record.json).

The Reviewer requested a bounded Frontend repair rather than editing the reviewed product. The Frontend produced later commits, ending at `108bcd4c48fa4fb4d7dedf286e94c8e4937f7f10`; its focus, recovery and supplied isolated reports are retained in [frontend-repair-108](evidence/local-sol/frontend-repair-108). The supplied suites passed 120/120 inherited Stage 1 and 25/25 Stage 2 checks. A fresh clean-container publication check at 06:47:46–06:48:37Z independently reran the supplied isolated suites against exact 108bcd4 and again observed 120/120 inherited Stage 1 plus 25/25 Stage 2 with zero failures, errors or skips. Its next-stage probe failed as intended; no Stage 3 was claimed. This is supplied-suite validation, not the still-pending independent factory acceptance of the repair. Green owner checks do not erase the original rejection or finish that gate.

Two infrastructure problems were also encountered. First, inbound handoff batching required a nested room identifier even when the BAND WebSocket envelope supplied the authoritative room and the nested field was absent. The isolated source correction accepted the envelope while rejecting explicit conflicting rooms; 117 affected tests passed. Second, a restarted normal supervisor lacked sender tools for an old pending protocol notice before any new model callback. The correction derived tools only from supported authenticated live SDK contexts under exact ownership/roster checks; 132 affected tests passed, followed by an exact-sender regression. These tests establish transport behavior, not application correctness.

## Human interventions and autonomy limits

The original complete Tablekeeper task was dispatched once at `2026-10-06T03:07:44.138296Z`. During this practice run the human authorized implementation to proceed without waiting for QA planning, while retaining final independent QA/Reviewer gates. The human also increased the local total allowance from 50 million to 100 million, then to 300 million tokens. An administrative quiescence message requested an idle boundary; after the cap was actually applied through a same-room restart, one explicit resume instruction released the existing queue.

The operator repaired infrastructure in a separate source directory and restarted with sealed configurations while preserving the original dispatch, consumed tokens, clocks, turns, product commits, claims and completed events. These interventions were authorized local development actions. They are not an untouched dark-factory execution and must be disclosed rather than hidden by a selected export or rewritten history.

## Measured time and consumption

At `2026-10-06T06:23:11.996979Z`, the retained ledger recorded **460,625,777 cumulative reported tokens** against a historical baseline of **356,587,855**. The local increment was therefore **104,037,922 tokens**, including cached input. From the initial dispatch this was **3 hours 15 minutes 27.859 seconds**. This is a dated, in-progress accounting observation, not a final total, an invoice, a subscription-quota conversion or an independently measured monetary charge. **USD cost was unavailable.**

The final authorized local allowance was 300 million total tokens, giving cumulative cap 656,587,855. It did not add 300 million on top of the previously consumed local usage. Original clocks and other limits stayed in force: one active seat, 1,200 seconds per turn, 14,400 seconds per active room/stage clock, cumulative overall limit 220,339 seconds, 769 cumulative turns per seat, 120-second ACK timeout and three bounded repairs. The unusual cumulative values preserve historical accounting; they are not a fresh-run recommendation.

The retained Render verification around 06:04Z describes a separate free-plan demo with its own ephemeral seed adapter. Fresh client-byte checks at 06:46:36Z found all nine live assets matching deployment commit 382998fe and the older b1 product client; they do not independently identify the server binary. Smoke and rendered observations do not establish a judged stage or durable production readiness.

The included repaired video retains real BAND handoff/rejection footage. A raw-frame filename collision had overwritten app scenes; four affected app segments were replaced with uniquely named genuine live captures at 06:45:55Z. [Media QA](submission/video/QA.json) records full decode PASS and inspected scene midpoints/fresh stills, with independent operator sampling of key app scenes. Narration is synthetic and inspection was point sampling rather than continuous human playback. [Repair provenance](submission/video/repair-provenance.json) records the failure and correction. The official offline packaging check passed according to the operator; participant authorship, end-of-run room refresh and final independent application acceptance still remain in [the requirements matrix](docs/SUBMISSION-REQUIREMENTS.md).
