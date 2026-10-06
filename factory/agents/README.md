# Seven registered seats, one configurable official adapter

`factorykit.runtime` loads all seven role records from `config/factory.yaml`; their
checked-in mandate files are passed verbatim through the official
`CodexAdapterConfig.custom_section` field. Roles are not implementations of another
messaging framework. BAND owns room messaging, mentions, task events and thread
continuity. The runtime supplies bounded process ownership and policy wrappers.

Initially every seat uses the configured result checkout, with **one active turn**
at a time. After the first real BAND-authored commit, the team may configure each
seat's `rehearsal_cwd` or `judged_cwd` to its own worktree under
`runs/worktrees/<mode>/<seat>`. Worktrees must not be inside result directories.
Copy matching non-versioned `.env*` files securely before local app/auth checks.
A maximum of two active turns is permitted only with distinct checkout paths.

Names (`Factory PM`, etc.) are intended display names, not fabricated BAND handles.
The registration probe reads real identities and room membership. Set each real
handle/id and rerun the probe; all config changes invalidate registration proof.
