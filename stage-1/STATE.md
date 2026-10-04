# Portable state schema 1

The export envelope is `{track:"tablekeeper",format_version:1,state:{...}}`.
`state.schema_version` is 1. `state.entities` contains seven named logical
entities. Each has a fixed `columns` array and a `rows` array. SQL text, process
addresses, locks and filenames never enter the export. Rows contain scalar
values; `data`, `body` and `response` columns encode JSON objects as strings.

- `users`: id, unique email, data (id/email/display_name/password_hash).
  The hash is scrypt with n=16384, r=8, p=1, 16-byte salt and 32-byte digest,
  hexadecimal encoded. No plaintext passwords are retained.
- `sessions`: SHA-256 token digest and user_id. Raw bearer tokens are returned
  once during authentication; existing tokens resolve after import.
- `restaurants`: id, zero-based fixture position, validated configuration JSON,
  and mutation counter. Counter increments once per effective reservation write,
  including one increment for a batch; replay and no-op do not increment it.
- `dining_tables`: id scoped to restaurant_id, fixture position and table JSON.
- `reservations`: id, unique reference, owner, restaurant and data. Data contains
  all stage-1 public fields, user_id, a one-item table_ids list, revision,
  accepted_terms and history. Starts/ends retain their original explicit offsets;
  comparisons use their UTC instants.
- `allocations`: reservation_id, restaurant_id, table_id; foreign keys bind the
  allocation to its restaurant's table, even if another restaurant reuses its ID.
- `receipts`: user_id, method, concrete path, key, original parsed request body,
  and immutable successful response. Full JSON values include ignored fields.
  JSON comparison distinguishes booleans from numbers and ignores object order.

Accepted terms retain fixture policy version 0, grid, duration, cutoff and opening
hours. New records also snapshot timezone and all table capacities. History stores
consecutive sequence/revision, event, nondecreasing timestamp, full public result
and accepted terms; new records include changed_fields in table/time/party/status
order. Seeded bookings have a creation entry at reset time; no earlier history is
invented. No later-stage API is exposed.

The earlier development checkpoint's schema-1 snapshots remain readable: missing
optional timezone/table snapshots and changed_fields are retained unchanged.
Required initial terms and full result history remain available; a future reader
can obtain unchanged fixture capacities from restaurant configuration. Existing
receipts are never regenerated from current records or enriched with new fields.

Import validates types, fixed row layout, uniqueness, foreign keys, configuration,
password hash parameters, session digests, occupancy, exact time resolution,
capacity, identities, histories and receipt-result membership in preserved history
in a separate database. Only then does one transaction replace the destination.
Invalid state becomes 422 and leaves the destination unchanged. Export and reset
share the same lock and transaction boundary as booking writes. Reset validates
fixture shape before replacing records, ignores unknown fields, and clears all
sessions, receipts and histories. Control endpoints intentionally need no token.

Snapshots are private: they contain password hashes and authentication lookup
material. Tests hold snapshot/token material in memory and publish only summaries.

Batch validation follows stage-1 §11 input order. After global list shape and
unique-reference checks, each item resolves its owner-visible reference, checks
cancelled/cutoff state, checks membership in the first item's restaurant, and
validates its proposed amendment before the next lookup. A later missing or
cross-owner reference cannot replace an earlier error. The same captured UTC time
is used throughout. All proposals remain detached from live rows until every
non-occupancy check succeeds; collective occupancy then validates swaps and
unchanged listed items before one atomic commit and receipt.
