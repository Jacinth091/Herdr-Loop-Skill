import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("access_probe_under_test", ROOT / "tools" / "access_probe.py")
access_probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(access_probe)


class AccessProbeTests(unittest.TestCase):
    def git_ok(self, argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "workspace\n", "")

    def test_reports_evidence_without_claiming_approval_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".herdr-loop").mkdir()

            def completed(argv, **kwargs):
                if argv[0] == "git":
                    return self.git_ok(argv)
                if argv[-1] == "--version":
                    name = Path(argv[0]).name
                    version = "codex-cli 1.2.3" if name == "codex" else "herdr 0.9.3"
                    return subprocess.CompletedProcess(argv, 0, version, "")
                return subprocess.CompletedProcess(argv, 0, "ready", "")

            with mock.patch.dict(os.environ, {"HERDR_ENV": "1", "HERDR_PANE_ID": "pane-1"}), \
                    mock.patch.object(access_probe.shutil, "which", side_effect=lambda name: name), \
                    mock.patch.object(access_probe.subprocess, "run", side_effect=completed):
                result = access_probe.inspect(workspace, "worker", "pane-1", True, False, "codex")

        self.assertTrue(result["ready_for_role_handshake"])
        self.assertTrue(result["herdr_origin"]["confirmed"])
        self.assertTrue(result["named_agent_confirmed"])
        self.assertTrue(result["server_reachable"])
        self.assertEqual(result["platform"], access_probe.sys.platform)
        self.assertEqual(result["harness_version"], "1.2.3")
        self.assertEqual(result["approval_capability"]["status"], "unverified")
        self.assertTrue(result["workspace_write_test"]["ok"])

    def test_missing_herdr_executable_is_distinct(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"HERDR_ENV": "1"}), \
                mock.patch.object(access_probe.shutil, "which", return_value=None), \
                mock.patch.object(access_probe.subprocess, "run", side_effect=self.git_ok):
            result = access_probe.inspect(Path(tmp))
        self.assertIn("HERDR_CLI_UNAVAILABLE", result["blockers"])
        self.assertIsNone(result["server_reachable"])
        self.assertIsNone(result["herdr_version"])

    def test_wrong_pane_blocks_origin(self):
        def completed(argv, **kwargs):
            if argv[0] == "git":
                return self.git_ok(argv)
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"HERDR_ENV": "1", "HERDR_PANE_ID": "actual-pane"}), \
                mock.patch.object(access_probe.shutil, "which", return_value="herdr"), \
                mock.patch.object(access_probe.subprocess, "run", side_effect=completed):
            result = access_probe.inspect(Path(tmp), pane="expected-pane")
        self.assertIn("HERDR_ORIGIN_UNCONFIRMED", result["blockers"])
        self.assertFalse(result["herdr_origin"]["confirmed"])

    def test_reachable_herdr_without_agent_or_pane_does_not_confirm_origin(self):
        def completed(argv, **kwargs):
            if argv[0] == "git":
                return self.git_ok(argv)
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"HERDR_ENV": "1"}), \
                mock.patch.object(access_probe.shutil, "which", return_value="herdr"), \
                mock.patch.object(access_probe.subprocess, "run", side_effect=completed):
            result = access_probe.inspect(Path(tmp))
        self.assertIn("HERDR_ORIGIN_UNCONFIRMED", result["blockers"])
        self.assertFalse(result["herdr_origin"]["confirmed"])
        self.assertFalse(result["ready_for_role_handshake"])

    def test_herdr_socket_denial_is_not_reported_as_missing_cli(self):
        def completed(argv, **kwargs):
            if argv[0] == "git":
                return self.git_ok(argv)
            if argv[1:3] == ["status", "server"]:
                return subprocess.CompletedProcess(argv, 1, "", "IPC connection refused")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"HERDR_ENV": "1"}), \
                mock.patch.object(access_probe.shutil, "which", return_value="herdr"), \
                mock.patch.object(access_probe.subprocess, "run", side_effect=completed):
            result = access_probe.inspect(Path(tmp))
        self.assertIn("HERDR_TRANSPORT_DENIED", result["blockers"])
        self.assertFalse(result["server_reachable"])

    def test_windows_herdr_code_five_is_classified_as_transport_denial(self):
        error = 'Error: Os { code: 5, kind: PermissionDenied, message: "Access is denied." }'
        with mock.patch.object(access_probe.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 1, "", error)):
            result = access_probe.run(["herdr", "status", "server"], boundary="herdr_server")
        self.assertEqual(result["failure_code"], "HERDR_TRANSPORT_DENIED")

    def test_access_denial_is_classified_and_secrets_are_redacted(self):
        fake = subprocess.CompletedProcess([], 1, "", "sandbox policy denied command api_key=topsecret")
        with mock.patch.object(access_probe.subprocess, "run", return_value=fake):
            result = access_probe.run(["herdr", "status", "server"], boundary="herdr_server")
        self.assertEqual(result["failure_code"], "AGENT_SANDBOX_DENIED")
        self.assertNotIn("topsecret", result["error"])

    def test_named_pipe_denial_is_transport_and_os_launch_denial_is_distinct(self):
        pipe = subprocess.CompletedProcess([], 1, "", "named pipe access denied (WinError 5)")
        with mock.patch.object(access_probe.subprocess, "run", return_value=pipe):
            result = access_probe.run(["herdr", "status", "server"], boundary="herdr_server")
        self.assertEqual(result["failure_code"], "HERDR_TRANSPORT_DENIED")

        with mock.patch.object(access_probe.subprocess, "run", side_effect=PermissionError(13, "Access denied")):
            result = access_probe.run(["herdr", "status", "server"], boundary="herdr_server")
        self.assertEqual(result["failure_code"], "OS_ACCESS_DENIED")

    def test_local_probe_does_not_confirm_herdr_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".herdr-loop").mkdir()
            with mock.patch.object(access_probe.subprocess, "run", side_effect=self.git_ok):
                result = access_probe.inspect(workspace, write_probe=True, skip_herdr=True)
        self.assertFalse(result["ready_for_role_handshake"])
        self.assertFalse(result["herdr_origin"]["confirmed"])
        self.assertEqual(result["workspace_write_test"]["ok"], True)
        self.assertIn("HERDR_ORIGIN_UNCONFIRMED", result["blockers"])

    def test_workspace_symlink_is_refused_before_external_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace-link"
            real_is_symlink = Path.is_symlink

            def is_symlink(path):
                return path == workspace or real_is_symlink(path)

            with mock.patch.object(Path, "is_symlink", is_symlink):
                with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                    access_probe.inspect(workspace)

    def test_secret_looking_arguments_are_never_echoed(self):
        with mock.patch.object(access_probe.subprocess, "run", side_effect=PermissionError(13, "Access denied")):
            result = access_probe.run(["herdr", "--token", "secret-argument"], boundary="herdr_server")
        self.assertNotIn("secret-argument", str(result))


if __name__ == "__main__":
    unittest.main()
