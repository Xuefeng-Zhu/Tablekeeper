# Stage 2 diner experience — DESIGN-S2

Owner @frankzhu94/factory-designer; reviewer @frankzhu94/factory-reviewer; starting revision `f0e597f33e1cb389aed1d9cb35030ee022da0dcb`. This is a proposed buildable design specification. Product HTML, browser behavior, focus and responsive rendering are **NOT_TESTED**. Independent planning review precedes implementation; a separate fixed-candidate rendered review follows runnable Stage 2.

## Authority and requirement map

All clauses of these verified sources apply. Design choices below cannot waive them.

| Source, applicable sections | SHA-256 |
|---|---|
| `/Users/frank/mygit/Tablekeeper/challenge/tablekeeper/spec/stage-1.md`, all sections 1–11 (S1-1..11) | `9460189eac83802ce158f16ee90989af728a489b32a6147e2dc8e320f383055f` |
| `/Users/frank/mygit/Tablekeeper/challenge/tablekeeper/spec/stage-2.md`, all sections (S2-routes/auth, search-order, conflict, uncertain, replay, lookup, upgrade, combinations, visual) | `b1aa1b4affad456ff26f208a378eb2f6884153fc6ef667ab37b888fcda96c5dc` |
| `/Users/frank/mygit/Tablekeeper/result-local-sol-20261005/ARCHITECTURE.md`, inherited mechanisms and Stage 2 contract | `3ab218e821038f8468d8a8a835b250da31c7c59e1691c55d2c607186a277ace3` |
| `/Users/frank/mygit/Tablekeeper/result-local-sol-20261005/WORKING.md`, incoming brief, map and Stage 2 queue | `288990ca4b9edac4530614b0f3881f45b574f6c14c2d9a72f0fb6fb8a1db8c1e` (before this item's appended design entry) |

S2-visual supplies warm hospitality, scan hierarchy, distinct states, visible labels/focus/contrast and 375px usability. S2-routes/auth supplies routes and identity; S2-search-order supplies latest-search authority; S2-combinations supplies declared pair ordering and labels; S2-conflict/uncertain/replay supplies feedback and attempt preservation; S2-lookup supplies private detail/cancel; S2-upgrade plus S1-7/10 supplies original receipts and sign-in continuity. Preserve accepted Stage 1 output unchanged. HTTP amendments/moves remain inherited API requirements; no batch UI is required by S1-11. This Stage 2 screen plan covers the explicitly required booking, lookup and cancel browser flows without adding a dashboard or amendment editor.

## Visual direction and reusable tokens (proposed)

Tablekeeper feels like a welcoming restaurant reservation desk: warm paper, deep forest ink, fine rules and generous editorial headings. Its memorable detail is a quiet serif headline above a precise seating timetable. Use restaurant names and table labels as the content, rather than decorative imagery or invented restaurant metadata. No external assets, fonts, maps or libraries at runtime. No illustration is required.

| CSS token / rule | Value and intended use |
|---|---|
| `--paper`, `--surface` | `#F7F3EA`, `#FFFFFF`: page and form/result surfaces |
| `--ink`, `--muted` | `#243C31`, `#596354`: headings/body and secondary readable text |
| `--primary`, `--on-primary` | `#285340`, `#FFFFFF`: primary buttons and selected seating |
| `--line` | `#7F8878`: meaningful field/button borders; decorative rules may be lighter |
| `--available-bg`, `--available-ink` | `#EAF2E8`, `#285340`: available seating, explicit “Available” label |
| `--unavailable-bg`, `--unavailable-ink` | `#ECEBE5`, `#596354`: unavailable cells, explicit “Unavailable” label |
| `--success-bg`, `--success-ink` | `#EAF2E8`, `#285340`: confirmed receipt with a check and text |
| `--error-bg`, `--error-ink` | `#FAEDE6`, `#8D3424`: refused/error with explicit heading |
| `--uncertain-bg`, `--uncertain-ink` | `#FFF0D0`, `#725010`: unresolved outcome with explicit heading |
| `--focus` | `#8D3424`: 3px outline, 3px offset; white inner separation on dark controls |
| Fonts | Headings `Georgia, 'Times New Roman', serif`; body `ui-sans-serif, system-ui, sans-serif`; reference `ui-monospace, monospace`. All local fallbacks. |
| Type | Body/input16px, helper14px, h1 desktop44px/mobile32px, h2 26px/24px; body line-height1.5, heading1.12; reference24px with modest letter spacing |
| Spacing | 4,8,12,16,24,32,48,64px; content max1120px; desktop gutter32px, mobile16px |
| Shape | Inputs/buttons8px radius, panels12px; quiet 1px border; avoid excessive shadows |
| Targets | Interactive height at least44px, comfortable48px input/button; 8px between targets |
| Motion | Optional <=150ms background/focus transition; no delayed rendering; reduce/disable with prefers-reduced-motion |

Colour choices are presentation decisions, not observed compliance. Implementers must verify actual foreground/background pairs: normal text >=4.5:1, large text >=3:1, meaningful controls/focus >=3:1 against adjacent surfaces. Never communicate state through colour alone. If a token pairing fails measured contrast, adjust its shade while retaining the direction and report the change.

## Shared navigation and screen hierarchy (S2-routes/auth/visual)

All `/`, `/signup`, `/login`, `/lookup` return HTML on direct access. Header: Tablekeeper wordmark links home, “Find a table” and “Find my reservation”, then signed-out “Log in” / “Sign up” or signed-in display name and “Log out”. Current page uses aria-current. Signed-in `current-user` contains the actual display name on every route; `logout-button` is a real button. Long names wrap. Header/footer remain consistent; no menu that hides essential navigation at 375px. Two wrapping header rows on mobile are acceptable.

One main landmark and one h1 per screen. Home headline “A table for your next good evening.” followed by a compact explanation “Choose a restaurant, date and party size. Times are local to the restaurant.” The search form immediately follows. Result heading identifies the winning restaurant/date/party and timezone. Avoid arbitrary future-only date limits: past bookings are permitted by inherited API rules. Do not convert restaurant-local times through the diner's device timezone. Preserve full API local time for requests; readable display uses date plus HH:MM and named restaurant zone.

Desktop (reference viewport1280×900): header/content max1120px; search fields restaurant/date/party and primary action in one row where space allows. Below, results occupy approximately two-thirds, booking panel one-third with a24px gap. Form is inline in document, never a modal. Confirmation appears immediately below form. At intermediate widths where panels/fields no longer fit, stack rather than compress labels.

375×812: 16px gutters leave343px usable width; header wraps; search fields and full-width action stack. Results first, selected form next, receipt last. On selection, focus the booking heading and scroll it into view without hiding its top beneath the header. Every panel uses min-width:0; long table/restaurant names wrap; controls max-width:100%; no horizontal page scrolling. Avoid sticky mobile controls that cover feedback or keyboard focus.

## Availability layout and selection (S2-search-order/combinations/visual)

Render `availability-grid` as semantic time groups, not a spreadsheet that becomes wider than the phone. Each group has an HH:MM heading followed by seating cards: single tables in fixture order, then currently available declared pairs in combinable order. Desktop cards may form a two/three-column CSS grid; mobile uses one column or two only when labels remain fully readable. This preserves every single-table cell per legal slot without horizontal overflow, even with many tables. Group/heading semantics and accessible button names make both dimensions apparent.

Single card: “Table {label}”, capacity helper “Up to {capacity} guests”, state word. Accessible name includes table label, local time and availability. Pair card: “Tables {label A} & {label B}”, helper “Together · Up to {summed capacity} guests”; optional “Combined tables” badge. Never infer pairing from capacity or transitivity. Use API `available_options` for legal free pairs and declared member order for testids. Show pair cards only when available for the searched party; singles always remain present per legal slot. Human labels may be identical across tables, so a secondary table ID may clarify ambiguity, but identifiers never replace primary labels.

Available cells are buttons with `data-available="true"`. Unavailable singles carry `data-available="false"`, disabled semantics and no-op activation; grey appearance still readable. Single testid `slot-{table_id}-{HH:MM}`; pair `slot-{t_a}+{t_b}-{HH:MM}` in declaration order. Set attributes safely, not via raw HTML/selector interpolation. Selected available cell adds check + “Selected”, stronger outline and aria-pressed=true. After conflict refresh, retained selection may be unavailable: show “Selected · Unavailable” on its single card, preserve form; do not make unavailable cells actionable. An unavailable pair can disappear from results while its preserved form still identifies both tables and the refusal.

Search captures restaurant/date/party immediately on submit. Suspend old actionable results and old form while loading new search; a prior selection must not appear to belong to new results. All detail/list/availability/error/finally steps must guard the active search generation. Render one coherent result bundle; labels, grid and opened form must all describe B after B wins over A. Aborting older requests is optional. Input edits alone need not run a search: keep a visible “Showing results for …” summary; cell selection uses that captured searched party, not unsent input edits.

## Journey and feedback inventory

| Journey/state | Content and interaction | DOM/state obligation |
|---|---|---|
| Initial restaurant load | “Loading restaurants…”; select/action unavailable until options arrive; loading indicator with text | No pretend results. List errors offer “Try again”; late list cannot overwrite user's selection. |
| No restaurants | “No restaurants are available yet.” | No invented options/grid; explain search unavailable. |
| Search ready | Visible Restaurant, Date, Party size labels; “Find a table” primary action | Native select/date/number; all required values preserved on validation failure. |
| Searching | “Finding tables…”; results region aria-busy=true, in-flight indicator | Latest search may supersede old one; old response/error/loading cannot restore old content. |
| Search failure | “We couldn't load availability. Try your search again.”; retain search inputs | Distinct error region (additional testid optional); no stale actionable grid disguised as new results. |
| Closed/no legal slots | “No seating times on this date. Try another date.” | `no-slots` replaces `availability-grid` only when slots=[]; do not conflate fully occupied slots with closed day. |
| Legal slots, none free | “No tables fit your party at these times. Try a different date or party size.” | Grid retains every singleton/time with false availability; no-slots absent. Don't claim why unavailable beyond API facts. |
| Signed-out available click | “Log in to reserve this table.”; focus login link beside message | Choose inline `auth-error` on home, no form submission/confirmation. Public browsing stays possible. Login/signup return home; preserving pre-auth selection optional. |
| Selected, signed in | “Your table” panel, restaurant/date/time/all labels, party input prefilled from captured search | `booking-form`, `booking-summary`, `booking-party-size`, `booking-submit`; button “Confirm reservation”. Selection and party changes are real form revisions. |
| Submit pending | “Confirming your reservation…”; submit disabled or shared attempt; fields may be disabled during fetch | Remove old attempt's confirmation/error/uncertainty; aria-busy. Capture immutable body/key before send. Never mint key for double click. |
| Definite competitor409 | “That seating option was just booked. Your details are saved—choose another option or try a different party size.” | `booking-error` present, no new confirmation/uncertainty; selected form/all input values remain; scoped availability refresh does not overwrite them or newer search. |
| Refresh pending/failed | “Updating availability…” / “Availability couldn't be refreshed. Your booking details are still saved. Run your search again for current choices.” | Preserve booking-error/form; refresh error separate, never reinterpret rejection as success/uncertainty. Changing choices begins new form revision. |
| Confirmed validation refusal | Capacity: “This seating option cannot fit that party. Change the party size or choose another table.”; grid/hours/local-time: “That time cannot be booked. Search again for available times.” | `booking-error` with actual human API detail when useful; preserve editable form. Not raw error code alone. |
| Outcome uncertain | Heading “We couldn't confirm the result”; “Your reservation may have been made. Retry the same details to check and recover its reference.”; action “Retry same reservation” | Nonempty `booking-uncertain` only; booking-error and confirmation absent. Unchanged retry uses exact stored body/key/caller, even after committed lost response. Never say “not booked”. |
| Editing after uncertainty | Permanent helper near form: “Changing details starts a new reservation. If the previous result is uncertain, retry those details first to avoid a second booking.” | Any actual edit/selection change retires previous identity for next submit, including change/revert. No modal confirmation required. New attempt never reuses successful key with changed body. |
| Success201/replay200 | “Reservation confirmed” + restaurant, all tables, local date/time, party and reference | `confirmation`, `confirmation-reference` text exactly reference; surrounding label outside; `confirmation-details`, `confirmation-tables`; remove error/uncertainty; keep booking form. |
| Unchanged submit after success | Action “Check this reservation again”; small helper “Same details recover the same reservation.” | Actual authoritative POST with original key/body; same original reference, no duplicate. Hide old receipt while new response pending/unknown/refused. |
| Auth pending/failure | “Signing you in…” / “Check your email and password.”; signup duplicate “This email already has an account. Log in instead.” | Required auth inputs remain labelled; auth-error only on failure. Never render passwords/tokens; no invented verification/reset flow. |
| Lookup idle/loading | “Find your reservation”; Reference label; “Find reservation” / “Finding reservation…” | Sign-in required for private call. Signed-out screen links login and explains requirement without claiming missing record. |
| Lookup missing/refused | “We couldn't find a reservation for this account with that reference. Check the reference and try again.” | `reservation-error`; owner-hidden404 wording does not disclose other users. No stale detail from different reference. |
| Lookup found | Restaurant, date/time/zone, party, all labels, status, reference; “Cancel reservation” secondary outlined danger action | `reservation-detail`, `reservation-tables`; `reservation-status` text exactly confirmed/cancelled (no suffix inside). |
| Cancel pending/refused | “Cancelling…” / cutoff: “This reservation is too close to its start time to cancel.” | Disable duplicate cancel; `reservation-error` on refusal; retain confirmed detail and control once pending ends. Never announce cancellation before success. |
| Cancel network uncertainty | “We couldn't confirm whether cancellation completed. Look up the reference again to check its current status.” | Conservative nonempty reservation-error; keep last known status explicitly identified as last checked. Retry lookup/cancel checks server; no optimistic cancelled status. |
| Cancel success/already cancelled | “Reservation cancelled”; all labels and details remain | Authoritative status cancelled; cancel button absent; cancellation frees every table server-side. |

Booking attempt completion must guard both attempt ID/form revision and active search generation; a stale POST cannot confirm another form. A conflict refresh captures the form's winning search scope and cannot replace a newer search. Distinguish received, parseable rejection from network/unreadable/ambiguous outcome. Conditional feedback nodes are removed when inapplicable, not left empty/hidden. Timely transport failure shows uncertainty and restores retry control; no indefinite pending spinner.

Signup labels Email, Password, Your name; helper “Use at least 8 characters.”; `type=email`, password, suitable autocomplete (new-password/current-password/name). Submit does not rely solely on client validation: actual API remains authoritative. Display-name/email/party errors associate with fields; auth-error summarises received refusal. Login accepts seeded credentials without signup. Logout removes current-user and pending use for that caller; never transfer attempt keys to another user.

## Exact DOM contract checklist

These names derive solely from Stage 2; no renamed alternatives satisfy the contract.

| Screen | Required attributes / conditional content |
|---|---|
| Signup | `signup-email`, `signup-password`, `signup-display-name`, `signup-submit`; `auth-error` only when error |
| Login | `login-email`, `login-password`, `login-submit`; `auth-error` only when error |
| Every signed-in screen | `current-user` text contains display name; `logout-button` |
| Search | `restaurant-select` option values actual IDs; `date-input` YYYY-MM-DD; `party-size-input`; `search-button`; `availability-grid` OR `no-slots`; slot attributes as above |
| Booking | `booking-form`, `booking-summary` all labels/local start, `booking-party-size`, `booking-submit`; `booking-error` only refusal, `booking-uncertain` only unknown |
| Receipt | `confirmation`, `confirmation-reference` exactly reference, `confirmation-details` restaurant/labels/local start, `confirmation-tables` every label |
| Lookup | `lookup-reference-input`, `lookup-submit`; found `reservation-detail`, `reservation-status` exactly confirmed/cancelled, `reservation-tables`; `reservation-cancel-button` absent when cancelled; `reservation-error` not-found/cancel-refused |

Legacy imported original receipts may have table_id without table_ids: use response.table_ids or [response.table_id] to resolve labels without rewriting the receipt. Confirmation represents the ORIGINAL receipt even after subsequent cancellation/amendment; current lookup represents CURRENT server state. A helper “Original booking receipt. Look up the reference for current status.” prevents apparent contradiction. Never fill confirmation from a cached prior response or current lookup instead of successful submit/replay.

## Upgrade continuity (S2-upgrade; S1-7/10)

Import completes between requests. Existing display name/token, chosen form, exact pending body and key stay in browser memory/session state through upgrade. Next lookup uses the existing token and retained reference. Lost pre-export submit retries unchanged and confirms original response, including legacy fields. No sign-out, forced reload, form reset, new key or migration banner is necessary. If a genuine reset invalidates authentication, show sign-in feedback from the actual response; do not invent continuity. Refresh after upgrade cannot erase pending identity. Backend handles actual Stage1 snapshot migration; design claims no proof of that integration.

## Accessibility, responsive and rendered acceptance

Visible labels use label/for; helper/error IDs connect through aria-describedby; required inputs expose semantics and invalid fields aria-invalid after validation. Controls are native buttons/inputs/selects/links, not clickable divs. Time-group labels and seating names read clearly without relying on table position or colour. Real disabled unavailable cells are skipped in keyboard order; all legal cells reachable with Tab and Enter/Space. Use status/live polite announcements for loading/success/uncertainty, alert for new refusal; avoid repeated announcements per cell. Programmatic focus goes to booking heading on selection and feedback summary on completed submit when useful, with tabindex=-1, visible focus and no trap; updates do not move focus on every refresh. Retain focus on submit/retry while pending and return it if recreated. Signed-out selection focuses login link. Cancel result announces status without unexpected page jump.

Acceptance at1280×900 and375×812, plus narrow intermediate layout: no document horizontal overflow; date/native controls fit; arbitrary long labels/names/reference don't clip required text; all primary actions readable; keyboard focus visible and unobscured; text/meaningful boundaries meet measured contrast; 200% text/browser zoom reflows; reduced motion respected. Rendered evidence must include browser/version, OS, exact candidate full SHA, clean separate checkout verification, viewport CSS dimensions, UTC start/end, exact commands, unique absolute screenshot/trace/report paths and responsible handle. Screenshots support appearance; DOM, keyboard interaction and network fault observations support behavior. Source inspection alone cannot PASS these checks.

Later rendered assignment must observe: direct four routes; signed-out and signed-in auth; initial/loading/empty/full grid; single and pair labels/selection; success retained form and original replay; competitor409 preserved edited party/form plus scoped refresh; dropped postcommit single/pair responses and exact unchanged retry; A/B delayed detail/availability/error/finally races; stale POST; original receipt vs current cancelled lookup; cancel cutoff; upgrade without reload/auth/form loss. Contrast and keyboard pass/fail require actual measurements/interactions. Fault injection belongs to QA/Frontend execution with retained evidence; Designer independently inspects visible states at the fixed candidate and returns requirement-linked defects to owner, never repairs and approves the same candidate. Finite observed cases do not prove all possible races/calendars.

Optional presentation choices: modest rule motif, CSS-only check/status symbols, very short hover transitions. Essential navigation, labels, feedback and responsive state behavior remain mandatory. No optional dashboards, synthetic availability, illustrations or third-party runtime services are authorized here.
