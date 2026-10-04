# Required later rendered review — NOT EXECUTED

Work item TK-S2-DESIGN; owner @frankzhu94/factory-designer. This is a review plan, not acceptance. PM must dispatch the actual integrated Stage 2 candidate, full requirements, independent clean checkout, executable service and finite review lease. Do not silently repair a candidate being reviewed.

For each execution record work item, full candidate SHA, clean checkout and evidence absolute paths, responsible literal handle, start/end UTC, browser version, container/image identity, viewport, commands/exits and room delivery event. Use a new `.evidence/designer-s2-review-<UTC>/` directory. Chromium runs in the authorized harness Docker environment on an isolated test network; native Mac Chromium is not assumed available. Screenshots must come from the running service at that exact candidate. Do not describe a design prototype as product evidence.

## Visual evidence matrix

Capture desktop 1440x900 and mobile 375x812 for:

1. Search initial, list pending/empty/failure and retry; navigation signed out and signed in. All four routes direct-loaded as HTML. Signed-in display name visible on each route.
2. Ordinary populated search with unavailable singles, available singles and approved pairs; selected pair booking card names all members. Include exact submitted restaurant/date/party/zone. Use two restaurants with different labels to reveal stale-label errors.
3. Long-label fixture and enough tables/pairs to force wrapping: retain every single cell per returned slot; pairs in declaration order; no clipped labels, hidden cells, overlapping content or horizontal page scroll. Record scrollWidth/clientWidth. A long unbroken name must wrap at both widths.
4. Closed/no-fitting-time day with no-slots; nonempty slots but no available seating with full singleton grid. Distinguish these two screenshots.
5. Signup/login with visible labels, pending, validation and server refusal; password masked. No secret values in evidence; use synthetic accounts and redact transport credentials.
6. Booking pending, actual 409 conflict with refresh and preserved selected form/inputs/error even after pair vanishes, and refresh failure. Capture form and grid so preservation is observable.
7. Lost response for single and pair: nonempty amber booking-uncertain only; retry restores original reference and clears feedback. After successful replay, repeat submit reaches server with same body/key; redact key value but record equality. Screenshot success with persistent form and every table label.
8. Lookup found single/pair, not found, pending, cancellation refusal, cancellation uncertain and confirmed cancellation. Exact lowercase status text, all member labels, cancel button absent after confirmed cancellation. On uncertain cancellation, visibly qualify last known status.

Desktop screenshot and mobile screenshot alone do not prove interactions. Attach an interaction trace with observable before/action/after, expected requirement, result and screenshot filenames for each relevant case.

## Keyboard and accessible feedback

At each width use keyboard only from page entry: skip link, navigation, search fields, available options, selection → form heading, booking party edit and submit, confirmation → lookup, reference submit and cancellation. Record focused element/accessible name and screenshot of the visible ring. Disabled unavailable cells cannot activate; available choices activate by Enter/Space. Focus after success/error/cancel removal remains intentional and visible. Route changes focus h1. New background result/refresh cannot steal focus from a later user action. No modal/trap or positive tabindex.

Inspect label associations, input types/autocomplete, describedby/error relationships, aria-invalid, selected aria-pressed, status/alert nodes and accessible option names. Listen using available accessibility tooling where supported; otherwise report spoken announcement behavior NOT_TESTED rather than infer it from DOM alone. Check no duplicated live messages. Check text at 200% enlargement and reduced-motion preference. Measure actual foreground/background pairs for normal, hover, disabled, selected, error, uncertainty and focus; text >=4.5:1, meaningful outline >=3:1. Token calculations only support, not replace, these checks.

## Behavioral evidence shared with QA

- Delay A detail and availability independently; complete B, then A successes/failures. B headings, labels, cells and booking request stay consistent. Edit search drafts without submit: result heading/body still use last submitted bundle.
- Conflict refresh from A completes after B: no restore. Select/change form during pending create: obsolete callbacks never overwrite new form. Keep failed-attempt evidence.
- Let server commit then drop response (single and pair); unchanged retry sends same body/key, gets original reference, and no duplicate exists. Repeat success submits again; a semantic edit changes identity. First-send disconnect is also unknown outcome.
- Keep same loaded Stage 2 UI and origin; use Stage 1 API for singleton, sign in, commit/drop response, export/import to Stage 2 between requests, switch API destination without reload. current-user and pending form survive; unchanged retry and lookup recover original labels/reference. Separately exercise pair export/import. Browser storage/session must not be manually reset.
- A late lookup/cancel or label response cannot overwrite another reference's detail. Cancellation refusal preserves true status. Server/network failures cannot create confirmations.

QA/Reviewer own independent behavioral and harness acceptance. Designer records only layers actually observed. Review defects identify exact SHA, requirement, steps, expected/observed, viewport/browser, absolute evidence path and owner. Report ACCEPTABLE DESIGN OBSERVED or REJECTED for the tested scope to PM; independent release acceptance remains with Reviewer. All unexecuted cases remain NOT_TESTED. New repair revision requires fresh evidence; do not reuse earlier candidate screenshots as proof.
