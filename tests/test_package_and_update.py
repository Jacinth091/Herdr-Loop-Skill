#!/usr/bin/env python3
"""
Unit tests for tools/package.py, tools/update.py, and release integrity.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


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

    def test_package_rejects_version_mismatch(self):
        cmd = [sys.executable, str(ROOT / "tools" / "package.py"), "--version", "9.9.9"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
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

        # Inspect archive contents
        with zipfile.ZipFile(role_pack_zip, "r") as zf:
            names = zf.namelist()
            self.assertIn("manifest.json", names)
            self.assertIn("package.json", names)
            self.assertIn("README.md", names)
            self.assertIn("skills/herdr-engineering-loop/SKILL.md", names)
            self.assertNotIn(".git", [n.split("/")[0] for n in names])
            self.assertNotIn(".herdr-loop", [n.split("/")[0] for n in names])

    def test_update_archive_safety_detects_traversal(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            import update
            bad_zip = self.temp_dir / "evil.zip"
            with zipfile.ZipFile(bad_zip, "w") as zf:
                zf.writestr("../escaped.txt", "evil")

            errors = update.validate_archive_safety(bad_zip)
            self.assertTrue(any("traversal" in err.lower() for err in errors))
        finally:
            if str(ROOT / "tools") in sys.path:
                sys.path.remove(str(ROOT / "tools"))

    def test_update_dry_run_and_list_backups(self):
        cmd_list = [sys.executable, str(ROOT / "tools" / "update.py"), "--list-backups"]
        res_list = subprocess.run(cmd_list, capture_output=True, text=True)
        self.assertEqual(res_list.returncode, 0)

        cmd_dry = [sys.executable, str(ROOT / "tools" / "update.py"), "--dry-run"]
        res_dry = subprocess.run(cmd_dry, capture_output=True, text=True)
        self.assertEqual(res_dry.returncode, 0)

    def test_github_workflows_exist_and_reference_correct_files(self):
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

    def test_readme_quickstart_json_matches_binder_schema(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
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
        finally:
            if str(ROOT / "tools") in sys.path:
                sys.path.remove(str(ROOT / "tools"))


if __name__ == "__main__":
    unittest.main()
