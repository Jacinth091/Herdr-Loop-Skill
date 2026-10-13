"""Backend transfer: planning-checkpoint round trips, refusals, and crash recovery.

Drives run_backend.py through its CLI and through its functions (for crash injection).
Real Herdr lifecycle is not exercised here; see docs/SQLITE_WINDOWS_GATE.md.
"""
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIND = ROOT / "tools" / "role_bind.py"
BACKEND = ROOT / "skills" / "herdr-engineering-loop" / "scripts" / "run_backend.py"
CONFIG = ROOT / "examples" / "roles.example.json"

_spec = importlib.util.spec_from_file_location("run_backend", BACKEND)
run_backend = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_backend)


class Boom(Exception):
    """Injected crash; stands in for a process dying at a journal boundary."""


def crash_at(phase):
    def checkpoint(name):
        if name == phase:
            raise Boom(phase)
    return checkpoint


class BackendTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.config = json.loads(CONFIG.read_text(encoding="utf-8"))
        self.config["run_id"] = "transfer-run"
        self.config["state_backend"] = "markdown"
        self.run = self.repo / ".herdr-loop" / "transfer-run"
        self.approval_count = 0

    # --- helpers -----------------------------------------------------------
    def bind(self, backend=None):
        result = self.bind_result(backend)
        self.assertEqual(result.returncode, 0, result.stderr)

    def bind_result(self, backend=None, *extra):
        if backend is not None:
            self.config["state_backend"] = backend
        path = self.root / "roles.json"
        path.write_text(json.dumps(self.config), encoding="utf-8")
        return subprocess.run([sys.executable, str(BIND), "--config", str(path), "--repo", str(self.repo), *extra],
                              capture_output=True, text=True)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(BACKEND), "--run-dir", str(self.run), *args],
                              capture_output=True, text=True)

    def approval_for(self, target):
        """Human-authored approval: take the exact line a dry-run prints, write it to a file outside the run."""
        dry = self.cli("switch", "--to", target, "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        line = [l for l in dry.stdout.splitlines() if l.startswith("Backend transfer approval:")][0]
        self.approval_count += 1
        path = self.root / f"approval-{self.approval_count}.md"
        path.write_text("Approved by the human owner.\n" + line + "\n", encoding="utf-8")
        return path

    def selector(self):
        return json.loads((self.run / "backend.active.json").read_text(encoding="utf-8"))

    def snapshot(self):
        return sorted(str(p.relative_to(self.run)) for p in self.run.rglob("*"))

    def ledger(self):
        conn = sqlite3.connect(str(self.run / "ledger.sqlite"))
        try:
            return conn.execute("SELECT id, repo, max_workers, state, plan_sha, grant_ref FROM run").fetchall()
        finally:
            conn.close()

    # --- round trips --------------------------------------------------------
    def test_markdown_round_trip_restores_identical_bytes(self):
        self.bind("markdown")
        original = (self.run / "SESSION.md").read_bytes()
        self.assertEqual(self.cli("switch", "--to", "sqlite", "--approval-ref",
                                  str(self.approval_for("sqlite"))).returncode, 0)
        self.assertFalse((self.run / "SESSION.md").exists())
        self.assertEqual(self.selector()["selected_backend"], "sqlite")
        self.assertEqual(self.selector()["epoch"], 2)
        self.assertEqual(self.ledger()[0][3], "PLANNING")
        result = self.cli("switch", "--to", "markdown", "--approval-ref", str(self.approval_for("markdown")))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.run / "ledger.sqlite").exists())
        self.assertEqual((self.run / "SESSION.md").read_bytes(), original)
        self.assertEqual(self.selector()["epoch"], 3)

    def test_sqlite_round_trip_preserves_run_identity(self):
        self.bind("sqlite")
        before = self.ledger()
        self.assertEqual(self.cli("switch", "--to", "markdown", "--approval-ref",
                                  str(self.approval_for("markdown"))).returncode, 0)
        self.assertIn("Repo: `", (self.run / "SESSION.md").read_text(encoding="utf-8"))
        self.assertEqual(self.cli("switch", "--to", "sqlite", "--approval-ref",
                                  str(self.approval_for("sqlite")))
                         .returncode, 0)
        self.assertEqual(self.ledger(), before)
        self.assertEqual(self.selector()["planning_profile"]["max_active_workers"], 1)

    def test_switch_to_active_backend_is_a_noop(self):
        self.bind("markdown")
        before = self.snapshot()
        result = self.cli("switch", "--to", "markdown", "--dry-run")
        self.assertEqual(result.returncode, 0)
        self.assertIn("ALREADY_ACTIVE", result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_archive_keeps_hashed_source_snapshot(self):
        self.bind("markdown")
        source = (self.run / "SESSION.md").read_bytes()
        self.assertEqual(self.cli("switch", "--to", "sqlite", "--approval-ref",
                                  str(self.approval_for("sqlite"))).returncode, 0)
        receipts = list((self.run / "transfers").iterdir())
        self.assertEqual(len(receipts), 1)
        receipt = json.loads((receipts[0] / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["phase"], "COMPLETE")
        archived = receipts[0] / "archive" / "SESSION.md"
        self.assertEqual(archived.read_bytes(), source)
        self.assertEqual(run_backend.sha256_file(archived), receipt["archive"]["SESSION.md"])
        self.assertEqual(run_backend.sha256_file(receipts[0] / "retired" / "SESSION.md"), receipt["archive"]["SESSION.md"])

    # --- dry run, approval and fingerprint ----------------------------------
    def test_dry_run_writes_nothing(self):
        self.bind("markdown")
        before = self.snapshot()
        result = self.cli("switch", "--to", "sqlite", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Backend transfer approval:", result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_switch_without_approval_is_refused(self):
        self.bind("markdown")
        before = self.snapshot()
        result = self.cli("switch", "--to", "sqlite")
        self.assertEqual(result.returncode, 2)
        self.assertIn("APPROVAL_REQUIRED", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_wrong_direction_or_epoch_in_approval_is_rejected(self):
        self.bind("markdown")
        approval = self.approval_for("sqlite")
        before = self.snapshot()
        wrong = approval.read_text(encoding="utf-8").replace("to=sqlite", "to=markdown")
        bad = self.root / "wrong.md"
        bad.write_text(wrong, encoding="utf-8")
        result = self.cli("switch", "--to", "sqlite", "--approval-ref", str(bad))
        self.assertEqual(result.returncode, 2)
        self.assertIn("APPROVAL_MISMATCH", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_source_edit_after_dry_run_is_refused(self):
        self.bind("markdown")
        approval = self.approval_for("sqlite")
        session = self.run / "SESSION.md"
        session.write_text(session.read_text(encoding="utf-8") + "\nHuman note added later.\n", encoding="utf-8")
        before = session.read_bytes()
        result = self.cli("switch", "--to", "sqlite", "--approval-ref", str(approval))
        self.assertEqual(result.returncode, 2)
        self.assertIn("MARKDOWN_NOT_PRISTINE", result.stderr)
        self.assertEqual(session.read_bytes(), before)
        self.assertFalse((self.run / "ledger.sqlite").exists())

    def test_fingerprint_mismatch_reports_source_changed(self):
        self.bind("markdown")
        approval = self.approval_for("sqlite")
        text = approval.read_text(encoding="utf-8")
        tampered = self.root / "tampered.md"
        tampered.write_text(text.replace("source-fingerprint=" + text.split("source-fingerprint=")[1][:64],
                                         "source-fingerprint=" + "0" * 64), encoding="utf-8")
        result = self.cli("switch", "--to", "sqlite", "--approval-ref", str(tampered))
        self.assertEqual(result.returncode, 2)
        self.assertIn("SOURCE_CHANGED", result.stderr)
        self.assertTrue((self.run / "SESSION.md").exists())

    def test_multiple_approval_lines_are_rejected(self):
        self.bind("markdown")
        approval = self.approval_for("sqlite")
        doubled = self.root / "doubled.md"
        doubled.write_text(approval.read_text(encoding="utf-8") * 2, encoding="utf-8")
        result = self.cli("switch", "--to", "sqlite", "--approval-ref", str(doubled))
        self.assertIn("APPROVAL_INVALID", result.stderr)
        self.assertFalse((self.run / "ledger.sqlite").exists())

    # --- unsupported and tampered states -----------------------------------
    def test_hand_edited_markdown_is_not_transferable(self):
        self.bind("markdown")
        session = self.run / "SESSION.md"
        session.write_text(session.read_text(encoding="utf-8").replace(
            "Plan revision: NONE ACCEPTED", "Plan revision: R2 accepted by Manager"), encoding="utf-8")
        result = self.cli("switch", "--to", "sqlite", "--dry-run")
        self.assertEqual(result.returncode, 2)
        self.assertIn("MARKDOWN_NOT_PRISTINE", result.stdout)

    def test_activation_receipts_block_markdown_transfer(self):
        self.bind("markdown")
        session = self.run / "SESSION.md"
        session.write_text(session.read_text(encoding="utf-8").replace(
            "```json\n[]\n```", "```json\n[{\"agent\": \"x\"}]\n```"), encoding="utf-8")
        self.assertIn("MARKDOWN_NOT_PRISTINE", self.cli("switch", "--to", "sqlite", "--dry-run").stdout)

    def test_sqlite_with_task_or_approved_plan_is_not_transferable(self):
        self.bind("sqlite")
        conn = sqlite3.connect(str(self.run / "ledger.sqlite"))
        conn.execute("INSERT INTO task(id,title,outcome,deps,brief_path,brief_sha) VALUES('T01','t','o','[]','b.md','s')")
        conn.commit()
        conn.close()
        result = self.cli("switch", "--to", "markdown", "--dry-run")
        self.assertEqual(result.returncode, 2)
        self.assertIn("UNSUPPORTED_STATE: task has 1 row", result.stdout)

        conn = sqlite3.connect(str(self.run / "ledger.sqlite"))
        conn.execute("DELETE FROM task")
        conn.execute("UPDATE run SET plan_sha = ?", ("a" * 64,))
        conn.commit()
        conn.close()
        self.assertIn("approved plan or execution grant", self.cli("switch", "--to", "markdown", "--dry-run").stdout)

    def test_legacy_sqlite_pilot_without_selector_is_refused(self):
        self.bind("sqlite")
        (self.run / "backend.active.json").unlink()
        result = self.cli("switch", "--to", "markdown", "--dry-run")
        self.assertEqual(result.returncode, 2)
        self.assertIn("SQLITE_PILOT_NOT_LIVE", result.stdout)

    def test_inspect_reports_eligibility_and_blockers(self):
        self.bind("markdown")
        result = self.cli("inspect")
        self.assertEqual(result.returncode, 0)
        self.assertIn("eligible: yes", result.stdout)
        (self.run / "SESSION.md").write_text("garbage\n", encoding="utf-8")
        self.assertIn("eligible: no", self.cli("inspect").stdout)

    # --- crash injection and recovery ---------------------------------------
    def _load(self):
        return run_backend.get_active_backend(self.run)

    def _run_crash(self, target, phase):
        approval = self.approval_for(target)
        with self.assertRaises(Boom):
            run_backend.switch(self.run, target, approval, checkpoint=crash_at(phase))
        return approval

    def test_crash_before_source_retirement_rolls_back_on_recover(self):
        self.bind("markdown")
        original = (self.run / "SESSION.md").read_bytes()
        self._run_crash("sqlite", "PREPARED")
        inspected = self.cli("inspect")
        self.assertEqual(inspected.returncode, 0)
        self.assertIn("BACKEND_TRANSFER_IN_PROGRESS", inspected.stdout)
        self.assertIn("eligible: no", inspected.stdout)
        with self.assertRaisesRegex(RuntimeError, "BACKEND_TRANSFER_IN_PROGRESS"):
            self._load()
        self.assertEqual((self.run / "SESSION.md").read_bytes(), original)
        result = self.cli("recover")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ROLLED_BACK", result.stdout)
        self.assertEqual((self.run / "SESSION.md").read_bytes(), original)
        self.assertEqual(self.selector()["selected_backend"], "markdown")
        self.assertEqual(self.selector()["epoch"], 1)
        self.assertFalse((self.run / "ledger.sqlite").exists())
        # After rollback a fresh, identical-content approval completes the transfer.
        self.assertEqual(self.cli("switch", "--to", "sqlite", "--approval-ref", str(self.approval_for("sqlite"))).returncode,
                         2, "same fingerprint reuses the recorded transfer id and must be refused")

    def test_crash_after_source_retired_completes_forward_on_recover(self):
        self.bind("markdown")
        self._run_crash("sqlite", "SOURCE_RETIRED")
        self.assertFalse((self.run / "SESSION.md").exists())
        self.assertFalse((self.run / "ledger.sqlite").exists())
        result = self.cli("recover")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("COMPLETE", result.stdout)
        self.assertEqual(self.selector()["selected_backend"], "sqlite")
        self.assertEqual(self.selector()["epoch"], 2)
        self.assertEqual(self.ledger()[0][3], "PLANNING")
        self.assertFalse((self.run / "SESSION.md").exists())

    def test_crash_at_target_active_completes_on_recover(self):
        self.bind("sqlite")
        self._run_crash("markdown", "TARGET_ACTIVE")
        self.assertTrue((self.run / "SESSION.md").exists())
        self.assertEqual(self.cli("recover").returncode, 0)
        self.assertEqual(self.selector()["selected_backend"], "markdown")
        self.assertFalse((self.run / "ledger.sqlite").exists())

    def test_recover_refuses_stale_lock_until_explicitly_cleared(self):
        self.bind("markdown")
        self._run_crash("sqlite", "SOURCE_RETIRED")
        (self.run / "backend.lock").write_text("999999:stale", encoding="ascii")
        refused = self.cli("recover")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("BACKEND_LOCK_HELD", refused.stderr)
        self.assertTrue((self.run / "backend.lock").exists())
        self.assertEqual(self.cli("recover", "--clear-stale-lock").returncode, 0)
        self.assertFalse((self.run / "backend.lock").exists())

    def test_tampered_archive_blocks_forward_recovery(self):
        self.bind("markdown")
        self._run_crash("sqlite", "SOURCE_RETIRED")
        tdir = next((self.run / "transfers").iterdir())
        (tdir / "retired" / "SESSION.md").write_text("tampered\n", encoding="utf-8")
        result = self.cli("recover")
        self.assertEqual(result.returncode, 2)
        self.assertIn("RECOVERY_BLOCKED", result.stderr)
        self.assertFalse((self.run / "ledger.sqlite").exists())
        self.assertEqual(self.selector()["selected_backend"], "markdown")

    def test_transfer_fence_blocks_ledger_writers_during_recovery(self):
        self.bind("markdown")
        self._run_crash("sqlite", "PREPARED")
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "progress_view.py"),
                                 "--run-dir", str(self.run)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("BACKEND_TRANSFER_IN_PROGRESS", result.stderr)
        self.cli("recover")

    def test_rebind_follows_the_switched_backend(self):
        self.bind("markdown")
        self.assertEqual(self.cli("switch", "--to", "sqlite", "--approval-ref",
                                  str(self.approval_for("sqlite"))).returncode, 0)
        stale = self.bind_result("markdown")
        self.assertEqual(stale.returncode, 2)
        self.assertIn("BACKEND_SWITCH_REQUIRED", stale.stderr)
        fresh = self.bind_result("sqlite", "--force")
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self.assertEqual(self.selector()["epoch"], 2)
        self.assertFalse((self.run / "SESSION.md").exists())

    def test_recover_without_transfer_is_a_noop(self):
        self.bind("markdown")
        result = self.cli("recover")
        self.assertEqual(result.returncode, 0)
        self.assertIn("RECOVERY_NOT_NEEDED", result.stdout)

    def test_unknown_transfer_directory_blocks_the_run(self):
        self.bind("markdown")
        (self.run / "transfers").mkdir()
        (self.run / "transfers" / "not-a-transfer").mkdir()
        self.assertIn("BACKEND_INTEGRITY_BLOCKED", self.cli("inspect").stderr)


if __name__ == "__main__":
    unittest.main()
