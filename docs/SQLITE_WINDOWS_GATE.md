# SQLite live cutover gate: Windows observation

Checked 2026-10-13 against repository HEAD `2dfed01c73420fe925354a254cfca35773051ca5` (manifest 2.8.2), Python 3.13.7, and Herdr 0.9.3. No user pane was changed.

- `herdr agent get --help`, `agent prompt --help`, `pane process-info --help`, and `pane list --help` are available. `agent prompt --wait` can observe a later status, but its help explicitly says a concurrent active turn may satisfy that wait; it is not a correlated delivery receipt.
- The ordinary filesystem sandbox denied the Herdr socket with Windows error 5. An approved read-only `herdr agent list` outside that sandbox returned agent name, status, cwd, pane, workspace, terminal and revision metadata. It did not establish model, reasoning effort, process descendants, or run ownership.
- Read-only `pane process-info` for this agent's own pane returned a pane ID, shell PID, foreground process group ID and foreground PID list. It exposed no descendant tree or process start time, so a closed pane or absent foreground PID alone cannot prove every test child has stopped.
- No disposable Manager/Delegator/Worker spawn or complete SQLite run was observed. Existing panes belonged to active user work and were left untouched.
- 2026-10-13 read-only re-check: Herdr 0.9.3 `status server` reported `running`, endpoint and protocol compatible, socket reachable from the workspace shell. Workspace `Herdr Skill Disposable Workspace Test` held an idle `agy` pane and an idle named `claude` pane. No prompt was sent and no agent was started, because SQLite live activation is still gated.

**Historical gate result:** SQLite role activation and new-run default promotion remain disabled. Backend transfer is implemented locally for pristine planning checkpoints, with crash-injection and round-trip tests; that is not Windows Herdr evidence. A disposable Herdr lifecycle must still prove observed identity and liveness, correlated role receipts, and exact candidate verification before this gate can be lifted. Tests with mocked Herdr observations do not satisfy it.

## PR #8 implementation gate, 2026-10-13

The user explicitly approved disposable-workspace E2E and pushing verified commits in the current conversation. Read-only rediscovery found workspace `w37`, label `Herdr Skill Disposable Workspace Test`, with idle Agy (`w37:p3`) and named Claude (`w37:p5`). Both processes point at the implementation checkout; neither is an isolated Worker checkout. Active user workspace `w32` is excluded from test prompting.

SQLite activation now records observed identity and bounded submission receipts in its event table. Correlated acknowledgment and current backend epoch precede role-authorized typed mutations. Markdown remains the default. Mocked typed acceptance/cancellation is local contract evidence, not real pane acceptance.

Heavy verification owns Windows Job Objects or Linux process groups through one shared machine gate. A missing Windows job after owner loss is unknown, not stopped. The gate remains held after unknown descendant observation. See [Microsoft Job Object contract](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

Herdr `pane process-info` reports foreground process identity but no persistent descendant scope. `attempt-release` therefore retains a launched Worker's claim even when the original PID is absent. Transfer refuses that history with `TRANSFER_REQUIRES_RECONCILIATION`; it supports observed absent, never-started reservations after claim release. No transfer kills workers or closes panes.

A full B34/B35 pass, real accepted-task transfer and default promotion remain open. Any authorized live attempt must use the exact committed runtime and record its actual outcome separately; startup or setup success alone cannot close these gates. Local disposable receipts remain outside the PR under `.sqlite-tracer/`.
