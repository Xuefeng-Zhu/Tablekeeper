# Submission requirements and evidence boundaries

This is a preparation record for the current local Sol practice run. No final form submission or artifact upload is implied. README.md and FACTORY.md are explicitly AI-assisted drafts requiring participant authorship/review.

The source of these requirements is the [official participant guide, pinned commit 803560d](https://github.com/band-ai/dark-factory-wearedevs/blob/803560d2a678ace1414465c098eb0ab5380ffade/docs/participant-guide.md). In particular it requires a public repository, generic mandates, real room export, separately buildable stage folders, participant-written README/FACTORY, presentation and a video showing actual room work, handoff and produced result. Supplied tests are partial directional feedback.

| Requirement | Current artifact/status | Remaining action or limitation |
|---|---|---|
| Team and track | Tablekeeper; current video title The Build Guild | Participant confirms registered team and author facts |
| README.md and FACTORY.md | Complete factual AI-assisted drafts at repository root | Participant must author/review them; no human-authorship claim |
| At least three distinct coding seats | Seven distinct Codex/gpt-6.1-sol seats | Authenticate against genuine room export; sanitized runtime summary is supplementary |
| Generic seat mandates with harness/model | Seven `mandates/factory-*.md`, unchanged from current factory candidate | Final offline checker verifies exact room naming and genericity |
| Reusable factory setup and protocols | Bundled `factory/` toolkit, FACTORY setup, three `protocols/` files and scoped runtime correction evidence | Recreate fresh-machine auth, membership, model and capability proof; do not reuse local attestations |
| Original incremental Git history | Branch begins at exact candidate 108bcd4 with original factory history retained | Push without rebasing/squashing factory commits; preserve stage bytes |
| Stage 1 complete service | `stage-1/Dockerfile`, RUN.md and source; accepted at b3df393 | Acceptance is local independent review; judging remains external |
| Stage 2 complete candidate | `stage-2/Dockerfile`, RUN.md and source at 108bcd4; supplied checks 120/120 + 25/25 pass | Repaired UI, QA/API and final independent release acceptance still pending |
| Stages 3 and 4 | Not present | Not claimed complete; do not manufacture folders or copy later code backwards |
| Failure and repair evidence | Reviewer b1 rejection plus later108 owner repair reports | Preserve negative evidence; candidates and validation layers differ |
| Runtime/network isolation | Supplied isolated reports and actual container-browser evidence | Subset tests and finite observations do not cover held-back tests or every race |
| Live demonstration | https://tablekeeper-stage2.onrender.com, separate `demo/` code and Render receipts | 06:04Z smoke plus 06:46:36Z nine-asset provenance match 382998fe/b1 client, differing from 108; server release id not independently queried; ephemeral state; no final factory acceptance implied |
| Actual full room export | Genuine 6,462-event console download at 2026-10-06T06:38:57.986Z; SHA afe76f7fe500f88dbb21f5a9b0529c5d5e7270db55fe62809aa361d856f096e8 | Unchanged root room.json included; room and retained Git-history privacy reviews PASS; in-progress export needs end-of-run refresh |
| Video shows actual teamwork and app | Repaired final MP4, SRT, transcript, README, repair provenance and VERIFIED_MEDIA QA included; 205.9 seconds/8.7MB | Full decode and sampled visual review PASS; original frame collision corrected; synthetic narration and point sampling disclosed; no complete human playback or portal upload claimed |
| Presentation | Current seven-slide PPTX, PDF and QA included under submission/presentation | Conversion, individual rendered-page review and overflow checks PASS; participant review still required; no old Run6 deck |
| Measured time and model consumption | Dated06:23Z snapshot: 104,037,922 incremental tokens,3h 15m 27.859s; USD unavailable | In-progress measurement, cached input included; not invoice/final quota conversion |
| Judged autonomy | This is explicitly local development practice with human amendments/restarts | Cannot claim task-only human input for this run; do not conceal interventions |
| Offline package check | Operator reports official `harness check` PASS after privacy receipt wording correction | Packaging only; builds nothing and does not establish Stage 2 acceptance, judged autonomy or participant authorship |
| Final isolated stage checks | Fresh clean-container check exact 108bcd4 at 06:47:46–06:48:37Z PASS 120/120 Stage 1 + 25/25 Stage 2, zero failures/errors/skips; reports/counts/logs in evidence/publication-check | Shipped partial tests only; highest contiguous 2/claimed 2; intentional Stage 3 next-stage probe FAIL; no final independent acceptance implied |
| Public repository and final entry | Artifact preparation in canonical repository | Operator handles authorized visibility/push; final form submission is separate and not recorded |

## Export and privacy

Use BAND console Sessions → the actual room's menu → Download → **Download full session**. Do not use filtered download, a paged conversation response or an operator's synthesized timeline. The room is `4084182d-0b85-46d6-a137-f0bd7d44b498`.

<!-- ROOM_EXPORT_STATUS -->
The genuine current download contains 6,462 events, exported 2026-10-06T06:38:57.986Z, SHA-256 `afe76f7fe500f88dbb21f5a9b0529c5d5e7270db55fe62809aa361d856f096e8`. The unchanged root [room.json](../room.json) is included; [room privacy](../evidence/privacy/room-privacy-receipt.json) and [existing-history privacy](../evidence/privacy/git-history-privacy-receipt.json) receipts record PASS. It is a whole-room snapshot as of that timestamp, not the final completed run. Refresh after the run ends.
<!-- END_ROOM_EXPORT_STATUS -->

Read all tool output before publication: full-session exports are not automatically redacted. The guide requires credential rotation and `[REDACTED]` replacement if credentials are present. Preserve the original download privately and record any necessary sanitization honestly. No app state exports, auth files, private credentials, runtime owner tokens, raw source snapshots or intermediate media captures are part of the selected package.

## Provenance

[artifact-provenance.json](../evidence/artifact-provenance.json) lists each selected source path and exact SHA-256. Original absolute paths inside copied reports are retained as provenance and may not exist on a judge's machine; the copied artifact paths and candidate commits identify the evidence. Copied reports are not rerun results. The deployment provenance keeps the reviewed public snapshot separate from unchanged `stage-1/` and `stage-2/` factory code.

The original assembled MP4 was withheld after a frame-filename collision audit found incorrect app scenes. The included repaired final video replaces four app segments with uniquely named genuine live captures, retaining the actual BAND room clips and original synthetic narration. Its SHA-256 is `6c4741e326a6c019d363b7df49466e692f99fe2823bc09f503b29ba3cb059cd8`. Full decode and sampled visual QA passed; continuous human playback is not claimed. Raw captures, audio intermediates and assembler files remain excluded. See [video README](../submission/video/README.md), [QA](../submission/video/QA.json) and [repair provenance](../submission/video/repair-provenance.json).

After the final artifact set is present, run from the pinned challenge checkout:

```sh
python -m harness check /absolute/path/to/this/repository --track tablekeeper
python -m harness run --track tablekeeper --repo /absolute/path/to/this/repository \
  --stage 2 --mode isolated --out /new/unique/evidence/directory
```

Use the pinned environment and a new output directory for each run. Keep the original failure logs. The first command checks packaging/room/mandates/credential shapes and builds nothing; the second checks only the supplied subset against a specific candidate. Neither command proves judged autonomy, participant authorship, independent final acceptance or final portal submission.
