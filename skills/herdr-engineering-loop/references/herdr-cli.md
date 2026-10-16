# Herdr CLI Playbook (examples, not a pinned API)

The installed `herdr` version wins over this file. Use `herdr --help` and the official `herdr` skill; documentation: https://herdr.dev/docs/cli-reference/ and https://herdr.dev/docs/agent-automation/ . Herdr v0.9.3 documentation was checked while this workflow was authored. Do not invoke bare `herdr` to inspect capabilities; it starts/attaches a TUI.

## Discover, do not guess

```sh
herdr --version
herdr status
herdr workspace list
herdr tab list
herdr pane list
herdr agent list
herdr worktree list --cwd <repo-path>
git -C <repo-path> status --short
git -C <repo-path> worktree list --porcelain
```

The skill may control Herdr only from inside a Herdr pane with `HERDR_ENV=1`, unless the user explicitly invokes the external CLI manually. For Unix shells check `[ "${HERDR_ENV:-}" = 1 ]`; for PowerShell inspect `$env:HERDR_ENV -eq '1'`. Agent control must refuse when that check fails. When using `--machine`, discover remote IDs and pass the machine selector on every command; never use local pane IDs remotely.

## Space, tab, pane and agent

If the human already created a workspace or tab, **reuse** it. Creating a workspace also creates its first tab and root pane. Creating a tab creates a root pane. A split creates a new pane. Parse actual IDs from JSON output; never hardcode examples like `w1:p2`.

```sh
herdr workspace create --cwd <repo-path> --label <run-label> --no-focus
herdr tab create --workspace <workspace-id> --label <tab-label> --no-focus
herdr pane split <existing-pane-id> --direction right --no-focus
herdr agent start manager --kind claude --pane <shell-pane-id>
herdr agent start delegator --kind codex --pane <shell-pane-id>
herdr agent prompt delegator "Read the accepted PLAN.md at <path>; begin T01." 
herdr agent get delegator
herdr agent read delegator --source recent-unwrapped --lines 100
```

Swap Claude/Codex roles when selected by the user. Manually started agents can be named after detection using `herdr agent rename <pane-id> <unique-name>`. `agent start` requires an idle shell pane and does not create the pane. Use lower-case unique agent names matching Herdr's name rules; do not start a second agent in an occupied pane. Governed roles must resolve to a registered name, never a bare pane: addressing an agent by pane id, or one whose name was never set, is refused as `HERDR_AGENT_UNNAMED`, and the fix is `herdr agent rename <pane-id> <unique-name>` followed by passing that same name. `herdr agent list` does not report names, so confirm one with `herdr agent get <unique-name>`. Herdr's installed CLI states that it clears a name when that agent exits, is released, or is replaced, so a name registered earlier can be gone while its pane still hosts the agent; the ledger then probes the pane and reports `HERDR_AGENT_UNNAMED` with the re-name fix when the agent is still live, and a plain `HERDR_ERROR` with a start command when the pane is gone — a cleared name is never reported as an unreachable transport.

## Worktree worker lifecycle

`worktree create` opens a linked workspace with root pane. Its workspace and root pane identifiers must be read from the response or discovery commands. Git worktree branches should be task-specific and rooted at an approved immutable base ref. To reassign a worker, first check whether reuse of its existing branch/worktree is valid; otherwise create a new worktree only after safe cleanup.

**Mandatory for Codex Delegators:** use the Herdr commands below, not Codex's native `spawn_agent`/subagent/delegation feature. Native Codex subagents are not visible as independently named Herdr agents, do not guarantee the required separate pane/worktree, and cannot satisfy the worker contract. Classify a failed command and use the current CLI's native approval only for the same authorized operation when offered. If Herdr remains unavailable, return `BLOCKED: HERDR_WORKER_UNAVAILABLE`; do not invoke a native subagent as a fallback.

```sh
herdr worktree create --cwd <repo-path> --branch herdr/<run-id>/t01 --base <base-sha> --no-focus
herdr worktree list --cwd <repo-path>
herdr workspace list
herdr pane list --workspace <worker-workspace-id>
```

The delegator copies the task brief to `<worker-worktree>/.herdr-loop/TASK.md` using local file operations appropriate to its platform and keeps a canonical copy in the run directory. Add `.herdr-loop/` to the repo-local Git exclude file (obtained via `git rev-parse --git-path info/exclude`) only if needed and not already ignored. Never commit the task artifacts by accident.

### Agy worker (governed/manual default)

Inspect `agy --help` on the target platform before using optional flags. Governed work and approval-path checks use the CLI's normal permission flow. On Windows, verify the installed UI/version and confirm sandbox startup; do not infer approval support from a flag.

```sh
herdr agent start worker_t01 --kind agy --pane <worker-shell-pane>
herdr agent prompt worker_t01 "Read your local ROLE.md/TASK.md, verify Herdr provenance and signal target, write READY.md and send ROLE_READY to Delegator. DO NOT edit product files before TASK_START." 
```

For a human-selected smooth unattended run, use only a task-specific disposable worktree with no unrelated changes or secrets. After confirming the installed sandbox works, pass both flags to Agy:

```sh
herdr agent start worker_t01 --kind agy --pane <worker-shell-pane> -- --sandbox --dangerously-skip-permissions
```

This approves all Agy tool calls and cannot verify native approval. If the sandbox fails to start, stop and resolve that environment issue; do not retry without the sandbox or use the bypass to get past a denial. See [Native permission capability by harness](permission-capabilities.md) for the full boundary.

### Codex Luna worker

Use a local validated Codex model **slug** associated with the user's Luna choice. "Luna" is a role/model nickname, not necessarily a valid `-m` value. Inspect `codex --help`/configured models; never invent one.

```sh
herdr agent start worker_t01 --kind codex --pane <worker-shell-pane> -- -m <configured-luna-model-slug>
herdr agent get worker_t01
herdr agent prompt worker_t01 "Read your local ROLE.md/TASK.md, verify Herdr provenance and signal target, write READY.md and send ROLE_READY to Delegator. DO NOT edit product files before TASK_START."
```

Once Worker `READY.md` exists and directed `ROLE_READY` was observed by Delegator, record receipt in `SESSION.md` and send `herdr agent prompt worker_t01 "TASK_START <run-id> T01 <accepted-plan-revision>"`. No implementation is dispatched by the setup prompt. On permission denial, use [Access-Denied Preflight](access-triage.md), not admin elevation or disabled sandbox. See [Signal and Receipt Contract](signals.md).

## Approved non-code isolated-copy exception

For a human/Manager-approved documentation-only edit, a separate **Herdr workspace with a dedicated worker pane** may point to a narrowly prepared copy instead of a Git worktree. This is not a general alternative for product code. Record `non-code-isolated-copy`, allowed files and trust inspection in TASK and SESSION. Stage only needed files, not parent folders with hidden/nested patches or secrets; inspect sandbox visibility before Agy launch. Record no source commit and verify the exact copied diff when applying to the real target. Herdr registration remains mandatory; never use Codex native `spawn_agent`.

## Collect, signal, verify, reclaim

```sh
herdr agent get worker_t01
herdr agent wait worker_t01 --timeout 120000
herdr agent read worker_t01 --source recent-unwrapped --lines 100
herdr agent prompt delegator "REPORT_READY <run-id> T01 <report-path> <sha>"
```

The worker's prompt is an optional hint and must not be repeated blindly. Do not build `sleep`/poll loops around `agent wait`; a one-shot bounded wait is optional. Herdr `agent wait` observes lifecycle, not a specific turn, and may return `blocked`; never conflate `idle/done` with delivered files. Timeouts and `agent_prompt_stalled` do not prove the previous prompt was lost. Inspect status and report before retrying.

Use `herdr pane run <pane-id> "<command>"` and `herdr pane wait-output <pane-id> --match "<text>" --timeout 120000` for test shells, not `agent start`. Only one verifier/test command at a time. A fresh test pane is optional; on 8 GB RAM, favor the delegator running serial checks in its integration checkout.

After the delegator verifies integration and Git cleanliness, remove **only the worker worktree owned by the run**, without `--force`. An approved non-code scratch copy follows a separate path-boundary and before/after evidence cleanup checklist:

```sh
herdr worktree remove --workspace <worker-workspace-id>
```

Do not infer deletion from the command submission. Re-inspect workspace/worktree lists and Git worktree list. Herdr's `worktree remove` does not delete the Git branch. Do not use `workspace close --group` as a shortcut: it may close the human's primary group.

## Native Windows guidance

Herdr supports Windows/PowerShell sessions; examples above are command shapes, not literal PowerShell scripts. PowerShell users should parse creation JSON via `ConvertFrom-Json` (not `jq`), quote paths, and use `Copy-Item` for Markdown handoffs. Powershell cwd reporting can lag after `cd`; specify `--cwd` explicitly for new layouts and verify the resulting checkout. Windows support details: https://herdr.dev/docs/windows-beta/ . Do not rely on Unix-only `terminal attach` on Windows.
