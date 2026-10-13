# Native permission capability by harness

Approval is an interaction between the current AI's execution tool and its provider/user reviewer. Herdr commands, role packets, and `access_probe.py` cannot grant or detect a live approval decision. When using native approval, ask only for the exact in-scope command, wait for that tool call's result, and verify its postcondition.

## Session modes

- **Approval-path checks and tasks requiring native prompts:** keep the provider's ordinary permission flow. Do not use an automatic mode to test whether native approval works.
- **Optional smooth unattended work:** use only when the human selects it for a task-specific disposable worktree with no unrelated changes or secrets. Keep the setting session-only. Claude Code can use **Auto** mode (`Shift+Tab`; enable it with `claude --enable-auto-mode` when available). Auto makes permission decisions with Claude's safeguards, so it is not evidence of a human approval. For Agy, launch with `--sandbox --dangerously-skip-permissions`; this approves every tool call, including file writes and command execution. Confirm the sandbox is active before work. If sandbox startup fails, stop; do not retry without the sandbox or use the flag to get past a denial.
- Never use either mode for approval-capability tests, to retry an explicitly denied operation, or in a shared/dirty checkout. These modes do not expand task scope or role authority.

Official references: [Claude Code Auto mode](https://claude.com/resources/articles/auto-mode), [Antigravity CLI flags](https://antigravity.google/docs/cli/using/), [Antigravity sandbox](https://antigravity.google/docs/cli-sandbox).

## Codex

- Use the active Codex command tool's native request when it presents one for the exact command. An installed `codex` binary or a role packet does not prove that the current session can request approval.
- Honor the existing approval policy and reviewer choice. If `on-request` or an already-configured reviewer surfaces a decision, wait for it. Never edit Codex configuration, turn on auto-review, switch to full access, or assume a noninteractive invocation can ask.
- Approval covers only the reviewed tool call. It does not authorize another worker, change the assigned role, or permit publishing/secrets/admin actions.

## Claude Code

- Let Claude's own Bash/PowerShell tool evaluate its installed permission rules. Request approval only when that same tool offers the native Ask/review path for the in-scope command.
- An explicit deny is terminal. Do not change project/global settings, use `bypassPermissions`, or move the equivalent command to another provider or shell. For an optional unattended isolated run, use session Auto mode as described above; do not use it to test native approval.

## Antigravity (`agy`)

- For approval-path checks and tasks requiring a native prompt, use the installed CLI's ordinary configured permission flow and wait for an actual Ask/Allow/Deny decision. For the optional isolated unattended mode above, `--sandbox --dangerously-skip-permissions` is allowed only after confirming sandbox startup; never use it as a recovery from a denial or sandbox failure.
- Antigravity documents different permission controls on Windows than on macOS/Linux; confirm the actual installed version and Windows UI before describing a capability. A permission preset or `--sandbox` option does not prove that a particular Herdr command can request approval.
- Official references: [Agent permissions](https://www.antigravity.google/docs/permissions) and [Agent settings](https://www.antigravity.google/docs/agent-settings).

## Other or unknown CLI kinds

Treat approval capability as `UNVERIFIED` until the current runtime visibly offers a native request and the exact supported path is confirmed. Permit read-only discovery and a manual user-owned next step. Do not borrow Codex, Claude, or Agy behavior or add unverified launch flags.
