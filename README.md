# Tablekeeper

Tablekeeper is a restaurant reservation service with timezone-aware availability, multi-table bookings, recurring reservations and seating replans. It includes a browser interface for diners and APIs for restaurant policies, reservation histories and collective schedule changes.

All four stages are implemented and accepted by the local QA and Reviewer workflow. The latest release is Stage 4, candidate [`ac1eb494`](https://github.com/Xuefeng-Zhu/Tablekeeper/commit/ac1eb4945a2adaeaaf8a62d0817bdbc6a021e566). A fresh isolated container run passed **158 supplied checks** across all four stages. The original incremental Git history, release records and retained failures are included for review.

## Run locally

Requirements: Docker. Python 3.12 or later is needed for the optional fixture and owner-check commands below. Run these commands from the repository root:

```sh
docker build -t tablekeeper-stage4 stage-4
docker run --rm --cpus=2 --memory=2g \
  -e PORT=8080 -p 127.0.0.1:8080:8080 tablekeeper-stage4
```

In another terminal, check readiness:

```sh
curl --fail http://127.0.0.1:8080/health
```

Open [localhost:8080](http://localhost:8080) for search, signup, login and reservation lookup. The original service starts with **empty state**. To load the existing synthetic review fixture into this local instance:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests python3 -c \
  'import json; from backend_stage4_inherited import Stage4InheritedTests; print(json.dumps(Stage4InheritedTests().fixture()))' \
  | curl --fail -X POST http://127.0.0.1:8080/_test/reset \
      -H 'Content-Type: application/json' --data-binary @-
```

A successful reset returns HTTP 204 with no body. Search restaurant **R**, date **2030-01-07**, party size **2**. The fixture opens daily from 18:00 to 23:00 UTC and includes tables A, B and C. You can create an account or use the synthetic fixture account `a@b` / `password1`; that account also has manager access for API review. Resetting replaces all local state.

State is held in memory and disappears when the process stops. The service uses Python's standard library, includes IANA timezone data in its Docker image and needs no external database or service at runtime. Stage 3 and 4 capabilities extend the APIs; the packaged browser retains the Stage 2 diner interface. See [Stage 4 RUN.md](stage-4/RUN.md) for API behavior, snapshot compatibility and additional checks.

## What each stage adds

Each stage is a separate buildable service with its own Dockerfile and RUN.md. Later stages preserve the earlier contracts.

| Stage | Capabilities | Accepted candidate | Run guide |
|---|---|---|---|
| 1 | Authentication, timezone-aware availability, private reservation create/update/cancel, atomic batch moves and portable state | [`b3df393`](https://github.com/Xuefeng-Zhu/Tablekeeper/commit/b3df39340cee6f738ac79a12dd1347fc38e969e9) | [Stage 1](stage-1/RUN.md) |
| 2 | Declared two-table combinations; browser search, signup, login and reservation lookup | [`108bcd4`](https://github.com/Xuefeng-Zhu/Tablekeeper/commit/108bcd4c48fa4fb4d7dedf286e94c8e4937f7f10) | [Stage 2](stage-2/RUN.md) |
| 3 | Dated manager policies, accepted terms and revisions, reservation histories and recurring series | [`335f167`](https://github.com/Xuefeng-Zhu/Tablekeeper/commit/335f1677316e4a9dfe0cff43ec41ff0d9b0b25a3) | [Stage 3](stage-3/RUN.md) |
| 4 | Exact seating replan preview/apply, table closures and collective series amendments | [`ac1eb494`](https://github.com/Xuefeng-Zhu/Tablekeeper/commit/ac1eb4945a2adaeaaf8a62d0817bdbc6a021e566) | [Stage 4](stage-4/RUN.md) |

Stage 4 replans minimize changed table sets, unused seats and then fixture/declaration ranks in reservation-reference order. Mutations preserve immutable retry receipts and publish reservation, history and revision changes atomically. Schema 1, 2 and 3 snapshots migrate into Stage 4 while preserving their original responses.

## Validation

The fresh publication run on **October 6, 2026, 10:49–10:50 UTC** checked unchanged accepted Stage 4 source in isolated containers:

| Supplied suite | Passed | Failures / errors / skips |
|---|---:|---|
| Stage 1 | 120 | 0 / 0 / 0 |
| Stage 2 | 25 | 0 / 0 / 0 |
| Stage 3 | 7 | 0 / 0 / 0 |
| Stage 4 | 6 | 0 / 0 / 0 |
| **Total** | **158** | **0 / 0 / 0** |

The [publication receipt](evidence/publication-check-stage-4/publication-check-receipt.json) and [official report](evidence/publication-check-stage-4/official/report.json) bind those results to the exact candidate. The [offline package check](evidence/package-check.json) verifies packaging and mandates; it does not build or accept the application.

Independent local release review is recorded separately: [Stage 2](evidence/local-sol/reviewer-release-108/record.json), [Stage 3](evidence/local-sol/stage-3/reviewer-release-335/record.json), and [Stage 4](evidence/local-sol/stage-4/reviewer/record.json). The [Stage 4 completion observation](evidence/local-sol/stage-4/completion-observation.json) records the completed work-board handoff. Reviews include actual API, browser-focus and snapshot-migration observations, with unsuccessful attempts retained alongside corrections.

The supplied suites are a subset of the challenge tests. Local acceptance covers the finite cases in those records; exhaustive solver/calendar/concurrency coverage and external hackathon judging remain separate.

Run the additional Stage 4 owner HTTP checks locally with Python 3.12 or later:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/backend_stage4_http.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/backend_stage4_inherited.py
```

These commands start and stop their own local service processes. To reproduce the official isolated checks, use the [pinned challenge environment and commands](docs/SUBMISSION-REQUIREMENTS.md#provenance).

## Review guide and artifacts

| Artifact | Purpose |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md), [DESIGN.md](DESIGN.md), [WORKING.md](WORKING.md) | Architecture, interaction plan and requirement/work map |
| [stage-1/](stage-1), [stage-2/](stage-2), [stage-3/](stage-3), [stage-4/](stage-4) | Original stage source, Dockerfiles and run guides |
| [tests/](tests) | Additional HTTP, compatibility and regression checks |
| [FACTORY.md](FACTORY.md), [factory/](factory), [mandates/](mandates), [protocols/](protocols) | Seven-seat BAND workflow, reusable toolkit, collaboration protocols and dated run history |
| [evidence/local-sol/](evidence/local-sol) | Selected local QA/Reviewer records, failures, corrections and completion evidence |
| [room.json](room.json) | Genuine full-room BAND console snapshot: 10,138 messages, exported October 6 at 10:50:23 UTC after Stage 4 acceptance |
| [Presentation](submission/presentation/README.md) | Editable seven-slide PPTX, PDF and media QA |
| [Video](submission/video/README.md) | MP4, captions, transcript, media QA and capture-repair provenance |
| [Artifact inventory](evidence/artifact-inventory.json) and [provenance](evidence/artifact-provenance.json) | SHA-256 inventory, source-copy records and release bindings |
| [Requirements checklist](docs/SUBMISSION-REQUIREMENTS.md) | Hackathon artifact requirements, evidence scope and final submission actions |

The presentation and video show a historical snapshot before the later Stage 2 acceptance; they contain no Stage 3 or 4 demonstration. The room export is a post-acceptance snapshot while the supervisor was idle, rather than a final shutdown record. Download provenance and privacy review are linked in the requirements checklist.

## Hosted demo

The [Render demo](https://tablekeeper-stage2.onrender.com) now runs the accepted **Stage 4** application through [demo-stage4/](demo-stage4), retaining the existing service and hostname. Deployment commit [`dfa1457`](https://github.com/Xuefeng-Zhu/Tablekeeper/commit/dfa145748ea91b1863d56b2a6ca270679285324a) went live on October 6, 2026 at 15:44 UTC. The [deployment receipt](evidence/render/stage-4/deployment-receipt.json) and [live checks](evidence/render/stage-4/live-http-smoke.json) record matching revision metadata and all nine static assets, booking/lookup, recurring adoption, Stage 4 collective amendments, retries and access controls.

The demo seeds fictional restaurants; signup creates ordinary diner accounts. The browser supports search/auth/booking/lookup, while advanced Stage 4 operations use the APIs. Manager-only policies and seating replans require a configured manager and remain forbidden to public diner accounts. Accounts, reservations and series are held in memory and reset on restart. The previous Stage 2 [demo source](demo) and [deployment records](evidence/render/deployment-receipt.json) remain as dated history; presentation/video footage still shows that older snapshot.

## Build and submission context

[FACTORY.md](FACTORY.md) documents the seven BAND seats, exact model configuration, integration/review process and authorized run continuations. This was a local development run with human scheduling, budget and transport interventions; those dated records are preserved. All original factory commits and the earlier main history remain reachable.

This repository contains the completed application and review artifacts. A final hackathon portal submission is separate and has not been recorded. The [requirements checklist](docs/SUBMISSION-REQUIREMENTS.md) is based on the official participant guide at challenge commit `803560d2a678ace1414465c098eb0ab5380ffade`.
