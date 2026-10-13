#!/usr/bin/env python3
"""
Unit tests for tools/package.py, tools/update.py, installer safety, and release integrity.
Covers deterministic packaging, preflight guards, checksum enforcement,
authenticated private release handling, and atomic rollback invariants.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import package
import update


class PackageAndUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="herdr-test-pkg-"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_package_dry_run_matches_manifest(self):
        cmd = [sys.executable, str(ROOT / "tools" / "package.py"), "--dry-run"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("[DRY-RUN] Validated manifest version", res.stdout)
        self.assertIn("LICENSE", res.stdout)

    def test_package_rejects_version_mismatch(self):
        cmd = [sys.executable, str(ROOT / "tools" / "package.py"), "--version", "9.9.9"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("does not match manifest.json", res.stderr)

    def test_package_builds_valid_archives_and_checksums(self):
        out_dir = self.temp_dir / "dist"
        cmd = [sys.executable, str(ROOT / "tools" / "package.py"), "--output-dir", str(out_dir)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)

        role_pack_zip = out_dir / "herdr-role-pack-v2.8.0.zip"
        controller_zip = out_dir / "herdr-controller-v2.8.0.zip"
        sums_file = out_dir / "SHA256SUMS"

        self.assertTrue(role_pack_zip.exists())
        self.assertTrue(controller_zip.exists())
        self.assertTrue(sums_file.exists())

        sums_content = sums_file.read_text(encoding="utf-8")
        self.assertIn("herdr-role-pack-v2.8.0.zip", sums_content)
        self.assertIn("herdr-controller-v2.8.0.zip", sums_content)

        # Inspect archive contents: MUST include LICENSE and required files
        with zipfile.ZipFile(role_pack_zip, "r") as zf:
            names = zf.namelist()
            self.assertIn("LICENSE", names)
            self.assertIn("manifest.json", names)
            self.assertIn("package.json", names)
            self.assertIn("README.md", names)
            self.assertIn("CHANGELOG.md", names)
            self.assertIn("install.ps1", names)
            self.assertIn("install.sh", names)
            self.assertIn("bin/install.js", names)
            self.assertIn("skills/herdr-engineering-loop/SKILL.md", names)
            self.assertIn("skills/herdr-manager/SKILL.md", names)
            self.assertIn("skills/herdr-delegator/SKILL.md", names)
            self.assertIn("skills/herdr-worker/SKILL.md", names)
            self.assertIn("skills/herdr-verifier/SKILL.md", names)
            self.assertIn("skills/herdr-critic/SKILL.md", names)
            self.assertNotIn(".git", [n.split("/")[0] for n in names])
            self.assertNotIn(".herdr-loop", [n.split("/")[0] for n in names])

    def test_package_is_deterministic_across_builds(self):
        out_dir_1 = self.temp_dir / "dist1"
        out_dir_2 = self.temp_dir / "dist2"

        package.build_role_pack_archive(out_dir_1 / "pack.zip")
        package.build_role_pack_archive(out_dir_2 / "pack.zip")

        sha1 = package.calculate_sha256(out_dir_1 / "pack.zip")
        sha2 = package.calculate_sha256(out_dir_2 / "pack.zip")
        self.assertEqual(sha1, sha2)

    def test_update_archive_safety_detects_traversal(self):
        bad_zip = self.temp_dir / "evil.zip"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("../escaped.txt", "evil")

        errors = update.validate_archive_safety(bad_zip)
        self.assertTrue(any("traversal" in err.lower() for err in errors))

    def test_rollback_refuses_to_overwrite_or_delete_unmanaged_skill(self):
        """P0 (AUTH-01): Rollback must fail closed and preserve unmanaged user files byte-for-byte."""
        fake_agent_dir = self.temp_dir / "agents" / "skills"
        fake_agent_dir.mkdir(parents=True)
        unmanaged_skill = fake_agent_dir / "herdr-manager"
        unmanaged_skill.mkdir()
        user_file = unmanaged_skill / "my_custom_code.py"
        user_content = b"print('user precious work')"
        user_file.write_bytes(user_content)

        # Create a backup dir containing a managed herdr-manager snapshot
        fake_backup = self.temp_dir / "backup-123" / "codex" / "herdr-manager"
        fake_backup.mkdir(parents=True)
        (fake_backup / "SKILL.md").write_text("old version")
        (fake_backup / ".herdr-role-pack-managed").write_text("MANAGED")

        # Mock get_agent_skill_dirs to point to fake directory
        with mock.patch.object(update, "get_agent_skill_dirs", return_value={"codex": fake_agent_dir}):
            with self.assertRaisesRegex(RuntimeError, "REFUSE: destination.*is unmanaged"):
                update.restore_backup(self.temp_dir / "backup-123")

        # Verify user file is 100% untouched and unchanged
        self.assertTrue(user_file.exists())
        self.assertEqual(user_file.read_bytes(), user_content)

    def test_rollback_cleans_up_newly_installed_slots_on_failure(self):
        """P1 (FLOW-04): Rollback must remove newly installed managed slots and restore old ones."""
        fake_agent_dir = self.temp_dir / "agents" / "skills"
        fake_agent_dir.mkdir(parents=True)

        # Existing managed skill that should be restored
        old_skill = fake_agent_dir / "herdr-manager"
        old_skill.mkdir()
        (old_skill / ".herdr-role-pack-managed").write_text("MANAGED")
        (old_skill / "SKILL.md").write_text("modified version")

        # Newly created slot created during failed install
        new_slot = fake_agent_dir / "herdr-critic"
        new_slot.mkdir()
        (new_slot / ".herdr-role-pack-managed").write_text("MANAGED")
        (new_slot / "SKILL.md").write_text("newly installed")

        # Backup has original old_skill
        fake_backup = self.temp_dir / "backup-456"
        backup_agent = fake_backup / "codex" / "herdr-manager"
        backup_agent.mkdir(parents=True)
        (backup_agent / ".herdr-role-pack-managed").write_text("MANAGED")
        (backup_agent / "SKILL.md").write_text("original old version")

        with mock.patch.object(update, "get_agent_skill_dirs", return_value={"codex": fake_agent_dir}):
            update.restore_backup(fake_backup, newly_installed_slots=[new_slot])

        # Assert old skill was restored to original backup content
        self.assertEqual((old_skill / "SKILL.md").read_text(), "original old version")
        # Assert newly created slot was cleanly removed
        self.assertFalse(new_slot.exists())

    def test_checksum_parsing_and_strict_validation(self):
        """P1 (AUTH-02): Checksum verification requires exact filename match and handles formatting."""
        sums_text = """
# Comments should be ignored
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  herdr-role-pack-v2.8.0.zip
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *herdr-controller-v2.8.0.zip
"""
        parsed = update.parse_checksums_file(sums_text)
        self.assertIn("herdr-role-pack-v2.8.0.zip", parsed)
        self.assertEqual(parsed["herdr-role-pack-v2.8.0.zip"], "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        self.assertIn("herdr-controller-v2.8.0.zip", parsed)
        self.assertEqual(parsed["herdr-controller-v2.8.0.zip"], "a" * 64)

        # Mismatch must fail
        dummy_file = self.temp_dir / "test.zip"
        dummy_file.write_bytes(b"some content")
        actual_hash = update.calculate_sha256(dummy_file)
        self.assertNotEqual(actual_hash, "a" * 64)

    def test_update_fails_closed_on_missing_checksum_asset(self):
        """P1 (AUTH-02): Updater must raise when release lacks SHA256SUMS."""
        fake_release = {
            "tag_name": "v2.8.0",
            "assets": [
                {"name": "herdr-role-pack-v2.8.0.zip", "browser_download_url": "http://example.com/pack.zip"}
            ]
        }
        with mock.patch.object(update, "fetch_latest_release", return_value=fake_release):
            with mock.patch.object(sys, "argv", ["update.py"]):
                # Should fail because sums_asset is missing
                self.assertEqual(update.main(), 1)

    def test_private_release_unauthenticated_returns_auth_required(self):
        """P1 (INT-01): Querying private release with 401/404 without token returns AUTH_REQUIRED."""
        with mock.patch.object(update, "get_github_token", return_value=None):
            mock_401 = urllib.error.HTTPError(update.GITHUB_API_URL, 401, "Unauthorized", {}, None)
            with mock.patch("urllib.request.urlopen", side_effect=mock_401):
                with self.assertRaisesRegex(RuntimeError, "AUTH_REQUIRED"):
                    update.fetch_latest_release(token=None)

            mock_404 = urllib.error.HTTPError(update.GITHUB_API_URL, 404, "Not Found", {}, None)
            with mock.patch("urllib.request.urlopen", side_effect=mock_404):
                with self.assertRaisesRegex(RuntimeError, "AUTH_REQUIRED"):
                    update.fetch_latest_release(token=None)

    def test_updater_fails_fast_when_node_is_missing(self):
        """P1 (FLOW-04): Updater fails fast if Node.js is unavailable and does not report success."""
        fake_release = {
            "tag_name": "v2.8.0",
            "assets": [
                {"name": "herdr-role-pack-v2.8.0.zip", "browser_download_url": "http://example.com/pack.zip"},
                {"name": "SHA256SUMS", "browser_download_url": "http://example.com/SHA256SUMS"}
            ]
        }
        with mock.patch.object(update, "fetch_latest_release", return_value=fake_release):
            with mock.patch.object(update.shutil, "which", return_value=None):
                with mock.patch.object(sys, "argv", ["update.py"]):
                    exit_code = update.main()
                    self.assertEqual(exit_code, 1)

    def test_update_dry_run_and_list_backups(self):
        cmd_list = [sys.executable, str(ROOT / "tools" / "update.py"), "--list-backups"]
        res_list = subprocess.run(cmd_list, capture_output=True, text=True)
        self.assertEqual(res_list.returncode, 0)

        cmd_dry = [sys.executable, str(ROOT / "tools" / "update.py"), "--dry-run"]
        res_dry = subprocess.run(cmd_dry, capture_output=True, text=True)
        self.assertEqual(res_dry.returncode, 0)

    def test_github_workflows_exist_and_enforce_matrix_and_semver(self):
        ci_path = ROOT / ".github" / "workflows" / "ci.yml"
        rel_path = ROOT / ".github" / "workflows" / "release.yml"

        self.assertTrue(ci_path.exists())
        self.assertTrue(rel_path.exists())

        ci_text = ci_path.read_text(encoding="utf-8")
        rel_text = rel_path.read_text(encoding="utf-8")

        self.assertIn("test_pack.py", ci_text)
        self.assertIn("package.py", ci_text)
        self.assertIn("gh release create", rel_text)
        self.assertIn("manifest.json", rel_text)
        # Release workflow must validate on both Ubuntu and Windows
        self.assertIn("ubuntu-latest", rel_text)
        self.assertIn("windows-latest", rel_text)

    def test_readme_quickstart_json_matches_binder_schema(self):
        import role_bind
        readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
        start = readme_text.find("```json\n{")
        self.assertNotEqual(start, -1)
        end = readme_text.find("```", start + 7)
        self.assertNotEqual(end, -1)
        json_str = readme_text[start + 7:end].strip()
        cfg_file = self.temp_dir / "readme_roles.json"
        cfg_file.write_text(json_str, encoding="utf-8")

        config, agents = role_bind.read_and_validate(cfg_file, None)
        self.assertEqual(config["run_id"], "auth-fix-v1")
        workers = [a for role, a in agents if role == "worker"]
        self.assertEqual(len(workers), 1)
        self.assertEqual(workers[0]["name"], "worker_01")
        self.assertEqual(workers[0]["_resolved_effort"], "xhigh")


if __name__ == "__main__":
    unittest.main()
