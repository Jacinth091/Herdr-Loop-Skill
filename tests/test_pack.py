"""Offline smoke tests for role binder and activation guards."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BIND = ROOT / "tools" / "role_bind.py"
ACTIVATE = ROOT / "tools" / "activate_roles.py"
CONFIG = ROOT / "examples" / "roles.example.json"


class RolePackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.config = json.loads(CONFIG.read_text(encoding="utf8"))
        self.path = self.root / "roles.json"
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.config), encoding="utf8")

    def bind(self, *args):
        return subprocess.run([sys.executable, str(BIND), "--config", str(self.path), "--repo", str(self.repo), *args],
                              capture_output=True, text=True)

    def activate(self, *args, env=None):
        return subprocess.run([sys.executable, str(ACTIVATE), "--run-dir", str(self.repo / ".herdr-loop" / self.config["run_id"]), *args],
                              capture_output=True, text=True, env=env)

    def use_standalone_config(self):
        self.config = {
            "run_id": "standalone-one-off",
            "execution_mode": "standalone",
            "agents": {"workers": [{
                "name": "solo_worker", "kind": "codex",
                "task": "Fix the scoped behavior. Preserve unrelated dirty files and report what you checked.",
            }]},
        }
        self.save()
        return self.repo / ".herdr-loop" / self.config["run_id"]

    def test_sqlite_pilot_shipped_inside_controller(self):
        pilot = ROOT / "skills" / "herdr-engineering-loop" / "scripts" / "herdr_ledger.py"
        self.assertTrue(pilot.is_file())
        self.assertIn("default promotion remains gated", pilot.read_text(encoding="utf8").lower())

    def test_bind_refuses_existing_sqlite_authority(self):
        run=self.repo / ".herdr-loop" / self.config["run_id"]
        run.mkdir(parents=True)
        (run / "ledger.sqlite").write_bytes(b"existing pilot")
        result=self.bind()
        self.assertNotEqual(result.returncode,0)
        self.assertIn("SQLITE_PILOT_NOT_LIVE",result.stderr)
        self.assertFalse((run / "SESSION.md").exists())

    def test_bind_sqlite_mode_creates_planning_ledger_without_dispatch(self):
        self.config["state_backend"] = "sqlite"
        self.save()
        result = self.bind()
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.repo / ".herdr-loop" / self.config["run_id"]
        self.assertTrue((run / "ledger.sqlite").is_file())
        self.assertFalse((run / "SESSION.md").exists())
        env = dict(os.environ)
        env.pop("HERDR_ENV", None)
        activation = self.activate(env=env)
        self.assertEqual(activation.returncode, 2)
        self.assertIn("HERDR_ENV=1 required", activation.stderr)
        self.assertNotIn("SQLITE_BACKEND_NOT_LIVE", activation.stderr)

    def test_bind_invalid_state_backend_fails(self):
        self.config["state_backend"] = "unknown_backend"
        self.save()
        result = self.bind()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid state_backend", result.stderr)

    def test_bundle_roles_exist(self):
        for skill in ("herdr-engineering-loop", "herdr-manager", "herdr-delegator", "herdr-lead", "herdr-worker", "herdr-verifier", "herdr-critic"):
            skillfile = ROOT / "skills" / skill / "SKILL.md"
            self.assertTrue(skillfile.is_file(), skillfile)
            self.assertIn("name: " + skill, skillfile.read_text(encoding="utf8"))

    def test_creates_unique_packets_and_activation(self):
        result = self.bind()
        self.assertEqual(result.returncode, 0, result.stderr)
        base = self.repo / ".herdr-loop" / "feature-example"
        self.assertTrue((base / "SESSION.md").exists())
        roles = json.loads((base / "ACTIVATION.json").read_text(encoding="utf8"))["agents"]
        self.assertEqual([(a["role"], a["kind"]) for a in roles], [("manager", "claude"), ("delegator", "codex"), ("worker", "agy"), ("critic", "claude")])
        self.assertIn("$herdr-delegator", roles[1]["prompt"])
        self.assertIn("herdr worktree create", roles[1]["prompt"])
        self.assertIn("herdr agent start", roles[1]["prompt"])
        self.assertIn("NEVER use `spawn_agent`", roles[1]["prompt"])
        self.assertIn("BLOCKED: HERDR_WORKER_UNAVAILABLE", roles[1]["prompt"])
        self.assertIn("/herdr-worker", roles[2]["prompt"])
        self.assertIn("NO tests", roles[2]["prompt"])
        worker_packet = (base / "role-packets/worker_t01.md").read_text(encoding="utf8")
        self.assertIn("normal permission prompts", worker_packet)
        self.assertIn("human-selected unattended disposable worktree", worker_packet)
        self.assertIn("--sandbox --dangerously-skip-permissions", worker_packet)
        self.assertIn("confirming sandbox startup", worker_packet)

    def test_all_codex_roles_explicitly_ban_internal_subagents(self):
        self.config["agents"]["manager"]["kind"] = "codex"
        self.config["agents"]["workers"][0]["kind"] = "codex"
        self.config["agents"]["verifier"] = {"name": "check_t01", "kind": "codex"}
        self.save()
        result = self.bind()
        self.assertEqual(result.returncode, 0, result.stderr)
        run_dir = self.repo / ".herdr-loop" / "feature-example"
        manifest = json.loads((run_dir / "ACTIVATION.json").read_text(encoding="utf8"))
        for agent in manifest["agents"]:
            if agent["kind"] != "codex":
                continue
            with self.subTest(role=agent["role"]):
                prompt = agent["prompt"]
                self.assertIn("NEVER use `spawn_agent`", prompt)
                self.assertIn("Codex internal subagents", prompt)
                self.assertIn("no fallback to internal agents", prompt)
                packet = (run_dir / "role-packets" / (agent["name"] + ".md")).read_text(encoding="utf8")
                self.assertIn("Codex `spawn_agent`", packet)
                self.assertIn("Herdr", packet)
        self.assertIn("Codex internal subagents are forbidden", (run_dir / "ROSTER.md").read_text(encoding="utf8"))

    def test_claude_delegator_still_requires_herdr_spawn(self):
        self.config["agents"]["manager"]["kind"] = "codex"
        self.config["agents"]["delegator"]["kind"] = "claude"
        self.save()
        self.assertEqual(self.bind().returncode, 0)
        manifest = json.loads((self.repo / ".herdr-loop" / "feature-example" / "ACTIVATION.json").read_text(encoding="utf8"))
        delegator = next(agent for agent in manifest["agents"] if agent["role"] == "delegator")
        self.assertIn("herdr agent start", delegator["prompt"])
        self.assertIn("NEVER substitute native subagents", delegator["prompt"])

    def test_manifest_disallows_codex_subagents(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf8"))
        self.assertEqual(manifest["version"], "2.8.2")
        self.assertEqual(manifest["policy"]["claude_verifier_model_default"], "claude-haiku-5-5")
        self.assertFalse(manifest["policy"]["codex_native_subagents_allowed"])
        self.assertTrue(manifest["policy"]["workers_must_be_herdr_spawned"])

    def test_same_inputs_idempotent_and_session_preserved(self):
        self.assertEqual(self.bind().returncode, 0)
        base = self.repo / ".herdr-loop" / "feature-example"
        session = base / "SESSION.md"
        session.write_text("hand-edited ledger\n", encoding="utf8")
        self.assertEqual(self.bind().returncode, 0)
        self.assertEqual(session.read_text(encoding="utf8"), "hand-edited ledger\n")

    def test_reassignment_requires_force(self):
        self.assertEqual(self.bind().returncode, 0)
        self.config["agents"]["manager"]["kind"] = "codex"
        self.save()
        self.assertNotEqual(self.bind().returncode, 0)
        self.assertEqual(self.bind("--force").returncode, 0)
        base = self.repo / ".herdr-loop" / "feature-example"
        self.assertIn("$herdr-manager", (base / "role-packets/architect.md").read_text(encoding="utf8"))

    def test_reject_duplicate_agent(self):
        self.config["agents"]["delegator"]["name"] = "architect"
        self.save()
        self.assertNotEqual(self.bind().returncode, 0)
        self.assertFalse((self.repo / ".herdr-loop").exists())

    def test_reject_path_traversal_and_invalid_kind(self):
        self.config["run_id"] = "../bad"
        self.save()
        self.assertNotEqual(self.bind().returncode, 0)
        self.config["run_id"] = "okay"
        self.config["agents"]["workers"][0]["kind"] = "../runner"
        self.save()
        self.assertNotEqual(self.bind().returncode, 0)

    def test_unknown_agent_kind_binds_with_warning(self):
        self.config["agents"]["workers"][0]["kind"] = "gemini"
        self.save()
        result = self.bind()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING Agent worker_t01 uses unverified kind 'gemini'", result.stderr)
        run_dir = self.repo / ".herdr-loop" / "feature-example"
        self.assertIn("## Warnings", (run_dir / "ROSTER.md").read_text(encoding="utf8"))
        worker = next(a for a in json.loads((run_dir / "ACTIVATION.json").read_text(encoding="utf8"))["agents"] if a["role"] == "worker")
        self.assertIn("Activate the herdr-worker skill (load its SKILL.md)", worker["prompt"])
        self.assertIn("NO tests", worker["prompt"])
        self.assertNotIn("CODEX HARD RULE", worker["prompt"])
        self.assertEqual(worker["model"], "")

    def test_known_kinds_keep_their_role_rules(self):
        self.config["agents"]["manager"]["kind"] = "agy"
        self.save()
        result = self.bind()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not supported for role manager", result.stderr)

    def test_opencode_kind_is_known_in_every_role(self):
        self.config["agents"]["manager"]["kind"] = "opencode"
        self.config["agents"]["delegator"]["kind"] = "opencode"
        self.config["agents"]["workers"][0]["kind"] = "opencode"
        self.save()
        result = self.bind()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("unverified kind", result.stderr)
        roles = json.loads((self.repo / ".herdr-loop" / "feature-example" / "ACTIVATION.json").read_text(encoding="utf8"))["agents"]
        manager = next(a for a in roles if a["role"] == "manager")
        worker = next(a for a in roles if a["role"] == "worker")
        self.assertEqual(manager["kind"], "opencode")
        self.assertIn("@herdr-manager", manager["prompt"])
        self.assertIn("@herdr-worker", worker["prompt"])

    def test_parallel_budget_requires_explicit_authorization(self):
        for count in (2, 3):
            with self.subTest(workers=count):
                self.config["max_active_workers"] = count
                self.config["parallel_workers_authorized"] = False
                self.save()
                failure = self.bind()
                self.assertNotEqual(failure.returncode, 0)
                self.assertIn("parallel_workers_authorized=true", failure.stderr)
                self.assertFalse((self.repo / ".herdr-loop").exists())
                self.config["parallel_workers_authorized"] = True
                self.save()
                success = self.bind()
                self.assertEqual(success.returncode, 0, success.stderr)
                session = (self.repo / ".herdr-loop" / "feature-example" / "SESSION.md").read_text(encoding="utf8")
                self.assertIn("concurrent heavy local verification jobs 1", session)
                self.assertIn("remote CI separately observed", session)
                self.assertIn("human-owned", session.lower())
                self.assertIn("Source SHA(s)", session)
                self.assertIn("Latest-head CI", session)
                self.assertIn("Verification lock: NONE", session)
                # Remove only test output between subtests; no user files involved.
                import shutil
                shutil.rmtree(self.repo / ".herdr-loop")

    def test_four_workers_is_not_supported_even_with_approval(self):
        self.config["max_active_workers"] = 4
        self.config["parallel_workers_authorized"] = True
        self.save()
        result = self.bind()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be 1, 2 or 3", result.stderr)

    def test_docs_only_policy_does_not_relax_herdr_or_code_rules(self):
        files = {
            "delegator": ROOT / "skills/herdr-delegator/SKILL.md",
            "worker": ROOT / "skills/herdr-worker/SKILL.md",
            "controller": ROOT / "skills/herdr-engineering-loop/SKILL.md",
        }
        for role, path in files.items():
            body = path.read_text(encoding="utf8")
            with self.subTest(role=role):
                self.assertIn("non-code-isolated-copy", body)
                self.assertIn("Herdr", body)
        delegator = files["delegator"].read_text(encoding="utf8")
        self.assertIn("task-specific Git worktree", delegator)
        self.assertIn("NEVER call `spawn_agent`", delegator)
        self.assertIn("UNSAFE_WORKER_INPUT", delegator)

    def test_session_ledger_not_overwritten_when_budget_changes(self):
        self.assertEqual(self.bind().returncode, 0)
        session = self.repo / ".herdr-loop" / "feature-example" / "SESSION.md"
        original = "Live handoff: a human-owned pane; accepted content awaiting CI\n"
        session.write_text(original, encoding="utf8")
        self.config["max_active_workers"] = 3
        self.config["parallel_workers_authorized"] = True
        self.save()
        res = self.bind("--force")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(session.read_text(encoding="utf8"), original)
        self.assertIn("Max active workers: `3`", (session.parent / "ROSTER.md").read_text(encoding="utf8"))

    def test_only_one_verification_process_allowed(self):
        self.config["verification_concurrency"] = 2
        self.save()
        self.assertNotEqual(self.bind().returncode, 0)

    def test_preserve_unmanaged_roster_even_with_force(self):
        base = self.repo / ".herdr-loop" / "feature-example"
        base.mkdir(parents=True)
        (base / "ROSTER.md").write_text("other project\n", encoding="utf8")
        self.assertNotEqual(self.bind("--force").returncode, 0)
        self.assertEqual((base / "ROSTER.md").read_text(encoding="utf8"), "other project\n")

    def test_dry_run_doesnt_create_dirs(self):
        self.assertEqual(self.bind("--dry-run").returncode, 0)
        self.assertFalse((self.repo / ".herdr-loop").exists())

    def test_dry_run_activation_without_herdr(self):
        self.assertEqual(self.bind().returncode, 0)
        result = self.activate("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY RUN architect", result.stdout)
        self.assertNotIn("DRY RUN worker_t01", result.stdout)

    def test_worker_activation_refuses_without_worktree_packets(self):
        self.assertEqual(self.bind().returncode, 0)
        self.assertNotEqual(self.activate("--roles", "worker", "--dry-run").returncode, 0)
        worktree = self.root / "worker"
        worktree.mkdir()
        self.config["agents"]["workers"][0]["worktree"] = str(worktree)
        self.save()
        self.assertEqual(self.bind("--force").returncode, 0)
        self.assertNotEqual(self.activate("--roles", "worker", "--dry-run").returncode, 0)
        (worktree / ".herdr-loop").mkdir()
        (worktree / ".herdr-loop" / "ROLE.md").write_text("role", encoding="utf8")
        (worktree / ".herdr-loop" / "TASK.md").write_text("task", encoding="utf8")
        self.assertEqual(self.activate("--roles", "worker", "--dry-run").returncode, 0)

    def test_real_activation_requires_herdr_env(self):
        self.assertEqual(self.bind().returncode, 0)
        env = dict(os.environ)
        env.pop("HERDR_ENV", None)
        self.assertNotEqual(self.activate(env=env).returncode, 0)

    def test_standalone_binds_one_worker_without_managed_artifacts(self):
        run = self.use_standalone_config()
        result = self.bind()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((run / "SESSION.md").exists())
        self.assertFalse((run / "ledger.sqlite").exists())
        self.assertFalse((run / "BACKEND.json").exists())
        activation = json.loads((run / "ACTIVATION.json").read_text(encoding="utf8"))
        self.assertEqual(activation["execution_mode"], "standalone")
        self.assertEqual(len(activation["agents"]), 1)
        worker = activation["agents"][0]
        self.assertFalse(worker["requires_role_ready"])
        self.assertEqual(worker["signal_transport"], "manual-direct")
        self.assertIn("human's direct assignment", worker["prompt"])
        self.assertIn("Fix the scoped behavior", worker["prompt"])
        self.assertNotIn("TASK_START <", worker["prompt"])
        self.assertNotIn("ROLE_READY <", worker["prompt"])
        self.assertIn("does not create a governed run, ledger", (run / "ACTIVATE.md").read_text(encoding="utf8").lower())
        self.assertIn("no SESSION.md", result.stdout)

    def test_standalone_binds_a_claude_worker_without_herdr_metadata(self):
        run = self.use_standalone_config()
        self.config["run_id"] = "standalone-claude"
        self.config["agents"]["workers"][0]["kind"] = "claude"
        self.save()
        result = self.bind()
        self.assertEqual(result.returncode, 0, result.stderr)
        activation = json.loads((run.parent / self.config["run_id"] / "ACTIVATION.json").read_text(encoding="utf8"))
        worker = activation["agents"][0]
        self.assertEqual(worker["kind"], "claude")
        self.assertFalse(worker["requires_role_ready"])
        self.assertIn("Do not require Herdr registration", worker["prompt"])
        self.assertNotIn("TASK_START <", worker["prompt"])

    def test_standalone_accepts_dirty_checkout_without_changing_it(self):
        dirty = self.repo / "unrelated.txt"
        dirty.write_text("preserve this dirty file\n", encoding="utf8")
        before = dirty.read_bytes()
        run = self.use_standalone_config()
        result = self.bind()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dirty.read_bytes(), before)
        self.assertIn(str(self.repo.resolve()), (run / "role-packets" / "solo_worker.md").read_text(encoding="utf8"))

    def test_standalone_rejects_governed_run_id_reuse(self):
        self.assertEqual(self.bind().returncode, 0)
        governed_run = self.repo / ".herdr-loop" / self.config["run_id"]
        self.config = {
            "run_id": self.config["run_id"],
            "execution_mode": "standalone",
            "agents": {"workers": [{"name": "solo_worker", "kind": "claude", "task": "Do the assigned scoped task."}]},
        }
        self.save()
        result = self.bind("--force")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot bind standalone mode over a governed run", result.stderr)
        self.assertTrue((governed_run / "SESSION.md").is_file())

    def test_standalone_activation_does_not_use_managed_dispatcher(self):
        run = self.use_standalone_config()
        self.assertEqual(self.bind().returncode, 0)
        env = dict(os.environ, HERDR_ENV="1")
        result = self.activate(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Standalone assignment is delivered directly", result.stderr)

    def test_standalone_worker_contract_keeps_direct_and_governed_modes_distinct(self):
        skill = (ROOT / "skills" / "herdr-worker" / "SKILL.md").read_text(encoding="utf8")
        for required in ("Standalone Mode", "Governed Mode", "stale `CONTINUATION_CONTEXT.md`",
                         "independent verification", "explicit deny", "dirty"):
            self.assertIn(required.lower(), skill.lower())

    def test_role_list_has_a_single_source_of_truth(self):
        # role_bind derives its role maps from herdr_ledger.ROLE_ORDER. Guard the derivation so
        # the two cannot drift, and check every declared role's skill is actually installed.
        ledger_spec = importlib.util.spec_from_file_location(
            "ledger_roles_under_test", ROOT / "skills" / "herdr-engineering-loop" / "scripts" / "herdr_ledger.py")
        ledger = importlib.util.module_from_spec(ledger_spec)
        ledger_spec.loader.exec_module(ledger)
        bind_spec = importlib.util.spec_from_file_location("role_bind_roles_under_test", BIND)
        bind = importlib.util.module_from_spec(bind_spec)
        bind_spec.loader.exec_module(bind)
        self.assertEqual(set(ledger.ROLE_ORDER), set(bind.SKILLS))
        self.assertEqual(set(ledger.ROLE_ORDER), set(bind.KINDS))
        for role in ledger.ROLE_ORDER:
            with self.subTest(role=role):
                self.assertEqual(bind.SKILLS[role], "herdr-" + role)
                self.assertTrue((ROOT / "skills" / bind.SKILLS[role] / "SKILL.md").is_file())

    def test_herdr_mock_activation_uses_pinned_agent_names_and_prompts(self):
        self.assertEqual(self.bind().returncode, 0)
        if os.name == "nt":
            spec = importlib.util.spec_from_file_location("activate_roles_under_test", ACTIVATE)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            fake = subprocess.CompletedProcess([], 0, "{}", "")
            with mock.patch.dict(os.environ, HERDR_ENV="1"), mock.patch.object(module.shutil, "which", return_value="herdr.exe"), mock.patch.object(module.subprocess, "run", return_value=fake) as calls:
                self.assertEqual(module.main(["--run-dir", str(self.repo / ".herdr-loop" / self.config["run_id"])]), 0)
            argv = [call.args[0] for call in calls.call_args_list]
            self.assertEqual([a[1:4] for a in argv[:2]], [["agent","get","architect"],["agent","get","dispatcher"]])
            self.assertEqual([a[1:4] for a in argv[2:]], [["agent","prompt","architect"],["agent","prompt","dispatcher"]])
            return
        executable_dir = self.root / "bin"
        executable_dir.mkdir()
        log = self.root / "called.txt"
        fake = executable_dir / ("herdr.cmd" if os.name == "nt" else "herdr")
        fake.write_text('@echo off\r\necho %*>>"%HERDR_TEST_LOG%"\r\nexit /b 0\r\n' if os.name == "nt" else
                        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$HERDR_TEST_LOG\"\nexit 0\n", encoding="utf8")
        fake.chmod(0o755)
        env = dict(os.environ, HERDR_ENV="1", HERDR_TEST_LOG=str(log), PATH=str(executable_dir) + os.pathsep + os.environ.get("PATH", ""))
        result = self.activate(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        contents = log.read_text(encoding="utf8")
        self.assertIn("agent get architect", contents)
        self.assertIn("agent get dispatcher", contents)
        self.assertIn("agent prompt architect", contents)
        self.assertIn("agent prompt dispatcher", contents)
        self.assertNotIn("agent get worker_t01", contents)

    def test_prompt_submission_receipt_prevents_blind_replay(self):
        self.assertEqual(self.bind().returncode, 0)
        spec = importlib.util.spec_from_file_location("activate_roles_receipt_test", ACTIVATE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        preflight = subprocess.CompletedProcess([], 0, "agent exists", "")
        with mock.patch.dict(os.environ, HERDR_ENV="1"), \
                mock.patch.object(module.shutil, "which", return_value="herdr"), \
                mock.patch.object(module.subprocess, "run", return_value=preflight), \
                mock.patch.object(module, "_run_prompt", return_value=subprocess.CompletedProcess([], 0, "", "")) as prompts:
            self.assertEqual(module.main(["--run-dir", str(self.repo / ".herdr-loop" / self.config["run_id"])]), 0)
            self.assertEqual(prompts.call_count, 2)
            self.assertEqual(module.main(["--run-dir", str(self.repo / ".herdr-loop" / self.config["run_id"])]), 2)
            self.assertEqual(prompts.call_count, 2)
        session = self.repo / ".herdr-loop" / self.config["run_id"] / "SESSION.md"
        receipts = module._read_receipts(session)[1]
        self.assertEqual([x["status"] for x in receipts], ["OUTCOME_UNCERTAIN", "OUTCOME_UNCERTAIN"])
        self.assertTrue(all(len(x["operation_id"]) == 24 for x in receipts))

    def test_partial_prompt_timeout_requires_reconciliation_and_allows_one_retry(self):
        self.assertEqual(self.bind().returncode, 0)
        spec = importlib.util.spec_from_file_location("activate_roles_partial_test", ACTIVATE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        preflight = subprocess.CompletedProcess([], 0, "agent exists", "")
        run_dir = self.repo / ".herdr-loop" / self.config["run_id"]
        with mock.patch.dict(os.environ, HERDR_ENV="1"), \
                mock.patch.object(module.shutil, "which", return_value="herdr"), \
                mock.patch.object(module.subprocess, "run", return_value=preflight), \
                mock.patch.object(module, "_run_prompt", side_effect=[
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.TimeoutExpired(["herdr", "agent", "prompt"], 30, stderr="request timed out"),
                ]) as prompts:
            self.assertEqual(module.main(["--run-dir", str(run_dir)]), 2)
            self.assertEqual(prompts.call_count, 2)

        session = run_dir / "SESSION.md"
        receipts = module._read_receipts(session)[1]
        self.assertEqual([x["status"] for x in receipts], ["SUBMITTED", "OUTCOME_UNCERTAIN"])
        receipts[0]["status"] = "CONFIRMED_APPLIED"
        receipts[0]["evidence"] = "matching recipient artifact observed"
        receipts[1]["status"] = "CONFIRMED_NOT_APPLIED"
        receipts[1]["evidence"] = "authoritative target readback proved no prompt receipt"
        module._write_receipts(session, receipts)
        retry_id = receipts[1]["operation_id"]

        with mock.patch.dict(os.environ, HERDR_ENV="1"), \
                mock.patch.object(module.shutil, "which", return_value="herdr"), \
                mock.patch.object(module.subprocess, "run", return_value=preflight), \
                mock.patch.object(module, "_run_prompt", return_value=subprocess.CompletedProcess([], 0, "", "")) as retry:
            self.assertEqual(module.main(["--run-dir", str(run_dir), "--roles", "delegator"]), 0)
            self.assertEqual(retry.call_count, 1)
            self.assertEqual(module.main(["--run-dir", str(run_dir), "--roles", "delegator"]), 2)
            self.assertEqual(retry.call_count, 1)
        receipt = next(x for x in module._read_receipts(session)[1] if x["operation_id"] == retry_id)
        self.assertEqual(receipt["attempts"], 2)
        self.assertEqual(receipt["retry_count"], 1)
        self.assertEqual(receipt["status"], "OUTCOME_UNCERTAIN")


    def test_generated_roles_have_explicit_signal_recipients(self):
        self.config["agents"]["verifier"] = {"name": "checker", "kind": "codex"}
        self.save()
        self.assertEqual(self.bind().returncode, 0)
        run = self.repo / ".herdr-loop" / self.config["run_id"]
        agents = json.loads((run / "ACTIVATION.json").read_text(encoding="utf8"))["agents"]
        expected = {"manager": "dispatcher", "delegator": "architect", "worker": "dispatcher", "verifier": "dispatcher", "critic": "dispatcher"}
        for entry in agents:
            with self.subTest(role=entry["role"]):
                self.assertEqual(entry["signal_recipient"], expected[entry["role"]])
                self.assertEqual(entry["signal_transport"], "herdr-agent-prompt")
                self.assertTrue(entry["requires_role_ready"])
                self.assertIn("ROLE_READY", entry["prompt"])
                self.assertIn("NEVER auto-elevate", entry["prompt"])
                packet = (run / "role-packets" / (entry["name"] + ".md")).read_text(encoding="utf8")
                self.assertIn("Signal recipient (exact Herdr agent name)", packet)
                self.assertIn("`" + expected[entry["role"]] + "`", packet)
        self.assertIn("Signal readiness and receipts", (run / "SESSION.md").read_text(encoding="utf8"))

    def test_worker_and_verifier_activation_are_setup_only(self):
        self.config["agents"]["verifier"] = {"name": "checker", "kind": "codex"}
        self.save()
        self.assertEqual(self.bind().returncode, 0)
        activation = json.loads((self.repo / ".herdr-loop" / self.config["run_id"] / "ACTIVATION.json").read_text(encoding="utf8"))["agents"]
        worker = next(a for a in activation if a["role"] == "worker")
        verifier = next(a for a in activation if a["role"] == "verifier")
        self.assertIn("READINESS ONLY", worker["prompt"])
        self.assertIn("DO NOT edit product files until a separate TASK_START", worker["prompt"])
        self.assertIn("READY.md", worker["prompt"])
        self.assertIn("VERIFY_START", verifier["prompt"])
        self.assertIn("serial verification lock", verifier["prompt"])
        delegator = next(a for a in activation if a["role"] == "delegator")
        self.assertIn("SETUP-ONLY PROMPT FIRST", delegator["prompt"])
        self.assertIn("observed ROLE_READY", delegator["prompt"])

    def test_every_role_skill_teaches_directed_signaling(self):
        for role in ("herdr-engineering-loop", "herdr-manager", "herdr-delegator", "herdr-lead", "herdr-worker", "herdr-verifier", "herdr-critic"):
            with self.subTest(role=role):
                path = ROOT / "skills" / role
                self.assertTrue((path / "references" / "signals.md").exists())
                text = (path / "SKILL.md").read_text(encoding="utf8")
                self.assertIn("references/signals.md", text)
                self.assertIn("ROLE_READY", text)
                self.assertIn("access", text.lower())
                self.assertTrue((path / "references" / "access-triage.md").exists())

    def test_access_recovery_is_self_contained_for_installed_roles(self):
        role_names = ("herdr-manager", "herdr-delegator", "herdr-lead", "herdr-worker", "herdr-verifier", "herdr-critic")
        copies = []
        for role in role_names:
            with self.subTest(role=role):
                triage = (ROOT / "skills" / role / "references" / "access-triage.md").read_text(encoding="utf8")
                copies.append(triage)
                self.assertIn("same authorized in-scope command", triage.lower())
                self.assertIn("explicit deny", triage.lower())
                self.assertIn("never self-grant", triage.lower())
        self.assertEqual(len(set(copies)), 1)
        manager = (ROOT / "skills" / "herdr-manager" / "SKILL.md").read_text(encoding="utf8")
        self.assertIn("references/access-triage.md", manager)

    def test_agy_normal_default_and_isolated_bypass_are_bounded(self):
        readme = (ROOT / "README.md").read_text(encoding="utf8")
        for text in (
            "--sandbox --dangerously-skip-permissions",
            "human-selected unattended run",
            "disposable worktree",
            "does not prove native approval",
            "Stop on sandbox failure",
        ):
            self.assertIn(text, readme)
        for rel in (
            "skills/herdr-engineering-loop/SKILL.md",
            "skills/herdr-delegator/SKILL.md",
        ):
            with self.subTest(path=rel):
                content = (ROOT / rel).read_text(encoding="utf8")
                self.assertNotIn("--sandbox --dangerously-skip-permissions", content)
        for rel in (
            "skills/herdr-engineering-loop/references/herdr-cli.md",
            "skills/herdr-delegator/references/herdr-cli.md",
        ):
            with self.subTest(path=rel):
                content = (ROOT / rel).read_text(encoding="utf8").lower()
                self.assertIn("--sandbox --dangerously-skip-permissions", content)
                self.assertIn("disposable worktree", content)
                self.assertIn("cannot verify native approval", content)
        capabilities = (ROOT / "skills/herdr-engineering-loop" / "references" / "permission-capabilities.md").read_text(encoding="utf8")
        self.assertIn("Windows", capabilities)
        self.assertIn("--dangerously-skip-permissions", capabilities)
        self.assertIn("Auto mode", capabilities)

    def test_standalone_docs_explain_no_managed_run_and_self_verification(self):
        readme = (ROOT / "README.md").read_text(encoding="utf8")
        operations = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf8")
        self.assertIn("Standalone Worker task (no managed run)", readme)
        self.assertIn("SESSION.md", readme)
        self.assertIn("label them self-verification", operations)
        self.assertIn("does not start the agent", operations)

    def test_codex_override_routing_claude_manager_codex_delegator(self):
        self.config["agents"]["workers"][0]["kind"] = "codex"
        self.save()
        self.assertEqual(self.bind().returncode, 0)
        base = self.repo / ".herdr-loop" / "feature-example"
        entries = json.loads((base / "ACTIVATION.json").read_text(encoding="utf8"))["agents"]
        worker = next(a for a in entries if a["role"] == "worker")
        manager = next(a for a in entries if a["role"] == "manager")
        self.assertEqual(worker["model"], "gpt-6-luna")
        self.assertEqual(worker["reasoning_effort"], "xhigh")
        self.assertEqual(worker["spawn_owner"], "manager")
        self.assertIn("SPAWN_REQUEST", manager["prompt"])
        self.assertIn("SPAWN_READY", manager["prompt"])
        self.assertIn("model-overridden Codex", entries[1]["prompt"])
        packet = (base / "role-packets" / "worker_t01.md").read_text(encoding="utf8")
        self.assertIn("Required reasoning effort (confirm runtime): `xhigh`", packet)
        self.assertIn("Spawn owner (for a model-overridden Codex Worker): `manager`", packet)

    def test_claude_delegator_launches_codex_worker_even_with_codex_manager(self):
        self.config["agents"]["manager"]["kind"] = "codex"
        self.config["agents"]["delegator"]["kind"] = "claude"
        self.config["agents"]["workers"][0].update(kind="codex", reasoning_effort="max")
        self.save()
        self.assertEqual(self.bind().returncode, 0)
        entries = json.loads((self.repo / ".herdr-loop/feature-example/ACTIVATION.json").read_text(encoding="utf8"))["agents"]
        worker = next(a for a in entries if a["role"] == "worker")
        self.assertEqual(worker["spawn_owner"], "delegator")
        self.assertEqual(worker["reasoning_effort"], "max")
        self.assertIn("max", worker["prompt"])

    def test_both_codex_leaders_require_non_codex_or_human_spawn(self):
        self.config["agents"]["manager"]["kind"] = "codex"
        self.config["agents"]["delegator"]["kind"] = "codex"
        self.config["agents"]["workers"][0]["kind"] = "codex"
        self.save()
        self.assertEqual(self.bind().returncode, 0)
        run = self.repo / ".herdr-loop/feature-example"
        entries = json.loads((run / "ACTIVATION.json").read_text(encoding="utf8"))["agents"]
        worker = next(a for a in entries if a["role"] == "worker")
        self.assertEqual(worker["spawn_owner"], "human")
        self.assertIn("both leaders are Codex", entries[1]["prompt"])
        self.assertIn("both Codex -> human/non-Codex operator required", (run / "ROSTER.md").read_text(encoding="utf8"))

    def test_claude_haiku_worker_and_sonnet_critic_are_distinct(self):
        self.config["agents"]["workers"][0]["kind"] = "claude"
        self.save()
        self.assertEqual(self.bind().returncode, 0)
        run = self.repo / ".herdr-loop/feature-example"
        entries = json.loads((run / "ACTIVATION.json").read_text(encoding="utf8"))["agents"]
        worker, critic = (next(a for a in entries if a["role"] == role) for role in ("worker", "critic"))
        self.assertEqual(worker["model"], "claude-haiku-5-5")
        self.assertEqual(critic["model"], "claude-sonnet-5-5")
        self.assertIn("CRITIQUE_START", critic["prompt"])
        self.assertIn("CRITIQUE_READY", critic["prompt"])
        self.assertIn("No code edits, tests", critic["prompt"])
        self.assertIn("No edits without TASK_START", worker["prompt"])

    def test_haiku_defaults_to_dedicated_verifier_not_worker(self):
        self.config["agents"]["verifier"] = {"name": "verify_t01", "kind": "claude"}
        self.save()
        result = self.bind()
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.repo / ".herdr-loop" / "feature-example"
        roles = json.loads((run / "ACTIVATION.json").read_text(encoding="utf8"))["agents"]
        verifier = next(x for x in roles if x["role"] == "verifier")
        worker = next(x for x in roles if x["role"] == "worker")
        self.assertEqual(verifier["model"], "claude-haiku-5-5")
        self.assertEqual(verifier["kind"], "claude")
        self.assertEqual(worker["role"], "worker")
        self.assertIn("dedicated tester", verifier["prompt"])
        self.assertIn("REQUIRED MODEL: claude-haiku-5-5", verifier["prompt"])
        self.assertIn("VERIFY_START", verifier["prompt"])
        self.assertIn("serial verification lock", verifier["prompt"])
        self.assertNotIn("TASK_START", verifier["prompt"])
        packet = (run / "role-packets" / "verify_t01.md").read_text(encoding="utf8")
        self.assertIn("Verifier", packet)
        self.assertIn("separately named Herdr tester", packet)
        self.assertIn("Forbidden: product code edits", packet)
        session = (run / "SESSION.md").read_text(encoding="utf8")
        self.assertIn("Verifier: optional dedicated Claude Haiku 5.5 tester", session)
        self.assertIn("concurrent heavy local verification jobs 1", session)

    def test_haiku_tester_worker_and_sonnet_critic_are_separate_roles(self):
        self.config["agents"]["workers"][0]["kind"] = "claude"
        self.config["agents"]["verifier"] = {"name": "test_t01", "kind": "claude"}
        self.save()
        result = self.bind()
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.repo / ".herdr-loop" / "feature-example"
        roles = json.loads((run / "ACTIVATION.json").read_text(encoding="utf8"))["agents"]
        worker, tester, critic = (next(a for a in roles if a["role"] == role) for role in
                                  ("worker", "verifier", "critic"))
        self.assertEqual(worker["model"], "claude-haiku-5-5")
        self.assertEqual(tester["model"], "claude-haiku-5-5")
        self.assertEqual(critic["model"], "claude-sonnet-5-5")
        self.assertEqual(len({worker["name"], tester["name"], critic["name"]}), 3)
        self.assertIn("NO tests", worker["prompt"])
        self.assertIn("Do not modify product code", tester["prompt"])
        self.assertIn("No code edits, tests", critic["prompt"])

    def test_reject_using_same_agent_as_haiku_worker_and_tester(self):
        self.config["agents"]["workers"][0]["kind"] = "claude"
        self.config["agents"]["verifier"] = {"name": "worker_t01", "kind": "claude"}
        self.save()
        failure = self.bind()
        self.assertNotEqual(failure.returncode, 0)
        self.assertIn("more than one role", failure.stderr)

    def test_example_dedicated_tester_roster_and_skill_instructions(self):
        example = json.loads((ROOT / "examples" / "roles.haiku-verifier.json").read_text(encoding="utf8"))
        self.assertEqual(example["agents"]["verifier"]["model"], "claude-haiku-5-5")
        self.assertEqual(example["agents"]["critic"]["model"], "claude-sonnet-5-5")
        self.assertEqual(example["verification_concurrency"], 1)
        for rel in ("skills/herdr-verifier/SKILL.md", "skills/herdr-delegator/SKILL.md",
                    "skills/herdr-engineering-loop/SKILL.md"):
            self.assertIn("Haiku", (ROOT / rel).read_text(encoding="utf8"))
        self.assertIn("verifier_t01 --kind claude", (ROOT / "skills" / "herdr-verifier" / "references" / "model-routing.md").read_text(encoding="utf8"))

    def test_reject_bad_codex_effort_and_critic_kind(self):
        self.config["agents"]["workers"][0].update(kind="codex", reasoning_effort="low")
        self.save()
        failure = self.bind()
        self.assertNotEqual(failure.returncode, 0)
        self.assertIn("reasoning_effort", failure.stderr)
        self.config["agents"]["workers"][0]["reasoning_effort"] = "xhigh"
        self.save()
        self.assertEqual(self.bind().returncode, 0)

    def test_critic_model_is_a_recommendation(self):
        self.config["agents"]["critic"]["model"] = "claude-opus-5-5"
        self.save()
        result = self.bind("--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("claude-sonnet-5-5 is the recommended Claude critic model", result.stderr)
        self.config["agents"]["critic"] = {"name": "critic_t01", "kind": "codex"}
        self.save()
        result = self.bind("--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        critic = next(a for a in json.loads((self.repo / ".herdr-loop" / "feature-example" / "ACTIVATION.json").read_text(encoding="utf8"))["agents"] if a["role"] == "critic")
        self.assertEqual(critic["model"], "")

    def test_critic_can_be_optional_without_breaking_legacy_configs(self):
        self.config["agents"].pop("critic")
        self.save()
        self.assertEqual(self.bind().returncode, 0)
        entries = json.loads((self.repo / ".herdr-loop/feature-example/ACTIVATION.json").read_text(encoding="utf8"))["agents"]
        self.assertFalse(any(a["role"] == "critic" for a in entries))

    def test_critic_activation_is_setup_only(self):
        self.assertEqual(self.bind().returncode, 0)
        act = self.activate("--roles", "critic", "--dry-run")
        self.assertEqual(act.returncode, 0, act.stderr)
        self.assertIn("critic_t01", act.stdout)
        self.assertIn("WAIT for CRITIQUE_START", act.stdout)
        self.assertIn("CRITIQUE_READY", act.stdout)

    def test_critic_prompt_for_non_claude_does_not_demand_sonnet(self):
        self.config["agents"]["critic"] = {"name": "critic_t01", "kind": "codex"}
        self.save()
        self.assertEqual(self.bind("--force").returncode, 0)
        entries = json.loads((self.repo / ".herdr-loop/feature-example/ACTIVATION.json").read_text(encoding="utf8"))["agents"]
        critic = next(a for a in entries if a["role"] == "critic")
        self.assertNotIn("Verify Sonnet 5.5", critic["prompt"])
        self.assertIn("Verify configured active model", critic["prompt"])

    def test_access_probe_without_herdr_is_not_readiness_proof(self):
        workspace = self.root / "worker"
        workspace.mkdir()
        scratch = workspace / ".herdr-loop"
        scratch.mkdir()
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "access_probe.py"),
                               "--workspace", str(workspace), "--probe-write", "--skip-herdr"],
                              capture_output=True, text=True)
        info = json.loads(proc.stdout)
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(info["checks"]["scratch_write"]["ok"])
        self.assertFalse(info["ready_for_role_handshake"])
        self.assertEqual(list(scratch.iterdir()), [])

    def test_access_probe_refuses_unknown_or_mismatched_pane(self):
        workspace = self.root / "worker"
        workspace.mkdir()
        env = dict(os.environ)
        env.pop("HERDR_ENV", None)
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "access_probe.py"),
                               "--workspace", str(workspace), "--agent", "worker_t01"],
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("HERDR_ORIGIN_UNCONFIRMED", json.loads(proc.stdout)["blockers"])

    def test_access_probe_herdr_readiness_requires_named_agent_and_scratched_write(self):
        workspace = self.root / "worker"
        workspace.mkdir()
        (workspace / ".herdr-loop").mkdir()
        executable_dir = self.root / "bin"
        executable_dir.mkdir()
        fake = executable_dir / ("herdr.cmd" if os.name == "nt" else "herdr")
        fake.write_text("@echo off\r\necho {}\r\nexit /b 0\r\n" if os.name == "nt" else
                        "#!/bin/sh\nprintf '%s\n' '{}'\nexit 0\n", encoding="utf8")
        fake.chmod(0o755)
        env = dict(os.environ, HERDR_ENV="1", HERDR_PANE_ID="w2:p1",
                   PATH=str(executable_dir) + os.pathsep + os.environ.get("PATH", ""))
        argv = [sys.executable, str(ROOT / "tools" / "access_probe.py"), "--workspace", str(workspace),
                "--agent", "worker_t01", "--pane", "w2:p1", "--probe-write"]
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ready_for_role_handshake"])
        env["HERDR_PANE_ID"] = "w3:p1"
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("HERDR_ORIGIN_UNCONFIRMED", json.loads(proc.stdout)["blockers"])


    def test_session_records_execution_intent_and_write_ownership(self):
        res = self.bind()
        self.assertEqual(res.returncode, 0, res.stderr)
        session = (self.repo / ".herdr-loop/feature-example/SESSION.md").read_text(encoding="utf8")
        self.assertIn("Execution grant: NOT RECORDED", session)
        self.assertIn("Approved intent:", session)
        self.assertIn("## Active attempts and write ownership", session)
        self.assertIn("one active writer per checkout", session)
        self.assertIn("Candidate writer state: OPEN", session)
        self.assertIn("## Cancellation state", session)

    def test_worker_attempt_is_distinct_from_task_and_can_be_reassigned(self):
        self.assertEqual(self.bind().returncode, 0)
        run = self.repo / ".herdr-loop/feature-example"
        initial = json.loads((run / "ACTIVATION.json").read_text(encoding="utf8"))
        worker = next(a for a in initial["agents"] if a["role"] == "worker")
        self.assertEqual(worker["task_id"], "T01")
        self.assertEqual(worker["attempt_id"], "T01-A1")
        self.assertIn("T01-A1", (run / "role-packets/worker_t01.md").read_text(encoding="utf8"))
        self.assertIn("T01-A1", (run / "ROSTER.md").read_text(encoding="utf8"))
        ledger = run / "SESSION.md"
        ledger.write_text("Live attempt T01-A1 failed; keep its report and commit.\n", encoding="utf8")
        self.config["agents"]["workers"][0]["attempt_id"] = "T01-A2"
        self.save()
        res = self.bind("--force")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(ledger.read_text(encoding="utf8"), "Live attempt T01-A1 failed; keep its report and commit.\n")
        refreshed = json.loads((run / "ACTIVATION.json").read_text(encoding="utf8"))
        worker = next(a for a in refreshed["agents"] if a["role"] == "worker")
        self.assertEqual(worker["attempt_id"], "T01-A2")
        self.assertIn("T01-A2", worker["prompt"])

    def test_worker_attempt_validation_rejects_wrong_task_and_duplicates(self):
        self.config["agents"]["workers"][0]["attempt_id"] = "T02-A1"
        self.save()
        res = self.bind()
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("attempt_id", res.stderr)
        self.config["agents"]["workers"][0]["attempt_id"] = "T01-A1"
        duplicate = dict(self.config["agents"]["workers"][0])
        duplicate["name"] = "worker_two"
        self.config["agents"]["workers"].append(duplicate)
        self.save()
        res = self.bind()
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Duplicate worker attempt", res.stderr)

    def test_cancel_signal_and_independent_verification_contracts(self):
        for role in ("herdr-engineering-loop", "herdr-manager", "herdr-delegator", "herdr-lead", "herdr-worker", "herdr-verifier", "herdr-critic"):
            with self.subTest(role=role):
                body = (ROOT / "skills" / role / "SKILL.md").read_text(encoding="utf8")
                self.assertIn("cancel", body.lower())
                signals = (ROOT / "skills" / role / "references/signals.md").read_text(encoding="utf8")
                self.assertIn("CANCEL_RUN", signals)
                self.assertIn("CANCEL_ACK", signals)
        verifier = (ROOT / "skills/herdr-verifier/SKILL.md").read_text(encoding="utf8")
        self.assertIn("producer transcript", verifier)
        self.assertIn("STALE/INCOMPLETE", verifier)
        delegator = (ROOT / "skills/herdr-delegator/SKILL.md").read_text(encoding="utf8")
        self.assertIn("one active writer", delegator)
        self.assertIn("candidate writer: FROZEN", delegator)
        self.assertIn("CANCELLATION_BLOCKED", delegator)

    def test_run_integrity_is_small_conditional_reference_not_relay_runtime(self):
        for role in ("herdr-engineering-loop", "herdr-manager", "herdr-delegator", "herdr-verifier"):
            with self.subTest(role=role):
                path = ROOT / "skills" / role
                self.assertIn("references/run-integrity.md", (path / "SKILL.md").read_text(encoding="utf8"))
                reference = (path / "references/run-integrity.md").read_text(encoding="utf8")
                self.assertIn("CANCELLED", reference)
                self.assertIn("one active writing attempt", reference)
                self.assertLess(len(reference.splitlines()), 100)
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf8"))
        self.assertEqual(manifest["version"], "2.8.2")
        self.assertTrue(manifest["policy"]["relay_runtime_not_required"])

    # --- Lead mode: one agent holds Manager + Delegator duties ---

    def use_lead_config(self):
        self.config = json.loads((ROOT / "examples" / "roles.lead.json").read_text(encoding="utf8"))
        self.save()
        return self.repo / ".herdr-loop" / self.config["run_id"]

    def test_lead_roster_binds_one_agent_for_both_leaders(self):
        run_dir = self.use_lead_config()
        result = self.bind()
        self.assertEqual(result.returncode, 0, result.stderr)
        agents = json.loads((run_dir / "ACTIVATION.json").read_text(encoding="utf8"))["agents"]
        self.assertEqual([(a["role"], a["name"]) for a in agents], [("lead", "lead"), ("worker", "worker_t01")])
        lead, worker = agents
        self.assertIn("/herdr-lead", lead["prompt"])
        self.assertIn("wait for the human's GO", lead["prompt"])
        self.assertIn("NEVER implement product code", lead["prompt"])
        self.assertIn("the human gives final acceptance", lead["prompt"])
        self.assertIn("herdr agent start", lead["prompt"])
        self.assertEqual(lead["signal_recipient"], "the human (not a Herdr agent)")
        self.assertEqual(lead["signal_transport"], "human-direct")
        self.assertEqual(worker["signal_recipient"], "lead")
        self.assertEqual(worker["spawn_owner"], "lead")
        self.assertIn("Signal roles: Lead lead -> human", (run_dir / "ROSTER.md").read_text(encoding="utf8"))
        session = (run_dir / "SESSION.md").read_text(encoding="utf8")
        self.assertIn("Lead: inspect Herdr", session)
        self.assertNotIn("Manager: inspect", session)
        packet = (run_dir / "role-packets" / "lead.md").read_text(encoding="utf8")
        self.assertIn("Forbidden: implementing product code, acting as Worker/Verifier/Critic", packet)
        self.assertIn("Transport: `human-direct`", packet)
        self.assertNotIn("herdr agent prompt the human", packet)

    def test_lead_cannot_be_mixed_with_manager_or_delegator(self):
        self.use_lead_config()
        self.config["agents"]["manager"] = {"name": "architect", "kind": "claude"}
        self.save()
        result = self.bind()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not both", result.stderr)

    def test_lead_still_cannot_double_up_other_roles(self):
        self.use_lead_config()
        self.config["agents"]["workers"][0]["name"] = "lead"
        self.save()
        result = self.bind()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Agent assigned more than one role: lead", result.stderr)

    def test_lead_kind_must_be_a_leader_cli(self):
        self.use_lead_config()
        self.config["agents"]["lead"]["kind"] = "agy"
        self.save()
        result = self.bind()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not supported for role lead", result.stderr)

    def test_codex_lead_never_launches_overridden_codex_worker(self):
        run_dir = self.use_lead_config()
        self.config["agents"]["lead"]["kind"] = "codex"
        worker = self.config["agents"]["workers"][0]
        worker["kind"] = "codex"
        worker.pop("model")
        self.save()
        self.assertEqual(self.bind().returncode, 0)
        agents = {a["role"]: a for a in json.loads((run_dir / "ACTIVATION.json").read_text(encoding="utf8"))["agents"]}
        self.assertEqual(agents["worker"]["spawn_owner"], "human")
        self.assertIn("$herdr-lead", agents["lead"]["prompt"])
        self.assertIn("CODEX_OVERRIDE_SPAWNER_UNAVAILABLE", agents["lead"]["prompt"])
        self.assertIn("NEVER use `spawn_agent`", agents["lead"]["prompt"])

    def test_claude_lead_launches_overridden_codex_worker(self):
        run_dir = self.use_lead_config()
        worker = self.config["agents"]["workers"][0]
        worker["kind"] = "codex"
        worker.pop("model")
        self.save()
        self.assertEqual(self.bind().returncode, 0)
        agents = {a["role"]: a for a in json.loads((run_dir / "ACTIVATION.json").read_text(encoding="utf8"))["agents"]}
        self.assertEqual(agents["worker"]["spawn_owner"], "lead")
        self.assertEqual(agents["worker"]["model"], "gpt-6-luna")

    def test_activation_defaults_include_the_lead(self):
        self.use_lead_config()
        self.assertEqual(self.bind().returncode, 0)
        result = self.activate("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY RUN lead (lead, claude)", result.stdout)
        self.assertNotIn("worker_t01", result.stdout)

    def test_lead_skill_stays_in_its_lane(self):
        text = (ROOT / "skills" / "herdr-lead" / "SKILL.md").read_text(encoding="utf8")
        for required in ("herdr-manager", "herdr-delegator", "Never implement", "Never act as a Worker, Verifier or Critic",
                         "Plan gate", "Acceptance gate", "final acceptance", "CODEX_OVERRIDE_SPAWNER_UNAVAILABLE"):
            self.assertIn(required, text)
        for role in ("herdr-manager", "herdr-delegator"):
            self.assertIn("`herdr-lead` governs", (ROOT / "skills" / role / "SKILL.md").read_text(encoding="utf8"))


if __name__ == "__main__":
    unittest.main()
