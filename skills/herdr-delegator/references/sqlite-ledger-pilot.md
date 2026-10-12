# SQLite state pilot: future Delegator path, not live policy

The controller skill (`herdr-engineering-loop`) bundles an **experimental** offline CLI at `../herdr-engineering-loop/scripts/herdr_ledger.py` (relative to this `herdr-delegator` skill directory). Consult `../herdr-engineering-loop/references/sqlite-ledger-pilot.md` for exact limits and the isolated tracer.

**Do not use this pilot in an active governed Herdr session.** Continue updating the current `SESSION.md` for all existing runs. Never create `ledger.sqlite` alongside an authoritative `SESSION.md` or rewrite history. Any future migration must be a separate, explicit, validated change of state authority.

The intended future behavior is: one deterministic typed command records each task/attempt/report/review/verification transition; a compact query returns only the next relevant state. Manager still judges semantics. Herdr still owns live process truth, Git owns checkout/commit truth, signals are transport receipts, and humans still own privileged approvals. No hidden agents, no automatic permissions escalation and no skipped test gates in pursuit of token savings.
