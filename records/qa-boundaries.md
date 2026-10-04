# Independent QA checkpoint — QA-BOUNDARIES

Owner: @frankzhu94/factory-qa. Planning base: `0b193c068341b5e81f82b354e25eab12355e77dc`.
State: REVIEW upon committed handoff; no product acceptance. Execution: Codex tool calls in the assigned `/Users/frank/mygit/Tablekeeper/result-run-5` checkout. Exact model identifier comes from injected authenticated roster (`gpt-6-astra`), not a new operator probe.

The 53 classes in `qa-boundaries.json` expand the existing full-text requirement map. Each has an absolute specification path, matching SHA-256, clause, valid extremes, invalid neighbors/precedence, independent oracle and execution status. All product observations remain **NOT_TESTED** because no stage service exists at this planning revision. A test method link indicates representative executable assertions, not that every subcase in that class is automated or has passed.

## Independent oracle and order

Expected results were derived before product source exists. Assertions use literal error codes, hard-coded transition offsets, UTC elapsed arithmetic, explicit slot labels, client-retained response snapshots, small state ledgers and independently counted concurrent outcomes. No product helper or official test implementation supplies an oracle. Source specifications at the four pinned hashes were verified locally.

At the first runnable stage-1 checkpoint, use dedicated synthetic containers and run the HTTP suite against a fixed committed candidate. Start with types/keys/DST/batches, then execute both 50-request races and portable import against a second independent service. Each group resets its source; portability resets its destination. Never point the suite at shared or production state. Tests use only synthetic credentials and hold opaque exports/tokens in memory. Do not persist raw export responses to public logs.

Example command (replace candidate and unique evidence path with actual values):

```sh
python3 tests/qa_stage1_boundaries.py --base-url http://127.0.0.1:18080 --destination-url http://127.0.0.1:18081 --revision FULL_40_HEX_CANDIDATE --out /Users/frank/mygit/Tablekeeper/result-run-5/.evidence/qa-s1-UNIQUE/http-results.json
```

The report rejects an existing output file, identifies candidate and owner, preserves each group's PASS/FAIL/NOT_TESTED outcome, records UTC start/end and elapsed seconds, and exits nonzero for either FAIL or NOT_TESTED. Provide container image IDs, startup commands, network configuration and candidate cleanliness separately: HTTP observation alone does not prove isolated deployment. An absent destination is explicit NOT_TESTED. Syntax/discovery checks are test-artifact checks only.

## Decisions and ambiguities

- Parsed request equality includes unknown fields even though endpoint processing ignores them. Object member order/whitespace do not matter; arrays retain order. JSON numbers `2` and `2.0` represent the same numeric value. Boolean `true` is not numeric `1`. Compare receipt equality separately from canonical table-set equality.
- A key scopes to authenticated user, method and path. The runnable cross-path test deliberately sends one superset object valid on both creation and moves, so differing business payloads cannot mask path scoping defects.
- Endpoint-specific validation overrides generic type rules: invalid party sizes are 422; wrong type for ordinary fields is 400; malformed local-time strings are 422. Used-key mismatch is resolved after JSON-object parse/authentication and before endpoint validation. General simultaneous auth/header/syntax precedence is not fully specified; do not invent a total ordering.
- Batch nonoccupancy errors take input order, with cutoff before proposed changes for each booking, and beat any occupancy conflict. Stage-3 stale revision precedes cutoff/new validation. For other combinations without an explicit precedence rule, assert permitted individual constraints without prescribing an invented winner.
- Duration is elapsed time. Berlin fall-back repeated hour is 02:00, New York is 01:00; use each zone's explicit transition table rather than applying the illustrative 01:30 fall-back example to both zones. Grid is local and rooted at opening; resolve closing to an instant when checking absolute duration. Do not silently omit an emitted fully occupied slot.
- Cutoff is measured against the existing start and accepted terms, and equality is inside the refusal interval. The executable suite uses approximately two-minute margins to avoid clock/network flakiness. Exact equality needs a permitted deterministic clock seam or a separately justified timed experiment; no clock-control endpoint is assumed by this specification.
- Stage-1 fixture numeric maxima are not stated. Do not impose stage-3 policy publication ranges retroactively on stage-1 fixture acceptance. Validate stated 64-character IDs and endpoint-specific limits separately.
- Stage-3 phrase “stage 1's shape” without explain is interpreted as no explanation fields, while inherited stage-2 `available_options` remains present. Stage-2 pair order is declared order; reverse input pair is semantically equal while reversed arrays remain distinct idempotency bodies.
- A stage-4 plan applied with another key returns `plan_already_applied`; successful original-key replay must still return its original response even after subsequent changes. Preserve no-op and failure non-increment rules in restaurant/series counters.

## Explicit inventory of missing execution and automation

All 53 classes are unexecuted. Eleven runnable groups cover representative stage-1 behavior; their assertions are not a full test of every class. Still requiring extra stage-1 probes: 64/65-character fixture IDs and reset failure atomicity; arbitrary calendar extremes and leap dates; full malformed bearer variants; empty-slot occupancy on all tables; ordering ties by absolute instant; signup missing fields/email boundary combinations; password-hash storage inspection; every key boundary on the moves path; nested unknown-field/array and boolean-vs-number receipt equality; concurrent failure followed by same-key changed-body retry under overlap; cutoff at exact equality; comprehensive failed amendment snapshots across all errors; full batch 1/8/9/type/owner/restaurant/unchanged occupancy combinations; export during concurrent source writes; invalid opaque state variants and malformed import JSON. These remain plans, not passing evidence.

Stage-2 browser out-of-order searches, conflict refresh preserving form, pre/postcommit lost responses, unchanged retry identity, changed-field identity, upgrade without reload, 375px/desktop renders and keyboard checks need actual Chromium execution in the approved Docker environment. Pair concurrency needs serial-history comparison beyond single-resource contention.

Stage-3 policy extreme values, publication-date ties, all explanation truth combinations, frozen history/terms, optimistic races, DST series failure rollback, exception/counter interactions and cross-version import need new executable cases when that stage is runnable. Stage-4 planning needs an independent small exhaustive assignment enumerator and hand-computed tie cases; no optimizer has yet been written or exercised. Plans/applications/series amendments need competing writes plus intermediate reads and cross-stage migration checks.

No official isolated harness, container build, runtime resource/network restriction, password-storage review, browser render or upgrade has run. Final repository metadata and actual room export remain coordinator/post-run work. Cost and current cumulative consumption are UNAVAILABLE; the injected ceiling is not a measured usage statement.

## Next action and finite limits

PM evaluates plan completeness, then dispatches ARCH-S1 followed by implementation. Schedule QA-S1 at the earliest fixed runnable checkpoint using this suite plus missing critical probes before independent GATE-S1. Do not treat this planning commit as permission to skip product gates. One writer lease releases on handoff. Maximum repairs 3; acknowledgment timeout 120 seconds; retry original delivery at most twice; admitted-turn work ends at `2026-10-04T21:20:25.579325Z`, with handoff reserve through `21:21:25.579325Z`. Global deadline `2026-10-05T02:35:03.700437Z`; no new spending or human clarification.
