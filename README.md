# Tablekeeper — The Build Guild

**AI-assisted factual draft. The participant must author/review this README and FACTORY.md before submission, as required by the official participant guide. This repository is not a submitted or accepted entry.**

Tablekeeper is a restaurant reservation service produced by a seven-seat BAND factory. The track is **Tablekeeper**. “The Build Guild” is the title used by the current factory video; the participant must confirm the registered team name and authorship details before using this as a final entry.

This branch preserves the original local factory Git history and candidate `108bcd4c48fa4fb4d7dedf286e94c8e4937f7f10`. Stage 1 was independently accepted at `b3df39340cee6f738ac79a12dd1347fc38e969e9`. Stage 2 was independently accepted at `108bcd4c48fa4fb4d7dedf286e94c8e4937f7f10` by Reviewer verdict `95e67bf5-cad5-44a1-8a77-5684ab15f6b5`. The supervisor stopped at `2026-10-06T07:01:51.206225Z` after the cumulative overall time cap was exhausted. Stages 3 and 4 are not included or claimed complete.

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
```

Both stages bind `0.0.0.0:$PORT`, default to port 8080 and provide `GET /health`. Read each stage's `RUN.md`. These original services start with empty state; their test endpoints support controlled local fixtures. For the seeded demo instead, use `docker build -t tablekeeper-demo demo` and run that image. The demo adapter is separate from the stage folders and does not change their factory-authored bytes.

## Read the repository

| Path | What it contains |
|---|---|
| [FACTORY.md](FACTORY.md) and [factory/](factory) | Roles, bundled reusable toolkit, setup, failure handling, measured counters and human intervention disclosure |
| [mandates](mandates) and [protocols](protocols) | Seven generic Codex mandates and reusable collaboration, Git/review and evidence protocols |
| [WORKING.md](WORKING.md), [ARCHITECTURE.md](ARCHITECTURE.md), [DESIGN.md](DESIGN.md) | Factory-authored requirement map, decisions and interaction plan |
| [stage-1](stage-1), [stage-2](stage-2) | Separate buildable services with Dockerfile, RUN.md and source |
| [tests](tests) | Factory-authored additional owner checks; official supplied suites remain in the pinned challenge package |
| [evidence/local-sol](evidence/local-sol) | Selected QA results, retained Reviewer rejection, Frontend repair and fresh independent Stage 2 acceptance |
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

The independent Reviewer rejected deployed candidate `b1f7ef6` for keyboard-focus loss after certain booking responses. That negative record is retained beside the later repair results. The earlier Designer render evidence was accepted as evidence, while its product gate failed; those are different decisions. The later integrated Stage 2 candidate `108bcd4` was independently accepted in verdict `95e67bf5-cad5-44a1-8a77-5684ab15f6b5`; Designer repair evidence was accepted in `7085ec67-9920-4968-b246-8469a2b0bf8c`. The [Reviewer release record](evidence/local-sol/reviewer-release-108/record.json) records unfiltered isolated 120/120 inherited and 25/25 Stage 2 checks, sixteen fresh actual browser focus cases and nine browser/API groups. This is finite local factory acceptance, not external judging. [Stop summary](evidence/local-sol/stop-summary.json) records the overall time-cap stop, 126,096,220 reported local tokens and 173,903,780 remaining within the 300-million total allowance. Stages 3 and 4 remain absent; continuation startup is not yet verified.

No final LabLab submission or upload is authorized or recorded by this artifact preparation. The participant must finish the documented review and submission requirements. The source requirements are the [official participant guide at pinned challenge commit 803560d](https://github.com/band-ai/dark-factory-wearedevs/blob/803560d2a678ace1414465c098eb0ab5380ffade/docs/participant-guide.md).

<!-- CONTINUATION_STATUS -->
At the participant’s explicit instruction, **“Extend 8 hours and resume,”** an eight-hour continuation was authorized on October 6. The approved amendment specifies an increase of exactly 28,800 seconds to both cumulative overall and current-room caps: 220,339 → 249,139 seconds overall and 14,400 → 43,200 seconds for this room. Original epochs, token usage, the 300-million local total allowance (656,587,855 cumulative cap), turns, failed evidence and single-writer limit are preserved. The approved target deadlines are `2026-10-06T15:01:44.700437Z` overall and `2026-10-06T15:07:44.288395Z` for the room; the stopped snapshot retained 173,903,780 tokens within the authorized allowance.

**Startup verification pending at 2026-10-06T07:20Z.** Initial checks found the stopped Reviewer trigger and its ACK still marked processing in SDK state. Narrow reconciliation is in progress. The original failed turn and ACK must remain recorded; recovery uses a new operational notice rather than replaying the failed model turn or original human task. This paragraph records authorization and pending recovery, not a verified running supervisor or completed Stage 3/4.
<!-- END_CONTINUATION_STATUS -->
