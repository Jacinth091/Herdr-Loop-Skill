# Human-Facing Task Presentation

Internal identifiers such as `T01` and `T01-A2` are stable **routing and audit** keys. They belong in `SESSION.md`, `PLAN.md`, task briefs, signal envelopes, filenames, Git mappings, and debug reports. They are not the default way to describe work to the human.

## Source of truth

1. Manager gives each approved slice a **specific descriptive title** and an **observable outcome**, not a label such as "T01 changes" or "Backend work".
2. Delegator copies that pair into the matching `SESSION.md` frontier row and task brief; never invent scope from IDs or worker models.
3. Show the user **title + actual status + why it matters/what changes**. Tie the status to observed report/commit/verification and keep content acceptance distinct from pending tests/CI.
4. When replaced workers or retries occur, keep their attempt IDs internal. The slice retains the same user-facing title; update its status or describe the real blocker.
5. When a title or outcome is absent, obtain it from the accepted plan before `TASK_START` or publishing a progress update. Do not use meaningless fallback labels in a user-facing update.
6. If the user specifically requests IDs, worktrees, panes, SHAs, or debugging details, show them with the one-to-one description mapping.

## Example status updates

**Improving session-expiry messages**  
In progress. The worker is updating the sign-in flow so expired sessions explain how to recover.

**Preventing duplicate renewal records**  
Testing. The change is integrated; independent verification is running against the pinned revision.

**Updating account validation errors**  
Implementation finished; integration review pending. Tests have not run yet.

Do not write `T01 completed` or present `REPORT_READY` as user-visible completion. Avoid routine per-minute status messages: share meaningful milestones, blockers, or requested progress.

## State translation

| Internal state | Human-facing status |
| --- | --- |
| ready | Queued |
| assigned / working | In progress |
| reported | Implementation finished; reviewing |
| integrated | Changes integrated; checks pending |
| verifying | Testing |
| verified | Tests verified; awaiting acceptance |
| repair-needed | Fixes needed |
| blocked | Blocked (explain cause/action) |
| content-accepted-verification-pending | Implementation approved; checks pending |
| accepted | Complete only if final acceptance gates actually passed |
| cancelled / cancellation-blocked | Cancelled / cancellation pending |

The Delegator may use `tools/progress_view.py --session <path>` to render a **read-only** view from the existing `SESSION.md`. It creates no second store and never verifies Git or Herdr itself. Treat its output as a projection of recorded evidence, not an independent proof that a task passed.
