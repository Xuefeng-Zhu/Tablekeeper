# Tablekeeper product brief

Author: @frankzhu94/factory-pm. Work item PM-BOOT. UTC: 2026-10-05.
Source: Frank Zhu judged all-stage dispatch; pinned challenge 803560d2a678ace1414465c098eb0ab5380ffade. Source hashes and complete normative sections are recorded in requirements-coverage.csv.

Diners need trustworthy restaurant availability, confirmations that survive uncertain connections, private booking management, and recurring agreements. Managers need dated policies and deterministic seating repairs without silently altering accepted terms.

Deliver stages 1–4 in order, each a self-contained service with Dockerfile and RUN.md. Stage 1 establishes API atomicity, authentication, DST, retries and portable state. Stage 2 adds approved pairs and a warm, polished responsive booking experience. Stage 3 adds immutable policy decisions, history, revisions and recurring adoption. Stage 4 adds deterministic closure planning/application and atomic recurring amendments. Every later stage inherits earlier requirements. Preserve accepted earlier stage directories without copying later behavior backward.

Architecture decisions precede backend implementation. QA adds boundary classes and Reviewer challenges independent oracles before first implementation. Designer specifies journeys, visual hierarchy and states before UI work. Backend and Frontend own implementation; QA owns independent behavior tests; Reviewer alone accepts exact clean candidates after isolated harness checks. PM owns coordination and history-preserving integration.

Priority is required behavior, then stage-2 polish. Optional dashboards, integrations and decorative assets are excluded. No hosted runtime services or outbound dependencies. No new spending or human steering. Root README.md, FACTORY.md and actual complete room.json are post-run human assembly; do not fabricate them or claim packaging passes without them.

One writer and one active seat. No worktrees absent provisioned roots. Official checks use pinned interpreter from challenge, isolated mode, writable .evidence outputs; caches and temporary review clones use platform temporary storage. Retain failed attempts. Repairs at most three, with changed diagnosis after repeated identical failures. Each stage requires independent acceptance before next-stage copy.

Known risks: DST absolute durations and cutoff ordering; idempotency JSON equality and original receipts; atomic collective writes; import upgrades; late search and uncertain UI outcomes; per-booking accepted policy versus current availability; deterministic planner ordering. All classes remain NOT_TESTED until observed.

Frozen cumulative deadline: 2026-10-05T02:35:03.700437+00:00 (2026-10-04 19:35:03 America/Los_Angeles). Individual turn 600 seconds with tool-reported handoff reserve; acknowledgment 120 seconds; stage 28800 seconds, cumulative deadline wins. Measured monetary usage UNAVAILABLE; readiness reports 221514710 cumulative tokens and 64496867 remaining at freeze, not current live usage.
