# Run the public Stage 4 demo

From this package directory:

```sh
docker build -t tablekeeper-stage4-demo .
docker run --rm --cpus=2 --memory=2g -e PORT=8080 -p 127.0.0.1:8080:8080 tablekeeper-stage4-demo
```

For the canonical repository build context use:

```sh
docker build -f demo-stage4/Dockerfile -t tablekeeper-stage4-demo demo-stage4
```

The deployment entrypoint is `deploy.py`; it binds `0.0.0.0:$PORT` and seeds only
two fictional restaurants. `/health` is readiness; `/deployment` is public build
provenance. Accounts, sessions, bookings and series are ephemeral and reset on
restart. There is no preseeded account or manager, and no authorization change.
Private test reset/export/import routes are disabled, including nested encodings.

Existing Render service: `tablekeeper-stage2`, free Oregon Docker,
https://tablekeeper-stage2.onrender.com. Source is canonical
https://github.com/Xuefeng-Zhu/Tablekeeper `main`; rootDir unset, Dockerfile
`./demo-stage4/Dockerfile`, context `./demo-stage4`, automatic deployments off.
Use the existing service; do not create a second service from this blueprint.
