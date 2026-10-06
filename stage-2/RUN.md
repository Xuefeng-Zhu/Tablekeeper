# Stage 2

From the repository root:

```sh
docker build -t tablekeeper-stage2 stage-2
docker run --rm --cpus=2 --memory=2g -e PORT=8080 -p 8080:8080 tablekeeper-stage2
```

The service binds 0.0.0.0, defaults to port 8080 and becomes healthy at
GET /health. For a different port change both PORT and the port mapping.
No external service, mounted data or runtime network access is required.
All state is ephemeral in one process. Reset seeds it using POST /_test/reset.
IANA timezone data is included at image build. The API includes authentication,
availability, reservations, atomic batch moves, and private export/import.

Exports contain password hashes and bearer tokens: keep them private.
For local development, PORT=8080 python3 stage-2/server.py uses the same service.
The HTTP owner suite runs with python3 tests/backend_stage2_inherited.py and python3 tests/backend_stage2_pairs.py.

Opening/closing boundaries falling inside a DST gap advance to the first valid
minute; this boundary convention is unspecified by the inherited Stage 1 contract. Ordinary nonexistent
booking starts are rejected, repeated starts choose the first occurrence, and
durations always use absolute elapsed time.

Stage 2 adds declared two-table combinations and schema1-to-schema2 portable migration. Old successful receipts are returned without adding fields; current singleton records carry both table_id and table_ids. The server serves static/index.html at /, /signup, /login and /lookup and confines /static/ files to that asset root. Frontend supplies these local assets in its separate integration slice; until then the HTML routes return JSON404. No browser result is claimed by the API slice.
