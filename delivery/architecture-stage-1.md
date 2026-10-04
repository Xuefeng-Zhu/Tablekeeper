# Stage 1 architecture and implementation contract

Work item: S1-ARCHITECTURE. Owner: @frankzhu94/factory-architect. Next recipient: @frankzhu94/factory-pm. Decision candidate for independent review, not product acceptance.

Starting revision: `2e91c86a49c509e73289831aa0539f2e458e57b0`. Repository: `/Users/frank/mygit/Tablekeeper/result`. Normative input: `/Users/frank/mygit/Tablekeeper/challenge/tablekeeper/spec/stage-1.md`, source revision `803560d2a678ace1414465c098eb0ab5380ffade`, SHA-256 `9460189eac83802ce158f16ee90989af728a489b32a6147e2dc8e320f383055f`. Complete dispatched requirements govern when this decision adds an implementation choice. No existing restaurant product code, schema or API documentation was consulted.

## Decision 1: one process and one authoritative state root

Use Python 3.12+ with a threaded HTTP adapter, standard-library domain code and IANA tzdata installed in the Docker image. A single process owns an in-memory `State` and `threading.RLock`. All readers and writers use this same lock to obtain consistent state. Ephemeral state is permitted; no runtime disk persistence or external service is needed. Never start multiple workers/processes with independent state. HTTP body reads and response network writes occur outside the state lock. Configure the HTTP listen backlog for at least 50 concurrent clients; validate actual container concurrency before acceptance.

The proposed Node/SQLite solution remains viable but adds a native database dependency and a separate local-time resolution mechanism. SQLite would provide persistence and multi-process coordination that Stage 1 does not require. The selected alternative reduces setup while making atomicity directly auditable. Its cost is serialized mutation and in-memory limits; this is an explicit capacity risk to measure under 2 vCPU, 2 GiB and 50 requests, not an assumed performance pass. Do not add an external backend, auth provider, queue, browser bundle or frontend for Stage 1.

Build a staged state before publication. At commit, replace the entire authoritative root once while holding the lock. Unchanged immutable records may be shared across roots; changed maps/records and response snapshots must be copied. No record reachable from an older root or a stored receipt may later be mutated in place. Full deep-copy is acceptable initially if measured within limits; localized copy-on-write avoids growing request costs. Derived indexes are rebuilt/updated with the root, never authoritative separately.

Each write linearizes at root replacement; a read linearizes when it captures a detached representation under the lock. Validation failure publishes nothing. All bookings, cancellations, moves, account/token creation, reset and import share this boundary. Capture `now_utc` once inside the transaction for cutoff checks. Do not perform password hashing or network I/O under the global lock.

## Decision 2: modules and records

Backend owns the Stage 1 implementation after PM dispatch. Suggested modules (names may vary, boundaries must hold):

| Module | Contract |
|---|---|
| HTTP adapter | Route/method matching, object JSON decoding, bearer/header extraction, query parsing, response/error encoding; never mutates domain state directly. |
| Validation | Missing/type/range distinctions, strict dates/local times/IDs, endpoint error precedence. Unknown body/query fields do not become validation errors. |
| Time | Local parsing, fold/gap resolution, local slot iteration, instant duration, RFC 3339 formatting. Injectable clock for tests. |
| State | Root, lock, immutable record discipline, transactional root publication and derived indexes. |
| Auth | Password hashing/verification, user/token lookup, signup/login publication, ownership. |
| Reservations | Pure proposal validation, half-open occupancy, create/cancel/amend/move transactions. |
| Receipts | JSON-semantic body equality, user/method/path/key namespace, immutable original responses. |
| Portability | Fixture normalization, complete export encoding, import validation and atomic replacement. |

Authoritative data: users keyed by opaque ID, password records containing algorithm/parameters/salt/digest, tokens mapped to user IDs, restaurants in fixture order with ordered tables/opening hours, reservations keyed by ID, and receipts. Reservation records retain ID, immutable reference, owner, restaurant, table, party size, confirmed/cancelled status, start/end UTC instants, original/current local start and created timestamp. Store the response timestamp strings as needed to avoid import regeneration. Reference and email indexes are derived. A table is scoped by `(restaurant_id, table_id)` so unrelated restaurants cannot collide internally.

Generate opaque IDs no longer than 64 characters, non-expiring random tokens, and unique 6–12 character `A-Z0-9` references. A 10-character uppercase alphanumeric random reference with collision check under the lock is sufficient. Check uniqueness against cancelled reservations too. Fixtures preserve supplied IDs and references; seed `created_at` once if absent. Imported IDs, references, statuses and timestamps are never regenerated. No assumed prefix or numeric sequence is allowed for fixture IDs.

## Decision 3: transport and validation precedence

Every JSON response uses `application/json; charset=utf-8`; 204 sends no body. Every error uses `{"error":{"code":"...","message":"..."}}`. Catch decoding, calendar, timezone and numeric conversion failures deliberately. Prevent invalid input from leaking exceptions or HTML responses. A generic 500 guard is useful diagnostically but does not satisfy the requirement that requests produce no 5xx.

For keyed writes use this order:

1. Parse strict JSON and require an object; malformed JSON or wrong top-level type is 400 `malformed_request`. Reject non-JSON NaN/Infinity. Decode all unknown values too; they remain part of the receipt body.
2. Authenticate bearer token against the current state under lock; missing/malformed/unknown token is 401 `unauthenticated`.
3. Require `Idempotency-Key`: absent/empty is 400 `missing_idempotency_key`; over 255 characters is 422 `validation_failed`. Do not silently truncate.
4. Look up `(user_id, method, path, key)`. An existing equal body returns 200 with the immutable original JSON response. A different body returns 409 `idempotency_key_reuse` before field/resource/cutoff validation. Different methods/paths have independent namespaces.
5. Only an unused key reaches endpoint-specific validation and proposals. Publish a successful response receipt and state change together; every rejected request leaves the key reusable.

Missing required fields/parameters: 422 `validation_failed`. Other wrong JSON field types: 400 `malformed_request`, with explicit exceptions: all invalid `party_size` values give 422; malformed `starts_at_local` strings give 422; moves shape/reference rules below give 422; invalid import state gives 422. A non-string `starts_at_local` remains 400. Null is a value of the wrong type, not an omitted field. Integer-valued body numbers may be expressed as `4.0`; accept if mathematically integral, positive and within domain range; booleans are never integers. Query integers must match ASCII `[0-9]+`, be positive, and satisfy applicable range checks; `1e9`, `4.0`, `+4`, negatives and blanks give 422. Preserve blank query values during parsing. IDs of wrong type give 400; length over 64 gives 422; well-formed unknown IDs give 404. Strictly validate calendar dates and exact `YYYY-MM-DDTHH:MM` local strings before timezone conversion; offsets, seconds and `Z` are not accepted.

Ordinary booking proposal order: required/type/format checks, restaurant/table membership, local time resolution, opening window (422 `outside_opening_hours`), grid (422 `not_on_slot_grid`), capacity (422 `party_exceeds_capacity`), then occupancy (409 `table_unavailable`). Bound/check numeric and datetime arithmetic before conversion; representational overflow is validation failure, never a 5xx. Where the specification does not choose between two simultaneous invalid fields, this order makes behavior deterministic. Never let occupancy obscure a batch non-occupancy error.

## Decision 4: JSON-semantic receipts

Keep the complete parsed JSON value, including ignored fields, for equality. Object key order/whitespace do not matter; array order does. JSON numbers compare by value, booleans remain distinct from numbers, null remains distinct from absence, and strings compare exactly. Python's plain object equality is insufficient because `True == 1`. Parse fractional numbers with `Decimal`, compare numbers exactly, and recursively compare with explicit JSON type handling. Do not use binary-float rounding for body identity, or serialize with sorted keys alone and assume `1` equals `1.0`.

A portable receipt can store `{user_id, method, path, key, request_json, response}` where `request_json` is the original validated JSON text and `response` is a deep immutable JSON snapshot. Reparse `request_json` for semantic comparison; its whitespace is irrelevant. This keeps full numeric fidelity without inventing a lossy JSON number encoder. The field is private implementation-defined state, not a public API addition. Serialize receipt entries as a list, avoiding compound map-key encodings. Cache parsed values only as rebuildable derived data.

Hold the same state lock from receipt lookup through proposal validation and publication. Two identical requests then cannot both create; exactly one receives 201, remaining replays receive 200. Send the detached response after releasing the lock. A disconnected client after commit still has a receipt. Later amendment/cancellation cannot alter the saved response. Failed requests create neither receipt nor reservation, and importing/exporting does not reevaluate a successful request against present resources.

## Decision 5: time and occupancy

Use `zoneinfo.ZoneInfo`; package IANA data in the image and assert both required zones load before healthy startup. Python documents `fold=0` as the offset before a backward transition and requires system tzdata or the tzdata package ([official zoneinfo documentation](https://docs.python.org/3/library/zoneinfo.html)).

Resolve a valid naive local minute by attaching the restaurant zone with `fold=0`, converting to UTC, then back to the zone. If the wall-clock round trip differs, return 422 `invalid_local_time`; availability omits that candidate. Ambiguous times use the first occurrence and appear once. Do not accept a client offset as an alternate occurrence. Convert start to a UTC instant before adding duration, then format end in the restaurant zone. Direct local-aware datetime addition gives wall-time arithmetic and must not implement duration.

Generate slot labels by stepping local wall-clock minutes from `opens`, so a skipped hour does not shift the grid. The start must be in a same-local-date opening interval and `(local_minutes - opens_minutes) % slot_minutes == 0`. The end instant is `start_utc + duration`; it must not exceed the resolved close instant. Interpret the duration condition in §8 using §9's absolute duration, including transitions. Do not additionally filter by naive `local_start + duration <= local_close` across a fold, which can reject valid real-time slots. If an opening boundary itself falls in a gap, clamp that boundary to the first existing local minute at/after it; fold boundaries use the first occurrence. This boundary choice is an implementation convention for an unspecified fixture edge and needs an explicit test if exercised.

All interval ordering, descending reservation sorting and cutoff checks use UTC instants. Overlap is `a.start < b.end and b.start < a.end`. Only confirmed records occupy tables. Back-to-back intervals are allowed. Capacity eligibility uses the requested party size; all generated slots remain present even when their available-table list is empty. Available table IDs preserve fixture order. Closed weekdays return empty slots. Do not reject a booking simply because its start is past. Cancellation/amendment cutoff fails when `now >= current_start - cutoff_minutes`, including equality.

## Decision 6: endpoint transactions

Public routes: `GET /health`, `POST /_test/reset`, `GET /_test/export`, `POST /_test/import`, signup/login, `GET /restaurants`, `GET /restaurants/{id}`, `GET /availability`. All reservation routes require bearer authentication. Unknown restaurant/table or wrong restaurant/table association gives 404 `not_found`.

Signup validates `local@domain`, minimum password length 8 and required display name. Store only salted password hashes, never plaintext. Use `hashlib.scrypt` with explicit encoded parameters (initial choice N=16384, r=8, p=1, dklen=32, maxmem=64 MiB), random salt and constant-time digest comparison. Python documents scrypt as a password-based KDF ([official hashlib documentation](https://docs.python.org/3/library/hashlib.html)). Seed accounts use the same hashing format and can immediately log in. Exact email matching is the initial policy; no unrequested address transformations. Duplicate signup is 409 `email_taken`; unknown email/wrong login password is 401. Login's wrong password result must not be replaced with signup's minimum-length error. Multiple tokens per account remain valid indefinitely.

Hash outside the state lock with bounded parallel work, initially two concurrent hash workers. For login, copy the hash record and state generation, verify outside the lock, then recheck generation/user/hash under lock before issuing a token. If reset/import replaced state, restart against the current state within the request deadline; never issue a stale token into new state. Signup rechecks email uniqueness after hashing. Reset prepares hashes outside the lock then publishes its fully built replacement. Do not publish partially seeded users or tokens.

Create: validate, check all confirmed occupancy, allocate ID/reference/timestamp, stage reservation and receipt, publish, return 201. Response fields are exactly the supplied shape: reservation_id, reference, restaurant_id, table_id, party_size, status, starts_at_local, starts_at, ends_at, created_at.

PATCH success returns 200 with the complete current reservation response. Restaurant list returns 200 `{"restaurants":[{"id":"...","name":"...","timezone":"..."}]}`; detail returns the full fixture restaurant shape. Availability returns restaurant_id, date, timezone and slots with starts_at_local, offset-bearing starts_at and available_table_ids. Signup returns 201 and login 200 with user_id, display_name and token. Health returns exactly 200 `{"status":"ok"}` only after initialization.

List: caller's confirmed and cancelled bookings sorted by descending start instant; stable ID tie-break is permissible. Detail/cancel/PATCH first resolve caller ownership; another user's reference is 404, not 403. Cancel an already cancelled record with 200 current state before checking cutoff. Otherwise check current cutoff then publish cancellation; the next completed availability read sees the freed table.

PATCH: find owned reservation, reject cancelled with 409 `reservation_cancelled`, check cutoff against the old start, merge only table/time/party fields, validate the resulting proposal, test occupancy excluding itself, then replace one record atomically. Never change identity, restaurant, reference, owner or creation timestamp. Failed changes retain original occupancy. Empty/unknown-only changes retain all existing values after the normal state/cutoff checks.

Moves: require a `moves` array of 1–8 objects, each with a distinct string reference; invalid shape/duplicate references is 422. Under the transaction lock, iterate input order: owned reference lookup (404), same-restaurant check (422), cancelled (409), current cutoff (409), then that item's ordinary amendment non-occupancy validation. A cutoff for an item precedes that item's proposed field changes. Do not scan later items for errors before finishing the current item's non-occupancy checks. Keep all original records untouched while building proposals, including unchanged entries. After all non-occupancy checks pass, exclude old occupancy of every listed record; test each proposed interval against unlisted confirmed records and every other proposal. This permits swaps while unchanged listed bookings still occupy their proposed interval. Any conflict is 409 `table_unavailable`. Publish all proposals plus one receipt in one root replacement. Return 201 `{"reservations":[...]}` in input order; replay 200 original response. No-op moves retain all existing values.

## Decision 7: reset, export, import and evolution

Reset constructs a new root from users, restaurants/tables and optional seeded confirmed reservations, validating cross-references and ID bounds, hashing passwords and deriving absolute timestamps. Ignore unknown fixture fields. Clear old accounts, tokens, bookings and all receipts. Publish once; after 204 only the replacement state exists. Startup begins with a valid empty root and requires no manual fixture or authentication setup.

Export takes a detached consistent snapshot under the lock, then serializes outside it. Envelope: `{"track":"tablekeeper","format_version":1,"state":{...}}`. State contains `schema_version:1`, ordered restaurants, user/password records, all session tokens, reservations with preserved timestamps/identities, completed receipts and any required identity-generation metadata. No plaintext password, process handle, file path, pickled object, source URL or external secret is needed. These private exports must never be printed in ordinary logs or committed as evidence.

Import first parses the envelope, checks exact track/version and validates state records/types, uniqueness, ownership/references, password formats, receipt request JSON/response shapes, statuses, temporal integrity and non-overlapping confirmed occupancy. An unchanged self-export must always pass, even if reservations are now past/cutoff or a receipt describes an older state. Do not apply current cutoff or replay side effects while importing. Envelope missing fields, wrong track/version or any invalid state is 422; unparseable body is 400. Stage everything off to the side, rebuild derived indexes, then swap the root under lock and increment an internal generation. Invalid import leaves destination entirely unchanged. Valid import removes every prior destination account/token/receipt; repeated import restores the same snapshot without duplicates. Hash records bring algorithm/parameters/salt/digest with them; no machine-local pepper/signing key dependency is allowed.

Keep outer format_version=1 while internal schema versions evolve through explicit pure migrations in later dispatched stages. Future fields can be namespaced in the state object; do not implement them now. Preserve old receipts as original JSON values across migrations, independently of newer reservation presentation. Copy Stage 1 forward only after independent acceptance; subsequent stages cannot rewrite the accepted Stage 1 tree. Later UI consumes the HTTP contract; it is not a transaction authority.

## Risks and required evidence before product acceptance

| Risk / requirement | Required focused check |
|---|---|
| S1-01,07,11 atomicity | 50 concurrent same key: exactly one 201, 49 equal 200, one booking. Different keys competing for one table: one winner; losers 409, no receipt. Concurrent moves/amends/creates preserve non-overlap. |
| S1-05 validation | Wrong-type/missing/format matrix; party bool/string and local-offset exceptions; raw query plus/decimal/exponent; reused key beats invalid body; failed-key retry works; unknown fields ignored for validation but included in receipt equality. |
| S1-04,08 identity | Arbitrary fixture IDs, seeded login/bookings, no 64-character overflow, reference uniqueness, table order, public browsing, ownership 404, cancelled listing, descending instant sorting. |
| S1-08 cutoff | Exact boundary, existing versus proposed start, double cancel after cutoff, cancelled PATCH, failed amendment preserves occupancy, past creation allowed. |
| S1-09 DST | Required Berlin/New York gaps/folds, one fold slot, UTC absolute end, offset changes, grid after spring gap and fold-close eligibility. |
| S1-10 portability | Export A, mutate A, import B with different preexisting credentials, prove old tokens/login/IDs/timestamps/replays on B; destination-only credentials removed; repeated import; invalid import unchanged; reset clears imported state. |
| S1-11 moves | Swap, no-op occupied entry, later item's non-occupancy error before earlier conflict, input order, per-item cutoff before changes, rollback all records and retry key, immutable replay after cancellation. |
| S1-02,03 runtime | Actual Docker build and isolated run, alternate PORT and default 8080 on 0.0.0.0, no outbound runtime dependency, health within 60s, 50 in flight within 5s (control calls 10s), 2 vCPU/2 GiB. |
| S1-06 hashing | No plaintext retained, seeded and imported login, concurrent sessions, signup collision, reset/login race and bounded hash cost in actual constrained container. |

Host-only evidence at `/Users/frank/mygit/Tablekeeper/result/.evidence/S1-ARCHITECTURE-20261004T043417Z`: `probe.py` and `probe-output.txt`. At Python 3.14.3 on Darwin, probes passed both gap rejections and first-fold resolutions, New York 90-minute absolute duration, numeric/boolean JSON equality distinctions, synthetic 50-thread single receipt publication, detached snapshot, half-open adjacency, final-state swap and scrypt hash/verification. The concurrency experiment establishes the selected lock mechanism in a synthetic model; it is not an HTTP load test. `threading.RLock` is documented as a reentrant synchronization primitive ([official threading documentation](https://docs.python.org/3/library/threading.html)).

Container build, official harness, real HTTP, actual resource constraints and independent review are NOT_TESTED in this architecture-only item. No service implementation is included. PM must pass complete requirements plus this contract to Backend and later QA; only independent review of the exact committed product candidate can accept the product gate.
