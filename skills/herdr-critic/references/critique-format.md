# Critic intake and report templates

These are reusable *shapes*, not permission to start critique without a Herdr `CRITIQUE_START` event. Never copy secrets, unrelated archive content or large source files into the Critic's view.

## Delegator-created `CRITIQUE-BRIEF.md`

```markdown
# Critique request <review-id>
Run: <run-id>
Plan revision: <accepted-number>
Fixed point: <full commit SHA(s) or exact plan identifier>
Scope: <specific plan sections, task IDs or changed paths>
Question: <what should be challenged>
Evidence paths: <read-only evidence files>
Non-goals: <unrelated work to ignore>
Signal recipient: <exact Herdr Delegator name>
``` 

## Critic-created `critiques/<review-id>.md`

```markdown
# Critique <review-id>
Run: <run-id>
Observed model: claude-sonnet-5-5
Plan revision: <number>
Inspected fixed point: <full SHA>
Scope: <narrow paths/tasks>
Verdict: FINDINGS | NO_BLOCKING_FINDINGS | INCOMPLETE | BLOCKED

## Findings
### C-01 <specific finding>
Severity: blocker | major | minor
Evidence: <path:line, diff hunk or SHA>
Impact: <consequence, not speculation>
Smallest correction: <actionable remedy>

## Gaps and assumptions
- <unverified dependencies or missing evidence>

Tests run: NOT RUN (critic role policy)
Handoff: CRITIQUE_READY <run> <review-id> <path> <fixed-point-sha>
```

An empty findings section is acceptable when the named fixed point has been fully reviewed, but is not an approval, a test result, or proof of correctness. The Delegator delivers the critique and the Manager decides whether anything changes the accepted plan. Never open follow-up tasks or prompt a Worker directly from Critic.
