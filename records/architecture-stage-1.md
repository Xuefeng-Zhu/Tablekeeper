# ARCH-S1 — reservation core and portable state

State: REVIEW upon committed delivery. Owner: @frankzhu94/factory-architect.
Independent decision-record reviewer and next recipient: @frankzhu94/factory-pm.
Starting revision: `46aea39e74eecc153ab8e51e699c114c19d8e021`.
Repository: `/Users/frank/mygit/Tablekeeper/result-run-5`; branch `run-5`.
Only this record and architecture-only evidence are owned by this item. No product files were created.

The delivered four-part requirements packet ARCH-S1-D1 has SHA-256
`1593304c1b9b4abb16d053481d6a07e63250f840557ff1280f104857b3c23695`.
Receipt event: `f48a9f03-91d6-4724-8245-896bb20f64d7`; IN_PROGRESS event:
`25ee690d-95a0-4bc3-b357-419aa62f9cc5`. The exact specifications, not examples in this
record, govern behavior. No domain-product source, API or schema was consulted.

## Decisions and consequences

Use **Python 3.12, SQLite, and zoneinfo** for the API. Retain **TypeScript,
React and Vite** for Stage 2's browser application, built into the same image and
served by the API process. Pin the concrete Python image and dependency versions
when constructing the first service. Include IANA tzdata during build; assert both
required zones load before marking healthy. No runtime downloads or remote services.

The alternative TypeScript/Node/SQLite stack is compliant, but introduces a timezone
adapter dependency and native SQLite binding/version coordination. Python provides
SQLite and zoneinfo in its standard library, exact decimal JSON parsing, and a small
single-process transaction boundary. This trades shared client/server language for
fewer server dependencies. Use explicit HTTP DTO contracts; do not share business
rules with the browser. Do not replace React/Vite with a Python UI.

Use a threaded HTTP server in one process, one SQLite connection, one reentrant
application state lock. Python's standard HTTP server is sufficient for this bounded
container API; set its listen backlog to at least 128, consume request bodies
correctly and return every response with a content length. No worker-process pool,
Redis, hosted auth, external database, scheduler or external clock is needed.
Every database operation, including reads and authentication against tokens, acquires
the same lock. A write uses explicit `BEGIN IMMEDIATE` and commits once. Transaction
rollback precedes returning any rejection. No `await`, remote call, password hashing,
or slow serialization under the state lock. This deliberately serializes short state
operations; it meets correctness by construction but still requires 50-request HTTP
latency evidence within the 2 CPU / 2 GiB limits. An application mutex alone without
transaction rollback was rejected because exceptions could leave partial batches.

SQLite allows one writer and an immediate transaction acquires the write transaction
at entry; our mutex also excludes intermediate reads and connection misuse.
[SQLite transaction documentation](https://www.sqlite.org/lang_transaction.html).
`zoneinfo` supplies IANA conversion and fold support; it requires installed timezone
data. [Python zoneinfo documentation](https://docs.python.org/3/library/zoneinfo.html).
Scrypt is provided by OpenSSL-backed Python builds; image startup must verify the
actual build includes it. [Python hashlib documentation](https://docs.python.org/3/library/hashlib.html).
These are mechanism references, not evidence of a completed service.

## Modules and ownership contract for backend

| Module | Responsibility and boundary |
| --- | --- |
| `http` | Routing, body/object parsing, bearer/header syntax, explicit JSON errors and response serialization. Public/test routes are explicit allowlists. Later HTML routes bypass API JSON serialization. |
| `validation` | Missing versus wrong type versus range/format, strict calendar parsing, party-size exceptions, fixture validation; never mutate state. |
| `time_rules` | Local grid enumeration, fold/gap resolver, UTC elapsed arithmetic and RFC3339 formatting. Clock is an injected function for unit tests, not a public clock-control endpoint. |
| `store` | Connection, state lock, explicit transactions, unique constraints, consistent read snapshots; only module allowed to commit. |
| `auth` | Portable password records, token issuance/lookup, caller identity; never infer ownership from a submitted user ID. |
| `receipts` | User/method/path/key lookup, exact parsed-value comparison and immutable original response. No reservation recomputation on replay. |
| `reservations` | Read/create/cancel/amend, pure proposal building, shared occupancy predicate, immutable identities. |
| `moves` | Batch shape and ordered validation; build all proposals, check final occupancy, commit once. Reuses amendment validation. |
| `state_io` | Validate reset/import completely, migration, atomic replacement, detached export; not the public booking API loop. |
| `presenters` | Stage-specific reservation/restaurant response fields; no live recomputation of stored receipts. |

Backend owns implementation. Keep pure validation and proposal builders reusable for
Stage 3 series and Stage 4 plans; do not implement later endpoints in Stage 1.
Designer/frontend own browser state and rendering in their later assignment. HTTP
contracts below are fixed; any proposed incompatible change returns to PM before integration.

## Data lifecycle and representation

Use relational identity/lookup indexes plus JSON documents for immutable configuration
and structured snapshots. Required logical entities (physical column names may vary):

- Users keyed by opaque ID with unique email, display name, and password record only.
- Sessions keyed by a random bearer token (at least 32 random bytes, encoded), user ID;
  multiple tokens per user, no expiry. Store the token itself in private state, or its
  deterministic unsalted digest with an equally portable lookup rule. Choose one rule
  and preserve it across versions. No process signing secret or source-host dependency.
- Restaurants keyed by ID with fixture order and original config. Tables keyed by
  `(restaurant_id, table_id)` with order, label and baseline capacity. Table IDs need
  not be globally unique across restaurants. Preserve fixture order in all outputs.
- Reservations: opaque ID, globally unique reference, owner, restaurant, canonical
  selected table IDs, local start string, start/end UTC instants, stored explicit-offset
  strings, created timestamp and status. Index restaurant/start/end; selected tables
  form a membership relation or equivalently indexed document. Use integer UTC
  microseconds rather than floating epoch seconds for interval and cutoff comparisons.
- Successful receipts uniquely keyed by `(user_id, uppercase_method, exact_route_path,
  idempotency_key)`, retaining the complete request JSON text and the original response
  JSON text as immutable detached values. Store receipt and mutation in one transaction.
  There is no committed pending/failed receipt state.

Generate IDs with randomness, validate supplied IDs as strings of at most 64 characters,
and use 10 uppercase alphanumeric random characters for references with a unique
constraint and collision retry inside the transaction. Never regenerate an imported ID,
reference, timestamp, token or receipt. List reservations by stored start instant
descending, using reservation ID only as a stable tie-breaker; both statuses appear.

Internally retain accepted baseline rule snapshots at creation and each real amendment,
plus monotonically sequenced change records and reservation revisions. They are hidden
from Stage 1 responses. This is a small forward-migration seam, not early policy API
implementation. A no-op retains all existing fields. Stage 3 can expose these records
without reconstructing old accepted duration/cutoff from later fixture or policy state.
Stage 1 reset starts restaurant revision at zero even with seeded bookings; new writes
increment it once per logical changed operation. Track this internally for continuity.
Cancelled rows remain and never contribute occupancy.

## JSON and validation contract

Parse only JSON objects for object-body endpoints. Invalid JSON and top-level null,
array or scalar produce 400 `malformed_request`. Reject non-JSON NaN/Infinity constants.
Parse both integers and decimal numbers with `decimal.Decimal`, and use a recursive
JSON comparator: types must match; object key sets and recursively compared values
match without member order; arrays preserve order; Decimal numeric equality makes
`2` equal `2.0`; booleans remain distinct from numbers; strings/null compare directly.
Preserve unknown fields, including nested fields, for receipt equality even though
business validation ignores them. Never compare Python dicts directly (`True == 1`),
round numbers through binary float, or compare raw serialized request text. Storing
request JSON text allows a lossless import/reparse; it is not itself the equality rule.
Endpoint output numbers become JSON integers after explicit integral validation.

Required fields missing: 422 `validation_failed`. Ordinary fields with wrong JSON
type: 400 `malformed_request`; correct type but invalid format/range: 422. Party size
always uses 422 for nonpositive, fractional, boolean, string or null. Numeric `4.0`
is integral and valid. A starts_at_local non-string is 400; a string failing exact
`YYYY-MM-DDTHH:MM` syntax/calendar validity is 422. Query integers accept ASCII digits
only, then positive range; do not accept sign, exponent, decimal point or coercion.
Unknown fields/queries are ignored without changing these rules. Do not trim or silently
normalize identity strings, dates or idempotency keys. Key absent/empty gives 400
`missing_idempotency_key`; length >255 gives 422. No arbitrary stage-3 maxima are
applied to Stage 1 fixture grid, duration, cutoff or capacity.

Preserve only specified error precedence, not an invented global ordering:

1. Object parse and authentication precede receipt resolution. Header validation is
   necessary to address a receipt. Existing matching receipt returns 200 before any
   endpoint/resource validation; different parsed body gives 409 `idempotency_key_reuse`.
2. Private lookup/mutation checks ownership through the query; missing or other-owner
   reference is 404. Missing/malformed/unknown bearer is ordinarily 401. Later Stage 3
   history/decision/series reads explicitly use 404 even with no token.
3. Cancelled cancel returns current state 200 before cutoff; cancelled amendment is
   409 `reservation_cancelled`. For editable amendments, check the existing cutoff
   before proposed fields. `now >= old_start - old_cutoff` gives 409 `cutoff_passed`.
4. Validate batch structure first (1..8 object entries, distinct string references;
   invalid shape/duplicates 422). Then in input order, resolve owner and same restaurant,
   cancelled state, old cutoff and proposed fields. Complete all nonoccupancy checks
   before any occupancy test. Later Stage 3 expected_revision validation/staleness
   precedes cutoff/proposed fields for each item.

The ordinary validator resolves restaurant/table membership (404), local time/gap,
opening and grid, party capacity and occupancy with the endpoint's specific errors.
When two ordinary rules conflict without a specified precedence, choose a stable
implementation order without claiming the specification requires that winner.
Unknown/cross-restaurant table is 404; off-grid is `not_on_slot_grid`, hours/end overflow
`outside_opening_hours`, excessive party `party_exceeds_capacity`, gap `invalid_local_time`.
Never leak another user's resource via a more specific mutation error.

## Time algorithm

Strictly parse the wall date/time and zone first. Construct candidates with fold 0 and
fold 1. Convert each to UTC and back to the zone. Keep only candidates whose returned
naive local date/time exactly equals the input; deduplicate UTC instants. No candidates
means nonexistent local time. Select the minimum UTC instant for an ambiguous time.
Do not assume attaching a zone rejects a gap; it does not. Do not use host-local time.

Enumerate wall labels `opening + k * slot_minutes` on the same local day. Resolve each
label, skip gaps and emit folds once. Add duration to the UTC start, then format the end
in the restaurant zone. Resolve closing to its first valid occurrence and require the
UTC end <= closing instant as well as a wall label within the local opening interval.
Grid divisibility is measured in local minutes from opening, never from midnight or UTC.
Emit every fitting slot even when its available IDs are empty. Closed weekday emits [].
A boundary in a gap has no resolved instant: for this unspecified corner, treat the
nonexistent boundary as its transition instant (first valid instant after the gap),
while keeping the original local opening label as the grid anchor. Document/test this
choice; it must not make an explicit gap booking valid. Ordinary close-fold resolution
uses the first occurrence, consistent with the booking fold rule.

Use Gregorian calendar operations for dates, including leap days and dates before now;
never reject a create because it is in the past. Handle arithmetic overflow as validation,
not 500. Python supports four-digit years 0001..9999; year 0000 is not a valid Gregorian
date. Test timezone conversion at representable edges and non-minute historical offsets
before claiming arbitrary-date coverage. Offset formatting must remain RFC3339-valid.
For historical second-based IANA offsets, serialize the exact instant in UTC with
+00:00 (valid RFC3339), while retaining the original starts_at_local and restaurant
zone. Never round the instant or emit offset seconds. This representational choice
avoids incompatible RFC3339/local-offset syntax; QA must check instant equality and
local roundtrip at these edges. The required 2026 transitions are checked below.

## Atomic operations

All state-dependent checks and final mutation occur in the same lock/transaction.
Capture one UTC `now` per logical operation under the lock. Authentication for a mutation
also occurs under it, so a concurrent reset/import cannot resurrect a removed account.

Create: validate request -> construct candidate -> check every selected table against
confirmed occupancy -> allocate identities -> insert -> freeze response -> insert
receipt -> commit. Overlap is `old.start < new.end AND new.start < old.end` on at least
one common table of the same restaurant. Adjacency is legal. Exactly one of 50 identical
unused-key requests commits; all other identical requests then read its receipt as 200.
Fifty different keys for one slot yield one 201 and 49 `table_unavailable`, never 5xx.

Amend: retain current record; validate editability and proposed full record; detect
semantic no-op; for a real change check occupancy excluding its own old row; replace
record/occupancy together. Keep ID/reference/owner/created_at. Failure rolls back every
change. No-op still requires confirmed/editable state and keeps all existing values.
Cancel: retain current values, mark cancelled and release membership occupancy in the
same commit; immediate next availability observes release. Repeated cancel does nothing.

Batch: after all ordered nonoccupancy checks, build a complete proposed set. For occupancy,
exclude old versions of ALL listed bookings, then compare each proposal against unlisted
confirmed bookings and every other proposal. Include unchanged proposals: removing them
without reinstating occupancy is incorrect. A table swap is valid; two proposals colliding
are not. Commit records plus immutable input-order response and receipt once. On any error,
no records, occupancy, receipt, history or counters change. A no-op successful batch still
stores its 201 receipt; replay returns its exact response as 200 without executing moves.

Read: lock, read transaction if multiple queries, build detached DTO/snapshot, release,
serialize. No handler returns references to mutable cached data. Export is the same kind
of read snapshot. Do not catch a busy/constraint error and claim success; the single lock
should prevent ordinary contention errors, and unique races use explicit safe resolution.
Error formatting covers all controlled paths; unhandled exceptions are defects, not a
license to return 500 for specified valid/invalid requests.

## Passwords, sessions, reset and import

Signup requires email of form nonempty-local@nonempty-domain (do not invent a required
TLD), password length >=8 and display_name. Duplicate email gives 409 `email_taken`.
Login wrong password/unknown email gives 401. Use scrypt with random 16-byte salt,
N=16384, r=8, p=1, dklen=64 and explicit maxmem >=64 MiB initially; record parameters,
salt and digest in a versioned password object. Use constant-time digest comparison.
Never retain plaintext passwords after hashing, including fixtures or logs. Hash work
runs outside the state lock with at most two concurrent derivations. Recheck the user
record/state generation inside the lock before issuing a token, so reset/import during
hashing cannot install a stale session. Benchmark 50 auth requests in the target image;
if the 5-second limit fails, revisit scheduling with evidence, not weaker/plaintext storage.

Reset parses and validates the whole fixture and hashes seed passwords before touching
live state. Validate unique IDs in their namespace, unique emails/references, valid zones,
weekday/hours rules (same day, closes later), numeric fields, foreign references, booking
rules and nonoverlapping seeded confirmed bookings. No cancellation cutoff is applied to
seeding past bookings. Build a complete candidate; a single replacement transaction deletes
all old state and inserts candidate state, including empty sessions/receipts. Repeated reset
returns 204 with no response body; old tokens and imported state disappear. No partial reset.
A reset and concurrent ordinary write are linearized by the same lock; a write occurring
before replacement is intentionally replaced. Test controls have a 10-second budget.

Stable outer export envelope remains `{track:"tablekeeper",format_version:1,state:{...}}`.
State has its own integer `schema_version:1`, plus ordered arrays `users`, `sessions`,
`restaurants`, `reservations`, `receipts` and internal history/revision metadata. Include
all hash parameters, tokens or portable token digests, original config/order, stored
instants and strings, statuses, IDs and original receipt request/response JSON strings.
No SQLite filename/page image, pickles, local paths, source URL, encryption/signing key
outside this envelope or expiring sessions. Every datum required to resume lives inside it.
Private export values must not enter public logs or committed evidence.

Import first parses the whole JSON object (invalid JSON 400); missing envelope members,
wrong track/version or invalid state give 422. Validate complete structure/types, supported
schema, uniqueness, foreign keys, ordering metadata, hash algorithms/parameters/encoding,
token ownership, references and time/status validity; confirm receipt keys unique and
request/response strings parse to appropriate JSON objects. Resource-bound hash validation
must accept all parameters this service emits. Validate preserved accepted terms, stored
occupancy and histories for internal consistency, not against today's newly selected policy
or cutoff. A changed or cancelled reservation need not equal its historical receipt body.
Never rerun a successful historical request to validate its receipt. Reject invalid state
before replacement; migration also works on a detached candidate. Replace all live tables
in one transaction, not merge; repeat import restores identically. Snapshot remains detached
from later source writes. Test two genuinely separate processes/containers, not just SQLite
connections, before claiming portability.

## Forward evolution without rewriting completed stages

Copy an independently accepted stage into the next stage only. Each has its own full source,
Dockerfile and RUN.md. Keep outer export format 1 and dispatch internal schema migrations by
schema_version, with readers for every earlier team stage. Migrations preserve receipts as
opaque historical request/response pairs: Stage 2/3 must not add table_ids, revision or terms
to an old receipt response. Ordinary current views can expose the newer shape.

Stage 2 canonicalizes single/pair selection separately from JSON equality. Pair identity is
unordered, stored and returned in declared combinable order; reversed array input is the
same table set but a different receipt body. Pairs occupy every member; combinations are not
transitive. Preserve single table_id response alongside table_ids only for a singleton.

Stage 3 adds immutable policy publications, baseline policy 0, greatest effective date <=
start date with greatest version tie-break, accepted-term snapshots, visible history and
optimistic revisions. Real amendment checks OLD accepted cutoff then validates every proposed
field under the NEW selected policy. No-op retains terms/history/revisions. Seeds start at
revision 1 under policy 0. Older internal history/terms migrate unchanged; any earlier schema
without these must synthesize a clearly defined baseline from stored booking state/fixture,
without modifying historical receipts. Prefer retaining metadata from Stage 1 as directed.
History and decision owner privacy includes no-token 404. Explanation rules are independent,
ordered capacity/no_overlap; no explain parameter means no explanation fields while retaining
Stage 2 available_options.

Series store immutable original scheduled dates, stable occurrence indices/references and
exception flags separately from current booking time. Adoption reuses anchor untouched,
checks generated occurrences in index order and commits the whole series/receipt once.
Use local calendar-week arithmetic, not elapsed 7*24h. Individual changes mark exceptions;
cancellation retains occurrences without marking exception. Bulk operations increment each
affected series and restaurant once, never per member. Failed writes/replays increment none.

Stage 4 stores plans with captured restaurant revision and immutable proposed assignments.
An exhaustive search is bounded by <=6 tables, <=4 pairs, <=6 considered bookings: up to
10^6 assignments before pruning. Sort references, rank singles then declared pairs; compare
(changed_count,total_unused_seats,rank_vector) lexicographically using each booking's accepted
capacities. Require measured runtime before accepting this strategy; pruning is an optimization,
not a changed objective. Preview changes no occupancy/counter. Apply rechecks revision then
atomically writes closure, assignments and histories. Original-key receipt beats current plan
state; another key on an already applied plan gets plan_already_applied. Closures join the same
half-open occupancy predicate. Series amendment reuses proposal/final-occupancy validation,
filters exceptions/cancelled occurrences, uses original scheduled dates, checks expected revision
first and does not mark exceptions. Detailed Stage 3/4 decisions still require their own review.

Frontend contract for later design coordination: search generation guards the entire results,
labels and form context; retain a submission's exact body/key through transport uncertainty
and replay success; field changes create a new request identity. A 409 conflict preserves the
form and refreshes server availability. Error, uncertain and confirmed are distinct states.
Sessions and pending identity stay in the mounted browser across import; no signing-secret
rotation or migration response should require reload. Designer must confirm presentation and
failure states before frontend READY; this record does not substitute for that review.

## Requirement coverage and evidence obligations

| Requirement map row | Implementation decision | Required independent evidence |
| --- | --- | --- |
| S1-01 scope | Shared half-open occupancy predicate, one lock/transaction | 50 competing keys; reads during batch; adjacency |
| S1-02 deployment | One self-contained Python container, build-time assets/tzdata | Official isolated harness: 2 CPU/2GiB, no outbound, <=60s healthy, 5s requests |
| S1-03 runtime | 0.0.0.0, PORT/default8080; explicit public health/reset; JSON UTF-8 and offset timestamps | Alternate PORT, ready datastore, repeated reset, 64/65 IDs |
| S1-04 model | Ordered fixtures, local hours, hashed seed users, seeded bookings | Empty/full fixtures, leap/past dates, invalid reset unchanged |
| S1-05 errors | Type/range distinction and endpoint overrides | QA literal codes; malformed/unknown fields; no 5xx |
| S1-06 auth | Portable scrypt, multiple nonexpiring sessions, owner-filtered reads | Seed/login/signup boundaries, stored hash review, retained sessions |
| S1-07 receipts | Complete parsed JSON equality; user/method/path/key; transaction-bound frozen response | Same key 1x201+49x200, changed/failed keys, bool/number and unknown-field cases |
| S1-08 endpoints | Public restaurant/availability; private create/list/lookup/cancel/PATCH | All routes, ordering, empty occupancy slots, identities, atomic rejected PATCH |
| S1-09 time | Roundtrip fold/gap resolver; local grid and UTC duration | Both zones' four transitions, closing/grid boundaries, exact cutoff with injected clock |
| S1-10 portability | Versioned logical state, detached export, validated replacement | Two-container import, concurrent snapshot, restored hashes/tokens/receipts, invalid import rollback |
| S1-11 moves | Ordered nonoccupancy proposals then final occupancy; one transaction | 1/8/9 boundaries, swaps, unchanged occupancy, late invalid item, cross-owner/restaurant, replay |

The existing 53 QA classes remain NOT_TESTED. Eleven runnable HTTP groups are representative,
not exhaustive. PM must schedule QA-S1 at the first runnable committed Stage 1 and independent
GATE-S1 afterwards. No gate is waived by this record or a green mechanism experiment.

## Observed evidence, risks and limits

Connected execution used Codex exec and BAND tools in the assigned checkout. Model
`gpt-6-astra` is injected roster metadata, not a separately observed runtime model probe.
Git status was clean at the starting revision; fetch/fast-forward check exited 0 and left
HEAD unchanged. Local author is the literal architect handle with roster-ID-based local email.

Evidence directories (all under the assigned repository):

- `/Users/frank/mygit/Tablekeeper/result-run-5/.evidence/arch-s1-20261004T2128Z/`:
  mechanisms.py and attempt-1.json. `python3 .../mechanisms.py` exited 1 before tests:
  Homebrew Python 3.14.3's SQLite dynamic library was absent. Preserved, not repaired globally.
- `/Users/frank/mygit/Tablekeeper/result-run-5/.evidence/arch-s1-20261004T2130Z/`:
  `/usr/bin/python3 .../mechanisms.py` exited 1 overall. Python 3.9.6 / SQLite 3.51.0:
  PASS four literal DST transitions + two gaps; five JSON equality cases; 50-thread same-key
  and different-key toy transactions, rollback and detached SQL snapshot. Scrypt subtest FAIL:
  that interpreter lacks hashlib.scrypt. UTC execution 2026-10-04T21:28:51.058624Z through
  21:28:51.075826Z. Directory suffix is only a unique identifier, not an execution timestamp.
- `/Users/frank/mygit/Tablekeeper/result-run-5/.evidence/arch-s1-20261004T2131Z/password-result.json`:
  separate `python3` scrypt-only experiment exited 0; portable synthetic hash/parameter JSON
  roundtrip PASS, 2026-10-04T21:29:06.129430Z through 21:29:06.196056Z. No hashes/tokens logged.

These experiments validate mechanisms separately; they do NOT establish a single working
runtime image, HTTP behavior, application import, resource limits, official harness or browser
success. Container integration and all product tests remain NOT_TESTED. Build verification must
catch both missing SQLite and missing scrypt in the selected image before service acceptance.
High risks are DST/calendar edges, historical offset formatting, exact cutoff boundary,
transaction scope accidentally bypassed by a handler, receipt mutation during migration,
reset/login generation races, and auth throughput. Each has a concrete acceptance target above.

Remaining restrictions: one writer; no production implementation in this item; no parallel
agents/worktrees; no operator probes; no paid services/API billing or human steering; no history
rewrites. Three repairs maximum (two changed-approach experiment recoveries used); deadline for
work 2026-10-04T21:35:38.372158Z and final handoff 21:36:38.372158Z; 120-second receipt deadline,
<=2 same-identity delivery retries; global deadline 2026-10-05T02:35:03.700437Z. Monetary cost and
remaining cumulative consumption UNAVAILABLE. Product acceptance belongs to Factory Reviewer.
