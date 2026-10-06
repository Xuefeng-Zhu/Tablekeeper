# Tablekeeper Stage 2 demo

Deployment snapshot of only `stage-2/` from local factory candidate
`b1f7ef64db7f0dce0b3f3a913a3af280c96d70b9`.
Original application modules are unchanged. The HTML adds a visible demo/reset notice. The Docker startup command
uses `deploy.py`, a separate demo adapter that seeds two fictional restaurants
with singleton and declared two-table combinations. All `/_test/*` HTTP routes,
including percent-encoded spellings, return 404. No private exports, test fixtures,
room records, credentials, or factory evidence are included.

Use this public service for synthetic demo information only. Create a demo
account through Signup; no account or password is preseeded. Pick a future date
for booking so cancellation remains outside the two-hour cutoff. Names marked
“(demo)” identify the fictional venues.

## Run

```sh
PORT=8080 python3 deploy.py
# or
docker build -t tablekeeper-stage2-demo .
docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage2-demo
```

The service binds `0.0.0.0:$PORT`; `/health` reports readiness. Render configuration
uses a free Oregon web service, the root Dockerfile and disabled automatic deploys.
It needs no API keys, external database, persistent disk, or paid storage. Run one
instance with one service process. All accounts, tokens, and reservations exist
only in memory: restart, redeploy, or free-service suspension clears them and
restores the sample restaurants. This is not persistent reservation storage.

This deployment adapter is separate from the immutable factory candidate.
Supplied isolated checks passed for that Stage 2 candidate; deployment-specific
and live-host verification remain separate. Independent final factory Stage 2
acceptance was pending when this snapshot was prepared.

Render fields follow https://render.com/docs/blueprint-spec.
