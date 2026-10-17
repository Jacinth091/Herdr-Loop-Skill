#!/usr/bin/env python3
"""Explicitly send existing Herdr agents their assigned role prompts.

No agent launch, no Git/worktree manipulation, no unsafe permission bypass.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
SCRIPTS_DIR = TOOLS_DIR.parent / "skills" / "herdr-engineering-loop" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import access_probe
import run_backend
import herdr_ledger

ALLOWED = {"manager", "delegator", "lead", "worker", "verifier", "critic"}
RECEIPTS_START = "<!-- HERDR_ACTIVATION_RECEIPTS_START -->"
RECEIPTS_END = "<!-- HERDR_ACTIVATION_RECEIPTS_END -->"


def _read_receipts(session_path):
    if session_path.is_symlink() or not session_path.is_file():
        raise ValueError("Markdown run ledger is missing or symlinked; activation refused")
    text = session_path.read_text(encoding="utf-8")
    start = text.find(RECEIPTS_START)
    end = text.find(RECEIPTS_END)
    if start < 0 and end < 0:
        return text, [], None
    if start < 0 or end < start:
        raise ValueError("Activation receipt section is malformed; inspect SESSION.md before continuing")
    body_start = start + len(RECEIPTS_START)
    body = text[body_start:end].strip()
    match = re.fullmatch(r"```json\s*(.*?)\s*```", body, re.DOTALL)
    if not match:
        raise ValueError("Activation receipt JSON is malformed; inspect SESSION.md before continuing")
    try:
        receipts = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("Activation receipt JSON is malformed; inspect SESSION.md before continuing") from exc
    if not isinstance(receipts, list) or any(not isinstance(item, dict) or not item.get("operation_id") for item in receipts):
        raise ValueError("Activation receipt JSON must be a list of operation records")
    ids = [item["operation_id"] for item in receipts]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate activation operation IDs in SESSION.md; reconcile before continuing")
    return text, receipts, (start, end)


def _write_receipts(session_path, receipts):
    text, _, bounds = _read_receipts(session_path)
    newline = "\r\n" if "\r\n" in session_path.read_bytes().decode("utf-8") else "\n"
    block = "\n".join((
        RECEIPTS_START,
        "```json",
        json.dumps(receipts, indent=2, sort_keys=True),
        "```",
        RECEIPTS_END,
    ))
    if bounds:
        start, end = bounds
        updated = text[:start] + block + text[end + len(RECEIPTS_END):]
    else:
        updated = text.rstrip() + newline * 2 + block + newline
    if newline == "\r\n":
        updated = updated.replace("\n", "\r\n")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", dir=str(session_path.parent),
                prefix=".SESSION.activation.", suffix=".tmp", delete=False) as temp:
            temp_path = Path(temp.name)
            temp.write(updated)
        os.chmod(str(temp_path), stat.S_IMODE(session_path.stat().st_mode))
        os.replace(str(temp_path), str(session_path))
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _store_receipt(session_path, receipt):
    _, receipts, _ = _read_receipts(session_path)
    for index, previous in enumerate(receipts):
        if previous["operation_id"] == receipt["operation_id"]:
            history = list(previous.get("history", []))
            history.append({"status": previous.get("status", "UNKNOWN"),
                            "evidence": previous.get("evidence", "")})
            receipt["history"] = history
            receipts[index] = receipt
            break
    else:
        receipts.append(receipt)
    _write_receipts(session_path, receipts)


def _operation(entry, run_id):
    prompt_hash = hashlib.sha256(entry["prompt"].encode("utf-8")).hexdigest()
    identity = {
        "run_id": run_id,
        "task_id": entry.get("task_id", ""),
        "attempt_id": entry.get("attempt_id", ""),
        "role": entry["role"],
        "agent": entry["name"],
        "prompt_sha256": prompt_hash,
    }
    if entry.get("state_backend") == "sqlite":
        identity.update(backend_epoch=entry["backend_epoch"], observed_process=entry.get("_observed"))
    operation_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return operation_id, prompt_hash


def _receipt(entry, run_id, operation_id, prompt_hash, status, attempts=1, retry_count=0, evidence=""):
    return {
        "operation_id": operation_id,
        "run_id": run_id,
        "backend_epoch": entry.get("backend_epoch", 1),
        "task_id": entry.get("task_id", ""),
        "attempt_id": entry.get("attempt_id", ""),
        "role": entry["role"],
        "agent": entry["name"],
        "action": "agent_prompt",
        "target": entry["name"],
        "prompt_sha256": prompt_hash,
        "status": status,
        "attempts": attempts,
        "retry_count": retry_count,
        "evidence": evidence,
    }


def _dispatch_state(entry, run_id, receipts_by_id):
    operation_id, prompt_hash = _operation(entry, run_id)
    previous = receipts_by_id.get(operation_id)
    if previous is None:
        return "send", _receipt(entry, run_id, operation_id, prompt_hash,
                                 "SUBMITTING", evidence="Intent recorded before one prompt submission."), ""
    status = previous.get("status")
    if status == "CONFIRMED_APPLIED":
        return "skip", previous, ""
    if status == "CONFIRMED_NOT_APPLIED":
        attempts = int(previous.get("attempts", 1))
        retry_count = int(previous.get("retry_count", 0))
        if attempts >= 2 or retry_count >= 1:
            return "blocked", previous, "one authorized retry has already been used"
        return "send", _receipt(entry, run_id, operation_id, prompt_hash,
                                 "SUBMITTING", attempts + 1, retry_count + 1,
                                 "One retry authorized after recorded read-only reconciliation."), ""
    if status in ("SUBMITTING", "SUBMITTED"):
        uncertain = dict(previous)
        uncertain["status"] = "OUTCOME_UNCERTAIN"
        uncertain["evidence"] = "Submission may have reached the named agent; correlate its task receipt before replay."
        return "blocked", uncertain, "prompt submission is not proof of receipt; reconcile before retry"
    if status == "OUTCOME_UNCERTAIN":
        return "blocked", previous, "reconcile the same agent, task and artifact before retry"
    if status == "DENIED":
        return "blocked", previous, "this operation was denied; change authorization or environment before any new operation"
    return "blocked", previous, "unknown receipt state; inspect SESSION.md before continuing"


def _run_prompt(herdr, entry):
    prompt = entry["prompt"]
    if entry.get("_ack_envelope"):
        prompt += "\n" + entry["_ack_envelope"]
    return subprocess.run([herdr, "agent", "prompt", entry["name"], prompt],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", timeout=30, check=False)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", type=Path, required=True, help="Directory holding ACTIVATION.json")
    p.add_argument("--roles", default="manager,delegator,lead", help="Comma-separated roles to activate, or all (default: the run leaders)")
    p.add_argument("--dry-run", action="store_true", help="Print prompts without invoking Herdr or writing receipts")
    args = p.parse_args(argv)
    held_lock = None
    try:
        run_dir = args.run_dir.expanduser().resolve(strict=True)
        manifest = json.loads((run_dir / "ACTIVATION.json").read_text(encoding="utf-8"))
        if manifest.get("generated_by") != "herdr-role-pack":
            raise ValueError("Activation manifest was not created by Herdr Role Pack")
        if manifest.get("execution_mode", "governed") == "standalone":
            raise ValueError("Standalone assignment is delivered directly from ACTIVATE.md; managed activation requires a governed run and ledger")
        if not args.dry_run:
            lock = run_backend.run_lock(run_dir)
            lock.__enter__()
            held_lock = lock
            manifest = json.loads((run_dir / "ACTIVATION.json").read_text(encoding="utf-8"))
        if manifest.get("generated_by") != "herdr-role-pack":
            raise ValueError("Activation manifest was not created by Herdr Role Pack")
        backend, selector = run_backend.get_active_backend(run_dir)
        if manifest.get("state_backend", "markdown") != backend or manifest.get("backend_epoch", 1) != (selector["epoch"] if selector else 1):
            raise ValueError("BACKEND_INTEGRITY_BLOCKED: activation packet does not match selected backend and epoch")
        requested = ALLOWED if args.roles == "all" else set(args.roles.split(","))
        if not requested or not requested <= ALLOWED:
            raise ValueError("--roles must contain only manager,delegator,lead,worker,verifier,critic or all")
        targets = [a for a in manifest["agents"] if a["role"] in requested]
        if not targets:
            raise ValueError("No agents match the requested roles")
        names = [a["name"] for a in targets]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate agent name in activation manifest")
        for entry in targets:
            entry["backend_epoch"] = manifest.get("backend_epoch", 1)
            entry["state_backend"] = backend
            if entry["role"] == "worker":
                worktree_text = entry.get("worktree", "")
                if not worktree_text:
                    raise ValueError("Worker " + entry["name"] + " has no assigned workspace; Delegator must allocate one")
                worktree = Path(worktree_text).expanduser().resolve(strict=True)
                for file in (worktree / ".herdr-loop" / "ROLE.md", worktree / ".herdr-loop" / "TASK.md"):
                    if not file.is_file():
                        raise ValueError("Worker packet/brief not copied: " + str(file))
            if entry["role"] == "critic":
                print("NOTICE critic activation does not grant CRITIQUE_START or authority to edit/test")
            if entry["role"] == "verifier":
                print("NOTICE verifier activation does not grant a test lock or approve any commands")

        if args.dry_run:
            for entry in targets:
                op_id, _ = _operation(entry, manifest["run_id"])
                print(f"DRY RUN {entry['name']} ({entry['role']}, {entry['kind']}) operation_id={op_id}: {entry['prompt']}")
            return 0
        if os.environ.get("HERDR_ENV") != "1":
            raise ValueError("HERDR_ENV=1 required; run from an existing Herdr-managed pane")
        session_path = run_dir / "SESSION.md"
        read_receipts = (lambda: herdr_ledger.activation_receipts(run_dir)) if backend == "sqlite" else (lambda: _read_receipts(session_path)[1])
        store_receipt = (lambda receipt: herdr_ledger.store_activation(run_dir, receipt)) if backend == "sqlite" else (lambda receipt: _store_receipt(session_path, receipt))
        receipts = read_receipts()
        if backend == "markdown" and not receipts and RECEIPTS_START not in session_path.read_text(encoding="utf-8"):
            # Older Markdown runs get the receipt section on their first attempted submission.
            _write_receipts(session_path, [])
        herdr = shutil.which("herdr")
        if not herdr:
            raise ValueError("HERDR_CLI_UNAVAILABLE: herdr command not found")

        preflight = []
        for entry in targets:
            try:
                result = subprocess.run([herdr, "agent", "get", entry["name"]], stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding="utf-8", timeout=15, check=False)
                preflight.append((entry["name"], result.returncode == 0,
                                  access_probe.sanitize_error(result.stderr or result.stdout)))
            except (OSError, subprocess.TimeoutExpired) as exc:
                message = getattr(exc, "strerror", None) or getattr(exc, "stderr", None) or type(exc).__name__
                preflight.append((entry["name"], False, access_probe.sanitize_error(message)))
        unavailable = [item for item in preflight if not item[1]]
        if unavailable:
            for name, ok, error in preflight:
                print("PREFLIGHT", name, "CONFIRMED" if ok else "BLOCKED", error)
            print("ERROR no role prompts were sent; all selected agents must pass read-only preflight", file=sys.stderr)
            return 2

        if backend == "sqlite":
            for entry in targets:
                agent = herdr_ledger.herdr_json("agent", "get", entry["name"])["agent"]
                entry["_observed"] = herdr_ledger.observe_herdr(entry["name"], agent["pane_id"],
                    entry.get("worktree") or manifest["repo"], entry["kind"])
        by_id = {item["operation_id"]: item for item in receipts}
        dispatch = []
        blocked = False
        for entry in targets:
            state, receipt, reason = _dispatch_state(entry, manifest["run_id"], by_id)
            if state == "blocked":
                if receipt.get("status") == "OUTCOME_UNCERTAIN" and by_id.get(receipt["operation_id"], {}).get("status") != "OUTCOME_UNCERTAIN":
                    store_receipt(receipt)
                    by_id[receipt["operation_id"]] = receipt
                print("BLOCKED", entry["name"], receipt.get("status"),
                      "operation_id=" + receipt["operation_id"], reason)
                blocked = True
            elif state == "skip":
                print("CONFIRMED_APPLIED", entry["name"], "operation_id=" + receipt["operation_id"])
            else:
                if backend == "sqlite":
                    receipt.update(epoch=manifest["backend_epoch"], observed=entry["_observed"])
                dispatch.append((entry, receipt))
        if blocked:
            print("ERROR no new prompts sent; reconcile blocked operation(s) in the selected ledger", file=sys.stderr)
            return 2

        for entry, receipt in dispatch:
            if backend == "sqlite":
                recipient = "human" if entry["role"] == "lead" else entry["signal_recipient"]
                entry["_ack_envelope"] = "\n".join((
                    f"ROLE_ACK operation={receipt['operation_id']} prompt_sha={receipt['prompt_sha256']} "
                    f"run={manifest['run_id']} epoch={manifest['backend_epoch']}",
                    "For SQLite SETUP, send the following exact acknowledgment line to the assigned recipient "
                    "after writing current readiness evidence; task/attempt ROLE_READY is a separate handshake:",
                    f"ROLE_READY {manifest['run_id']} {receipt['operation_id']} {entry['name']} "
                    f"{entry['_observed']['pane']} {recipient}"))
            store_receipt(receipt)
            try:
                result = _run_prompt(herdr, entry)
            except subprocess.TimeoutExpired as exc:
                output = access_probe.sanitize_error(getattr(exc, "stderr", None) or getattr(exc, "stdout", None))
                receipt["status"] = "OUTCOME_UNCERTAIN"
                receipt["evidence"] = "Herdr prompt call timed out; delivery may have occurred. " + output
                store_receipt(receipt)
                print("OUTCOME_UNCERTAIN", entry["name"], "operation_id=" + receipt["operation_id"], receipt["evidence"])
                break
            except OSError as exc:
                error = getattr(exc, "strerror", None) or type(exc).__name__
                receipt["status"] = "CONFIRMED_NOT_APPLIED"
                receipt["evidence"] = "Herdr process could not be started: " + access_probe.sanitize_error(error)
                store_receipt(receipt)
                print("CONFIRMED_NOT_APPLIED", entry["name"], "operation_id=" + receipt["operation_id"], receipt["evidence"])
                break

            if result.returncode == 0:
                receipt["status"] = "SUBMITTED"
                receipt["evidence"] = "Herdr returned 0; submission is not proof of recipient receipt."
                store_receipt(receipt)
                print("SUBMITTED", entry["name"], "(prompt submission is not proof of receipt)",
                      "operation_id=" + receipt["operation_id"])
                continue

            error = access_probe.sanitize_error(result.stderr or result.stdout)
            failure = access_probe.classify_failure(error, "agent_prompt")
            receipt["status"] = "DENIED" if failure == "DENIED" else "OUTCOME_UNCERTAIN"
            receipt["evidence"] = (failure + (": " + error if error else "; Herdr returned " + str(result.returncode)))
            store_receipt(receipt)
            print(receipt["status"], entry["name"], "operation_id=" + receipt["operation_id"], receipt["evidence"])
            break
        return 0 if all(item.get("status") in ("SUBMITTED", "CONFIRMED_APPLIED")
                        for item in read_receipts()
                        if item.get("operation_id") in {r["operation_id"] for _, r in dispatch}) else 2
    except (ValueError, KeyError, OSError, RuntimeError, herdr_ledger.LedgerError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        message = getattr(exc, "strerror", None) or str(exc)
        print("ERROR", access_probe.sanitize_error(message), file=sys.stderr)
        return 2
    finally:
        if held_lock is not None:
            held_lock.__exit__(None, None, None)


if __name__ == "__main__":
    sys.exit(main())
