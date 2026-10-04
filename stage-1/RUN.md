# Stage 1 API

From this directory, build and start without manual initialization:

```sh
docker build -t tablekeeper-stage1 .
docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage1
```

The server binds 0.0.0.0 and PORT defaults to 8080. `GET /health` returns ready;
the initial database is empty. Populate it with the specified `POST /_test/reset`
fixture. Dependencies and timezone data are included in the image. No outbound
network access, external service, volume or compose setup is needed at runtime.
State is in-memory SQLite and is intentionally lost on process restart. Export and
import transfer it without depending on the source container. See STATE.md.

The image enforces one Gunicorn worker and 50 request threads. Do not increase the
worker count: the connection and transaction lock belong to this one process.

Own HTTP resource and independent-process portability check (requires Docker and
Python 3 on the host; Python host dependencies are standard library only):

```sh
python3 tests/container_checks.py tablekeeper-stage1
```

This starts two sequential containers with 2 CPUs, 2 GiB and network disabled,
uses real HTTP on container loopback, measures 50-request create/login batches,
and destroys the source before destination import. It removes its own containers.
No private exports or tokens are written to disk. Printed metrics are observations
for that run, not a guarantee for arbitrary fixture size or hardware.

Own focused in-process tests:

```sh
python3 -m venv /tmp/tablekeeper-tests
/tmp/tablekeeper-tests/bin/pip install -r requirements.txt
PYTHONPATH=. /tmp/tablekeeper-tests/bin/python -m unittest discover -s tests -v
```

The official isolated harness is separate from these own checks. Independent QA
and release review are required before stage acceptance; green shipped checks
alone do not establish acceptance. Execution evidence is retained at repository
root under `.evidence/`, outside the image.
