# Tablekeeper — attempt index

All application attempts now share this private GitHub repository. Each attempt has its own branch and preserved history. The reusable factory tools remain in [Tablekeeper-factory](https://github.com/Xuefeng-Zhu/Tablekeeper-factory).

| Branch | Status | Preserved revision |
| --- | --- | --- |
| [run-2](https://github.com/Xuefeng-Zhu/Tablekeeper/tree/run-2) | Stage 1 implementation; owner-run checks recorded, without independent stage acceptance | `86ad17c600fd994bcb55c222049b7d0546f2e472` |
| [run-3](https://github.com/Xuefeng-Zhu/Tablekeeper/tree/run-3) | Independently accepted Stage 1; full BAND export and submission drafts; stages 2–4 unimplemented | `dc4cf0c4dcf8b96e155f156694960df7fc1f4745` |
| [run-4](https://github.com/Xuefeng-Zhu/Tablekeeper/tree/run-4) | Stopped and closed with a BLOCKED outcome; no accepted stage; full BAND export preserved | `13bc38fc1d4396c70c42b529eab369d6f84a0186` |
| [run-5](https://github.com/Xuefeng-Zhu/Tablekeeper/tree/run-5) | One approved task dispatch; runtime in progress; no accepted stage claimed | Remote head `0b193c068341b5e81f82b354e25eab12355e77dc` observed at `2026-10-04T21:08:57Z`; live branch may advance |

`main` is the navigation branch. Its retained implementation files come from Run 2; use the named attempt branch to inspect that attempt. This operator-generated index is not a judged attempt's participant-authored submission README.

Future tries use `run-N` branches in this repository. A fresh judged attempt starts in an empty independent local checkout, with no previous application files or commit, and points `origin` here. Its first real agent commit creates the remote branch. Completed attempt histories are never merged into a fresh try. Preserve each attempt's room export, evidence and commits on its own branch.

Run 4 is preserved at `/Users/frank/mygit/Tablekeeper/result-run-4`. Its Stage 1 candidate failed independent review after all three repair cycles were exhausted; stages 2–4 were not started. The full `room.json` contains 3,774 events and one human task dispatch. See the factory's [Run 4 outcome](https://github.com/Xuefeng-Zhu/Tablekeeper-factory/blob/main/docs/run4-outcome.md).

Run 5 began in the empty independent checkout `/Users/frank/mygit/Tablekeeper/result-run-5` and uses BAND room `12cde259-0fcd-4551-84c7-52c443cb5600`. One approved task was dispatched at `2026-10-04T21:06:55.943682Z` (2:06 PM PDT), event `d5861cdd-0b57-4393-a97f-0dfb9201cec2`. All seven seats connected with at most one active model seat. Its remote branch now exists; initial execution and publication do not establish product acceptance. The approved launch retains the existing cumulative token ceiling and October 4, 2026 at 7:35 PM PDT deadline. See the factory's [Run 5 launch review](https://github.com/Xuefeng-Zhu/Tablekeeper-factory/blob/main/docs/run-5-preparation.md).

The former Run 3 GitHub repository remains as a historical copy. No repository was deleted, no history was rewritten, and this organization change starts no BAND workers or model turns. Select and verify the intended completed branch before final submission; a normal clone must open that submission rather than this index.
