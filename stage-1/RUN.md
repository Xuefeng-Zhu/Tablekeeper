# Stage 1

From this directory:

```sh
docker build -t tablekeeper-stage1 .
docker run --rm --cpus=2 --memory=2g -e PORT=8080 -p 8080:8080 tablekeeper-stage1
```

The service binds 0.0.0.0 and starts with an empty state. GET /health reports readiness.
POST /_test/reset replaces state with the supplied fixture. All dependencies and IANA
timezones are in the image; no outbound runtime connection is made. State is ephemeral.
Export/import test endpoints are enabled and unauthenticated; exports contain private
password hashes and sessions and should not be logged or published.

Host development: `PORT=8080 python3 server.py`. Focused tests:
`python3 -m unittest discover -s tests -v`.

All state access uses one process-wide lock and mutations commit a detached candidate.
Receipts retain exact numeric request values in JSON document strings. Never start
multiple server processes behind a load balancer: in-memory state is process-local.
Private record `facts` preserve actual creation/change/cancellation timestamps, revisions,
and policy-0 terms. `Service.ledger(reference)` is a private in-process test adapter and
returns only a detached fact list; there is no public history endpoint in stage 1.
