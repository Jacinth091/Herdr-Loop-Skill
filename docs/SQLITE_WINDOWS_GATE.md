# SQLite live cutover gate: Windows observation

Checked 2026-10-13 against repository HEAD `2dfed01c73420fe925354a254cfca35773051ca5` (manifest 2.8.2), Python 3.13.7, and Herdr 0.9.3. No user pane was changed.

- `herdr agent get --help`, `agent prompt --help`, `pane process-info --help`, and `pane list --help` are available. `agent prompt --wait` can observe a later status, but its help explicitly says a concurrent active turn may satisfy that wait; it is not a correlated delivery receipt.
- The ordinary filesystem sandbox denied the Herdr socket with Windows error 5. An approved read-only `herdr agent list` outside that sandbox returned agent name, status, cwd, pane, workspace, terminal and revision metadata. It did not establish model, reasoning effort, process descendants, or run ownership.
- Read-only `pane process-info` for this agent's own pane returned a pane ID, shell PID, foreground process group ID and foreground PID list. It exposed no descendant tree or process start time, so a closed pane or absent foreground PID alone cannot prove every test child has stopped.
- No disposable Manager/Delegator/Worker spawn or complete SQLite run was observed. Existing panes belonged to active user work and were left untouched.

**Gate result:** SQLite role activation, new-run default promotion, and backend switching remain disabled. A disposable Herdr lifecycle must prove observed identity and liveness, correlated role receipts, exact candidate verification, and safe transfer before this gate can be lifted. Tests with mocked Herdr observations do not satisfy it.
