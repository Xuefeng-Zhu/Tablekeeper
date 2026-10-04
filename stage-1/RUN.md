# Tablekeeper Stage 1

Build and run from this directory:

```sh
docker build -t tablekeeper-stage-1 .
docker run --rm --cpus=2 --memory=2g -e PORT=8080 -p 8080:8080 tablekeeper-stage-1
```

The service listens on 0.0.0.0; PORT defaults to 8080. GET /health returns JSON when ready. It starts with empty state; POST /_test/reset supplies restaurants and accounts. State is ephemeral and all dependencies are packaged in the single image. The service makes no outbound requests. Test endpoints remain unauthenticated and enabled as required. Exported state contains password hashes and bearer credentials; keep exports private.

For local owner checks use Node 24, `npm ci`, `npm run build`, then `npm test`. Tests launch independent HTTP processes on ports 18241 and 18242. The image pins Node 24.14.1 Debian bookworm slim by manifest digest; Temporal 0.5.1 and all build dependencies are locked.

One process publishes synchronous copy-on-write transactions, including original idempotency receipts. Password hashing is asynchronous outside transactions; login rechecks credentials before token publication. An import fully replaces state and invalid imports preserve the destination. No disk or signing secret is needed to transfer exports.

Timezone rules use the image's ICU data. Slots use local wall-clock grid points, choose the first fold occurrence, skip gaps and measure duration on the absolute timeline. If an opening/closing boundary itself lies in a gap, its boundary advances to the first existing local minute on that day; the original grid remains unchanged.

Owner tests are supporting checks, not independent acceptance. Consult delivery evidence for untested behaviors and the exact candidate's official harness result.
