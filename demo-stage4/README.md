# Tablekeeper Stage 4 demo deployment package

Application candidate: `ac1eb4945a2adaeaaf8a62d0817bdbc6a021e566`.
All accepted Stage 4 Python modules are copied byte-for-byte from the canonical
repository. Deployment changes are limited to a separate `deploy.py` wrapper,
its Docker startup command, the visible ephemeral-demo notice, and deployment
configuration/documentation. The wrapper seeds the same fictional Cedar & Coast
and Olive House venues. It seeds no users, passwords, sessions, reservations,
series or managers. Signup creates ordinary diner accounts; no role is granted.

The existing browser interface covers search, signup/login, booking and private
lookup. Stage 4 series adoption/amendment and other advanced contracts are API
features; this package adds no advanced UI. Manager-only policy/replanning calls
retain their existing authorization and are unavailable without a manager.

All `/_test` routes and descendants return 404 for every handled method, including
nested percent-encoded spellings. `GET /deployment` returns `stage: 4`, the exact
application candidate, and `deploymentCommit` from `RENDER_GIT_COMMIT` only (null
when that Render-provided public commit variable is absent). No private export,
credentials, factory room evidence, or real user information belongs in this
package. Use fictional information in the public demo.

## Run this standalone package

```sh
PORT=8080 python3 deploy.py
docker build -t tablekeeper-stage4-demo .
docker run --rm --cpus=2 --memory=2g -e PORT=8080 -p 127.0.0.1:8080:8080 tablekeeper-stage4-demo
```

One process binds `0.0.0.0:$PORT`; `/health` reports readiness. State is in memory:
restart, redeploy or suspension clears accounts, tokens, bookings and series,
then restores only the fictional venues. No external database or keys are needed.

## Existing Render service

Update the existing free Oregon Docker service **tablekeeper-stage2**
(`srv-db28udrtqb8s73chaqmg`), retaining
https://tablekeeper-stage2.onrender.com and `/health`.
The approved source is https://github.com/Xuefeng-Zhu/Tablekeeper, branch `main`.
Root directory remains unset; Dockerfile is `./demo-stage4/Dockerfile` and Docker
context is `./demo-stage4`. Automatic deploys remain off; deployment is manual.
The supplied `render.yaml` documents these settings. This package's preparation
and local smoke checks do not create a service, push Git, or deploy to Render.

Independent factory QA/Reviewer acceptance and official supplied checks apply to
the original application candidate. Local deployment smoke and later live Render
verification are separate evidence layers; preparation does not prove live hosting.
