#!/usr/bin/env python3
"""Render a readable user progress summary from a Markdown or SQLite run (read-only).

Uses the Delegator ledger as the only source; never queries Git, Herdr or CI.
Fails if task titles/outcomes have not been recorded, rather than inventing them.
"""
import argparse
import re
import sys
import subprocess
from pathlib import Path


STATE_LABELS = {
    "unstarted": "Not started",
    "planned": "Planned",
    "ready": "Queued",
    "assigned": "Assigned",
    "working": "In progress",
    "executing": "In progress",
    "reported": "Implementation finished; reviewing",
    "integrated": "Changes integrated; checks pending",
    "verifying": "Testing",
    "verified": "Verified; awaiting acceptance",
    "repair-needed": "Fixes needed",
    "blocked": "Blocked",
    "content-accepted-verification-pending": "Implementation approved; checks pending",
    "accepted": "Complete",
    "cleanup-blocked": "Cleanup needs attention",
    "cancelling": "Stopping work",
    "cancelled": "Cancelled",
    "cancellation-blocked": "Cancellation needs attention",
}
TASK_RE = re.compile(r"^T[0-9]{2,4}$")
PLACEHOLDERS = {"", "-", "tbd", "unknown", "unset", "none", "pending", "not recorded", "needs approved title"}


def cells(line):
    return [s.strip() for s in line.strip().strip("|").split("|")]


def get_frontier(content):
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        if line.strip().lower() == "## task frontier":
            table = []
            for current in lines[idx+1:]:
                if current.startswith("## "):
                    break
                if current.strip().startswith("|"):
                    table.append(cells(current))
                elif table and current.strip():
                    break
            if len(table) < 2:
                return [], None
            columns = [h.strip().lower() for h in table[0]]
            if not all(re.fullmatch(r"[: -]+", x) for x in table[1]):
                raise ValueError("Task frontier table has no Markdown header separator")
            return table[2:], columns
    raise ValueError("SESSION.md is missing ## Task frontier")


def header_index(headers, *options):
    for candidate in options:
        if candidate in headers:
            return headers.index(candidate)
    return None


def human_state(state):
    state = state.strip().lower().replace("_", "-")
    if re.search(r"\bci pending\b|\bverification pending\b", state) and "accepted" in state:
        return "Implementation approved; checks pending"
    # Internals such as SHAs and signal names must not appear in this label.
    for key in sorted(STATE_LABELS, key=len, reverse=True):
        if state == key or state.startswith(key + " ") or state.startswith(key + " (") or state.startswith(key + " /"):
            return STATE_LABELS[key]
    return "Status needs reconciliation"


def render(content, include_internal_ids=False):
    rows, headers = get_frontier(content)
    if headers is None or not rows:
        return "# Work progress\n\nNo work slices have been recorded yet.\n"
    task_idx = header_index(headers, "task", "task id")
    title_idx = header_index(headers, "user-facing title", "slice title")
    outcome_idx = header_index(headers, "expected outcome", "user-visible outcome")
    state_idx = header_index(headers, "state", "status")
    if None in (task_idx, title_idx, outcome_idx, state_idx):
        raise ValueError("Task frontier requires Task, User-facing title, Expected outcome, State columns")
    seen = set()
    entries = []
    for row in rows:
        if len(row) != len(headers):
            raise ValueError("Task frontier has inconsistent Markdown table columns")
        task = row[task_idx]
        if not TASK_RE.fullmatch(task):
            raise ValueError("Task frontier has invalid internal task keys")
        if task in seen:
            raise ValueError("Task frontier contains duplicate task keys")
        seen.add(task)
        title = row[title_idx].strip()
        outcome = row[outcome_idx].strip()
        if (title.lower() in PLACEHOLDERS or outcome.lower() in PLACEHOLDERS or
                "<" in title or ">" in title or "<" in outcome or ">" in outcome):
            raise ValueError("Task frontier has missing descriptive title/outcome; update it from the approved plan before publishing status")
        suffix = f" ({task})" if include_internal_ids else ""
        entries.append(f"- **{title}{suffix}**: {human_state(row[state_idx])}\n  {outcome}")
    return "# Work progress\n\n" + "\n".join(entries) + "\n\n_This reflects the recorded session ledger, not a live Git/Herdr verification._\n"


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "herdr-engineering-loop" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import run_backend


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument("--session", type=Path, help="Path to Delegator-owned SESSION.md")
    source.add_argument("--run-dir", type=Path, help="Path to a run directory (auto-resolves active backend)")
    p.add_argument("--include-internal-ids", action="store_true", help="Explicit diagnostic view only")
    args = p.parse_args(argv)
    try:
        if args.run_dir:
            run_dir = args.run_dir.resolve()
            backend, selector = run_backend.get_active_backend(run_dir)
            if backend == "sqlite":
                ledger = SCRIPTS_DIR / "herdr_ledger.py"
                command = [sys.executable, str(ledger), "--run-dir", str(run_dir), "status", "--human"]
                if args.include_internal_ids:
                    command.append("--ids")
                result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
                if result.returncode:
                    raise ValueError(result.stderr.strip() or "ledger status unavailable")
                print(result.stdout, end="")
                return 0
            elif backend == "markdown":
                session_path = run_dir / "SESSION.md"
                if not session_path.exists():
                    raise ValueError(f"SESSION.md missing in Markdown run {run_dir}")
                print(render(session_path.read_text(encoding="utf-8"), args.include_internal_ids), end="")
                return 0
            else:
                raise ValueError(f"Run in {run_dir} has no active backend or is uninitialized")
        if args.session:
            session_path = args.session.resolve()
            if session_path.parent.is_dir():
                backend, _ = run_backend.get_active_backend(session_path.parent)
                if backend == "sqlite":
                    raise ValueError(f"Cannot read archived/inactive SESSION.md: run '{session_path.parent.name}' active backend is sqlite")
            print(render(session_path.read_text(encoding="utf-8"), args.include_internal_ids), end="")
            return 0
    except (ValueError, OSError, subprocess.TimeoutExpired, RuntimeError) as e:
        print("ERROR: " + str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
