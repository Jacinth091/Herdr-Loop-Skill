#!/usr/bin/env python3
"""Explicitly send existing Herdr agents their assigned role prompts.

No agent launch, no Git/worktree manipulation, no unsafe permission bypass.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ALLOWED = {"manager", "delegator", "worker", "verifier", "critic"}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", type=Path, required=True, help="Directory holding ACTIVATION.json")
    p.add_argument("--roles", default="manager,delegator", help="Comma-separated roles to activate, or all")
    p.add_argument("--dry-run", action="store_true", help="Print prompts without invoking Herdr")
    args = p.parse_args(argv)
    try:
        run_dir = args.run_dir.expanduser().resolve(strict=True)
        manifest = json.loads((run_dir / "ACTIVATION.json").read_text(encoding="utf-8"))
        if manifest.get("generated_by") != "herdr-role-pack":
            raise ValueError("Activation manifest was not created by Herdr Role Pack")
        requested = ALLOWED if args.roles == "all" else set(args.roles.split(","))
        if not requested or not requested <= ALLOWED:
            raise ValueError("--roles must contain only manager,delegator,worker,verifier,critic or all")
        targets = [a for a in manifest["agents"] if a["role"] in requested]
        if not targets:
            raise ValueError("No agents match the requested roles")
        names = [a["name"] for a in targets]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate agent name in activation manifest")
        for entry in targets:
            if entry["role"] == "worker":
                worktree_text = entry.get("worktree", "")  # config field also holds a human-approved non-code isolated copy path
                if not worktree_text:
                    raise ValueError("Worker " + entry["name"] + " has no assigned workspace; Delegator must allocate one")
                worktree = Path(worktree_text).expanduser().resolve(strict=True)
                for file in (worktree / ".herdr-loop" / "ROLE.md", worktree / ".herdr-loop" / "TASK.md"):
                    if not file.is_file():
                        raise ValueError("Worker packet/brief not copied: " + str(file))
            if entry["role"] == "critic":
                print("NOTICE critic activation does not grant CRITIQUE_START or authority to edit/test")
            if entry["role"] == "verifier":
                # Verifiers must get their own approved brief and lock; delivery does not grant permission to test.
                print("NOTICE verifier activation does not grant a test lock or approve any commands")
        if args.dry_run:
            for a in targets:
                print(f"DRY RUN {a['name']} ({a['role']}, {a['kind']}): {a['prompt']}")
            return 0
        if os.environ.get("HERDR_ENV") != "1":
            raise ValueError("HERDR_ENV=1 required; run from an existing Herdr-managed pane")
        herdr = shutil.which("herdr")
        if not herdr:
            raise ValueError("herdr command not found")
        # Preflight existence of ALL selected agents before sending ANY role prompt.
        for a in targets:
            res = subprocess.run([herdr, "agent", "get", a["name"]], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=15, check=False)
            if res.returncode != 0:
                raise ValueError("Agent not available: " + a["name"] + ": " + (res.stderr or res.stdout).strip()[:250])
        for a in targets:
            res = subprocess.run([herdr, "agent", "prompt", a["name"], a["prompt"]],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, timeout=30, check=False)
            if res.returncode != 0:
                raise ValueError("Prompt failed for " + a["name"] + ": " + (res.stderr or res.stdout).strip()[:250])
            print("SENT", a["name"], "(prompt submitted, not proof of role activation or completion)")
        return 0
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print("ERROR", str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
