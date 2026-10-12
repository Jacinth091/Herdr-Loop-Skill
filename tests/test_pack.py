"""Offline smoke tests for role binder and activation guards."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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

    def test_sqlite_pilot_shipped_inside_controller(self):
        pilot = ROOT / "skills" / "herdr-engineering-loop" / "scripts" / "herdr_ledger.py"
        self.assertTrue(pilot.is_file())
        self.assertIn("offline", pilot.read_text(encoding="utf8").lower())

    def test_bundle_roles_exist(self):
        for skill in ("herdr-engineering-loop", "herdr-manager", "herdr-delegator", "herdr-worker", "herdr-verifier", "herdr-critic"):
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
        self.assertIn("--dangerously-skip-permissions", (base / "role-packets/worker_t01.md").read_text(encoding="utf8"))

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
        self.assertEqual(manifest["version"], "2.8.0")
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
        self.config["agents"]["workers"][0]["kind"] = "python-runner"
        self.save()
        self.assertNotEqual(self.bind().returncode, 0)

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

    def test_herdr_mock_activation_uses_pinned_agent_names_and_prompts(self):
        self.assertEqual(self.bind().returncode, 0)
        executable_dir = self.root / "bin"
        executable_dir.mkdir()
        log = self.root / "called.txt"
        fake = executable_dir / "herdr"
        fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$HERDR_TEST_LOG\"\nexit 0\n", encoding="utf8")
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
        for role in ("herdr-engineering-loop", "herdr-manager", "herdr-delegator", "herdr-worker", "herdr-verifier", "herdr-critic"):
            with self.subTest(role=role):
                path = ROOT / "skills" / role
                self.assertTrue((path / "references" / "signals.md").exists())
                text = (path / "SKILL.md").read_text(encoding="utf8")
                self.assertIn("references/signals.md", text)
                self.assertIn("ROLE_READY", text)
                self.assertIn("access", text.lower())
                if role != "herdr-manager":
                    self.assertTrue((path / "references" / "access-triage.md").exists())

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
        self.config["agents"]["critic"]["kind"] = "codex"
        self.save()
        self.assertNotEqual(self.bind().returncode, 0)

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
        fake = executable_dir / "herdr"
        fake.write_text("#!/bin/sh\nprintf '%s\n' '{}'\nexit 0\n", encoding="utf8")
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
        for role in ("herdr-engineering-loop", "herdr-manager", "herdr-delegator", "herdr-worker", "herdr-verifier", "herdr-critic"):
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
        self.assertEqual(manifest["version"], "2.8.0")
        self.assertTrue(manifest["policy"]["relay_runtime_not_required"])


if __name__ == "__main__":
    unittest.main()
