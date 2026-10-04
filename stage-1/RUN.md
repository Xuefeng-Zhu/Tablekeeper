# Tablekeeper Stage 1

From this directory, build and start:

```sh
docker build -t tablekeeper-stage-1 .
docker run --rm --cpus=2 --memory=2g -e PORT=8080 -p 8080:8080 tablekeeper-stage-1
```

For another port, set `-e PORT=9090 -p 9090:9090`. Health is `GET /health`.
The image contains Python and IANA timezone data. Runtime requires no outbound
network, files, credentials, external database or initialization. Initial state is
empty. `POST /_test/reset` supplies users, restaurants and optional reservations.
One threaded process owns atomic in-memory state; restart discards it. Do not run
multiple workers against the same logical service.

`GET /_test/export` and `POST /_test/import` transfer complete private state,
including hashed credentials, bearer tokens and immutable retry receipts. Protect
these unauthenticated test endpoints and exports from public access in deployments
outside the test environment. No state or request bodies are logged.

Owner tests: `python -m unittest discover -s ./tests -t ./tests -v` (Python 3.12+).
