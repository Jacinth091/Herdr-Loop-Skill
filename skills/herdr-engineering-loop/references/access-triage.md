# Access-Denied Preflight and Escalation

`Access is denied`, `EPERM`, `EACCES`, `WinError 5`, and `permission denied` are **symptoms, not instructions to elevate**. Distinguish a wrong pane/session, sandbox policy, file ACL, Herdr socket failure, and protected external services before selecting the smallest next step. Do not auto-run as Administrator, disable the sandbox, change ACLs/owner recursively, weaken PowerShell/system policy, expose a Docker TCP endpoint, copy credentials or retry indefinitely.

## Preflight before starting agents or executing an assigned task

1. **Origin:** In the assigned pane, check `HERDR_ENV=1`, `herdr --version`, `herdr status server`, `herdr agent get <assigned-name>`. Compare observed Herdr pane/workspace/cwd with the role packet and `SESSION.md`. A `HERDR_ENV` variable by itself does not prove agent registration. Run `herdr agent list` or pane inspection only when needed.
2. **Code provenance:** Confirm worktree path, branch, fixed base and clean/unrelated changes with `git rev-parse`, `git branch --show-current`, `git status --short`, `git worktree list --porcelain` (for code roles). Do not run in an arbitrary current directory.
3. **Narrow filesystem check:** Worker should be able to create/remove a harmless temporary file **only inside its own** `.herdr-loop/`, not the full repo or another worktree. A successful probe proves only that particular path is writable. No secrets or full environment dumps in diagnostics.
4. **Signal path:** Resolve the exact recipient from the local role packet, check `herdr agent get <recipient>`, and perform the `ROLE_READY` handshake. An agent whose recipient is missing or whose own pane is not registered does not start implementation.
5. **Verification services:** Only an authorized Delegator/Verifier checks Docker/WSL/database access and only when needed for approved tests. Confirm disposable DB identity and serial test lock first. A Windows `npipe` failure is distinct from source-code test failure.

## Classify, report, and route

| Observation | Likely class | Required response |
| --- | --- | --- |
| `HERDR_ENV` missing, wrong pane, or `herdr agent get` cannot confirm role | `HERDR_ORIGIN_UNCONFIRMED` | Stop; inspect actual Herdr workspace and use a valid Herdr-spawned agent, never Codex `spawn_agent` |
| Herdr CLI cannot reach running server/socket or reports denied | `HERDR_TRANSPORT_DENIED` | Record exact command, server/session and redacted error; ask Delegator/human to repair socket/session context |
| Assigned `.herdr-loop` or worktree write probe denied | `WORKTREE_WRITE_DENIED` | Confirm actual cwd, permitted root and ownership; no blind elevation or sandbox disabling; Delegator chooses authorized reprovisioning |
| CLI permits read-only operations but worker edits blocked by its sandbox | `AGENT_SANDBOX_DENIED` | Preserve isolation; escalate for human review or choose a pre-approved narrower writable worktree |
| Docker CLI works outside agent but `npipe:////./pipe/docker_engine` denied inside | `DOCKER_PIPE_DENIED` | Treat as verification **environment** block; human may approve a separate trusted verifier/pane, no daemon/ACL reconfiguration by agent |
| WSL launch errors `E_ACCESSDENIED` from agent | `WSL_ACCESS_DENIED` | Distinguish sandbox/process restrictions from product failure; do not mutate WSL policy |
| Named destination absent, ambiguous, or prompt denied | `SIGNAL_TRANSPORT_UNAVAILABLE` | Keep Markdown, report missing recipient/route, no fabricated delivery; retry only after observed repair |
| OS-denied specific file outside assigned root | `OUT_OF_SCOPE_ACCESS_DENIED` | Do not seek access; task needs a narrower brief or authorized handoff |
| Cause unknown | `ACCESS_DENIED_UNCLASSIFIED` | Stop at the smallest failing step and escalate with redacted evidence |

## Blocker evidence minimum

Save to worker-local `REPORT.md`, verification report, or Delegator's `SESSION.md`: `run/task/role`, **actual** Herdr name and pane (if observed), cwd/worktree, CLI executable/version (if relevant), exact failing command **without secret args**, protected target category, exit code and short redacted error, whether the same operation is allowed from an ordinary human-owned shell (if human confirms), last successful read-only check, and proposed smallest approval/reprovisioning action. Distinguish `ENV_BLOCKED` from `PRODUCT_FAIL`.

If no file is writable, leave the error plainly in the agent pane and inform the human/Delegator through any already-authorized channel. Do not pretend a report file or signal exists. A bounded single recheck is acceptable after the environment changes; no speculative retries or permission expansion.
