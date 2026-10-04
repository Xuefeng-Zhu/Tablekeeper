# Product brief

- Author handle: @frankzhu94/factory-pm
- UTC timestamp: 2026-10-04T06:41:03.821825+00:00
- Work item: TK-PM-BOOT
- Dispatch: Frank Zhu addressed all-stage packet, configuration 1434400dfbbda37a4b8ea21b29f4db785b898b1d3055845e0d6abd9ec21fb691; receipt room event b2d60256-5c4e-4069-b63c-d16686c34c29
- Artifact: /Users/frank/mygit/Tablekeeper/result-run-3/planning/product-brief.md
- Full commit: initial checkpoint to be identified in room handoff

## User outcomes
Diners browse restaurant availability before authentication, reserve suitable seating, retain reliable confirmation references, and manage bookings without double bookings or lost-request duplicates. Managers publish policies and repair seating without changing accepted customer terms. Recurring diners retain stable agreements and histories through upgrades.

## Required scope and acceptance
Stage 1 delivers the complete reservation HTTP API, authentication, DST handling, atomic amendments and moves, idempotency and portable export/import. Stage 2 independently extends the accepted stage-1 service with approved table pairs and a polished responsive booking, authentication and lookup experience, including stale search suppression and uncertain response recovery. Stage 3 extends the accepted stage-2 service with policies, accepted terms, revision/history semantics and recurring adoption. Stage 4 extends the accepted stage-3 service with deterministic seating plans and atomic recurring amendments.
Every stage is a complete self-contained container with Dockerfile and RUN.md; listen on 0.0.0.0, PORT default 8080, no runtime outbound dependencies, 2 vCPU/2 GiB, healthy within 60 seconds, 50 concurrent requests, 5-second request deadline and 10 seconds for test controls. All source-section requirements are preserved verbatim in requirements-coverage.csv and inherited forward. Acceptance requires independent exact-commit review and official isolated harness evidence; developer tests alone never suffice.

## Optional proposals and exclusions
The proposed TypeScript/React/Vite/Node/SQLite stack remains a proposal pending Architect decision. Optional dashboards, decorative illustrations and integrations have lower priority than required behavior and Stage 2 polish. No external runtime authentication/database/CDN/AI API. No publication, purchases, repository visibility changes or fabricated room exports. Final README.md and FACTORY.md are human-authored after the autonomous run; room.json must be the actual full room download. Missing final metadata is explicitly pending, never a passing packaging check.

## Priorities, dependencies and risks
One writer only. Architecture precedes implementation. Designer interaction specifications precede UI implementation. QA independently authors behavioral tests; reviewer gates a fixed clean candidate. Stage N+1 cannot start until Stage N acceptance; never copy a later solution backward. Preserve every failed execution in unique .evidence directories. Three repairs maximum per item; change diagnosis after repeated identical failure. No human steering during execution. Ack deadline 120 seconds with at most two delivery retries; per turn 600 seconds subject to factory_turn_budget, stage 14400 seconds, overall 70834 seconds, 300 turns per seat, 100000000 token ceiling, subscription only, no API billing or paid provisioning. Existing supervisor cumulative consumption controls remain authoritative; measured monetary usage unavailable.

## Decisions and requirement evidence references
Freeze READY_TO_LAUNCH matches dispatch configuration. Initial repository has no commits or remote refs after successful fetch; create attributable bootstrap. All seven identities are present; reciprocal connected acknowledgments remain to be observed as each seat receives material work. Use only /Users/frank/mygit/Tablekeeper/result-run-3; .evidence for execution artifacts; temporary clean review clones outside stage folders. Do not run operator probes or alter runs records.

Pinned challenge commit: 803560d2a678ace1414465c098eb0ab5380ffade
- Stage 1: `9460189eac83802ce158f16ee90989af728a489b32a6147e2dc8e320f383055f`
- Stage 2: `b1aa1b4affad456ff26f208a378eb2f6884153fc6ef667ab37b888fcda96c5dc`
- Stage 3: `36b7ff5294b3992a6fa2894e9b4f2350072912fe10ed212c1f9a8828be1f9abe`
- Stage 4: `08d02b5a432d41d71f989ccb30585b3cad0977c9c1dd11145c97fb7ddd4946c4`
