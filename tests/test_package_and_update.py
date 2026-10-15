#!/usr/bin/env python3
"""
Unit tests for tools/package.py, tools/update.py, installer safety, and release integrity.
Covers deterministic packaging, preflight guards, checksum enforcement,
authenticated private release handling, and atomic rollback invariants.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
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

        version = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["version"]
        role_pack_zip = out_dir / f"herdr-role-pack-v{version}.zip"
        controller_zip = out_dir / f"herdr-controller-v{version}.zip"
        sums_file = out_dir / "SHA256SUMS"

        self.assertTrue(role_pack_zip.exists())
        self.assertTrue(controller_zip.exists())
        self.assertTrue(sums_file.exists())

        sums_content = sums_file.read_text(encoding="utf-8")
        self.assertIn(role_pack_zip.name, sums_content)
        self.assertIn(controller_zip.name, sums_content)

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
            self.assertIn("skills/herdr-lead/SKILL.md", names)
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

    # --- updater harness: a throwaway home, an in-memory release, a fake installer ---

    def isolate(self):
        """Point the updater at a throwaway home so no test touches real installs or backups."""
        self.dirs = {a: self.temp_dir / "home" / a for a in ("claude", "codex", "agy", "opencode")}
        for d in self.dirs.values():
            d.mkdir(parents=True)
        self.backups = self.temp_dir / "backups"
        for patch in (mock.patch.object(update, "get_agent_skill_dirs", return_value=self.dirs),
                      mock.patch.object(update, "BACKUP_ROOT", self.backups)):
            patch.start()
            self.addCleanup(patch.stop)

    def make_skill(self, agent, skill, body, managed=True, marker="MANAGED"):
        d = self.dirs[agent] / skill
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
        if managed:
            (d / update.MANAGED_MARKER).write_text(marker, encoding="utf-8")
        return d

    def fake_release(self, sums=None, *, good_sums=False):
        """In-memory release. `sums` is the SHA256SUMS text (None = asset absent)."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("bin/install.js", "// installer")
        archive = buf.getvalue()
        if good_sums:
            sums = f"{hashlib.sha256(archive).hexdigest()}  herdr-role-pack-v9.0.0.zip\n"
        files = {"herdr-role-pack-v9.0.0.zip": archive}
        if sums is not None:
            files["SHA256SUMS"] = sums.encode()
        release = {"tag_name": "v9.0.0", "assets": [{"name": n} for n in files]}

        def fake_download(asset, dest, token=None):
            Path(dest).write_bytes(files[asset["name"]])

        def fake_install(argv, check=False):
            # Behaves like 'install.js --force --agents ... --roles ...'
            roles = argv[argv.index("--roles") + 1].split(",") if "--roles" in argv else update.MANAGED_SKILLS
            for agent in argv[argv.index("--agents") + 1].split(","):
                for skill in roles:
                    self.make_skill(agent, skill, "v9")
            return subprocess.CompletedProcess(argv, 0)

        self.installer = mock.Mock(side_effect=fake_install)
        for patch in (mock.patch.object(update, "fetch_latest_release", return_value=release),
                      mock.patch.object(update, "download_asset", side_effect=fake_download),
                      mock.patch.object(update.subprocess, "run", self.installer),
                      mock.patch.object(update.shutil, "which", return_value="node")):
            patch.start()
            self.addCleanup(patch.stop)

    def run_update(self, *argv):
        with mock.patch.object(sys, "argv", ["update.py", *argv]):
            return update.main()

    # --- version awareness ---

    def test_parse_version(self):
        self.assertEqual(update.parse_version("Herdr Role Pack v2.8.0"), (2, 8, 0))
        self.assertEqual(update.parse_version("Herdr Role Pack v2.8"), (2, 8, 0))
        self.assertEqual(update.parse_version("v10.2.3"), (10, 2, 3))
        self.assertIsNone(update.parse_version("MANAGED"))

    def test_update_skips_when_current_and_force_reinstalls(self):
        self.isolate()
        self.make_skill("codex", "herdr-manager", "v9", marker="Herdr Role Pack v9.0.0")
        self.fake_release(good_sums=True)
        self.assertEqual(self.run_update("--check"), 0)
        self.assertEqual(self.run_update(), 0)
        self.installer.assert_not_called()
        self.assertFalse(self.backups.exists())

        self.assertEqual(self.run_update("--force"), 0)
        self.installer.assert_called_once()

    def test_update_offers_newer_release_over_older_install(self):
        self.isolate()
        self.make_skill("codex", "herdr-manager", "v8", marker="Herdr Role Pack v8.9.9")
        self.make_skill("codex", "herdr-worker", "v9", marker="Herdr Role Pack v9.0.0")
        self.fake_release(good_sums=True)
        self.assertEqual(self.run_update(), 0)
        self.installer.assert_called_once()

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_node_installer_target_dir_installs_and_uninstalls_anywhere(self):
        target = self.temp_dir / "some-other-ai" / "skills"
        node = ["node", str(ROOT / "bin" / "install.js"), "--target-dir", str(target)]
        subprocess.run(node + ["--roles", "controller,lead"], check=True, capture_output=True)
        self.assertEqual(sorted(p.name for p in target.iterdir()),
                         ["herdr-delegator", "herdr-engineering-loop", "herdr-lead", "herdr-manager"])
        foreign = target / "herdr-worker"
        foreign.mkdir()
        (foreign / "mine.txt").write_text("not the pack's", encoding="utf-8")
        res = subprocess.run(node + ["--roles", "worker"], capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual((foreign / "mine.txt").read_text(encoding="utf-8"), "not the pack's")
        subprocess.run(node + ["--uninstall"], check=True, capture_output=True)
        self.assertEqual([p.name for p in target.iterdir()], ["herdr-worker"])
        res = subprocess.run(node + ["--agents", "claude"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_node_installer_target_dir_validation_fails_fast(self):
        # Missing argument
        res = subprocess.run(["node", str(ROOT / "bin" / "install.js"), "--target-dir"],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        # Next token is another flag
        res = subprocess.run(["node", str(ROOT / "bin" / "install.js"), "--target-dir", "--dry-run"],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertIn("requires a valid absolute directory path", res.stderr)
        # Relative path
        res = subprocess.run(["node", str(ROOT / "bin" / "install.js"), "--target-dir", "relative/path"],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertIn("must be an absolute path", res.stderr)

    def test_ai_install_guide_only_uses_real_installer_flags(self):
        guide = (ROOT / "INSTALL-FOR-AI.md").read_text(encoding="utf-8")
        installer = (ROOT / "bin" / "install.js").read_text(encoding="utf-8")
        # Installer commands are the npx lines and the option tables; other flags belong to herdr/node.
        installer_lines = "\n".join(l for l in guide.splitlines() if "npx " in l or l.startswith("|"))
        flags = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", installer_lines))
        self.assertTrue({"--target-dir", "--dry-run", "--force", "--agents", "--uninstall"} <= flags, flags)
        for flag in flags:
            self.assertTrue(f"'{flag}'" in installer, f"INSTALL-FOR-AI.md uses {flag}, which bin/install.js does not accept")
        for role in re.findall(r"`(controller,[a-z,]+)`", guide):
            for name in role.split(","):
                self.assertIn(f"{name}:", installer.replace("'", ""), name)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_node_installer_marks_manifest_version(self):
        home = self.temp_dir / "home"
        home.mkdir()
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
        subprocess.run(["node", str(ROOT / "bin" / "install.js"), "--agents", "claude", "--roles", "manager"],
                       check=True, capture_output=True, env=env)
        version = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["version"]
        marker = home / ".claude" / "skills" / "herdr-manager" / update.MANAGED_MARKER
        self.assertEqual(marker.read_text(encoding="utf-8").strip(), f"Herdr Role Pack v{version}")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_node_installer_supports_opencode_agent(self):
        # OpenCode loads user-level skills from ~/.config/opencode/skills.
        home = self.temp_dir / "home"
        home.mkdir()
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
        result = subprocess.run(
            ["node", str(ROOT / "bin" / "install.js"), "--agents", "opencode", "--roles", "controller,worker", "--force"],
            check=True, capture_output=True, text=True, env=env)
        self.assertIn("INSTALLED", result.stdout)
        base = home / ".config" / "opencode" / "skills"
        self.assertTrue((base / "herdr-engineering-loop" / "SKILL.md").is_file())
        self.assertTrue((base / "herdr-worker" / update.MANAGED_MARKER).is_file())
        # The other agent folders must not be touched by a scoped install.
        self.assertFalse((home / ".claude").exists())
        self.assertFalse((home / ".agents").exists())

    def test_updater_knows_opencode_skill_dir(self):
        self.assertEqual(update.get_agent_skill_dirs()["opencode"], Path.home() / ".config" / "opencode" / "skills")

    # --- integrity gates (AUTH-02) ---

    def test_update_refuses_release_without_checksums(self):
        self.isolate()
        self.make_skill("codex", "herdr-manager", "v1")
        self.fake_release(None)
        self.assertEqual(self.run_update(), 1)
        self.installer.assert_not_called()
        self.assertFalse(self.backups.exists())

    def test_update_refuses_missing_entry_or_mismatched_digest(self):
        self.isolate()
        self.make_skill("codex", "herdr-manager", "v1")
        for sums in (f"{'a' * 64}  other.zip\n",
                     f"{'a' * 64}  herdr-role-pack-v9.0.0.zip\n",
                     f"{'a' * 64}  x-herdr-role-pack-v9.0.0.zip.bak\n",
                     "not a checksum line\n"):
            self.fake_release(sums)
            self.assertEqual(self.run_update(), 1, sums)
            self.installer.assert_not_called()
        self.assertEqual((self.dirs["codex"] / "herdr-manager" / "SKILL.md").read_text(), "v1")

    def test_checksum_parsing_is_exact_and_rejects_conflicts(self):
        parsed = update.parse_checksums_file(
            f"# comment\n{'e' * 64}  herdr-role-pack-v2.8.0.zip\n{'A' * 64} *herdr-controller-v2.8.0.zip\n"
        )
        self.assertEqual(parsed, {"herdr-role-pack-v2.8.0.zip": "e" * 64, "herdr-controller-v2.8.0.zip": "a" * 64})
        with self.assertRaisesRegex(ValueError, "conflicting"):
            update.parse_checksums_file(f"{'a' * 64}  x.zip\n{'b' * 64}  x.zip\n")

    # --- update/rollback transaction (AUTH-01, FLOW-04) ---

    def test_update_then_rollback_restores_old_and_removes_new_slots(self):
        self.isolate()
        self.make_skill("codex", "herdr-manager", "v1")
        self.fake_release(good_sums=True)

        self.assertEqual(self.run_update(), 0)
        argv = self.installer.call_args.args[0]
        self.assertEqual(argv[argv.index("--agents") + 1], "codex")  # never spreads to claude/agy
        self.assertEqual(argv[argv.index("--roles") + 1], "herdr-manager")  # only updates owned roles
        self.assertEqual(list(self.dirs["claude"].iterdir()), [])
        self.assertEqual((self.dirs["codex"] / "herdr-manager" / "SKILL.md").read_text(), "v9")

        # Simulate an update that added a new slot to verify rollback removes newly installed slots
        self.make_skill("codex", "herdr-critic", "v9")
        latest_backup = sorted(self.backups.iterdir(), reverse=True)[0]
        manifest = update.read_manifest(latest_backup)
        manifest["installed"] = {key: update.hash_tree(path) for key, path in update.managed_slots().items()}
        update.write_manifest(latest_backup, manifest)
        self.assertTrue((self.dirs["codex"] / "herdr-critic").exists())

        self.assertEqual(self.run_update("--rollback", "latest"), 0)
        self.assertEqual((self.dirs["codex"] / "herdr-manager" / "SKILL.md").read_text(), "v1")
        self.assertFalse((self.dirs["codex"] / "herdr-critic").exists())
        # The pre-rollback snapshot is now 'latest', so the rollback itself can be undone.
        self.assertEqual(len(list(self.backups.iterdir())), 2)

    def test_rollback_refuses_when_original_backup_file_is_missing(self):
        self.isolate()
        self.make_skill("codex", "herdr-manager", "v1")
        backup = update.backup_managed_skills()
        # Delete original backup file to simulate missing backup source
        shutil.rmtree(backup / "codex" / "herdr-manager")
        with self.assertRaisesRegex(RuntimeError, "REFUSE: backup copy .* is missing"):
            update.restore_backup(backup)
        # Original skill was NOT deleted or touched
        self.assertEqual((self.dirs["codex"] / "herdr-manager" / "SKILL.md").read_text(), "v1")

    def test_update_selective_roles_does_not_widen_installations(self):
        self.isolate()
        self.make_skill("codex", "herdr-manager", "v1")
        self.make_skill("claude", "herdr-lead", "v1")
        self.fake_release(good_sums=True)

        self.assertEqual(self.run_update(), 0)
        calls = [c.args[0] for c in self.installer.call_args_list]
        self.assertEqual(len(calls), 2)
        by_agent = {c[c.index("--agents") + 1]: c[c.index("--roles") + 1] for c in calls}
        self.assertEqual(by_agent["codex"], "herdr-manager")
        self.assertEqual(by_agent["claude"], "herdr-lead")
        # Ensure agy was never touched
        self.assertEqual(list(self.dirs["agy"].iterdir()), [])

    def test_rollback_latest_ignores_empty_node_backup_and_finds_valid_manifest(self):
        self.isolate()
        self.make_skill("codex", "herdr-manager", "v1")
        py_backup = update.backup_managed_skills()
        # Create a newer node backup without backup_manifest.json
        node_backup = self.backups / "install-backup-9999999999999"
        node_backup.mkdir()
        (node_backup / "some_file.txt").write_text("node backup data")

        # Edit skill
        (self.dirs["codex"] / "herdr-manager" / "SKILL.md").write_text("v2", encoding="utf-8")
        # Rollback latest should pick py_backup, NOT the newer node_backup
        valid = update.get_valid_backups()
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0][0], py_backup)

        self.assertEqual(self.run_update("--rollback", "latest"), 0)
        self.assertEqual((self.dirs["codex"] / "herdr-manager" / "SKILL.md").read_text(), "v1")

    def test_rollback_latest_fails_closed_when_no_valid_backups(self):
        self.isolate()
        self.backups.mkdir(parents=True, exist_ok=True)
        node_backup = self.backups / "install-backup-12345"
        node_backup.mkdir()
        self.assertEqual(self.run_update("--rollback", "latest"), 1)

    def test_rollback_refuses_slot_edited_after_update(self):
        self.isolate()
        self.make_skill("codex", "herdr-manager", "v1")
        self.make_skill("codex", "herdr-worker", "v1")
        self.fake_release(good_sums=True)
        self.assertEqual(self.run_update(), 0)
        edited = self.dirs["codex"] / "herdr-manager" / "SKILL.md"
        edited.write_text("v9 + my local notes", encoding="utf-8")

        self.assertEqual(self.run_update("--rollback", "latest"), 1)
        self.assertEqual(edited.read_text(), "v9 + my local notes")
        self.assertEqual((self.dirs["codex"] / "herdr-worker" / "SKILL.md").read_text(), "v9")  # nothing touched
        self.assertEqual(len(list(self.backups.iterdir())), 1)  # no snapshot on refusal

    def test_rollback_refuses_unmanaged_destination(self):
        self.isolate()
        self.make_skill("codex", "herdr-manager", "v1")
        backup = update.backup_managed_skills()
        shutil.rmtree(self.dirs["codex"] / "herdr-manager")
        user_file = self.make_skill("codex", "herdr-manager", "someone else's skill", managed=False) / "SKILL.md"

        with self.assertRaisesRegex(RuntimeError, "unmanaged"):
            update.restore_backup(backup)
        self.assertEqual(user_file.read_text(), "someone else's skill")

    def test_rollback_refuses_tampered_backup(self):
        self.isolate()
        self.make_skill("codex", "herdr-manager", "v1")
        backup = update.backup_managed_skills()
        (backup / "codex" / "herdr-manager" / "SKILL.md").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "does not match its manifest"):
            update.restore_backup(backup)
        self.assertEqual((self.dirs["codex"] / "herdr-manager" / "SKILL.md").read_text(), "v1")

    def test_updater_fails_fast_when_node_is_missing(self):
        self.isolate()
        self.make_skill("codex", "herdr-manager", "v1")
        self.fake_release(good_sums=True)
        with mock.patch.object(update.shutil, "which", return_value=None):
            self.assertEqual(self.run_update(), 1)
        self.installer.assert_not_called()
        self.assertFalse(self.backups.exists())

    # --- private release access (INT-01) ---

    def test_private_release_unauthenticated_returns_auth_required(self):
        for code in (401, 404):
            err = urllib.error.HTTPError(update.GITHUB_API_URL, code, "x", {}, None)
            with mock.patch.object(update._OPENER, "open", side_effect=err):
                with self.assertRaisesRegex(RuntimeError, "AUTH_REQUIRED"):
                    update.fetch_latest_release(token="")
        err = urllib.error.HTTPError(update.GITHUB_API_URL, 404, "x", {}, None)
        with mock.patch.object(update._OPENER, "open", side_effect=err):
            with self.assertRaisesRegex(RuntimeError, "ACCESS_DENIED"):
                update.fetch_latest_release(token="t")

    def test_token_is_not_forwarded_to_another_host_on_redirect(self):
        handler = update._DropAuthOnRedirect()
        req = urllib.request.Request("https://api.github.com/a", headers={"Authorization": "Bearer t"})
        cdn = handler.redirect_request(req, None, 302, "Found", {}, "https://objects.githubusercontent.com/b")
        self.assertIsNone(cdn.get_header("Authorization"))
        same = handler.redirect_request(req, None, 302, "Found", {}, "https://api.github.com/c")
        self.assertEqual(same.get_header("Authorization"), "Bearer t")

    def test_update_dry_run_and_list_backups(self):
        self.isolate()
        self.make_skill("codex", "herdr-manager", "v1")
        self.assertEqual(self.run_update("--list-backups"), 0)
        self.fake_release(good_sums=True)
        self.assertEqual(self.run_update("--dry-run"), 0)
        self.installer.assert_not_called()

    # --- Node installer, real process (FLOW-04) ---

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_node_installer_fresh_install_failure_removes_new_slots(self):
        home = self.temp_dir / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".agents").mkdir()
        (home / ".agents" / "skills").write_text("a file where codex's skills dir should be")
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
        res = subprocess.run(["node", str(ROOT / "bin" / "install.js"), "--agents", "claude,codex"],
                             capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("Rollback completed.", res.stderr)
        self.assertEqual(list((home / ".claude" / "skills").iterdir()), [])

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
