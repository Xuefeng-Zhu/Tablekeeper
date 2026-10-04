# MillieMoon — Tablekeeper Factory

Team: **MillieMoon** · Track: **Tablekeeper** · Selected evidence: **Run 3**

> Documentation provenance: AI-assisted draft prepared at the participant's request after the run. The participant has not yet reviewed or rewritten it. The guide's participant-authorship requirement remains unresolved; this is not a claim of submission readiness.

A seven-seat BAND factory built and independently reviewed a restaurant-reservation JSON API. **Stage 1 is complete in the shipped checks: 120/120 passed in isolated containers.** Stage 2 reached architecture and design planning before a factory acknowledgment-timer failure stopped the run. This repository contains only the implemented `stage-1/` output; it has no browser UI or Stage 2–4 implementation.

## Read this repository

| Path | Contents |
|---|---|
| `FACTORY.md` | Seat responsibilities, setup, design tradeoffs, measured usage and failure handling |
| `mandates/` | Seven generic seat mandates, each naming Codex and gpt-6-astra |
| `room.json` | Unchanged full BAND console export: 4,088 events, including tool calls and output |
| `stage-1/` | BAND-authored TypeScript source, lockfile, tests, Dockerfile and RUN.md |
| `planning/` | Requirements, architecture, integration decisions and unimplemented Stage 2 design |
| `presentation/` | Editable evidence deck, matching PDF and a video shotlist; no recorded video yet |

The challenge inputs are pinned to [`803560d2a678ace1414465c098eb0ab5380ffade`](https://github.com/band-ai/dark-factory-wearedevs/tree/803560d2a678ace1414465c098eb0ab5380ffade). The factory was built around that starter's requirements and harness; the submitted application began in a separate empty repository.

## Run Stage 1

Follow [stage-1/RUN.md](stage-1/RUN.md). With Docker running:

```sh
cd stage-1
docker build -t tablekeeper-stage-1 .
docker run --rm --cpus=2 --memory=2g -e PORT=8080 -p 127.0.0.1:8080:8080 tablekeeper-stage-1
```

In another terminal, `curl http://127.0.0.1:8080/health` returns `{"status":"ok"}`. The service starts empty, stores state in memory and supports fixture reset and portable export/import as required. It makes no outbound requests. Test endpoints are intentionally enabled; exported state includes test-user credentials and should be kept private. This hackathon service is not a production deployment.

## Evidence and limits

Independent Reviewer accepted exact candidate `52a179d778b3052acf68c8aafeec62aac8ae6ecd` after rejecting and rechecking an import-validation defect. The accepted Stage 1 tree is `4917af2a996c6e3327933deb84281ad29a30c1f1`; later planning and packaging commits did not change it. Acceptance is visible in room event `a75434a1-4acb-48a6-acc9-e9cfbe82b0a5`.

Post-run verification cloned the private remote at `27f677a9ca93ba1f4e80aa6fdb87392e7f89ec84` and ran the pinned harness with `--all --mode isolated` once: Stage 1 claimed, 120/120 passing. Its RUN.md build and health check also passed. These shipped checks do not substitute for the organizers' final evaluation.

The room has one human task dispatch, event `c3b8119d-d1e5-45eb-87a8-9d861f7741ec`, followed by agent collaboration. Eleven original BAND-authored commits are preserved through `8093befdf1525a7e200ab651a5946f49bbc54bd8`. Subsequent operator commits package evidence and documentation only.

The run stopped at 2026-10-04 08:47:13 UTC. Its full export SHA-256 is `2008460d0d1cff521027f03d974ff24c718aed8708584223638f6b754aa7ccae`. Stage 2 planning is not a claim of Stage 2 acceptance. Participant narrative review, the real-room video, approved public access and final submission remain outstanding.
