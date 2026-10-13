# Permission and access recovery

An access error is evidence to classify, not permission to broaden. First confirm the active mode, direct user/task authority, role, exact operation, target path and whether the operation actually requires Herdr. A standalone local task does not require `HERDR_ENV=1` or a working Herdr server. A Herdr operation requires the correct registered agent/pane and target.

## Decision order

1. Refuse operations outside the task, assigned workspace, role or explicit grant. A native tool approval cannot expand any of these boundaries.
2. Identify the failing boundary: CLI executable, provider sandbox/tool policy, Herdr server/IPC, assigned workspace, OS ACL/process token, or an external service. Use one bounded read-only probe when it can distinguish the cause.
3. For the same authorized in-scope command blocked only by the active harness's supported approval boundary, request approval through that AI's own execution tool. The tool's reviewer/user grants or denies it; this skill and Herdr cannot approve it. Continue only after the real decision and verify the postcondition.
4. Treat explicit deny as terminal for that operation. Unsupported or unknown approval capability is not a denial and is not permission; preserve the task and report the single actionable blocker.
5. Before repeating a command that may have created a workspace, agent, prompt, worktree, commit, test, or cleanup effect, reconcile the same target read-only. If the result remains ambiguous, preserve resources and stop.

Never request OS Administrator/root access as a routine fix, change ACLs/security settings, disable sandboxing, edit provider policy, switch shells/providers to bypass a deny, press approval keys in another pane, or spawn an unapproved agent.

## Probe facts and classifications

`tools/access_probe.py` is diagnostic only. It reports platform, optional explicitly supplied CLI kind/version, Herdr origin/server/agent/pane evidence, workspace scratch-write result, and typed blockers. `approval_capability: unverified` means this probe cannot observe the active harness tool or its current approval flow. A successful scratch write proves only that path is writable. The probe never requests or grants command approval.

| Evidence | Classification | Next action |
| --- | --- | --- |
| `HERDR_ENV` missing, pane mismatch, or named agent absent | `HERDR_ORIGIN_UNCONFIRMED` | Repair/confirm the actual Herdr assignment only if the requested operation needs Herdr. |
| Herdr executable missing | `HERDR_CLI_UNAVAILABLE` | Do not attempt Herdr control; standalone local work may continue if it does not need Herdr. |
| Server/socket/named pipe unreachable | `HERDR_TRANSPORT_DENIED` | Capture the exact boundary and sanitized error. Use native command approval only if the denial is demonstrably the harness boundary; otherwise preserve state for operator repair. |
| Current harness blocks an in-scope command and offers its own approval | `AGENT_SANDBOX_DENIED` / `APPROVAL_NEEDED` | Request the exact command using that harness tool and wait for its actual decision. |
| Provider/user rejects the request or an explicit deny rule matches | `DENIED` | Stop that operation. No retry or equivalent command through another route. |
| No visible approval tool, noninteractive mode, or capability not verified | `APPROVAL_UNAVAILABLE` / `UNVERIFIED` | Keep the task and give one user-owned next step; do not claim universal provider support. |
| OS ACL, process token, or WinError 5 denial | `OS_ACCESS_DENIED` | Do not seek admin or edit ACLs. Report the target and user-owned repair needed. |
| Prompt/command timed out after possible submission | `OUTCOME_UNCERTAIN` | Inspect same Herdr agent/pane plus correlated task artifact or Git postcondition; idle/status alone is not proof. |
| Cause cannot be isolated | `ACCESS_DENIED_UNCLASSIFIED` | Stop at the smallest failed operation and preserve the task. |
| Target is outside the task or role | `OUT_OF_SCOPE` | Do not request permission for it. Obtain a separate scope decision. |

Example diagnostic (supply identity only when actually observed):

```sh
python tools/access_probe.py --workspace <assigned-workspace> --agent <herdr-name> --pane <pane-id> --harness-kind codex
```

`--probe-write` creates and removes one temporary file inside `<workspace>/.herdr-loop`. `--skip-herdr` is filesystem-only troubleshooting and always leaves Herdr origin unconfirmed.

## Retry receipts

For governed Markdown runs, store one stable operation receipt in the existing `SESSION.md` `Agent prompt submission receipts` section. The binder/activator records a SHA-256-derived `operation_id`, target, task/attempt, status and short evidence; it never stores the prompt or raw output. `SUBMITTING` or `SUBMITTED` is not proof of delivery. On a later activation, the tool blocks replay until the Delegator/Lead records a read-only reconciliation as `CONFIRMED_APPLIED`, `CONFIRMED_NOT_APPLIED`, `DENIED`, or `OUTCOME_UNCERTAIN` with evidence. Only one retry is allowed after `CONFIRMED_NOT_APPLIED`.

For standalone work without a run ledger, keep recovery evidence with the task/report or final response; do not create a new permission database or state file. On ambiguous delivery, inspect the same recipient and task artifact before resending.

Never record secrets, raw prompts, full environment dumps, credential-bearing arguments, or unredacted transcripts. Do not infer `ROLE_READY`, `TASK_START`, test success, independent verification, or human acceptance from a command exit code.
