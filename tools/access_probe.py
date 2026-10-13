#!/usr/bin/env python3
"""Read-only Herdr origin probe; optional harmless write probe in workspace/.herdr-loop.

No elevation, ACL updates, sandbox changes, remote requests, or persistent files.
Python 3.8+ standard library. The probe never runs product tests.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run(argv):
    try:
        outcome = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=8)
        error = (outcome.stderr or "").strip().replace("\n", " ")[:200]
        return {"ok": outcome.returncode == 0, "exit": outcome.returncode, "error": error}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "exit": None, "error": str(exc)[:200]}


def inspect(workspace, agent=None, pane=None, write_probe=False, skip_herdr=False):
    result = {"workspace": str(workspace), "checks": {}, "blockers": []}
    root = workspace.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("--workspace must be a directory")
    if os.environ.get("HERDR_ENV") != "1" and not skip_herdr:
        result["blockers"].append("HERDR_ORIGIN_UNCONFIRMED")
    elif not skip_herdr:
        herdr = shutil.which("herdr")
        if not herdr:
            result["blockers"].append("HERDR_CLI_UNAVAILABLE")
        else:
            s = run([herdr, "status", "server"])
            result["checks"]["herdr_server"] = s
            if not s["ok"]:
                result["blockers"].append("HERDR_TRANSPORT_DENIED")
            if agent:
                a = run([herdr, "agent", "get", agent])
                result["checks"]["named_agent"] = a
                if not a["ok"]:
                    result["blockers"].append("HERDR_ORIGIN_UNCONFIRMED")
    observed = os.environ.get("HERDR_PANE_ID", "")
    if pane:
        result["checks"]["pane_matches_env"] = (observed == pane if observed else None)
        if observed and observed != pane:
            result["blockers"].append("HERDR_ORIGIN_UNCONFIRMED")
    git = run(["git", "-C", str(root), "rev-parse", "--show-toplevel"])
    result["checks"]["git_workspace"] = {"ok": git["ok"], "exit": git["exit"]}
    if write_probe:
        scratch = root / ".herdr-loop"
        if scratch.is_symlink() or not scratch.is_dir():
            result["checks"]["scratch_write"] = {"ok": False, "error": "missing or symlinked workspace-local .herdr-loop"}
            result["blockers"].append("WORKTREE_WRITE_DENIED")
        else:
            try:
                with tempfile.NamedTemporaryFile(dir=str(scratch), prefix=".herdr-probe-", delete=True) as f:
                    f.write(b"ready\n")
                    f.flush()
                result["checks"]["scratch_write"] = {"ok": True}
            except OSError as exc:
                result["checks"]["scratch_write"] = {"ok": False, "error": str(exc)[:200]}
                result["blockers"].append("WORKTREE_WRITE_DENIED")
    result["blockers"] = sorted(set(result["blockers"]))
    result["ready_for_role_handshake"] = len(result["blockers"]) == 0 and not skip_herdr
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", type=Path, required=True, help="Assigned worker workspace, never a parent folder")
    p.add_argument("--agent", help="Observed named Herdr agent, not role label")
    p.add_argument("--pane", help="Expected pane ID, compared to HERDR_PANE_ID when available")
    p.add_argument("--probe-write", action="store_true", help="Create/remove one temporary file ONLY in workspace/.herdr-loop")
    p.add_argument("--skip-herdr", action="store_true", help="Local-only filesystem troubleshooting outside Herdr; NOT proof of agent origin")
    args = p.parse_args(argv)
    try:
        result = inspect(args.workspace, args.agent, args.pane, args.probe_write, args.skip_herdr)
        print(json.dumps(result, indent=2))
        return 0 if result["ready_for_role_handshake"] else 2
    except (OSError, ValueError) as exc:
        print(json.dumps({"ready_for_role_handshake": False, "blockers": ["ACCESS_DENIED_UNCLASSIFIED"], "error": str(exc)[:200]}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
