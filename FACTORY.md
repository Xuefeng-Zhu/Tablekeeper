# MillieMoon factory

> AI-assisted factual draft, prepared after the run at the participant's request. Participant review and authorship under the event guide remain unresolved. Design rationales below describe the implemented mechanisms and their tradeoffs; they do not invent personal motivations.

## What ran

Run 3 used seven existing BAND identities and one human participant in room `9b059f6b-ee17-44bd-99cd-0277cf602a81`. Every seat used **Codex CLI 0.160.0**, the maintained **BAND SDK 4.0.0 CodexAdapter**, and **gpt-6-astra** through the existing ChatGPT subscription. Selecting one model for all seats simplified compatibility; the event does not require that choice. It was not established as the cheapest or fastest configuration.

The runtime admitted one active model turn at a time. That protected the shared checkout from overlapping mutations, while queued BAND messages remained asynchronous. Credentials were stored in an owner-only file outside Git. The runtime emitted tool calls, task events and usage; reasoning-content reporting was suppressed. Preparation assistants were not additional BAND seats and did not author the judged application.

| Seat / handle suffix | Ownership | Model effort |
|---|---|---|
| Factory PM / `factory-pm` | Requirements, queue, writer leases, integration and stage gates | medium |
| Factory Architect / `factory-architect` | Contracts, technical decisions and conflicting requirements | high |
| Factory Designer / `factory-designer` | Interaction states and design-review criteria | medium |
| Factory Backend / `factory-backend` | Assigned server implementation and owner checks | medium |
| Factory Frontend / `factory-frontend` | Assigned browser implementation | medium |
| Factory QA / `factory-qa` | Independent behavioral checks and defect evidence | high |
| Factory Reviewer / `factory-reviewer` | Independent fixed-candidate acceptance or rejection | high |

All handles are under `@frankzhu94/`. The actual UUIDs and event authors are in `room.json`; display names are not treated as identity proof. Frontend received no implementation turn during Run 3 because Stage 2 implementation never began. Seven seats configured does not mean seven seats contributed product code.

## Stand up the factory

The reproducibility baseline for this run is companion [Tablekeeper-factory](https://github.com/Xuefeng-Zhu/Tablekeeper-factory) commit `8833617aebd401dcf54f8cab943a722f404a1859`. It contains the runtime, protocols, pinned dependency locks, setup scripts and unapproved configuration examples. That companion repository is currently private; access must be made available before claiming anonymous judge reproducibility. Later offline repairs did not run in this judged room.

1. Install Git, Python 3.12+ (the recorded environment used 3.13.5), Docker and BAND Desktop; obtain your own model access. Use separate directories for official challenge sources, factory tooling, practice result, fresh judged result and evidence. Clone the official challenge at the pinned commit in README; keep it read-only. Create each new result as an empty Git repository.
2. Clone the companion factory and check out the baseline above. Run `python3 scripts/bootstrap.py --install-browser` from that factory directory. Its locks restore SDK 4.0.0, project-local Codex 0.160.0 and harness dependencies. Do not replace global Codex configuration.
3. Copy `config/factory.example.yaml` to `config/factory.yaml` only when absent. Set absolute paths for this machine. Configure seven real identities using the names and mandates in this repository, then use their actual verified handles and UUIDs. Create separate practice and judged rooms with that exact roster and the human owner. Never copy this team's IDs as credentials or claim unverified membership.
4. Store your SDK keys outside all repositories using the example credential schema, a mode-0700 parent and mode-0600 file. Set its path in the ignored configuration. Select model IDs from authenticated discovery and verify compatible role efforts. Use `scripts/runtime discover-models` and `scripts/runtime probe-registration --mode rehearsal` for read-only checks.
5. Obtain explicit finite consumption and elapsed-time approval; the example is intentionally unapproved. Configure one active seat, turn and room deadlines, token/turn caps, acknowledgment wait and repair limits. Verify the actual adapter can commit within the result directory, use the required dependency hosts, Docker socket and isolated browser environment. A host-only probe is insufficient.
6. Run `.venv/bin/python -m unittest discover -s tests -v`, `scripts/factory doctor`, `scripts/factory validate`, `scripts/factory generate-tasks` and `scripts/factory verify-tasks`. Resolve reported blockers with evidence. Rehearse complete directed handoffs and independent review on the toy track, using a practice room and result. Download the actual full practice room and run the official offline and isolated checks. Missing live proof stays blocked.
7. Bind readiness observations to this configuration and source hashes, then run `scripts/factory validate --ready`, `scripts/factory freeze` and `scripts/factory launch-prepare --mode all`. These steps do not send the task. Only after readiness and live allowance are valid, start the judged seats and dispatch the complete generated packet once to PM in a fresh room with an empty result. Observe without human steering. Do not reuse this stopped room or seed its code into a new judged run.
8. At completion or stop, retain every commit and failed check, export **Download full session** from BAND, review privacy and save its bytes as `room.json`. Run the official check and isolated harness against a fresh clone, and follow each actual stage's RUN.md. Publish and submit only within participant authorization.

The baseline records a known receipt-timer failure described below. Reproducing its setup is not a recommendation to launch that defect unchanged. Any updated factory requires its own fresh rehearsal, source binding and budget; the historical launch freeze cannot authorize changed code.

## How work and review flow

Mandates are generic and carry no track-specific implementation requirements. The dispatched packet supplies the full product specification, inherited requirements, exact source hashes, workspace paths, roster and limits. PM creates work items and grants bounded ownership. Architecture and design decisions precede their implementations; QA and Reviewer receive a committed candidate and complete acceptance context.

Long handoffs carry a work-item ID, delivery ID, numbered parts, declared SHA-256 and exact recipient. The receiver waits for every part and the final marker, validates the payload, and sends a canonical receipt. A receipt proves delivery, not product correctness. Incomplete or conflicting inputs cannot start implementation. This completeness rule improves traceability but large repeated packets consume context and create many queued callbacks.

PM freezes a candidate during independent review. Reviewer cannot approve work it repaired itself, and PM cannot waive a failed gate. Fixes return to the owner as new commits; history is never squashed or rewritten. This separates implementation confidence from release evidence.

## A defect the factory caught

Stage 1 candidate `739b1bf79c631dedb528d503800b954fbd3e61d9` passed the shipped harness, yet Reviewer found that imported credential fields with array types could bypass the intended scalar validation. Reviewer rejected that candidate. Backend repaired the type checks at `52a179d778b3052acf68c8aafeec62aac8ae6ecd`; Reviewer then rechecked the exact revision, including malformed-import rollback, valid import, independent QA regressions and all 120 official isolated Stage 1 checks. The accepted revision and rejection trail remain in the room and Git history.

That sequence demonstrates a useful limit of test scores: passing the shipped subset did not prevent an independent requirement review from finding a real bug. Acceptance event `a75434a1-4acb-48a6-acc9-e9cfbe82b0a5` is the result of the repair, not a retrospective claim about the rejected commit.

## What failed in delivery

Earlier development Run 2 stopped when Backend's 600-second turn ended after sending only four of five handoff parts. A subsequent communication-only rehearsal tested bounded missing-part recovery. Its success did not prove every failure branch or the later judged application.

In Run 3, PM completed normally and BAND accepted all twelve `TK-S2-BACKEND-D1` parts. The 120-second receipt timer matured while Backend was processing part 6; by shutdown it had processed only parts 1–8. The watchdog checked the active-turn semaphore but did not inspect the SDK's room queue. The missing queue barrier is a source-backed diagnosis: the old runtime did not record queue depth, so it cannot establish whether the last four delivered parts were pending locally or in the remote backlog. It attempted a coordinator notice from PM to PM; the SDK ignores self messages, so the factory stopped explicitly with `blocked_coordinator_self_notice` at 08:47:13 UTC. It neither fabricated an ACK nor substituted a sender.

Offline repair after this run targets queue-aware notice timing and bounded PM-to-original-recipient receipt recovery. It does not add implementation to this result, resume the room or retroactively change the factory that produced Stage 1. Live verification of that repair is still pending. Further improvements should reduce redundant payload transmission within the guide's complete-handoff requirement, then measure callback latency and actual context use before changing concurrency or model routing.

## Measured usage and scope

| Measure | Recorded value |
|---|---|
| Initial judged dispatch | 2026-10-04 06:38:46.779750 UTC |
| Runtime stop | 2026-10-04 08:47:13 UTC |
| Dispatch-to-stop wall time | About 2 hours 8 minutes 26 seconds |
| Run 3 admitted turns | 174: PM 99, Architect 18, Designer 10, Backend 27, QA 6, Reviewer 14, Frontend 0 |
| Run 3 reported tokens | 58,265,038, including cached input |
| Cumulative reported tokens, all retained work | 84,080,166 / 100,000,000 |
| Approved limits | 1 active seat; 600-second turn; 4-hour room; 300 cumulative turns per seat |
| Overall deadline | 2026-10-04 13:29:59.700437 UTC; expired |
| Monetary model cost | Unavailable; no API billing or paid provisioning used by this factory |

Reported token counters are not dollars, unique prompt text or remaining ChatGPT subscription quota. The ledger retains earlier failed attempts and rehearsal consumption. Offline packaging does not grant another live allowance.

The result is one accepted Stage 1 API, with portable container packaging, original seat-authored history and full-room evidence. Stage 2 design is retained as planning; no browser product or Stage 2–4 acceptance is claimed. Participant narrative review, a video showing the actual room/handoff/result, public repository access and submission are still outstanding.
