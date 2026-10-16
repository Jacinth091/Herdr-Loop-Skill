# Unproven and Gated Paths

This file records what the Herdr Role Pack does **not** claim. It holds the same line the role
skills hold their agents to: passing tests prove the mocked contracts, never a live end-to-end
path. A path listed here is not disproven — it is simply not demonstrated, so do not present it to
a human operator as working.

Update this file whenever a gate is cleared, a blocker is resolved, or a new gap is found.

| Path | State | What would prove it |
| --- | --- | --- |
| Native CLI approval (Claude, Codex, Agy) | **UNVERIFIED.** A read-only probe observed each CLI's approval-prompt surface, but no provider-level allow/deny outcome was demonstrated and no agent or session was created. | One governed operation per CLI that reaches that CLI's own approval prompt and returns its explicit outcome, with the command and output captured as evidence. |
| OpenCode approval capability | **UNVERIFIED.** The installed CLI exposes no native approval-request surface, so `tools/access_probe.py` reports the capability as unverified rather than supported. | An OpenCode release offering a native approval request, plus a governed probe demonstrating it. |
| Ledger-composed receipts (`--compose-receipt`) | **No live datapoint.** Covered by mocked contracts only; never exercised by two named Herdr agents exchanging a real signal. | A live two-agent run where `attempt-ready` composes a receipt and the ledger validates it. |
| Reversible backend transfer (B35) | **BLOCKED.** Transfer will not release a writer claim, because external worker descendant quiescence cannot be proven from the available Herdr process view. | A process-identity check that conclusively shows no surviving descendant, or an upstream Herdr surface that reports it. |
| Windows SQLite live lifecycle gate | **Stale.** The recorded gate ran at commit `2dfed01c73420fe925354a254cfca35773051ca5` (manifest 2.8.2) against Herdr 0.9.3. HEAD has since moved and the installed CLI is newer. | Re-run the read-only gate at the current revision and record it in [SQLITE_WINDOWS_GATE.md](SQLITE_WINDOWS_GATE.md). |
| SQLite as the default backend | **Gated by design.** `manifest.json` keeps `current_session_authority: "markdown"` and `sqlite_pilot_is_live_orchestration: false`. Markdown stays authoritative and the ledger never switches backend silently. | An explicit human decision plus a passing live gate on both Windows and Linux. |
| Observed model and effort for Agy and OpenCode | **Launch-dependent.** `attempt-attach` reads `-m`/`--model` and Codex's `-c model_reasoning_effort=`, so without an explicit `--model` the observed values are empty and no expectation can be confirmed. | A launch that passes an explicit `--model`, observed and recorded. |

## Environment gaps

- **Installed Herdr is newer than the documented version.** The workflow documentation was authored
  against Herdr 0.9.3; the machine this was last checked on runs 0.9.5. Nothing in this repository
  pins the CLI version, so re-check `herdr --help` and `herdr --skill` before relying on a specific
  subcommand. The agent naming contract — a name follows the current pane occupant and is cleared
  when that agent exits, is released, or is replaced — was confirmed against 0.9.5's own skill
  reference, and `observe_herdr` probes the pane to tell a cleared name from a missing one.
- **CI proves contracts, not Herdr integration.** The suite runs offline with mocked Herdr
  responses. No CI job starts an agent, and a green run must never be read as live evidence.
