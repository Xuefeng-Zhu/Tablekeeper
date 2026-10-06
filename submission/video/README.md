# The Build Guild — Tablekeeper video

The included repaired MP4 is 205.9 seconds (about 3 minutes 26 seconds), 8,737,530bytes, SHA-256 `6c4741e326a6c019d363b7df49466e692f99fe2823bc09f503b29ba3cb059cd8`. It includes H.264 video, AAC synthetic narration and captions. [QA.json](QA.json) records VERIFIED_MEDIA.

- [Final MP4](The-Build-Guild-Tablekeeper-Demo.mp4)
- [Captions](The-Build-Guild-Tablekeeper-Demo.srt)
- [Narration transcript](transcript.md)
- [Repair provenance](repair-provenance.json)
- [Media QA](QA.json)

Actual generating-room footage shows PM→Reviewer handoff, receipt, independent rejection and owner acknowledgments in room 4084182d-0b85-46d6-a137-f0bd7d44b498. These genuine room clips were retained. An audit found that raw-frame filename collisions had overwritten app scenes in the original assembly. That version was withheld; four app segments were replaced with uniquely named genuine live screenshots captured 2026-10-06T06:45:55.166Z. The correction and hashes are recorded in repair-provenance.json. Captions, transcript and synthetic narration were preserved.

Fresh app scenes show the actual home, Cedar six-guest availability, the signed-out pair login gate, and Olive two-guest Berlin availability. They are still captures held on screen, not continuous recordings of a completed transaction. No completed booking, private lookup or cancellation footage is claimed. The separate retained live HTTP receipts cover their stated checks.

At 2026-10-06T06:46:36Z all nine live client assets matched public deployment commit 382998fe143268754ade2ed2702090df3f71ef56; search/booking/style matched the older factory candidate b1f7ef64 rather than the repaired canonical 108bcd4 client. Server binary release identity was not independently queried. The deployment adapter adds disclosed synthetic restaurant seeds and closes test routes. Accounts and reservations are ephemeral.

Full FFmpeg decode passed. Review inspected twelve decoded scene midpoints and four fresh app images; the operator independently inspected corrected search, gate and Olive scenes. This is point visual inspection plus full decode, not continuous human playback or complete human listening QA. Narration uses the local synthetic Samantha voice.

The practice run had human scheduling/budget amendments and administrative restarts. The media preserves a historical pre-acceptance snapshot: its original narration describes the pending gate at recording time. Stage 2 integrated `108bcd4` was subsequently independently accepted by verdict `95e67bf5-cad5-44a1-8a77-5684ab15f6b5`; see the root Reviewer release evidence. The live demo remains b1/382998fe. The supervisor stopped at 07:01:51Z at the cumulative overall time cap; Stage 3/4 folders were absent at that stop. The later approved continuation started Stage 3 architecture; this subsequent work is outside the historical video. The video, captions and transcript have been packaged in this repository; no LabLab upload or final entry submission is claimed.

Current status update: Stage 3 `335f167` was independently accepted at 08:18:43Z. Stage 4 remains planning only; the runtime stopped at the 08:24:29Z observation after the PM reached its 769-turn cap; turn admission blocked a queued handoff. This media remains unchanged historical footage/slides, with older b1 live-demo provenance. No new Render release or final submission is claimed.
