# Tablekeeper — The Build Guild

**AI-assisted factual draft. The participant must author/review this README and FACTORY.md before submission, as required by the official participant guide. This repository is not a submitted or accepted entry.**

Tablekeeper is a restaurant reservation service produced by a seven-seat BAND factory. The track is **Tablekeeper**. “The Build Guild” is the title used by the current factory video; the participant must confirm the registered team name and authorship details before using this as a final entry.

This branch preserves the original local factory Git history through independently accepted Stage 3 candidate `335f1677316e4a9dfe0cff43ec41ff0d9b0b25a3`. Stage 1 was independently accepted at `b3df39340cee6f738ac79a12dd1347fc38e969e9`. Stage 2 was independently accepted at `108bcd4c48fa4fb4d7dedf286e94c8e4937f7f10` by Reviewer verdict `95e67bf5-cad5-44a1-8a77-5684ab15f6b5`. The supervisor stopped at `2026-10-06T07:01:51.206225Z` after the cumulative overall time cap was exhausted. Stage 3 is included and independently accepted as documented below; Stage 4 remains planning only.

The generating run was **local development practice**, not a judged dark-factory execution. Human scheduling and token-budget amendments, transport corrections and administrative restarts occurred. These are disclosed in [FACTORY.md](FACTORY.md); this practice run cannot establish the guide's “only the dispatched task as human input” autonomy requirement.

## Try the application

The separate public demo is [tablekeeper-stage2.onrender.com](https://tablekeeper-stage2.onrender.com). The retained live verification at approximately `2026-10-06T06:04Z` observed factory candidate `b1f7ef64db7f0dce0b3f3a913a3af280c96d70b9`, packaged in deployment commit `382998fe143268754ade2ed2702090df3f71ef56`. That deployment differs from the later factory candidate in `stage-2/`. A fresh [client-provenance check](evidence/render/live-deployment-provenance-20261006.json) at `2026-10-06T06:46:36Z` found all nine live static assets matching deployment commit `382998fe`; search/booking/style match b1 rather than 108bcd4. This verifies client bytes, not an independently queried server release id.

The packaged demo snapshot adds fictional restaurants and a visible reset notice, and disables test reset/import/export routes. Use synthetic account and reservation information. Accounts and reservations are held in memory and disappear on restart, redeployment or service suspension. This is a demonstration of Stage 2 behavior, not durable reservation storage. Deployment provenance, local/live smoke results and screenshots are in [evidence/render](evidence/render); the exact separate demo adapter is in [demo](demo).

For the unchanged factory candidates, run from this repository:

```sh
docker build -t tablekeeper-stage1 stage-1
docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage1

# Stop the previous container before using the same host port.
docker build -t tablekeeper-stage2 stage-2
docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage2

# Stop the previous container before using the same host port.
docker build -t tablekeeper-stage3 stage-3
docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage3
```

All three published stages bind `0.0.0.0:$PORT`, default to port 8080 and provide `GET /health`. Read each stage's `RUN.md`. These original services start with empty state; their test endpoints support controlled local fixtures. For the seeded demo instead, use `docker build -t tablekeeper-demo demo` and run that image. The demo adapter is separate from the stage folders and does not change their factory-authored bytes.

## Read the repository

| Path | What it contains |
|---|---|
| [FACTORY.md](FACTORY.md) and [factory/](factory) | Roles, bundled reusable toolkit, setup, failure handling, measured counters and human intervention disclosure |
| [mandates](mandates) and [protocols](protocols) | Seven generic Codex mandates and reusable collaboration, Git/review and evidence protocols |
| [WORKING.md](WORKING.md), [ARCHITECTURE.md](ARCHITECTURE.md), [DESIGN.md](DESIGN.md) | Factory-authored requirement map, decisions and interaction plan |
| [stage-1](stage-1), [stage-2](stage-2), [stage-3](stage-3) | Separate buildable services with Dockerfile, RUN.md and source |
| [tests](tests) | Factory-authored additional owner checks; official supplied suites remain in the pinned challenge package |
| [evidence/local-sol](evidence/local-sol) | Selected QA results, retained failures, repairs and independent Stage 2/3 acceptance |
| [evidence/render](evidence/render) | Separate deployed snapshot provenance and observed live checks |
| [evidence/factory-runtime](evidence/factory-runtime) | Reviewed local transport corrections and their unit-test records |
| [evidence/artifact-provenance.json](evidence/artifact-provenance.json) | Exact copied source paths and SHA-256 records |
| [submission/video](submission/video) | Verified repaired MP4, captions, transcript, media QA and transparent capture-repair provenance |
| [docs/SUBMISSION-REQUIREMENTS.md](docs/SUBMISSION-REQUIREMENTS.md) | Required-artifact matrix, evidence boundaries and remaining work |

<!-- ROOM_EXPORT_STATUS -->
The actual generating room is `4084182d-0b85-46d6-a137-f0bd7d44b498`, **Tablekeeper · Local Sol · October 5**. A genuine BAND console **Download full room transcript** snapshot was downloaded at `2026-10-06T07:11:34.620Z`, after the time-cap stop and independent Stage 2 acceptance. Its 7,018 messages, 10,274,439 bytes and SHA-256 `f79f7f9937325f9689c3e0cf55f450887751706b5cee53dac59afeb58787a132` are preserved unchanged in [room.json](room.json). The [new download receipt](evidence/room-download-20261006T071134Z.json) and [privacy review](evidence/privacy/room-privacy-receipt-20261006T071134Z.json) record actual full-export provenance and PASS. The earlier snapshot metadata and privacy review remain under evidence. Existing canonical Git history also passed the [history privacy review](evidence/privacy/git-history-privacy-receipt.json). This export predates the newly authorized eight-hour continuation; refresh it after further work. Paged API observations are not substituted for a full export.
<!-- END_ROOM_EXPORT_STATUS -->

The media below preserves a historical snapshot before the later Stage 2 acceptance; its pending-acceptance narration and slides describe that dated snapshot, not the current verdict. The deployed demo remains the older b1 client.

The repaired [video](submission/video/The-Build-Guild-Tablekeeper-Demo.mp4) is included: 205.9 seconds, 8,737,530 bytes, SHA-256 `6c4741e326a6c019d363b7df49466e692f99fe2823bc09f503b29ba3cb059cd8`. Its [QA](submission/video/QA.json) records full decode PASS, twelve decoded scene midpoints and four fresh app stills inspected, plus independent operator inspection of search, actual login gate and Olive scenes. This is point visual sampling plus full decode, not continuous human playback. Narration is synthesized locally. The [repair provenance](submission/video/repair-provenance.json) preserves the original frame-collision failure: four app segments were replaced with uniquely named genuine captures at 06:45:55Z while real BAND room clips and narration were retained. No completed booking/lookup/cancellation footage or final portal upload is claimed.

The current seven-slide [editable presentation](submission/presentation/Tablekeeper-Local-Sol-Factory.pptx), [PDF](submission/presentation/Tablekeeper-Local-Sol-Factory.pdf) and [QA record](submission/presentation/QA.json) are included. Its QA records conversion, seven inspected pages and a passing overflow check. The operator also reports official offline packaging `harness check` PASS; it builds nothing and does not accept Stage 2. Final participant review remains required.

## What the evidence establishes

A fresh clean-container supplied isolated check of exact candidate `108bcd4`, run `2026-10-06T06:47:46Z`–`06:48:37Z`, records 120/120 Stage 1 checks and 25/25 Stage 2 checks passing with zero failures, errors or skips; see [publication-check](evidence/publication-check). It reports highest contiguous stage 2 and claimed stage 2 on shipped partial checks. The intentional next-stage probe failed, so no Stage 3 is claimed. The guide says these are a subset of judging tests. These supplied checks alone do not establish complete specification coverage or hackathon judging acceptance. Fresh independent factory acceptance is recorded separately below.

The independent Reviewer rejected deployed candidate `b1f7ef6` for keyboard-focus loss after certain booking responses. That negative record is retained beside the later repair results. The earlier Designer render evidence was accepted as evidence, while its product gate failed; those are different decisions. The later integrated Stage 2 candidate `108bcd4` was independently accepted in verdict `95e67bf5-cad5-44a1-8a77-5684ab15f6b5`; Designer repair evidence was accepted in `7085ec67-9920-4968-b246-8469a2b0bf8c`. The [Reviewer release record](evidence/local-sol/reviewer-release-108/record.json) records unfiltered isolated 120/120 inherited and 25/25 Stage 2 checks, sixteen fresh actual browser focus cases and nine browser/API groups. This is finite local factory acceptance, not external judging. [Stop summary](evidence/local-sol/stop-summary.json) records the overall time-cap stop, 126,096,220 reported local tokens and 173,903,780 remaining within the 300-million total allowance. Stages 3 and 4 remain absent; the later authorized continuation is verified below.

No final LabLab submission or upload is authorized or recorded by this artifact preparation. The participant must finish the documented review and submission requirements. The source requirements are the [official participant guide at pinned challenge commit 803560d](https://github.com/band-ai/dark-factory-wearedevs/blob/803560d2a678ace1414465c098eb0ab5380ffade/docs/participant-guide.md).

<!-- CONTINUATION_STATUS -->
The participant explicitly authorized **“Extend 8 hours and resume.”** The applied amendment added exactly 28,800 seconds to both caps: 220,339 → 249,139 seconds overall and 14,400 → 43,200 seconds for the current room. Original epochs, token usage, the 300-million local total allowance (656,587,855 cumulative cap), turns, failed evidence, original dispatch and single-writer limit were preserved. The deadlines are `2026-10-06T15:01:44.700437Z` overall and `2026-10-06T15:07:44.288395Z` for the room. At amendment, 173,903,780 tokens remained within the authorized allowance.

**Running verified at 2026-10-06T07:28:22Z:** supervisor PID 76727, all seven SDK contexts running and one active model turn, with no source verification errors. The selected [continuation summary](evidence/factory-runtime/continuation-summary.json) records the actual post-release seal `f5a942cd381d323fff83b553d0de08d0882cdf8fc847a83be3d4f247e80fc14e`, reviewed isolated source, caps and 115 passing scoped tests. The stopped Reviewer160 trigger was recorded as failed; its interrupted local turn, blocked claim and confirmed ACK remain retained. Exact terminal reconciliation suppresses replay of that old delivery. This running observation does not identify the active callback as a particular recovery event or claim the old incident completed. Stages 3 and 4 remain absent and incomplete at this snapshot.
<!-- END_CONTINUATION_STATUS -->

At `2026-10-06T07:33:57Z`, the [live continuation proof](evidence/factory-runtime/live-continuation-summary.json) recorded acknowledged real Stage 3 architecture work (`arch-s3-001`), all seven SDK contexts running and one active Architect turn. The original Reviewer thread produced a new complete Stage 2 acceptance handoff; the interrupted old turn and blocked claim remain preserved. The ledger then showed 130,924,288 local reported tokens used and 169,075,712 remaining within the unchanged 300-million total. This establishes resumed work, not completed Stage 3 or Stage 4.

<!-- STAGE3_CURRENT_STATUS -->
**Stage 3 independently accepted:** exact candidate `335f1677316e4a9dfe0cff43ec41ff0d9b0b25a3`, recorded `2026-10-06T08:18:43.966960Z`. Reviewer verdict `4b78e06c-4920-4c97-8e65-c6402135dbf7` accepts BUILD-S3; `160d0a65-e132-420c-b0bc-192cc87a0392` accepts the truthful finite QA-S3-RUN evidence. Unfiltered supplied isolated checks passed 120 Stage 1, 25 Stage 2 and 7 Stage 3 checks, with finite independent actual API, browser and migration checks. Retained evidence includes initial backend failures, integer repair failures, QA driver corrections and the Reviewer's missing-helper first attempt; acceptance does not erase these records. See [Stage 3 release record](evidence/local-sol/stage-3/reviewer-release-335/record.json) and [selection/omission summary](evidence/local-sol/stage-3/selection-summary.json). Private migration bundles, native state and authentication/session material are omitted.

At the `2026-10-06T08:24:29Z` stop observation, the runtime reported **“handoff model claim did not complete.”** The PM had reached its cumulative 769/769 per-seat turn cap. The runtime claimed a queued Stage 3 release handoff before turn reservation; admission then denied a new PM turn and blocked the unacknowledged claim. No PM770 model turn was admitted. This admission-order explanation is supported by the ledger and sealed source; no underlying exception traceback was captured. The completed independent Stage 3 verdict remains valid. See [stop diagnosis](evidence/local-sol/stage-3/pm-turn-limit-stop.json). The local counter was 166,587,058 reported tokens, leaving 133,412,942 within the 300-million total allowance; USD cost remains unavailable. Stage 4 is planning only at `d0b3ba287222f11cc00f9d07b19ad631a077b6ed`, without implemented service or independent Stage 4 acceptance. No new Render deployment or final hackathon submission occurred. Existing video and presentation remain historical pre-acceptance snapshots.
<!-- END_STAGE3_CURRENT_STATUS -->

The publisher ran fresh clean-container supplied isolated checks at `2026-10-06T08:59:31Z`–`09:00:26Z`: 120/120 inherited Stage 1, 25/25 Stage 2 and 7/7 Stage 3, zero failures/errors/skips. [Publication check](evidence/publication-check-stage-3/provenance.json) binds unchanged application paths to accepted candidate `335f167`. The intentional next-stage probe is retained and does not accept Stage 4. The refreshed genuine BAND full-room console download at `2026-10-06T09:02:30.572Z` contains 8,743 messages and retains all 7,018 earlier IDs. An initial 4,400-message download omitted earlier history and is recorded as incomplete; only the verified retry replaces root `room.json`. The export describes this stopped snapshot, rather than a future final run. [Download provenance](evidence/room-download-20261006T090230Z.json) and its privacy receipt record the exact scope.
