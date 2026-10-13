#!/usr/bin/env python3
"""Resolve a run's selected ledger and report transfer blockers."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

SELECTOR_FILE = "backend.active.json"
VALID_BACKENDS = ("sqlite", "markdown")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@contextmanager
def run_lock(run_dir):
    path = run_dir / "backend.lock"
    token = f"{os.getpid()}:{os.urandom(8).hex()}"
    try:
        with path.open("x", encoding="ascii") as output:
            output.write(token)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise RuntimeError("BACKEND_LOCK_HELD: inspect the run before retrying") from exc
    try:
        yield
    finally:
        if path.read_text(encoding="ascii") == token:
            path.unlink()


def read_selector(run_dir):
    path = run_dir / SELECTOR_FILE
    if path.is_symlink():
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: selector is a symlink")
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: selector is unreadable or invalid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: selector must be an object")
    return data


def get_active_backend(run_dir):
    if run_dir.is_symlink():
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: run directory is a symlink")
    selector = read_selector(run_dir)
    session = run_dir / "SESSION.md"
    db = run_dir / "ledger.sqlite"
    if session.is_symlink() or db.is_symlink():
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: canonical ledger is a symlink")
    if selector is not None:
        backend = selector.get("selected_backend")
        if (backend not in VALID_BACKENDS or selector.get("run_id") != run_dir.name
                or type(selector.get("epoch")) is not int or selector["epoch"] < 1
                or selector.get("schema_version") != 3
                or selector.get("transfer_receipt_id") is not None):
            raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: invalid selector identity, epoch, or format")
        selected = db if backend == "sqlite" else session
        other = session if backend == "sqlite" else db
        if not selected.is_file() or other.exists():
            raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: selected ledger missing or competing ledger exists")
        if backend == "sqlite":
            try:
                with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
                    version = conn.execute("PRAGMA user_version").fetchone()[0]
                    row = conn.execute("SELECT id FROM run LIMIT 1").fetchone()
                if version != 3 or not row or row[0] != selector["run_id"]:
                    raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: SQLite identity or schema differs")
            except sqlite3.DatabaseError as exc:
                raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: SQLite ledger unreadable") from exc
        return backend, selector
    if session.exists() and db.exists():
        raise RuntimeError("BACKEND_CONFLICT: both canonical ledgers exist without a selector")
    if session.exists():
        return "markdown", None
    if db.exists():
        return "sqlite", None  # Existing offline pilot; binding must not promote it.
    return None, None


def init_selector(run_dir, run_id, backend, epoch=1):
    if not RUN_ID.fullmatch(run_id) or backend not in VALID_BACKENDS or epoch != 1:
        raise ValueError("invalid new-run backend selection")
    path = run_dir / SELECTOR_FILE
    if path.exists() or path.is_symlink():
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: selector already exists")
    staged = run_dir / (SELECTOR_FILE + ".tmp." + str(os.getpid()))
    with staged.open("x", encoding="utf-8") as output:
        json.dump({"run_id": run_id, "selected_backend": backend, "epoch": epoch,
                   "schema_version": 3, "transfer_receipt_id": None}, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(staged, path)


def inspect_run(run_dir):
    backend, selector = get_active_backend(run_dir)
    state = "UNINITIALIZED"
    tasks = 0
    blockers = []
    if backend == "sqlite":
        try:
            with sqlite3.connect((run_dir / "ledger.sqlite").resolve().as_uri() + "?mode=ro", uri=True) as conn:
                row = conn.execute("SELECT state FROM run LIMIT 1").fetchone()
                state = row[0] if row else "UNKNOWN"
                tasks = conn.execute("SELECT count(*) FROM task").fetchone()[0]
                claims = conn.execute("SELECT count(*) FROM claim WHERE active=1").fetchone()[0]
                attempts = conn.execute("SELECT count(*) FROM attempt WHERE state IN ('RESERVED','OBSERVED','READY','STARTED','UNCERTAIN')").fetchone()[0]
                checks = conn.execute("SELECT count(*) FROM verification WHERE state IN ('STARTED','RUNNING','UNCERTAIN')").fetchone()[0]
            if claims or attempts or checks:
                blockers.append(f"active or uncertain ownership: claims={claims}, attempts={attempts}, checks={checks}")
        except (sqlite3.DatabaseError, TypeError) as exc:
            blockers.append("SQLite state unreadable: " + str(exc))
    elif backend == "markdown":
        state = "RECORDED_IN_SESSION"
        blockers.append("Markdown claims and handwritten fields require reconciliation")
    else:
        blockers.append("run has no canonical ledger")
    if backend == "sqlite" and selector is None:
        blockers.append("legacy SQLite pilot requires explicit upgrade and authorization audit")
    blockers.append("TRANSFER_NOT_IMPLEMENTED: evidence-preserving conversion and cutover are not verified")
    return {"run_id": selector["run_id"] if selector else run_dir.name,
            "active_backend": backend, "epoch": selector["epoch"] if selector else None,
            "run_state": state, "task_count": tasks, "transfer_eligible": False,
            "blockers": blockers}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect")
    switch = commands.add_parser("switch")
    switch.add_argument("--to", choices=VALID_BACKENDS, required=True)
    switch.add_argument("--dry-run", action="store_true")
    switch.add_argument("--approval-ref")
    args = parser.parse_args(argv)
    try:
        run_dir = args.run_dir.expanduser()
        if not run_dir.is_dir():
            raise RuntimeError("run directory is missing")
        info = inspect_run(run_dir)
        print(f"Run: {info['run_id']} | backend: {info['active_backend'] or 'none'} | epoch: {info['epoch']} | state: {info['run_state']} | tasks: {info['task_count']}")
        print("Transfer eligible: no")
        for blocker in info["blockers"]:
            print("- " + blocker)
        if args.command == "switch":
            print(f"Requested target: {args.to}")
            return 0 if args.dry_run else 2
        return 0
    except (OSError, RuntimeError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
