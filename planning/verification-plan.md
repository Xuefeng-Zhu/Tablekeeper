# Independent verification plan — QA-BOUNDARIES

Planning owner: @frankzhu94/factory-qa. Starting revision: 758093050e2484a6435395409f501994b9e215b8. Source revision: 803560d2a678ace1414465c098eb0ab5380ffade. No product implementation was inspected or changed. All product classes below are NOT_TESTED. This document schedules tests; it is not stage acceptance.

The existing CSV retains every source byte, section ordering and all four original SHA-256 values. Its boundary_cases field contains the structured class matrix below. Each class states valid controls, violating/adjacent cases, independent expected outcomes and state effects. Later stages rerun inherited classes against that exact candidate, then add their new classes.

## Execution order and evidence

At each earliest runnable checkpoint, pin full revision and confirm clean independent review checkout; acquire the permitted single-writer/read lease. Use synthetic fixture passwords and private credential artifacts. Run HTTP comparisons before browser and isolated official gate. Capture UTC start/end, command, exit, candidate, responsible handle, room event, sanitized request/assertion output, environment and PASS/FAIL/NOT_TESTED per class in a new .evidence directory. Stop new work at finish_work_by_utc; preserve failed attempts. A green official suite never closes missing independent classes.

S1 priority: Q06/Q08/Q10/Q12 (idempotency, occupancy, DST, rollback), then Q03/Q04/Q09/Q11, then remaining runtime/auth/read classes. S2 priority: Q14/Q15/Q17/Q18, then actual Q16 upgrade and rendered Q13. S3 priority: Q22/Q23/Q24/Q25 followed Q19/Q20/Q21 and Q26 actual upgrades. S4 priority: Q28/Q29/Q32 then Q27/Q30/Q31/Q33. Dependencies require accepted earlier stage service for actual upgrades; test with two separate processes and unchanged exports, not a compatibility stub.

## Input-class and requirement-to-oracle matrix

### Q01 — S1-01, S1-03, S1-04, S1-05, S1-06, S1-07

First checkpoint: S1 container first runnable. Status: NOT_TESTED.

Inputs and controls: Default PORT 8080 and explicit nondefault port; fresh unseeded boot; 50 simultaneous requests; runtime network unavailable.

Independent expected result: Observe 0.0.0.0 binding, health 200 {status:ok} <=60s, ordinary responses <=5s/reset <=10s; Docker RUN.md command actually builds/starts under 2CPU/2GiB. Inspect dependencies and private image runtime, never equate host success to isolation.

State and side effects: No unexpected 5xx; separately record boot time, response latency, host vs isolated layer. No product runtime tests exist yet.

### Q02 — S1-08, S1-10, S1-11

First checkpoint: S1 reset/auth runnable. Status: NOT_TESTED.

Inputs and controls: Fixture A then B then B; empty users/restaurants/reservations; opaque 64-character IDs; seeded confirmed past booking; restaurant absent weekday.

Independent expected result: After 204 only B visible, seeded password login works immediately; past creation permitted; closed day slots []; seed occupancy matches half-open oracle. ID 65 is invalid range under shared format rule when endpoint accepts it; do not invent invalid-reset semantics beyond specified rules.

State and side effects: A credentials/tokens/receipts disappear; second B reset restores same fixture without accumulation.

### Q03 — S1-09, S1-12

First checkpoint: S1 HTTP parser runnable. Status: NOT_TESTED.

Inputs and controls: Unparseable JSON; nonobject JSON; body string vs integer vs boolean vs null; unknown body/query field; response offsets/charset; ID length 64/65; key lengths 0,1,255,256.

Independent expected result: Wrong JSON type ->400 malformed_request except explicit endpoint overrides. Correct type invalid format/range ->422 validation_failed. Empty/missing required key ->400 missing_idempotency_key, 256 ->422; unknown fields ignored. Object parsing + authentication precede idempotency; otherwise unspecified cross-error precedence is not invented.

State and side effects: Rejected writes leave records, occupancy and successful receipts unchanged; unknown fields still participate in parsed-body equality for replay.

### Q04 — S1-12, S1-18, S1-19

First checkpoint: S1 availability/create runnable. Status: NOT_TESTED.

Inputs and controls: party_size 0,-1,1,capacity,capacity+1,1.5,true,"4"; query 4,04,4.0,+4,1e9; date leap-valid 2028-02-29 vs 2026-02-29; local timestamp bare vs offset/Z/seconds/whitespace vs wrong JSON type.

Independent expected result: party_size strings/booleans/nonintegers ->422 validation_failed (override 400); query decimal digits only (04 allowed), other representations ->422. starts_at_local malformed string ->422, nonstring ->400 except endpoint-specific override; impossible date ->422. capacity+1 ->party_exceeds_capacity.

State and side effects: Each rejection has required error body and no state/receipt claim. Include missing required query/body fields ->422.

### Q05 — S1-13

First checkpoint: S1 auth runnable. Status: NOT_TESTED.

Inputs and controls: Password length 7 vs 8; email missing @ vs local@domain; duplicate signup, wrong password/unknown email; absent/malformed/unknown bearer; multiple concurrent login tokens.

Independent expected result: Signup201/login200 and exact user/display identity; invalid signup422, duplicate409 email_taken, wrong login401. Public health/reset/auth/restaurants/detail/availability need no token. Tokens do not expire and old token remains valid after second login. Inspect hashing storage separately with conservative output.

State and side effects: No plaintext password persistence; do not log credential exports. Authentication tests are separate from owner-only history/decision/series exceptions in later stages.

### Q06 — S1-14

First checkpoint: S1 create/moves runnable. Status: NOT_TESTED.

Inputs and controls: Identical parsed object with reordered keys/whitespace; ignored unknown field changes; used key with invalid new party; successful replay after cancel/amend; failed4xx then corrected body same key; same key two users/two paths; 50 identical concurrent submissions.

Independent expected result: Same JSON value ->one201 and remaining200 with deep-equal original receipt; different parsed value on same user/method/path ->409 idempotency_key_reuse before field/current-resource validation, including after reset fixture? Reset clears receipt, so then first use. Different path or user independent; failed4xx key reusable.

State and side effects: Exactly one operation/reference/history change; replay no state changes; immutable original receipt survives cancellation and import. Different-key competing occupancy separately tested.

### Q07 — S1-15, S1-16, S1-17, S1-18

First checkpoint: S1 public browsing runnable. Status: NOT_TESTED.

Inputs and controls: Known vs unknown restaurant; capacities2,4 in fixture order; opening18:00 closing23:00 grid30 duration90; empty capacity result vs closed weekday. Additional opening-anchored control: opens18:10 closes20:10 grid30 duration60; last valid19:10 vs adjacent19:40.

Independent expected result: Eight local slots [18:00,18:30,19:00,19:30,20:00,20:30,21:00,21:30], count=8; 21:30 ends exactly23:00 and is included; 22:00 excluded (end23:30). Additional non-midnight opening control: [18:10,18:40,19:10], count=3; 19:10 ends exactly20:10 and is included, 19:40 excluded (end20:40). Derive independently with m=opens+k*grid and m+duration<=closes. Capacity exactly party allowed; IDs in fixture order; slot with no eligible tables retained, closed day slots[]; unknown detail404.

State and side effects: Public reads unchanged state; compare fixture detail exactly, including original configuration after later policies.

### Q08 — S1-02, S1-18, S1-19, S1-23

First checkpoint: S1 occupancy runnable. Status: NOT_TESTED.

Inputs and controls: Existing19:00..20:30 booking; candidate18:00..19:30 overlap,20:00..21:30 overlap,20:30..22:00 adjacency; off-grid18:15; final legal21:30 vs22:00; unknown/foreign restaurant table.

Independent expected result: Independent overlap formula a.start<b.end AND b.start<a.end. Boundary equality is nonoverlap; grid anchored to opens, not midnight. 409 table_unavailable; 422 not_on_slot_grid; 422 outside_opening_hours; foreign/unknown table404. For overlaps of grid/opening/capacity errors not ordered by spec, avoid asserting invented priority.

State and side effects: Failed PATCH preserves old occupancy and reference/reservation_id; success atomically releases old and reserves new. Race 50 distinct-key creates on one interval yields exactly one201 and49 conflicts; no5xx.

### Q09 — S1-20, S1-21, S1-22, S1-23

First checkpoint: S1 lookup/cancel runnable. Status: NOT_TESTED.

Inputs and controls: Empty and mixed confirmed/cancelled own list; descending distinct starts; own vs other/unknown ref; cancel twice; PATCH subset/noop/cancelled; cutoff before/equal/after threshold against current start.

Independent expected result: Owner list descending by instant; other/unknown lookup404. Cancel frees immediately; repeat200 current state even after cutoff. Let threshold=start-cutoff; controlled now<threshold succeeds, now==threshold or later409 cutoff_passed. Do not assert unspecified tie order. PATCH cancelled409 reservation_cancelled.

State and side effects: Identity/reference stable, cancelled occupancy removed; failed amendments no partial field changes. Equality cutoff needs authorized deterministic clock or measured bracket, never wall-clock flakiness counted PASS.

### Q10 — S1-24, S1-18, S1-19

First checkpoint: S1 timezone runnable. Status: NOT_TESTED.

Inputs and controls: Berlin spring2026-03-29 02:00/02:30 skipped vs01:30/03:00 valid; fall2026-10-25 02:30 repeated; NewYork spring2026-03-08 02:30 skipped and fall2026-11-01 01:30 repeated; duration90. Unique-endpoint availability controls: Berlin2026-03-29 opens01:00 closes03:30 and NY2026-11-01 opens00:30 closes02:30, grid30 duration90.

Independent expected result: Hand-computed UTC: Berlin fall02:30+02=00:30Z, end02:00Z=03:00+01; NY fall01:30-04=05:30Z, end07:00Z=02:00-05. Berlin spring01:30+01=00:30Z, end02:00Z=04:00+02. Gap never listed and booking422 invalid_local_time; repeated local label appears once, first occurrence only. For unique valid opening/closing endpoints, local opening-grid candidate starts are resolved first, skipped starts omitted, duration added in UTC, absolute end<=closing instant. Berlin spring control only[01:00]; NY fold control[00:30,01:00,01:30].

State and side effects: Duration is90 real minutes, offsets IANA. Unique-endpoint controls are required. Closing endpoints themselves inside a skipped/repeated interval lack explicit disambiguation; record convention without mandatory invented error. See planning/oracle-review-guidance.md point3.

### Q11 — S1-25

First checkpoint: S1 export/import runnable. Status: NOT_TESTED.

Inputs and controls: Export A then mutate source; destinationB; import A twice; wrong track/version/missing state/invalid state; malformed JSON; failed booking key then export.

Independent expected result: Read-only snapshot unchanged by later source writes; unchanged envelope track tablekeeper/version1 imports204 to different process/port. Invalid import422 (malformed JSON400) atomically preserves B. Accounts/hash login/tokens/config/references/status/timestamps/receipts byte-equivalent as JSON values.

State and side effects: Import replaces B credentials and reservations, no duplicate on repeat; completed receipts immutable, failed keys reusable, reset clears imported state. Keep raw credential/token exports private and uncommitted; room gets only assertions.

### Q12 — S1-26

First checkpoint: S1 batch runnable. Status: NOT_TESTED.

Inputs and controls: moves length0,1,8,9; duplicate/nonstring reference; move objects wrong shape; own vs other owner; mixed restaurants; swap A:t1 B:t2; collision with fixed C; unchanged listed booking retains occupancy.

Independent expected result: Invalid shape/duplicates422; inaccessible404; mixed restaurants422. A/B atomic swap succeeds201 in input order despite transient occupied old assignments. Nonoccupancy errors by input order; cutoff precedes other changes for same booking. E.g item0 cutoff+bad capacity and item1 bad grid ->cutoff; swap control editable.

State and side effects: On failure compare all records/occupancy/receipts; key usable after failure. Replay200 original even after changes; noop all values retained; export/import preserves batch receipt. Wrong owner vs shape order beyond specified phases remains explicit ambiguity.

### Q13 — S2-01, S2-03, S2-04

First checkpoint: S2 first UI runnable. Status: NOT_TESTED.

Inputs and controls: Direct /,/signup,/login,/lookup at desktop and375px; signed-out and signed-in across routes; failed/signup/login/logout; keyboard tab focus; empty/loading/refused/uncertain/success.

Independent expected result: HTML routes200; documented testids/visible labels, every signed-in screen display name/current-user and logout; auth-error only actual error; no horizontal page scroll. Designer rendered judgments remain independent, with screenshots and focus evidence.

State and side effects: Local runtime assets load with no outbound network; storage/session can persist upgrade but no reload recovery requirement is invented.

### Q14 — S2-02, S2-05, S2-06

First checkpoint: S2 browser search runnable. Status: NOT_TESTED.

Inputs and controls: Hold searchA response, submitB, completeB thenA; different restaurant/table labels/date/party. Available and unavailable cells, signed-out available click.

Independent expected result: After A arrives grid, labels, selected form and prefill stillB; cell availability exactly API IDs for searched party; unavailable click inert; available signed-out login/auth-error. Closed day no-slots replaces grid.

State and side effects: Record requests and DOM with browser-controlled response latches, not timers; stale response must not restore old state or book stale restaurant/time.

### Q15 — S2-02, S2-06, S2-07, S2-08

First checkpoint: S2 browser booking runnable. Status: NOT_TESTED.

Inputs and controls: Another client claims selected table; drop response before server commit and after actual commit; unchanged retry; success submit again; change party/table field; lookup own/unknown/cancel/refused.

Independent expected result: Conflict409 ->nonempty booking-error, refreshed availability, same form inputs, no new confirmation. Lost response ->nonempty booking-uncertain, no booking-error or new confirmation; retry sends identical key+JSON body, returns original reference and clears uncertainty/error. Change field ->new booking identity.

State and side effects: Verify server state separately via owner list; one booking despite lost committed response. Confirmation-reference text exact; details labels/time, form remains; cancelled lookup cancel button absent. Do not manufacture success from browser cache.

### Q16 — S2-09, S1-25

First checkpoint: S2 upgrade checkpoint after acceptedS1 export. Status: NOT_TESTED.

Inputs and controls: Serve the actual delivered stage2 browser assets through a test routing layer against the real accepted stage1 API; use stage1-compatible table_id single-table body, sign in and lose committed response, exportS1/importS2 between requests, switch API routing toS2; retain actual browser form/token/body/key/reference.

Independent expected result: No reload or signin needed; same browser token authenticated, retained ref lookup works, unchanged pending form retries same key/body and server returns original receipt. Stage1 is API-only: do not require an S1 UI or substitute a synthetic form/compatibility stub. Real stage2 client must send the compatible table_id body while routed toS1. See planning/oracle-review-guidance.md point4.

State and side effects: Assert imported state independently; actual two-service upgrade evidence needed, mocked export/import is NOT_TESTED integration.

### Q17 — S2-10, S2-11, S2-12, S2-13, S2-14

First checkpoint: S2 pair API runnable. Status: NOT_TESTED.

Inputs and controls: Tables t1cap2,t2cap4,t3cap4; declared [t2,t1],[t2,t3]; party6; reversed input[t1,t2]; duplicate IDs; empty table_ids; wrong array/member JSON types; unknown/foreign member;3 IDs; undeclared[t1,t3]; both table_id/table_ids; one-member table_ids.

Independent expected result: Pair summed capacity6 allows party6; party7 exceeds; singles first fixture order then declared pairs order, pair member order canonical[t2,t1]. available_table_ids singles only. Both fields/duplicate/empty422 validation_failed; wrong field type follows400 malformed_request unless stated override; unknown/foreign member404; undeclared/>2 ->422 combination_not_allowed. t1+t3 not implied transitively. Responses pair omit table_id, singleton include both.

State and side effects: Any member overlap blocks whole option; cancelled seeded booking does not occupy; cancellation frees every member; reversed equivalent pair semantics differ from parsed-body replay equality (reversed body under used key is409).

### Q18 — S2-15, S2-16, S1-26

First checkpoint: S2 pair UI/concurrency runnable. Status: NOT_TESTED.

Inputs and controls: Pair select summary/confirmation/lookup; pair lost/conflict flows; concurrent t1 singleton vs[t1,t2] pair and concurrent pair/single amendments; batch swap pair members.

Independent expected result: Every selected table label visible; testid slot-t2+t1-HH:MM uses declaration order. Pair loss/retry same rulesQ15. Observed outcomes admit a serial order; at most one overlapping assignment per member.

State and side effects: Use barrier synchronized clients and reads; no partially changed pair visible. Linearizability examples do not prove all possible schedules; mark remaining invariant class coverage.

### Q19 — S3-01, S3-02, S3-04

First checkpoint: S3 availability explain runnable. Status: NOT_TESTED.

Inputs and controls: capacity/no_overlap truth table TT,TF,FT,FF; party equal capacity; no explain vs true vs false/1/empty; closed/all-unavailable day.

Independent expected result: Each table once fixture order; rules capacity then no_overlap both present, conjunction equals available and IDs; no explanation when absent (other inherited available_options remain); invalid explain422. Selected policy_version accurate.

State and side effects: Construct four tables with independent capacities/seed occupancy; confirm reports both false for FF, not short-circuit omissions. Reads never change history/counters.

### Q20 — S3-03, S3-07

First checkpoint: S3 history runnable. Status: NOT_TESTED.

Inputs and controls: Creation then actual changes then noop then cancel twice; same-second writes; single->pair, reversed pair->same pair, pair->single; replay; owner/no token/other user.

Independent expected result: Seq contiguous1..n oldest/at order; create three fields fromnull; changed only changed fields in defined order; pair transitions table_ids complete canonical lists; cancel emptychanges terminal. Noop/replay noentry; owner-only404 even no token.

State and side effects: Retain old entries terms/revisions immutable; response replay remains original; compare history lengths and state before/after rejected/noop requests.

### Q21 — S3-05

First checkpoint: S3 policy publish runnable. Status: NOT_TESTED.

Inputs and controls: manager/nonmanager/unknown restaurant/no token; policy version1 then2 with backdated/out-of-order dates/ties; valid min/max grid1/1440,duration1/1440,cutoff0/10080,capacity1/100; adjacent0/1441/-1/10081/101; boolean ints; missing/extra table capacities; duplicate weekday; missing field.

Independent expected result: Invalid policy422 for endpoint override even type violations; no version allocated on failure/replay. manager201; nonmanager403; no token401; unknown404. Public policies publication order excludes0; original restaurant detail stays fixture.

State and side effects: Publication immutable, no existing booking/history/end changes. Two successful new publications increment restaurant revision twice; failed/replayed none. Permission+field overlaps not universally ordered by spec.

### Q22 — S3-05

First checkpoint: S3 policy selection runnable. Status: NOT_TESTED.

Inputs and controls: Publish v1 effective10-10 duration120/cutoff60, v2 effective10-01 duration60/cutoff30, v3 effective10-10 duration30/cutoff0; booking dates09-30/10-01/10-09/10-10/10-11.

Independent expected result: Expected selected versions0,2,2,3,3 (date wins before version; version breaks date tie). Capacity and combination sum from selected snapshot. Entire terms exclude effective_from, include capacities/all weekdays, revision1.

State and side effects: A preexisting policy0 booking retains90min end and120 cutoff after publication. Real party-only amendment adopts newly selected policy and changes duration even same start; first old cutoff must allow edit.

### Q23 — S3-05, S3-03

First checkpoint: S3 revisions runnable. Status: NOT_TESTED.

Inputs and controls: Positive expected_revision matches/differs;0/-1/1.5/true/string invalid; stale plus expired cutoff/invalid newfields; real change vs unknown-field-only/noop; cancelled no-op; concurrent real amendments same revision.

Independent expected result: Invalid revision422; valid mismatch409 stale_revision before cutoff/validation. Noop requires confirmed editable booking and keeps terms/end/revision/history; real change old cutoff then ALL resulting fields validated newpolicy, increments once. At most one real change from same expectedrevision.

State and side effects: Failure no counters/history/terms changes; cancel revision+1 once; original receipt keeps original revision/terms. GET decision/history owner-only404 including unauthenticated and cancelled booking still accessible to owner.

### Q24 — S3-06

First checkpoint: S3 series adoption runnable. Status: NOT_TESTED.

Inputs and controls: count1,2,12,13 and bool; interval0,1,4,5; foreign/cancelled/already-adopted anchor; later occurrence unavailable/bad policy/DST gap; valid fall repeat; anchor importedS1/S2.

Independent expected result: count2..12/interval1..4 ints; first failing generated index gives ordinary code; gap422 invalid_local_time; all-or-none. Original local date+i*7*w handles DST/month/year calendar transition; clocks same, offsets/date policies independent.

State and side effects: Anchor identity/reference/revision/terms/history/timestamps/original receipt unchanged; no partial series/generated reservations/histories/counters/receipt on failure; success restaurantrevision+1 for entire adoption; refs distinct and indices stable.

### Q25 — S3-06, S3-08

First checkpoint: S3 series mutation/batch runnable. Status: NOT_TESTED.

Inputs and controls: Individual realPATCH/noop/fail/cancel/repeatcancel anchor/sibling; batch changes two members same series plus other series; batch overlap failure; omitted expectedrevision vs stale; replay adoption after changes.

Independent expected result: RealPATCH permanently exceptiontrue and seriesrev+1; noops/fails none. Cancel seriesrev+1 without marking exception; anchor cancel siblings unchanged. Batch each changed reservationrev+1/history and exceptiontrue, each affected seriesrev+1 once, restaurantrev+1 once total.

State and side effects: Failed batch/replay no revisions/history/flags; noop retains accepted terms; original series replay receipt immutable while GET returns current. Series GET unauthorized/no token404.

### Q26 — S3-06, S1-25, S2-09

First checkpoint: S3 upgrade runnable. Status: NOT_TESTED.

Inputs and controls: ActualS1 andS2 exports intoS3, imported anchor adoption; signed-in browser and lost booking retry; pair retry fromS2.

Independent expected result: All old sessions/refs/receipts valid and adoption succeeds; old receipts retain original shape/values while new current response includes required new fields. Exact compatibility rules for upgrading seeded historical revisions need architecture decision before oracle execution.

State and side effects: No retroactive new identity/reference/timestamps; preserving oldreceipt JSON does not imply omit newly required fields from current reservation response.

### Q27 — S4-01, S4-02

First checkpoint: S4 preview runnable. Status: NOT_TESTED.

Inputs and controls: Closure explicitoffset vs nooffset, from==to/from>to; unknown table; 6tables/4pairs/6considered vs7/5/7; booking ending exactlyfrom/starting exactlyto; confirmed vs cancelled; closed-other restaurant.

Independent expected result: Half-open intersection formulaQ08 determines considered set; all considered included reference order, others fixed. Invalid422, unknown404. At limits support; above limits MAY planning_limit (not must). Preview201 stores plan only; infeasible409 no_feasible_plan.

State and side effects: No closure/occupancy/history/reservation/restaurantrevision changes on preview/failure. Accepted per-booking capacity, duration/times retained; operator repairs permitted despite diner cutoff.

### Q28 — S4-02

First checkpoint: S4 exhaustive optimization runnable. Status: NOT_TESTED.

Inputs and controls: Controls for objective1 vs unusedseats, objective2 vs rank, objective3 ties; differing accepted capacities; pair options; fixed conflicts and prior closures; propose closed unused table with zero considered. Reproduce the each-booking accepted-capacity fixture and adjacent-vs-overlapping fixed-tail controls in planning/oracle-review-guidance.md point8.

Independent expected result: Independent Cartesian enumeration of options per considered booking, filter interval/member/fixed/prior/proposed closure conflicts, score lexicographic(changed_count,total_slack,rankvector sortedrefs). Up to10options^6 bounded officiallimit. Compare complete optimum/assignments not production solver. Empty set unique score(0,0,[]) plan allowed. Point8 expected scores: fixedtD starts20:30 -> four feasible, optimum(1,0,[1,4]); fixedtD starts20:00 -> one feasible, optimum(2,2,[2,1]).

State and side effects: Preserve every considered ref/owner/party/start/end/terms. Re-run oracle after only external fixture change; do not import production normalization or solver.

### Q29 — S4-02

First checkpoint: S4 apply runnable. Status: NOT_TESTED.

Inputs and controls: Apply validplan; otherrestaurant plan; same key replay after laterwrites; different key alreadyapplied; intervene real booking/change/cancel/policy/adoption/batch; noop/replay/preview intervenes; otherrestaurant closure; concurrent applies.

Independent expected result: Unknown/foreign plan404. Successful receipt replay resolves first:200 original even after later real writes. Applied plan with a new key returns409 plan_already_applied before staleness; unapplied plan with intervening own restaurantrevision returns409 stale_plan. Apply->newkey is already_applied; preview->realwrite->apply is stale; successful-key replay after realwrite is original200. Noop/replay/preview do not stale; otherrestaurant changes do not stale. This specific precedence prevents plan application itself from making the already_applied requirement unreachable. See planning/oracle-review-guidance.md point9.

State and side effects: Apply atomic closure+allassignments; moved reservationrev+1 with reassigned table_ids/history plan_id, unchanged none; restaurantrev+1 total; no partial reads. Failure no state changes. API reads/UI update current assignments; stale form conflict409 as authoritative.

### Q30 — S4-02, S3-02, S2-13

First checkpoint: S4 closures runnable. Status: NOT_TESTED.

Inputs and controls: Create/amend single/pair exactly adjacent to closure vs overlapping; capacity fails simultaneously; explain available slot closure false no_overlap; plan application acceptedterms oldpolicy.

Independent expected result: [from,to) adjacency free, member overlap409 table_unavailable; available_options excludes every option containing closed table during intersection. explain no_overlapfalse independentcapacity; preserved acceptedterms mean operator never adopts newestpolicy.

State and side effects: Closure durable through export/import; old booking receipt still original. Ref/start/end/party/owner never changed/cancelled; unchanged booking no history.

### Q31 — S4-03

First checkpoint: S4 series amend validation runnable. Status: NOT_TESTED.

Inputs and controls: expectedrevision1 vs0/bool/mismatch; from_index0,count-1,-1,count; local_time00:00/23:59 vs24:00/9:00/seconds/offset; own/foreign/unknown/no token; stale plus cutoff and occupancy.

Independent expected result: All invalidinput422 endpointoverride; mismatch409 stale_revision before occurrence cutoff/bookingvalidation; no token401, nonowner404. Cutoff/nonoccupancy first eligible index; only then occupancy conflicts; independent status oracle from requirement.

State and side effects: Rejected amendment histories/receipts/reservation/series/restaurantrevisions unchanged; failed key reusable; competing real amendments same expectedrevision at mostone succeeds.

### Q32 — S4-03, S3-06

First checkpoint: S4 series amend semantics runnable. Status: NOT_TESTED.

Inputs and controls: from_index interior; cancelled and exception exclusions; individually moved-date exception; seatingrepair movedtable preserved eligible; identicalclock/allnoop/emptyeligibles; realchanges encounter later gap/policyinvalid/closure; replayafterchanges.

Independent expected result: Eligible i>=from_index excluding cancelled/exception; newclock on original scheduled localdate, retain currenttables and parties. Each changed occurrence oldcutoff then datepolicy; noops terms unchanged. Failure atomic; no exceptions marked by collectiveamend.

State and side effects: Success changed reservationrev/history+1 each, series+1 and restaurant+1 once if anythingchanged; allnoop/empty201 countersunchanged. Unchanged excluded histories/terms unaffected. Replayed200 original currentseries receipt at original success, not latestGET.

### Q33 — S4-03, S4-02, S1-25

First checkpoint: S4 upgrade and repair series runnable. Status: NOT_TESTED.

Inputs and controls: ActualS1/S2/S3 exports intoS4; importedseries with individually moved/exception,cancelled and operator-reassigned occurrences; applyplan affects2members same series; subsequent amendment.

Independent expected result: Prior receipts/history/tokens/refs valid; repair preserves exception flags/originaldates/terms and affectedseriesrevision+1 once per application. Lateramend uses originaldates/currentselection and respects exclusions; ordinary browserlookup reflects newtables.

State and side effects: Run each sourceversion independently, replacement destination must be clean; mocked migration or empty series cannot close upgrade classes.

## High-risk relationship controls

R1 Occupancy/atomicity: Q08/Q12/Q18/Q30 use independent interval intersection and table-set intersection. Valid adjacency/swap controls; violation overlapping pair member or failed second move. Compare full owner records and availability before/after, not just response status. A few synchronized examples are observed sequence coverage; universal concurrent schedules remain unproved.

R2 Receipt/state separation: Q06/Q11/Q15/Q16/Q25/Q29/Q33 compare captured original JSON receipt to replay after cancel, publication, series amendment and import. Valid same-body/control uses reordered JSON keys; violating body adds ignored unknown field and must conflict. Compare references and all counters/history; request equality is parsed JSON, whereas pair-order semantic equality is a separate rule.

R3 Policy/date versus immutability: Q22/Q23/Q24 test non-monotone publication dates with explicit expected versions [0,2,2,3,3]. Valid no-op keeps old terms; real party-only amendment must revalidate all fields/adopt new terms after old cutoff, and invalid resulting capacity must leave old snapshot intact.

R4 Series/repair separation: Q25/Q32/Q33 distinguish real individual edits (permanent exceptions), cancellation (no new exception), collective amendments (no exception) and operator repair (preserves flags/dates/terms). Valid untouched eligible occurrence changes on original date; violating attempt includes cancelled/exception index and must leave it unchanged. One series bump for multiple members per operation.

R5 Plan minimization: enumerate independently all allowed assignments; capacity comes from each accepted snapshot. Hand controls: keep unaffected t3(cap6) for party2 even when t2(cap2) reduces waste, because zero changes wins; forced move off closedt1 selects t2(cap2) ahead of earlier-ranked t3(cap6), because waste wins before rank. Rank tie: one forced party2 booking on closedt1 with two equally fitting remaining tables picks earlier fixture rank. Multi-booking vector ties use reference sort, never insertion order. Fixed and previously closed options removed first; pair ranks follow declared order. Record feasible count, winning score and full assignments to prove objective ordering.

## Unresolved oracle questions and uncovered classes

Reviewer guidance is preserved in planning/oracle-review-guidance.md. Specific apply precedence is resolved: completed receipt replay, then already-applied under a new key, then stale unapplied plan. Unique valid DST closing endpoints use an absolute end comparison and the Q10 explicit vectors. Remaining unspecified cases are broad unrelated overlapping errors (authentication/resource/shape where not ordered) and closing endpoints themselves within skipped/repeated intervals; record chosen conventions without inventing mandatory precedence or codes. Explicit source precedence remains enforced for idempotency, expected revisions, input/index order and cutoff within a booking.

Cutoff equality requires permitted deterministic clock or an externally recorded bracket; no clock-hook mutation is authorized by this planning item. Runtime/network/resource ceilings need actual permitted isolated Docker execution; hashing storage review needs narrow safe inspection. If either cannot be observed, report NOT_TESTED, never substitute host/unit evidence.

The map covers representative boundaries, not every JSON nesting/type combination, IANA zone/date, pair graph, six-booking optimum, concurrency schedule, timeout location, viewport, font/accessibility metric or upgrade state. Product tests, connected browser smoke, rendered design review, official isolated harness and all four stage gates are NOT_TESTED. Source/digest/coverage validation and seat checkout smoke are the only observed checks in this task. Optional reload/cross-tab/polling behavior is not required. No exhaustive correctness claim.

PM receives this planning candidate and gives Reviewer the complete requirements and exact candidate for oracle challenge. Planning receipt/review is not product acceptance. An independent Reviewer must accept each later exact committed stage candidate.

## Independent review repair provenance

F1 rejected a9b717c69ed8b2361a81f3836a8c73d68c528b54 for the incorrect seven-slot count. Repair cycle1 corrects Q07 to the full eight-slot vector and adds the non-midnight opening/equality control. Prior failing evidence remains under .evidence/reviewer-oracles-20261005T062627Z and .evidence/qa-boundaries-20261005T0609Z. The nine reviewer guidance points are preserved in planning/oracle-review-guidance.md; all product checks remain NOT_TESTED. Only independently rerun planning acceptance by Reviewer can resolve this rejection.
