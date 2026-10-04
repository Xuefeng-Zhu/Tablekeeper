# Tablekeeper implementation contracts

Work item S1-ARCH. Owner @frankzhu94/factory-architect. Starting revision
`3eb5566b431caf6f6ca04469e5b241aacbf0e495`. Repository:
`/Users/frank/mygit/Tablekeeper/result-run-4`. Recorded 2026-10-04 UTC.
Scope: architecture for stages 1–4 and the immediate stage-1 implementation contract.
This is advisory architecture, not independent product acceptance. The exact official
specifications in the PM dispatch and requirements-coverage.csv govern all API details.

## Decisions and consequences

Use Python 3.13, Flask, Gunicorn, SQLite, and the standard-library zoneinfo adapter.
Pin concrete build dependencies and include tzdata in the image. Serve the later React/Vite
build from the same service. Python replaces the proposed Node backend because direct
IANA gap/fold checks and SQLite transactions reduce implementation surface. TypeScript
remains useful for the browser. There is no hosted database, auth provider, cache, queue,
CDN or runtime network dependency. Docker builds may download dependencies.

Run exactly one Gunicorn worker with threaded request handling. One process-wide RLock
serializes all state access, including reads, and protects one SQLite connection opened
with check_same_thread=False and explicit transaction control. Writes use BEGIN IMMEDIATE;
commit once on success, rollback on every failure. No domain helper commits or sends HTTP.
The lock is acquired before authentication against mutable state and held through the
committed response snapshot. JSON response transmission happens after releasing it.
This intentionally simple model gives serializable behavior for up to 50 requests and
avoids partly visible moves/imports. Multiple worker processes are prohibited under this
contract; they would invalidate a process-local lock and shared-connection assumptions.
The Docker command, not a developer convention, must enforce one worker.

SQLite stores normalized identities and relations plus JSON snapshots. An in-memory
SQLite database is sufficient because restart persistence is explicitly unnecessary;
a file in container-local writable storage is also compliant. Export/import is logical,
not a database filename or volume reference. Redis/Postgres would add an unneeded service;
mutable process dictionaries alone make atomic rollback and export validation harder.
Hold no lock during network I/O. Benchmark the delivered container under the official
2 CPU/2 GiB, 50 in-flight, 5-second request bounds; the host experiment is not that proof.

## State and module ownership

Backend modules and their boundaries:

| Module | Contract |
| --- | --- |
| app/routes | Parse JSON/query/header, route public and private endpoints, map domain errors into the exact envelope/status, serialize snapshots; never implement occupancy rules |
| store | Sole lock/transaction entry point; SQLite schema; reset, validated import, atomic export; no hidden commits |
| auth | Salted scrypt records, signup/login, portable non-expiring opaque sessions; no provider callbacks |
| validation | Shared scalar/shape/range checks and explicit endpoint precedence; booleans are never integers |
| time_rules | Strict local syntax, calendar validity, gap/fold resolution, policy grid, UTC intervals and RFC3339 formatting |
| reservations | Pure proposed-state validation plus transactional create/PATCH/cancel/moves; central occupancy predicate |
| receipts | Full parsed request identity and immutable original response; participates in caller transaction |
| policies/history/series/planning | Later-stage additions using the same transaction and proposal primitives |
| web | Stage-2 browser state, routes and presentation; calls HTTP only, never imports server state |

Persist users (id, email, display name, self-describing password hash), sessions
(token digest, user id), restaurants (original fixture and fixture order), tables
(restaurant, id, label, capacity, order), reservations, reservation-table memberships,
and receipts. Keep IDs opaque and at most 64 characters; enforce uniqueness on references,
emails, identifiers and receipt scope. Generate references from A–Z/0–9, 6–12 characters,
checking collisions transactionally. Generate session tokens using cryptographic randomness;
hash tokens for lookup and export those lookup hashes unchanged. Existing raw browser
tokens remain valid against imported hashes. Never log tokens, passwords or exports.

Reservation storage includes stable id/reference/owner/created_at, restaurant, canonical
ordered table ids, local start, UTC start/end, status. Represent every allocation internally
as a table-id list from stage 1; stage-1 responses still use their specified single-table
shape. Retain exact timestamp strings needed by existing responses. Store fixture order
explicitly instead of relying on database row order.

For forward continuity, reserve a versioned state schema from stage 1 and retain internal
accepted policy-0 terms, revision and history snapshots when bookings change. These need
not appear in stage-1/2 response shapes. Also retain restaurant mutation counters. This
small provenance layer avoids trying to reconstruct earlier changes during an upgrade;
it does not expose or implement later-stage endpoints early. Later tables add immutable
published policies, series/membership (index, scheduled date, exception), closures and plans.

## Transaction and occupancy contracts

All read endpoints read a single committed state while holding the state lock. Occupancy
is UTC `[start,end)` for confirmed reservations. Two intervals conflict exactly when
`a.start < b.end AND b.start < a.end` and their table sets intersect. Cancelled rows do not
occupy. Stage 4 additionally treats applied closures as blocking intervals on their table.
Availability and every mutation call the same predicate. A pair blocks both members.

Create: validate proposed booking, check occupancy, insert reservation and provenance,
update counter, store receipt, commit; return 201. A failure writes none of these.
Cancellation: resolve owner-visible reference; already cancelled returns its current
state without cutoff or counter changes; otherwise check accepted cutoff, cancel and
record history/counters once. PATCH: resolve record; reject cancelled; validate optional
expected_revision before cutoff/changes when supported; check current accepted cutoff;
merge recognized fields; normalize selections; detect no-op; validate actual changes and
occupancy excluding this reservation; atomically replace. Identity and created_at survive.
No-op remains subject to confirmed/editable checks but preserves terms/time/history.

Moves: validate list shape (1..8 objects, unique string references), ownership and common
restaurant; resolve all current rows. In input order apply each amendment's non-occupancy
checks, with its revision/cutoff precedence. Build a proposal map without mutating live
rows. Only after all non-occupancy checks pass, check proposals against all unlisted
confirmed rows and against each other, including unchanged listed rows. Never temporarily
free tables in live state. Apply all real changes and one receipt together. Return all
listed rows in input order, including no-ops. Swapping occupied tables works because old
allocations of listed rows are replaced only in the proposal view. A failed last move
must preserve every old row, history, counter and key. Capture one UTC 'now' per operation.

## Idempotency and validation precedence

Receipt primary key is `(user_id, HTTP method, concrete path, idempotency_key)`. The same
key on a different path is an independent request, including different plan/series ids.
Store the full parsed JSON body (including ignored fields) and the exact successful
response JSON value. Do not derive replay from the current reservation or rerun its
serializer after an upgrade. Store only successful responses, in the same transaction
as their side effects. First successful caller gets 201; all identical concurrent retries
get 200 and the original JSON body. Rejected requests leave the key unused.

JSON equality ignores object key order and whitespace; array order matters. Unknown body
fields are ignored for business rules but are part of retry identity. Use a recursive
type-aware JSON comparator or canonicalizer: distinguish booleans from numbers, normalize
equivalent JSON numbers, compare full nested objects, and reject non-JSON NaN/Infinity.
Do not use Python's plain equality (`True == 1`) or stringify unsorted maps as identity.
Storing canonical request text plus original parsed body is acceptable; a hash alone is
unnecessary and cannot substitute for preserving the request on export.

Pipeline for key-bearing endpoints:

1. Parse body as a JSON object and authenticate caller. Invalid JSON/non-object body is
   malformed_request; absent/malformed/unknown bearer is unauthenticated. Resource-private
   history/decision/series reads deliberately use their stage-3 owner-only 404 exception.
2. Check required key: absent/empty => 400 missing_idempotency_key; over 255 characters =>
   422 validation_failed. Look up receipt under the transaction lock.
3. If found, compare full body: mismatch => 409 idempotency_key_reuse; identical => original
   response with 200. This occurs before endpoint fields, authorization against the current
   resource, resource existence, cancellation state, capacity, cutoff or revision checks.
4. For unused keys perform endpoint validation and current permission/resource checks,
   then proposal/occupancy checks and commit. Manager authorization is still required on
   a fresh policy/replan/apply operation; manager status alone never grants diner access.

For ordinary fields: missing required field or invalid value => 422; wrong JSON type =>
400. Endpoint exceptions take priority: all invalid party_size values (including strings
and booleans) => 422; starts_at_local strings must be bare YYYY-MM-DDTHH:MM, with wrong
string format => 422 (other wrong types => 400); moves' invalid shape => 422; policy,
series and expected_revision special rules follow their explicit 422 definitions.
Validate query integers with ASCII decimal digits only, never float coercion. Strict date
validation must reject impossible calendar dates. Correctly typed IDs over 64 chars are
422. Unknown fields/queries are ignored except that receipts retain the original body.
Do not invent a blanket schema library default which replaces specified error codes.

Return the shared error envelope for every 4xx/5xx, including framework routing/parser
errors. Valid concurrent requests must not trigger 5xx or SQLite busy/connection exceptions.
No past-date ban exists. Reference lookup masks other owners with 404. General private
endpoints require 401 without auth unless their explicit later-stage exception says 404.

## Local time and policy interval adapter

Parse bare local strings using exact regex and calendar validation. Construct fold=0 and
fold=1 candidates with ZoneInfo; convert each to UTC and back. Retain only candidates whose
roundtrip local wall fields equal input. Zero candidates is invalid_local_time; otherwise
choose the smallest UTC instant, guaranteeing the first repeated occurrence. Enumerate
the wall-clock grid once, so repeated times never create two availability slots. Skip
nonexistent availability starts; directly booking one returns the specified 422.

Grid is based on local elapsed wall minutes since that day's opening, using the selected
policy's slot_minutes. Compute ends by adding duration to the UTC start, then format end
back in restaurant timezone. Never add duration to a zone-aware local datetime directly:
that can implement wall-time arithmetic across a fold. Resolve the opening/closing local
boundaries through the same adapter; require a start in the opening interval and absolute
end no later than the resolved closing instant. For a nonexistent boundary, define its
instant as the forward transition boundary; it does not make skipped local starts valid.
Test unusual boundary fixtures explicitly if encountered. Outside hours and off-grid
checks retain their distinct codes. A closed day produces no slots. Cutoff compares UTC
now with current UTC start minus accepted cutoff; equality is already cutoff_passed.

zoneinfo supports the IANA database and fold selection; bundle tzdata because the module
does not supply the database itself ([Python documentation](https://docs.python.org/3/library/zoneinfo.html)).
Host evidence checks Berlin and New York spring gaps and first fall folds and New York's
01:30 plus 90 real minutes => 02:00 -05:00. Container tzdata and end-to-end grid behavior
remain independent release checks.

## Passwords, snapshots and evolution

Use salted scrypt with stored algorithm/version/parameters/salt/digest, for example
N=16384,r=8,p=1 and 32-byte output. Use constant-time digest comparison. Seed passwords
are hashed during reset and immediately usable. Plaintext exists only in transient request
handling and is never retained in state, receipts for auth, files or logs. Signup/login
are not idempotency-key endpoints. Multiple non-expiring sessions per user are retained.
Check hash cost under the final container's concurrency limit. Expensive hashing may occur
before lock acquisition only if user/state identity is revalidated under the lock before
commit; the simplest initial implementation can hash under the lock and measure latency.

Export envelope remains `{track:"tablekeeper",format_version:1,state:{...}}` throughout.
Inside state use a separate schema_version, initially 1, and explicit entity arrays.
Document each entity field in backend code. Include all accounts/hash material/session
lookup hashes, original fixture config/order, reservation identities/statuses/timestamps,
accepted terms/revisions/history, successful receipt bodies and original responses.
Later schema versions additionally carry policies/counters/series/scheduled dates/
exceptions/closures/plans and their applied state. Do not export locks, SQLite paths,
ports, process ids, signing secrets dependent on a source instance, or executable SQL.

Export builds a deep JSON snapshot under the lock. Subsequent mutations cannot modify it.
Import validates envelope, schema version, types, unique IDs/references/keys, relations,
hash formats, JSON receipts, intervals and domain consistency into staging data; only a
fully validated state can replace the live tables inside one transaction. Reject invalid
state with 422 and leave destination unchanged; unparseable JSON remains 400. Repeat import
restores exactly the snapshot. Reset similarly replaces all state and removes sessions,
receipts, histories, policies, series and plans. Test control endpoints stay unauthenticated.

Migration functions are pure `v1 -> v2 -> ...`, never depend on source files/network. Keep
every older reader until stage 4; test each earlier stage export against each later target.
Responses stored in receipts are immutable even if they lack new stage fields. New reads
use the target-stage serializer. Stages 1/2 preserve the internal provenance described
above, so v3 can expose prior history and accepted policy-0 terms without regeneration.
If a documented early schema omitted provenance, explicitly migrate from preserved
creation/current state and record the limitation; do not fabricate intermediate events.

## Stage-2 client contract for designer/frontend

React/Vite compiled assets are packaged locally, served under `/`, `/signup`, `/login`,
`/lookup`; API stays same-origin. No font/CDN/network calls at runtime. Designer owns warm
hospitality hierarchy, labels, responsive 375px layout, keyboard focus and visual state
inventory before frontend implementation. Testids and exact-text elements follow the
official spec unchanged; use declared pair order for combination cell ids and table labels.

Maintain separate search draft, submitted search snapshot and active result generation.
Each search increments a monotonic generation; restaurant detail and availability belong
to that generation. Commit results/labels/form context only if generation still matches.
Aborting stale fetches is an optimization, not the correctness guard. A new search clears
or supersedes old selection so a late A response cannot restore A after B.

Booking form owns an immutable attempted body and random retry key, separate from editable
fields. Unchanged form repeats exactly that body/key after success or transport uncertainty.
A field edit creates a new logical request/key; never blindly retry an old uncertain
request with edited data. Sending shows loading. Parsed successful HTTP response shows
confirmation. Confirmed rejection shows booking-error; table_unavailable refreshes the
matching availability generation while preserving form/inputs. Lost connection, timeout
or uninterpretable submission outcome shows only booking-uncertain with no new confirmation
or booking-error. A successful retry clears uncertainty/error and shows original reference.
Keep the form after success. Never infer confirmation from availability or cache.

Store auth token/display name independently from pending body/key, persist auth as needed
across route navigation, and attach bearer on protected calls. Export/import between
browser requests must not clear the in-memory form or retry identity. Logout clears the
browser auth session. Import-preserved session hashes make existing tokens usable without
sign-in. Browser signed-in display does not substitute for actual authenticated API success.

## Stage-3 and stage-4 extensions

Policies: immutable per-restaurant publication version starting 1, allocated only within
a successful transaction. Policy selection sorts eligible effective dates descending then
version descending; fallback is fixture policy 0. Original detail endpoint keeps fixture
configuration. Reservation accepted_terms snapshots complete policy values, not a pointer.
Actual diner changes check old cutoff first, then all resulting fields under resulting-date
policy; no-op keeps old terms/end/revision. Replays always return original serialized terms.
History entries append with consecutive seq, nondecreasing at, exact changed-field order,
resulting revision and full accepted terms. Pair comparisons use unordered identity but
canonical declared order for output; reversing a pair alone is a no-op. Explanations evaluate
capacity and occupancy independently for every table, in fixture/rule order.

Series: store agreement id/owner/revision/interval and memberships with immutable occurrence
index and original scheduled local date. Adoption retains anchor exactly; generate dates
using local calendar weeks, never UTC 7-day durations. Validate generated occurrences in
index order against proposal occupancy and commit all or none, including counters/receipt.
Individual real edits permanently set exception and increment affected series once;
cancellations increment once but do not set exception. Collective moves increment each
affected series once, not once per member. Restaurant increments once per successful
operation that actually mutates bookings/policies; adoption increments once as specified.

Replans: enumerate at most ten ranked options (six singles/four pairs) for at most six
considered bookings. Reference-sort bookings; build candidates using each booking's accepted
capacities, fixed bookings, existing closures and proposed closure. Backtrack with early
conflict pruning, comparing objective tuple `(moved_count, unused_seats, option_rank_vector)`
lexicographically. At most 10^6 complete combinations before pruning is the explicit bounded
input; benchmark the final implementation for 5 seconds before declaring ready. Choose a
single deterministic optimum; no feasible complete assignment => no state or receipt.
Preview stores plan and base restaurant revision only; no closure/counter/history mutation.
Apply checks already-applied status for a different key, then matching restaurant revision;
replay resolution comes before both. Commit closure, assignments, moved revisions/history,
affected-series revisions and receipt once. Unmoved bookings stay untouched. Operator
reassignment preserves times/terms/cutoff exemption and exception flags/scheduled dates.

Series amend validates expected series revision before member cutoff/booking checks. Select
eligible noncancelled/nonexception members from_index onward, on their original scheduled
dates, retaining current tables. Build all proposals, perform non-occupancy checks in index
order, then collective occupancy including unchanged members and closures. Real changes
produce ordinary history; do not set exceptions. Series/restaurant increment once if any
change; all-no-op/empty selection still stores successful receipt with no increments.

## Implementation sequence and risk gates

1. Backend implements only stage-1 container/API now: shared transaction core, validation,
   time adapter, auth, reset/export/import, restaurants/availability, reservation operations,
   moves and receipts. Keep modules focused; maintain portable internal provenance.
2. Backend runs own focused tests and isolated official stage-1 harness. QA adds independent
   behavioral tests; reviewer checks a clean exact candidate. PM alone records acceptance.
3. Only after stage-1 acceptance copy its complete output into stage-2, add declared pairs
   and designer-approved frontend. Preserve stage-1 unchanged. Test real browser delayed
   responses, conflicts, lost responses, retry and upgrade; designer reviews desktop/375px.
4. After stage-2 acceptance copy forward to stage-3; add policies/history/series. After
   stage-3 acceptance copy forward to stage-4; add plans/closures/series amendments. Each
   independent directory contains Dockerfile/RUN.md and full source with no nested Git.

| Consequential risk | Required evidence before release |
| --- | --- |
| Split booking/receipt or partially visible moves | 50 concurrent identical creates; concurrent competing creates; swap and failed-last-move rollback; reads see only old/new state |
| Wrong error precedence | Used-key invalid-body mismatch; cancelled replay; stale revision versus cutoff; input-order non-occupancy errors before conflicts |
| DST/wall arithmetic | Both named zones and transitions; first folds only; skipped slots; absolute ends/opening bound; past dates allowed |
| Nonportable state | Fresh destination import with no source process; same tokens/login/references/receipts; failed import leaves sentinel destination; repeated replacement |
| Upgrade drift | Every earlier export to every later stage; unchanged receipt exact JSON including old shape; pending browser submission across upgrade |
| Browser race/uncertainty | Controlled response reorder and dropped post-commit response; preserved selected form; same body/key retry |
| Terms/history/counter drift | No-op, cancellation twice, replay, batch, policy tie, series exception, acceptance snapshots before/after each failure |
| Planner incorrect optimum or latency | Exhaustive tiny oracle, ties, fixed bookings, closures, imported series, stale/apply races, worst bounded 6/4/6 input timing |
| Container dependency/concurrency gap | Real isolated Docker harness, no outbound assets, PORT binding/health <60s, 50 in-flight <5s, control calls <10s |

Own check evidence is under
`/Users/frank/mygit/Tablekeeper/result-run-4/.evidence/S1-ARCH-20261004T1848Z`.
The host mechanism experiment validates gaps/folds, absolute duration, serialized receipts,
rollback and scrypt roundtrip only. Default host Python failed importing its broken SQLite
dynamic library; the pinned harness interpreter (Python 3.13.5/SQLite 3.47.1) passed. This
does not validate Flask/Gunicorn, container behavior, API correctness, frontend, planner
performance, official harness or independent acceptance: all remain NOT_TESTED here.

The [SQLite Python API](https://docs.python.org/3/library/sqlite3.html) documents connection
transaction control; [Flask's Gunicorn deployment guide](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/)
documents the server integration. The one-worker locking discipline and domain contracts
above are this architecture's decisions and require implementation verification.
