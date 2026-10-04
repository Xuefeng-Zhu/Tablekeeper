# MillieMoon / Tablekeeper Factory video shotlist

Proposed duration: approximately 3 minutes 45 seconds. This is an editable recording plan, not a finished video or a platform-duration claim. Record only after participant review of the facts. No new model turns, BAND messages or product edits are needed.

| Time | Shot | Factual talking point | Source to show |
|---|---|---|---|
| 0:00–0:20 | Deck cover, then speaker introduction | “MillieMoon built Tablekeeper with a seven-seat BAND factory. Run 3 reached independently accepted Stage 1.” Participant supplies their own introduction. | Slide 1 |
| 0:20–0:45 | API scope slide | Describe restaurant availability, reservations, atomic changes and retry receipts. State that Stage 1 exposes an HTTP API and Stage 2 has plans only. | Slide 2 and `stage-1/RUN.md` |
| 0:45–1:10 | Editable role-flow diagram | Explain separate implementation and review owners, all on gpt-6-astra, with one active writer. Distinguish Designer planning and the registered Frontend seat from actual UI implementation. | Slide 3 and seven `mandates/` files |
| 1:10–1:45 | F1 failure and repair evidence | Show the rejected candidate and the observed import/login failure. Then show repaired 422 rejection, unchanged state and successful login. Keep the failure visible. | Slide 4, two candidate-acceptance records and `f1-repro.mjs.log` |
| 1:45–2:20 | Official report plus service proof | Show exact candidate and 120 collected/passed, zero failures/skips. Show the fresh remote-clone report separately. Demonstrate the existing container's `/health` only if the operator has a running verified local demo. | Slide 5, official report, fresh-clone report and operator's RUN.md verification |
| 2:20–2:50 | Stop timeline | Explain twelve confirmed handoff parts, the receipt deadline during callback 6, and eight completed callbacks at stop. Pending-event location was not measured. State that no Stage 2 application resulted. | Slide 6 and selected public workflow evidence |
| 2:50–3:15 | Consumption chart | Report 58,265,038 Run 3 tokens and 84,080,166 cumulative tokens. State that cached input is included and dollar cost is unavailable. The runtime window has expired. | Slide 7 and the sanitized figures in the brief |
| 3:15–3:45 | Evidence and remaining work | Show the full export provenance and commit history. Disclose AI-assisted narrative drafts pending participant review. Close with accepted Stage 1 scope and the unimplemented later stages. | Slide 8 and export provenance |

## Recording setup

Use the editable PPTX or PDF in `output/`. Use a 16:9 capture and readable terminal/editor font. Keep long hashes and full citations in slide notes or the accompanying source manifest rather than shrinking the video text.

For service startup, the existing Stage 1 RUN.md contains the commands below. They are a future recording route and were not executed by this deck task:

```sh
cd /Users/frank/mygit/Tablekeeper/result-run-3/stage-1
docker build -t tablekeeper-stage-1 .
docker run --rm --cpus=2 --memory=2g -e PORT=8080 -p 8080:8080 tablekeeper-stage-1
```

A separate terminal can show the read-only health response:

```sh
curl --fail --silent --show-error http://127.0.0.1:8080/health
```

Label any new local recording as a post-run demonstration. If no demo is running, show the already-recorded actual container/check evidence and call it recorded evidence. Do not simulate a browser UI, reconstruct a successful Stage 2 workflow or present planning documents as shipped screens.

If the participant wants a reservation sequence beyond health, use an approved disposable local service and synthetic fixtures from the existing tests. Show requests/results with all login tokens and credential material hidden. A state export includes credentials and must not appear in the recording. Do not open raw runtime owner metadata or the full room JSON indiscriminately on camera; use selected public text events and sanitized report fields. Retain the unchanged full export separately for provenance.

## Recording review

- Every displayed report identifies the actual revision and its scope.
- The accepted candidate, later package commit and post-run remote-clone check remain distinguishable.
- The narration acknowledges the real F1 rejection and later factory stop.
- Stage 2 remains planning only; there is no fabricated UI or finished feature demonstration.
- Reported tokens are not described as API charges or measured dollars.
- AI-assisted packaging stays disclosed; participant-authorship compliance remains unresolved.
- No public publication, final submission or external judging result is asserted without separate evidence.

Deliver a real recording only after the participant has reviewed it. This task produced a shotlist and editable slides, not video footage, voiceover, publication or submission.
