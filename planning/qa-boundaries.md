# Independent QA boundary plan

QA-PLAN; @frankzhu94/factory-qa; starting revision bd8a3200f49012b7713147342ec35497ab4feb1e.
The complete four specifications remain in the existing requirement map. Forty-two input classes in `qa/boundary-cases.json` extend its existing 37 rows with explicit inputs, expected results, independent oracles and scheduling. The text companion is a human-readable source. All product behavior is **NOT_TESTED**. Planning validation and syntax checks are not service validation. Reviewer acceptance is pending.

## Earliest runnable schedule

1. At the first committed S1 service, verify exact SHA and clean candidate. Use disposable fixture state, never a live user's service. Run Q03/Q04/Q06/Q08/Q09/Q12/Q13/Q17/Q18 through the independent driver below, then complete uncovered S1 classes Q01–Q20/Q42. Each driver group covers selected examples only; a group's PASS does not satisfy the whole mapped row. Stop source before portable-state reads. Reserve first-run time for concurrency, swaps, receipt replay, all four DST vectors and rejection rollback before cosmetic work.
2. At first S2 service, retain S1 regression checks; add Q21–Q27 and actual accepted S1 export upgrades. Browser interception must obtain an actual successful server response before dropping it. Execute inside the authorized harness Chromium container; native browser is unavailable. Designer owns rendered desktop/375px quality review; QA owns sequence assertions and real competing client requests.
3. At first S3 service, Q28–Q35 and prior checks; upgrade from actual accepted S1 and S2 services, preserve every old receipt shape. Add policy/series operations only after recording their input-class oracle. Date-sensitive recurrence checks require editable anchors.
4. At first S4 service, Q36–Q41 and prior checks; independently enumerate tiny planner fixtures and compare the full lexicographic objective, then max supported 6-table/4-pair/6-booking performance. Upgrade from every earlier accepted stage. Test series revisions across moves, plans and amendments, not just isolated endpoints.
5. Reviewer runs official isolated gate against the exact frozen candidate in a clean separate checkout. QA evidence informs but never replaces that decision. Never copy later implementation backward into an earlier stage.

Example driver command (substitute actual assigned disposable endpoint and full committed SHA; this is a planned command, not an executed product test):

```sh
python3 qa/stage1_http.py --base-url http://127.0.0.1:8091 --candidate FULL_40_HEX_SHA --out /Users/frank/mygit/Tablekeeper/result-run-6/.evidence/S1-QA/UNIQUE_ATTEMPT/results.json
```

Driver resets its target repeatedly, uses only synthetic accounts, has no product imports and creates its output exclusively to preserve older attempts. Requests have 5s timeouts, controls 10s. Concurrency uses a 50-client barrier and captures exact outcome counts; this does not itself prove CPU/memory/network limits. The supplied candidate argument is provenance supplied by the operator: pair it with independently recorded running-image/commit evidence. A lying argument is not revision verification.

## Invariants and adversarial controls

- Receipt identity and domain equivalence differ: unordered JSON objects replay, typed bool/number changes and reordered arrays do not; reversed seating pairs are domain no-ops. Q06/Q27 each include a violating case and valid control. Check live state as well as original response.
- Occupancy is a half-open absolute interval: touching booking/closure boundaries pass, intersecting member tables fail. Q10/Q12/Q13/Q17/Q26/Q37 check records and occupancy together. A rejected operation leaves every listed record unchanged; an unchanged listed member still occupies its seat.
- Collective state commits once: successful swap changes both assignments, invalid later move leaves both old, and nonoccupancy errors precede occupancy. Q17/Q18/Q35/Q40 cover examples. Concurrent history linearizability and arbitrary interleavings remain broader invariant work; a single barrier run is not a proof.
- Accepted terms are immutable until a real diner amendment: policy publication, no-op, replay and operator repair preserve them. Q29/Q31/Q36/Q38/Q41 carry manual revision ledgers and saved snapshots. Series changes count once per operation, not once per member.
- Import replaces a world while receipts remain historical: Q19/Q20/Q25 preserve source identity and remove destination credentials, with offline source and repeat import controls. No export contents belong in logs, commits or room messages.
- Search and booking attempts are asynchronous: Q21/Q22/Q23 explicitly reverse response order and lose postcommit responses. Check labels, form inputs, exact request identity and server booking count; screenshots alone cannot prove these relations.

## Architecture challenges for Reviewer

Q14: Architecture §4 treats a nonexistent opening/closing boundary as no interval at all. The specification says nonexistent *starts* are omitted, but does not say to close a whole spring day when opening is in the gap. Opening02:00..05:00 can still have real03:00 grid starts. This is an unresolved convention, not independently accepted behavior. Require a documented decision without inventing a release gate.

Q15: Absolute duration is normative; first-fold resolution is specified for booking starts. It is not explicit for a closing boundary in the repeated hour. Preserve both plausible boundary calculations; do not silently assert architecture's first-fold closing convention as a source requirement.

Q16: Exact cutoff equality requires a deterministic clock. No clock-control endpoint is required by the public contract. Ordinary real-time HTTP tests prove before/after cases only; report equality NOT_TESTED unless an authorized isolated test mechanism fixes now. A unit monkeypatch may support the inequality but cannot be mislabeled external HTTP equality coverage.

Q32: Pre-S3 history did not exist as a required artifact. A synthetic created/cancelled migration baseline cannot recover actual old amendment or cancellation times. Review the proposed created_at baseline explicitly; preserve all actual old values and receipts, and do not claim invented historical fidelity. Existing S3 histories must remain exact.

Q33: The current date is after both mandatory spring transitions; past booking creation is allowed but adoption checks cutoff. A 2026 spring recurring adoption cannot be tested now through public HTTP as if editable. Test past single creation, future corresponding spring recurrence with explicit oracle, or an authorized deterministic clock; distinguish coverage of these cases.

Q42: Whole-root cloning and scrypt under one global lock may exceed 5s/2GiB at 50 in flight. Architect's lock and tuple microbenchmarks do not measure HTTP queueing, clone cost or concurrent hashing. Use real container resource limits and log actual fixture sizes/latencies. The specs set no total history cardinality maximum; 100/1000-booking stress sizes are diagnostics, not invented minima for acceptance.

The instruction to retain stage-1 shape without explanation in S3 must be reconciled with inherited stage-2 available_options. Interpret as omitting explanation fields only, and ask Reviewer to record that inherited-contract reading. Unspecified cross-field error order is never promoted into a test gate.

## Evidence and missing coverage

Each execution gets a unique absolute `.evidence/<work-item>/<UTC-attempt>` directory and records full service SHA, clean checkout/image binding, QA literal handle, room events, UTC start/end, exact command, exit status, environment and each case's PASS/FAIL/NOT_TESTED. Preserve failed attempts. Store screenshots/traces with synthetic data; hold credential-bearing exports only in restricted temporary storage, never include raw bodies in room output. Every defect states exact candidate, minimal request sequence, expected normative behavior and observed side effects.

This plan has not run an HTTP service, browser, Docker gate, export upgrade, concurrency test or performance test. `qa/stage1_http.py` is newly authored and only syntax/CLI checked. Missing executable classes: remaining S1 auth/ID/scope/import/cutoff/list/grid checks; all S2 browser/pair/upgrade checks; all S3 policy/history/series checks; all S4 planner/amendment checks; isolated resource gates. The matrix schedules them, not claims them complete. Driver behavior against malformed service responses and independent planner enumeration also remain to be exercised. Measured consumption/cost: UNAVAILABLE.
