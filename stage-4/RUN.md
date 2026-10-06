# Stage 4

From the repository root:

```sh
docker build -t tablekeeper-stage4 stage-4
docker run --rm --cpus=2 --memory=2g -e PORT=8080 -p 8080:8080 tablekeeper-stage4
```

One process binds 0.0.0.0, default PORT8080. GET /health reports readiness.
Change PORT and the published mapping together. State is ephemeral; no external
service, database, runtime dependency download or outbound networking is needed.
The image includes IANA timezone data and the accepted local Stage2 browser assets.
GET /, /signup, /login and /lookup serve HTML; /static files are confined to their root.

Stage4 inherits policies, histories and recurring adoption, and adds exact seating
preview/apply with closures plus original-schedule collective series amendments.
Previews, applications and series amendments require Idempotency-Key.
The solver minimizes changed sets, unused seats, then fixture/declaration ranks
in reference order using each booking’s accepted capacities and full intervals. Policy publication and series adoption
require Idempotency-Key, as do create and collective moves. Each transaction
publishes reservations, histories, counters, exception flags and receipt together.
Original receipts remain immutable across later changes and schema upgrades.

POST /_test/reset seeds fixtures. Private exports/imports use external format1,
internal schema4; actual schema1/2/3 snapshots migrate credentials/tokens and live
records while preserving original responses without backfilling new fields.
Cancelled legacy records bootstrap created/cancelled history at the same stored
created_at/revision1 because their earlier history is unavailable. Schema3 keeps
actual histories and validates terms against their originating policy.
Series snapshots carry private baseline revisions and a mutation ledger to validate
portable revisions/exceptions; public series responses omit this internal metadata.
Restaurant counters start at zero and advance once per real transaction, including
closure-only application. Previews/no-ops/replays leave them unchanged. Native
snapshots include immutable captured plans, applied closures and original scheduled
series dates; legacy Stage3 dates come from the original adoption receipt.
Rich mutation kinds preserve diner exceptions through repairs and series amendments.

Exports contain password hashes and bearer tokens. Keep them private and outside Git.
Opening/closing boundaries in DST gaps advance to the first valid minute, an
explicit convention for an underspecified boundary; booking gap starts are rejected,
fold starts choose their first occurrence, and durations use absolute elapsed time.

Owner HTTP checks:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/backend_stage4_http.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/backend_stage4_inherited.py
```

The focused suite starts actual Stage1, Stage2, Stage3 and two Stage4 services locally.
BACKEND_TEST_URLS can supply those five URLs in order Stage4, Stage4, Stage1, Stage2, Stage3.
This is distinct from packaged offline and actual Chromium evidence retained in
.evidence; owner checks do not substitute for independent release acceptance.
