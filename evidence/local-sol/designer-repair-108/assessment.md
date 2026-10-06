# RENDER-S2-REPAIR1 rendered assessment

Finite rendered gate PASS at 108bcd4c48fa4fb4d7dedf286e94c8e4937f7f10. R-F1 is resolved in the independently observed cases below. This is Designer evidence for independent assessment, not overall Stage2 acceptance.

Responsible: @frankzhu94/factory-designer. Exact detached clean clone /private/tmp/tablekeeper-render-s2-repair1-20261006T0637Z. Actual Docker Chromium 153.0.8010.12, internal network, two CPU / 2 GiB service limits; desktop 1280x900 and mobile 375x812. Browser timezone America/Los_Angeles; restaurant Europe/Berlin. No native Mac browser. Packaged nine static files match the clone byte-for-byte. Accepted Stage1 directory is unchanged against b3df39340cee6f738ac79a12dd1347fc38e969e9. Root and clone remain clean at the fixed candidate; scoped containers and network were removed.

## Observed focus and recovery

48 actual keyboard cases PASS: 12 single/pair × desktop/mobile × success201/real409/post-commit dropped-response uncertainty; 30 input, keyboard-moved seating, newer form, newer search and logout controls; six additional first-declared late pair controls across both widths and three outcomes. Pending Enter/Space produced exactly one actual POST. Native submit remains focusable, semantically aria-disabled with aria-busy/status feedback. Current submit retained visible natural focus through pending and completion, without completion-time refocusing. Unchanged uncertain retry preserved body/key/caller and returned the original receipt.

User-moved inputs and newer form/search/logout retained focus and scroll. Both late pair controls stayed connected, naturally focused and visible through same-scope conflict refresh. Mobile409 seating scrollY changed from5398 to4880 through browser anchoring while the focused control viewport top remained183.625 to184.125: preservation of visual position, not an assertion of unchanged absolute scrollY. Stale completion did not steal newer focus or add stale feedback.

Pending dashed styling was readable: measured computed text rgb(40,83,64) on rgb(234,242,232), contrast7.64694:1. Actual current focus matched :focus-visible and a3px outline; pending and outcome screenshots show the outlined control inside the viewport. Natural user-moved seating focus also remained visible. See every case's rectangles, computed styles, post counts and timestamp in the two authoritative focus reports.

## Observed presentation

Eight core journey groups PASS,75 snapshots and22 computed text contrast observations; minimum5.26778:1, no recorded page errors or material defects. Direct routes/auth, loading, closed/full/empty availability, selected single/pair, original confirmation/replay, current confirmed/cancelled/missing lookup, real conflict with retained inputs and scoped refresh, exact uncertain retry, cutoff feedback, native years0001/9999, stale completion controls and actual Stage1 browser export/import continuity were exercised. Long/duplicate human pair labels, capacity1e12 and200% CSS text enlargement reflowed at375px without observed horizontal overflow. Original legacy cancelled receipt remains distinct from current cancelled lookup.

Ten supplemental screenshots and DOM observations cover422 capacity field association, lookup loading, cancel response loss with conservative last-checked status and explicit uncertainty, refreshed cancelled lookup, and availability transport error.

Viewed original desktop selected-pair success, mobile pending/current success, mobile409 moved seating and retained form, and legacy cancelled lookup screenshots, plus the12-state mobile contact sheet and long-label/200% text crops. Warm readable hierarchy, wrapping labels and clear recovery feedback meet the approved finite design scope. Contact sheet/crops are derived review aids; original screenshots remain authoritative.

## Retained failed executions and limits

Initial driver exited1 because Playwright is_disabled() includes aria-disabled; this did not establish native disabled behavior. Corrected driver reads the DOM disabled property. A second focus run and an accidentally overlapping core run contaminated shared synthetic fixture state; these were interrupted/preserved and excluded from acceptance. Final focus3, core2, first and extra executions ran sequentially and passed. Exact commands, UTC and exits are in execution.json; overlap intervention is separately recorded in execution-interruption.txt. Prior product failures at b1f7ef64db7f0dce0b3f3a913a3af280c96d70b9 and intermediate owner repair failures remain preserved unchanged.

NOT_TESTED: native browser200% zoom, assistive audio/screen-reader behavior, exhaustive dates/races/locales, all schedules and full independent HTTP/official suites. CSS200% text enlargement and DOM alert associations are observed layers only. Resource limits were configured/inspected, not peak consumption measurements. Cost UNAVAILABLE; subscription only, noAPI billing/provisioning. Independent Reviewer and PM must assess full API/UI/QA/render evidence at this fixed candidate before Stage2 release. No Designer product repair or tracked changes.
