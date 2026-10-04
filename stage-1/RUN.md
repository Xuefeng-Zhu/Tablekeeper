# Stage 1 API

Build and start from this directory:

```sh
docker build -t tablekeeper-stage1 .
docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage1
```

The service uses an in-memory SQLite database and starts empty. Populate it using
`POST /_test/reset`. No runtime outbound connections are needed. PORT defaults to
8080. One Gunicorn worker and its shared transaction lock are required; do not
increase the worker count. Up to 50 request threads share that worker.

Export/import is a logical, versioned JSON snapshot, independent of the source
process. Exports contain password hashes and session lookup digests and must be
handled privately. State does not survive process restart.

Implementation and verification are in progress; this checkpoint is not accepted.
