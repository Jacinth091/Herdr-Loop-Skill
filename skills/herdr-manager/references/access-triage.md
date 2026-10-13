# Access and permission recovery

An access error is evidence to classify, not permission to broaden. First identify your active mode, human/task authority, role, exact operation, and target. Standalone local work does not require Herdr registration or `herdr status server`; inspect Herdr origin only when the requested operation actually needs Herdr.

For the same authorized in-scope command blocked only by this CLI's supported approval boundary, request permission through this agent's own native execution tool and wait for the real decision. A prompt is a request, not a grant. Explicit deny is terminal. Unknown or unavailable approval stays a blocker. Never self-grant, route around a deny, switch shells/providers, auto-elevate, alter ACLs, disable sandboxing, or press approval keys in another pane.

| Evidence | Class | Action |
| --- | --- | --- |
| Herdr env/pane/agent not confirmed | `HERDR_ORIGIN_UNCONFIRMED` | Stop only Herdr-dependent operations; ordinary standalone local work can continue. |
| Herdr executable/server/IPC unavailable | `HERDR_CLI_UNAVAILABLE` / `HERDR_TRANSPORT_DENIED` | Report the exact boundary; do not assume an Administrator/ACL fix. |
| Harness blocks an in-scope command | `AGENT_SANDBOX_DENIED` | Request the exact command through the current harness if it offers native approval. |
| Native reviewer/user rejects or explicit deny matches | `DENIED` | Stop that operation; do not try an equivalent route. |
| Active approval path is absent or unverified | `APPROVAL_UNAVAILABLE` / `UNVERIFIED` | Preserve the task and state the single user-owned next step. |
| OS ACL/process token/WinError 5 blocks access | `OS_ACCESS_DENIED` | Do not seek admin or edit ACLs; report the target and user-owned repair. |
| Prompt/command may have acted before timeout | `OUTCOME_UNCERTAIN` | Inspect the same recipient and correlated task artifact/postcondition before any retry; idle status is not proof. |
| Operation exceeds task, path, or role | `OUT_OF_SCOPE` | Do not request permission for it. |

The local `tools/access_probe.py` may classify platform, explicit CLI kind/version, Herdr origin, and a narrow `.herdr-loop` write probe. It cannot see the active approval UI or grant permissions. `approval_capability: unverified` is not evidence that approval is unavailable. A local write probe is not proof of shell or Herdr access.

For governed Markdown runs, keep stable prompt operation IDs and sanitized state in `SESSION.md`; `SUBMITTING`/`SUBMITTED` is not delivery. Reconcile before marking `CONFIRMED_APPLIED` or `CONFIRMED_NOT_APPLIED`; only one retry follows proven absence. Standalone tasks have no run ledger: keep minimal evidence with the task/report and do not create a separate permission store.

Role boundaries still apply. A governed Worker never runs tests; standalone Worker may run only scoped, authorized self-checks and must label them as such. A Verifier needs `VERIFY_START` plus the lock; a Critic stays read-only; Manager/Delegator/Lead authority remains limited to the approved run.
