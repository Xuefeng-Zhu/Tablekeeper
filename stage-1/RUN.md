# Stage 1

Build and run independently from this directory:

```sh
docker build -t tablekeeper-stage-1 .
docker run --rm --name tablekeeper-stage-1 --cpus=2 --memory=2g -e PORT=8080 -p 8080:8080 tablekeeper-stage-1
```

The API listens on `0.0.0.0:$PORT` (default 8080). `GET /health` checks the initialized SQLite store. Seed synthetic data using `POST /_test/reset`; the initial state is empty. All dependencies and IANA zones are included in the Python Debian image; no network is used at runtime. State is ephemeral in-memory SQLite. Test reset/export/import endpoints are intentionally enabled and unauthenticated as required; exports contain portable password hashes and sessions and must be kept private.

The server uses one process, threaded HTTP with backlog 256, a shared reentrant lock and explicit SQLite transactions. A versioned JSON state document is replaced within the transaction; reads return detached snapshots. Scrypt work occurs outside that lock with a maximum of two simultaneous derivations. Historical successful receipt request strings preserve decimal precision and response strings are immutable.

Local ambiguous starts resolve to the earliest UTC instant; gap starts are rejected. A nonexistent opening/closing boundary resolves to the first valid minute after the gap while retaining the original grid anchor. Historical sub-minute offsets are rendered in UTC to retain RFC3339 syntax. Calendar arithmetic outside Python's supported years 0001–9999 is rejected.

Owner unit checks: `docker run --rm -v "$PWD/tests:/tests:ro" tablekeeper-stage-1 python -m unittest discover -s /tests -v`. HTTP tests live separately in the repository's independent QA suite and require two disposable instances.
