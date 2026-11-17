"""The lead's side of the contract: the receipts the driver alone writes, and the context it reads.

The hook refuses a worker's attempt to write the plan's receipts and tells the lead what is still
unfinished; both are the lead's boundary, so both live here.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))
import pytest  # noqa: E402
from agent_runtime import (  # noqa: E402
    agent_runtime as lib,
)
from hook_harness import (  # noqa: E402
    CONTEXT,
    HOOKS,
    PLUGIN,
    PRE,
    SHIM,
    run_pre,
)
from hook_helpers import (  # noqa: E402
    HookStoreCase,
    bash,
    denied,
)

pytestmark = pytest.mark.integration

RECEIPTS = "~/.local/state/orchestrate/agent-runtime-0123456789ab/demo/receipts"


class PlanContractTests(unittest.TestCase):
    """yaml-eec D17/D26: the driver alone writes receipts and the Lead alone approves; the hook is defence in depth
    behind the CLI, so it refuses a worker's attempt and says who can do it instead. A worker is a native subagent
    (agent_id) or a session the runtime resolves as a spawned worker; `agent-runtime` on PATH is a fake here."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()
        self.asked = Path(self.temp.name) / "asked"
        fake = self.bin / "agent-runtime"
        fake.write_text(
            "#!/bin/sh\n"
            'echo asked >> "$FAKE_ASKED"\n'
            'case "$FAKE_ROLE" in\n'
            "  broken) exit 1;;\n"
            "  garbage) echo not-json;;\n"
            '  *) printf \'{"runtime": "rt", "role": "%s"}\' "$FAKE_ROLE";;\n'
            "esac\n"
        )
        fake.chmod(0o755)

    def hook(self, payload, **options):
        """The hook run as `role` says the runtime resolves this session; `path=False` leaves no agent-runtime.

        The options are the ones the callers spell out; the ceiling on a function's parameters is
        seven, and this helper declared eight.
        """
        unknown = set(options) - {"role", "path", "home", "timeout", "extra_env", "process"}
        if unknown:
            raise TypeError("unknown hook options: %s" % sorted(unknown))
        role = options.get("role", "lead")
        path = options.get("path", True)
        home = options.get("home", None)
        timeout = options.get("timeout", 20)
        extra_env = options.get("extra_env", None)
        process = options.get("process", False)
        env = {
            "PATH": (str(self.bin) + os.pathsep if path else "") + "/usr/bin:/bin",
            "HOME": home or self.temp.name,
            "FAKE_ROLE": role,
            "FAKE_ASKED": str(self.asked),
        }
        env.update(extra_env or {})
        if not process:
            return run_pre(payload, env=env)
        done = subprocess.run(
            [sys.executable, "-B", str(PRE)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return json.loads(done.stdout) if done.stdout.strip() else None

    def test_C2_a_spawned_worker_without_agent_id_is_denied_by_the_runtimes_answer(self):
        """yaml-eec/S3: the runtime's role worker is a worker, whatever the payload says"""
        path = str(
            Path(self.temp.name) / ".local/state/orchestrate/k/demo/receipts/S1.integration.1.json"
        )
        payloads = (
            bash("agent-orchestrator plan approve x.plan.yaml"),
            bash("echo '{}' > %s/S1.integration.1.json" % RECEIPTS),
            {"tool_name": "Write", "tool_input": {"file_path": path, "content": "{}"}},
        )
        for payload in payloads:
            self.assertTrue(denied(self.hook(payload, role="worker")), payload)
            self.assertIsNone(self.hook(payload, role="lead"), payload)

    def test_C2_the_hook_fails_open_when_the_runtime_cannot_answer(self):
        """yaml-eec/D26: defence in depth; the CLI is the authority"""
        command = bash("agent-orchestrator plan approve x.plan.yaml")
        for role in ("broken", "garbage", "unknown"):
            self.assertIsNone(self.hook(command, role=role), role)
        self.assertIsNone(self.hook(command, path=False))

    def test_C2_the_runtime_is_asked_only_when_a_command_touches_the_contract(self):
        """yaml-eec/D26: no extra process for every Bash call"""
        for command in ("ls -la", "git status", "agent-orchestrator plan check x.plan.yaml"):
            self.hook(bash(command), role="worker")
        self.assertFalse(self.asked.exists())
        self.hook(bash("agent-orchestrator plan approve x.plan.yaml"), role="worker")
        self.assertTrue(self.asked.exists())

    def test_S3_a_worker_is_denied_plan_approve_however_it_is_spelled(self):
        """yaml-eec/S3"""
        for command in (
            "agent-orchestrator plan approve docs/plans/x.plan.yaml",
            "uv run --frozen agent-orchestrator plan approve x.plan.yaml --repo .",
            "python3 -m agent_orchestrator plan approve x.plan.yaml",
            "python3 orchestrate.py plan approve x.plan.yaml",
            "cd repo && agent-orchestrator --state-dir s plan approve x.plan.yaml",
            "bash -c 'agent-orchestrator plan approve x.plan.yaml'",
            "echo $(agent-orchestrator plan approve x.plan.yaml)",
        ):
            out = self.hook(bash(command, agent_id="sub-1", agent_type="worker"))
            self.assertTrue(denied(out), command)
            reason = out["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertIn("Lead", reason, command)
            self.assertIn("plan approve", reason, command)

    def test_S3_the_lead_may_approve_and_a_worker_may_check_and_list(self):
        """yaml-eec/S3"""
        self.assertIsNone(self.hook(bash("agent-orchestrator plan approve x.plan.yaml")))
        for command in (
            "agent-orchestrator plan check x.plan.yaml",
            "agent-orchestrator plans list --json",
            "echo plan approve",
            "git commit -m 'plan approve flow'",
            "agent-orchestrator status x.plan.yaml",
        ):
            self.assertIsNone(
                self.hook(bash(command, agent_id="sub-1", agent_type="worker")), command
            )

    def test_D17_a_worker_is_denied_writes_to_receipts_by_shell(self):
        """yaml-eec/D17"""
        for command in (
            "echo '{}' > %s/S1.integration.1.json" % RECEIPTS,
            "echo '{}' >> %s/S1.integration.1.json" % RECEIPTS,
            "echo '{}'>%s/S1.integration.1.json" % RECEIPTS,
            "cat x | tee %s/S1.integration.1.json" % RECEIPTS,
            "cp fake.json %s/S1.integration.1.json" % RECEIPTS,
            "mv fake.json $HOME/.local/state/orchestrate/k/demo/receipts/S1.integration.1.json",
            "rm -f %s/S1.integration.1.json" % RECEIPTS,
            "cd %s && rm S1.integration.1.json" % RECEIPTS,
            "sed -i s/FAIL/PASS/ %s/S1.integration.1.json" % RECEIPTS,
            "python3 -c \"open('%s/S1.integration.1.json','w').write('{}')\"" % RECEIPTS,
            "sh -c 'echo {} > %s/S1.integration.1.json'" % RECEIPTS,
        ):
            out = self.hook(bash(command, agent_id="sub-1", agent_type="worker"))
            self.assertTrue(denied(out), command)
            reason = out["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertIn("receipts", reason, command)
            self.assertIn("driver", reason, command)

    def test_D17_a_worker_may_read_receipts_and_write_elsewhere(self):
        """yaml-eec/D17"""
        for command in (
            "cat %s/S1.integration.1.json" % RECEIPTS,
            "ls %s" % RECEIPTS,
            "jq .result %s/S1.integration.1.json" % RECEIPTS,
            "cd %s && grep -l FAIL *.json" % RECEIPTS,
            "echo ok > /tmp/receipts/note.txt",
            "mkdir -p src/receipts && echo x > src/receipts/a.txt",
            "cp a.txt docs/receipts/a.txt",
        ):
            self.assertIsNone(
                self.hook(bash(command, agent_id="sub-1", agent_type="worker")), command
            )

    def test_D17_the_lead_and_the_driver_are_not_stopped_from_writing_receipts(self):
        """yaml-eec/D17"""
        self.assertIsNone(self.hook(bash("echo '{}' > %s/S1.integration.1.json" % RECEIPTS)))

    def test_D17_a_worker_is_denied_the_file_tools_on_receipts(self):
        """yaml-eec/D17"""
        path = str(
            Path(self.temp.name) / ".local/state/orchestrate/k/demo/receipts/S1.integration.1.json"
        )
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            payload = {"tool_name": tool, "tool_input": {"file_path": path, "content": "{}"}}
            self.assertTrue(
                denied(self.hook({**payload, "agent_id": "sub-1", "agent_type": "worker"})), tool
            )
            self.assertIsNone(self.hook(payload), tool)
        other = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/repo/src/receipts/a.py"},
            "agent_id": "sub-1",
            "agent_type": "worker",
        }
        self.assertIsNone(self.hook(other))

    def patch(self, body, key="patch", cwd=None):
        """A Codex apply_patch payload; Codex versions carry the patch under different keys."""
        text = "*** Begin Patch\n%s\n*** End Patch\n" % body
        payload = {
            "tool_name": "apply_patch",
            "tool_input": {key: [text] if key == "command" else text},
        }
        return {**payload, "cwd": cwd} if cwd else payload

    def test_D31_a_worker_is_denied_apply_patch_on_receipts(self):
        """yaml-eec/D31: a Codex worker's file edits reach the same receipts branch"""
        receipts = str(Path(self.temp.name) / ".local/state/orchestrate/k/demo/receipts")
        payloads = (
            self.patch("*** Add File: %s/S1.integration.1.json\n+{}" % receipts),
            self.patch(
                "*** Update File: %s/S1.integration.1.json\n@@\n-{}\n+{}" % receipts, key="command"
            ),
            self.patch("*** Delete File: %s/S1.integration.1.json" % receipts, key="input"),
            self.patch(
                "*** Update File: src/a.py\n*** Move to: %s/S1.integration.1.json" % receipts
            ),
            self.patch(
                "*** Add File: receipts/S1.integration.1.json\n+{}", cwd=str(Path(receipts).parent)
            ),
        )
        for payload in payloads:
            self.assertTrue(denied(self.hook(payload, role="worker")), payload)
            self.assertTrue(denied(self.hook({**payload, "agent_id": "sub-1"})), payload)
            self.assertIsNone(self.hook(payload, role="lead"), payload)

    def test_D31_apply_patch_elsewhere_passes_without_asking_the_runtime(self):
        """yaml-eec/D31: the runtime is asked only after a path matches"""
        payloads = (
            self.patch("*** Add File: src/receipts/a.py\n+x", cwd="/repo"),
            self.patch("*** Update File: README.md\n@@\n-a\n+b"),
            {"tool_name": "apply_patch", "tool_input": {}},
            {"tool_name": "apply_patch", "tool_input": "*** Begin Patch\n*** End Patch\n"},
        )
        for payload in payloads:
            self.assertIsNone(self.hook(payload, role="worker"), payload)
        self.assertFalse(self.asked.exists())

    def ledger_root(self, name="root", marked=True):
        """A state root outside the default one, with `k/demo.json` beside `k/demo/receipts/`."""
        root = Path(self.temp.name) / name
        (root / "k/demo/receipts").mkdir(parents=True)
        ledger = {"kind": "agent-orchestrator/ledger"} if marked else {"plan": "demo"}
        (root / "k/demo.json").write_text(json.dumps(ledger))
        return root

    @pytest.mark.spec("Receipts path guard")
    def test_S6_a_fifo_candidate_never_blocks_lead_or_worker(self):
        fifo = Path(self.temp.name) / "candidate.json"
        os.mkfifo(fifo)
        for role in ("lead", "worker"):
            self.assertIsNone(self.hook(bash("jq . " + str(fifo)), role=role, timeout=2))
            self.assertIsNone(self.hook(self.as_worker(bash("rm " + str(fifo))), timeout=2))

    @pytest.mark.spec("Receipts path guard")
    def test_S6_a_large_ledger_is_identified_from_its_prefix(self):
        ledger = Path(self.temp.name) / "large.json"
        with ledger.open("wb") as handle:
            handle.write(b'{"kind": "agent-orchestrator/ledger", "data": "')
            handle.seek(90 * 1024 * 1024)
            handle.write(b'"}')
        self.assertTrue(denied(self.hook(self.as_worker(bash("rm " + str(ledger))), timeout=2)))

    @pytest.mark.spec("Receipts path guard")
    def test_S6_the_lead_does_not_read_a_candidate_ledger(self):
        ledger = Path(self.temp.name) / "candidate.json"
        ledger.write_text('{"kind": "agent-orchestrator/ledger"}')
        log = Path(self.temp.name) / "reads"
        (Path(self.temp.name) / "sitecustomize.py").write_text(
            "import sys\n"
            "def audit(event, args):\n"
            f"    if event == 'open' and str(args[0]) == {str(ledger.resolve())!r}:\n"
            f"        with open({str(log)!r}, 'a') as f: f.write('read\\n')\n"
            "sys.addaudithook(audit)\n"
        )
        extra = {"PYTHONPATH": self.temp.name}
        self.assertIsNone(self.hook(bash("rm " + str(ledger)), extra_env=extra, process=True))
        self.assertFalse(log.exists())
        self.assertTrue(
            denied(
                self.hook(bash("rm " + str(ledger)), role="worker", extra_env=extra, process=True)
            )
        )
        self.assertTrue(log.exists())

    def as_worker(self, payload):
        return {**payload, "agent_id": "sub-1", "agent_type": "worker"}

    @pytest.mark.spec("Receipts path guard")
    def test_S4_a_state_receipts_path_without_a_ledger_is_not_guarded(self):
        for path in ("src/state/receipts/slice.ts", "/repo/state/receipts/a.json"):
            for command in ("echo x > %s" % path, "rm -f %s" % path, "cp a %s" % path):
                self.assertIsNone(self.hook(self.as_worker(bash(command))), command)
            write = {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": "/repo"}
            self.assertIsNone(self.hook(self.as_worker(write)), path)
        self.assertIsNone(
            self.hook(
                self.patch("*** Add File: src/state/receipts/a.ts\n+x", cwd="/repo"), role="worker"
            )
        )

    @pytest.mark.spec("Receipts path guard")
    def test_S5_a_receipts_subtree_under_the_default_root_is_denied_and_readable(self):
        default = Path(self.temp.name) / ".local/state/orchestrate/k/demo/receipts/S1.json"
        for command in (
            "echo x > %s" % default,
            "rm %s" % default,
            "cd %s && rm S1.json" % default.parent,
        ):
            out = self.hook(self.as_worker(bash(command)))
            self.assertTrue(denied(out), command)
            reason = out["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertIn("driver", reason)
            self.assertIn("re-run the evidence", reason)
        self.assertIsNone(self.hook(self.as_worker(bash("cat %s" % default))))
        self.assertIsNone(self.hook(self.as_worker(bash("ls %s" % default.parent))))

    @pytest.mark.spec("Receipts path guard")
    def test_S5_home_spellings_guard_shell_and_file_edits(self):
        for home in ("$HOME", "${HOME}", "~"):
            path = home + "/.local/state/orchestrate/k/demo/receipts/S1.json"
            for payload in (
                bash("rm " + path),
                {"tool_name": "Write", "tool_input": {"file_path": path}},
            ):
                self.assertTrue(denied(self.hook(self.as_worker(payload))), (home, payload))

    @pytest.mark.spec("Receipts path guard")
    def test_S6_a_marked_ledgers_receipts_ledger_and_lock_are_denied_under_any_root(self):
        root = self.ledger_root()
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(root)
        for base in (root, alias):
            for target in (
                "k/demo/receipts/S1.json",
                "k/demo.json",
                "k/demo.json.lock",
                "k/demo.lock",
            ):
                path = base / target
                for payload in (
                    bash("echo x > %s" % path),
                    {"tool_name": "Write", "tool_input": {"file_path": str(path)}},
                ):
                    out = self.hook(self.as_worker(payload))
                    self.assertTrue(denied(out), str(path))
                    self.assertIn("driver", out["hookSpecificOutput"]["permissionDecisionReason"])
        cwd = str(root / "k/demo")
        for name in ("receipts/S1.json", "../demo.json"):
            relative = {"tool_name": "Write", "tool_input": {"file_path": name}, "cwd": cwd}
            self.assertTrue(denied(self.hook(self.as_worker(relative))), name)
        self.assertIsNone(self.hook(self.as_worker(bash("cat %s/k/demo.json" % alias))))

    @pytest.mark.spec("Receipts path guard")
    def test_S5_S6_the_file_tools_are_denied_when_a_directory_name_has_a_space(self):
        """S6: a path is one path, whatever characters its directories carry"""
        spaced = Path(self.temp.name) / "my home (x)"
        default = spaced / ".local/state/orchestrate/k/demo/receipts"
        default.mkdir(parents=True)
        marked = spaced / "marked root"
        (marked / "k/demo/receipts").mkdir(parents=True)
        (marked / "k/demo.json").write_text(json.dumps({"kind": "agent-orchestrator/ledger"}))
        for target in (
            default / "S1.json",
            marked / "k/demo/receipts/S1.json",
            marked / "k/demo.json",
        ):
            for tool in ("Write", "Edit"):
                payload = {"tool_name": tool, "tool_input": {"file_path": str(target)}}
                out = self.hook(self.as_worker(payload), home=str(spaced))
                self.assertTrue(denied(out), (tool, str(target)))
                self.assertIsNone(self.hook(payload, home=str(spaced)), str(target))
            patch = self.patch("*** Add File: %s\n+x" % target)
            self.assertTrue(denied(self.hook(patch, role="worker", home=str(spaced))), str(target))

    @pytest.mark.spec("Receipts path guard")
    def test_S6_an_unmarked_ledger_outside_the_default_root_is_not_protected(self):
        """the documented gap until the orchestrator writes the marker"""
        root = self.ledger_root("other", marked=False)
        for target in ("k/demo/receipts/S1.json", "k/demo.json", "k/demo.json.lock"):
            command = "echo x > %s/%s" % (root, target)
            self.assertIsNone(self.hook(self.as_worker(bash(command))), command)


class LeadContextTests(HookStoreCase):
    @pytest.mark.spec("Lead reminder context")
    def test_unseen_work_prints_the_wait_reminder_for_all_three_events(self):

        def seed(generation):
            store, team = self.team(generation)
            ident = self.worker(store, team)
            self.worker(store, team, "w2", "blocked")
            self.message(
                store, team, assignment=ident["assignment_id"], message_id="m" + generation
            )

        for payload in (
            self.PROMPT,
            {"hook_event_name": "SessionStart", "source": "compact"},
            {"hook_event_name": "SessionStart", "source": "clear"},
        ):
            out = self.in_process(
                payload, seed
            )  # each run pins a generation of its own, so seed per run
            self.assertEqual(out["hookSpecificOutput"]["hookEventName"], payload["hook_event_name"])
            self.assertEqual(
                self.line(out), "agent-runtime: 2 unfinished, 1 undelivered — call wait(all)"
            )

    @pytest.mark.spec("Lead reminder context")
    def test_working_assignment_the_lead_has_not_seen_is_enough(self):

        def seed(generation):
            store, team = self.team(generation)
            self.worker(store, team)
            self.last_wait(store, team, "timeout", 0)  # a timeout never counts as seen

        self.assertEqual(
            self.line(self.in_process(self.PROMPT, seed)),
            "agent-runtime: 1 unfinished, 0 undelivered — call wait(all)",
        )

    @pytest.mark.spec("Lead reminder context")
    def test_attention_already_delivered_and_unchanged_points_at_status(self):

        def seed(generation):
            store, team = self.team(generation)
            self.worker(store, team)
            self.worker(store, team, "w2")
            self.last_wait(store, team, "attention", 2)

        self.assertEqual(
            self.line(self.in_process(self.PROMPT, seed)),
            "agent-runtime: 2 workers need you — check status",
        )

    @pytest.mark.spec("Lead reminder context")
    def test_prints_nothing_without_needs_or_with_the_wrong_team(self):

        def delivered(generation):  # blocked work, one message already named by a receipt
            store, team = self.team(generation)
            self.worker(store, team, state="blocked")
            self.message(store, team)
            self.sql(
                store, "INSERT INTO receipts VALUES (?,?,?,?)", "r1", team, "t0", json.dumps(["m1"])
            )

        def seen(generation):  # working work whose wait was delivered, no attention
            store, team = self.team(generation)
            self.worker(store, team)
            self.last_wait(store, team, "completed", 0)

        def other_generation(generation):
            store, team = self.team("999:other")
            self.worker(store, team)

        def superseded(generation):  # the hook's team exists but a newer team for the lead does too
            store, team = self.team(generation)
            self.worker(store, team)
            self.stray_team(self.other_live_generation())

        def other_lead(generation):
            store, team = self.team(generation, lead="t7")
            self.worker(store, team)

        for seed in (None, delivered, seen, other_generation, superseded, other_lead):
            self.assertIsNone(
                self.in_process(self.PROMPT, seed), getattr(seed, "__name__", "no store")
            )

    @pytest.mark.spec("Lead reminder context")
    def test_session_start_sources_other_than_compact_and_clear_print_nothing(self):

        def seed(generation):
            store, team = self.team(generation)
            self.worker(store, team)

        for source in ("startup", "resume", "fork"):
            self.assertIsNone(
                self.in_process({"hook_event_name": "SessionStart", "source": source}, seed), source
            )

    @pytest.mark.spec("Hook input errors allow")
    def test_every_failure_allows_silently(self):

        def seed(generation):
            store, team = self.team(generation)
            self.worker(store, team)

        self.assertIsNone(self.in_process(None, seed, raw="not json"))
        self.assertIsNone(self.in_process(None, seed, raw="[1]"))
        no_identity = {k: v for k, v in self.env.items() if k != "HC_TERMINAL_ID"}
        self.assertIsNone(self.in_process(self.PROMPT, seed, env=no_identity))
        locked = {}

        def seed_locked(generation):
            seed(generation)
            locked["db"] = sqlite3.connect(
                self.state / "agent-runtime" / "dispatch.sqlite3", isolation_level=None
            )
            locked["db"].execute("BEGIN EXCLUSIVE")

        try:
            self.assertIsNone(self.in_process(self.PROMPT, seed_locked))
        finally:
            locked["db"].close()
        for args in (
            [],
            ["stop", "--cli", "codex"],
            ["context", "--cli", "codex"],
        ):  # default, stop, context
            done = subprocess.run(
                ["/bin/sh", str(SHIM)] + args,
                input="{}",
                env={"PATH": "/nonexistent"},
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual((done.returncode, done.stdout), (0, ""), args)

    def hook_twice(self, payload, seed, env):
        """Run the hook twice under ONE fake CLI process (one generation); returns both outputs."""
        payload_file = self.root / "payload.json"
        payload_file.write_text(json.dumps(payload))
        script = 'read -r _; for _ in 1 2; do "$@" <"$PAYLOAD"; done'
        proc = subprocess.Popen(
            ["codex", "-c", script, "codex", sys.executable, "-B", str(CONTEXT)],
            executable="/bin/bash",
            env=dict(env, PAYLOAD=str(payload_file)),
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        seed("%d:%s" % (proc.pid, lib.lstart(proc.pid)))
        out, err = proc.communicate("go\n", timeout=30)
        self.assertEqual(proc.returncode, 0, err)
        return [json.loads(line) for line in out.splitlines()]

    @pytest.mark.spec("Hooks never write the store")
    def test_hook_never_writes_the_store_and_honours_xdg_state_home(self):

        def seed(generation):
            store, team = self.team(generation)
            self.worker(store, team)

        def snapshot():  # the database and any SQLite sidecar files
            return sorted(
                (p.name, p.stat().st_mtime_ns, p.stat().st_size)
                for p in (self.state / "agent-runtime").glob("dispatch.sqlite3*")
            )

        for flag in (None, "1"):
            env = dict(self.env, **({"AGENT_RUNTIME_DISPATCH": flag} if flag else {}))
            outs = self.hook_twice(self.PROMPT, seed, env)
            self.assertEqual(
                [self.line(o) for o in outs],
                ["agent-runtime: 1 unfinished, 0 undelivered — call wait(all)"] * 2,
            )
            before = snapshot()
            self.hook_twice(
                self.PROMPT, lambda generation: None, env
            )  # reads only (other generation)
            self.assertEqual(snapshot(), before, flag)
        self.assertFalse((self.root / "home").exists())
        missing = dict(self.env, XDG_STATE_HOME=str(self.root / "absent"))
        self.in_process(self.PROMPT, env=missing)
        self.assertFalse((self.root / "absent").exists())

    @pytest.mark.spec("Hook input errors allow")
    def test_broken_import_exits_zero_silently(self):
        broken = self.root / "broken"
        broken.mkdir()
        (broken / "lead_context.py").write_text((HOOKS / "lead_context.py").read_text())
        (broken / "hook_lib.py").write_text("raise ImportError('boom')\n")
        done = subprocess.run(
            [sys.executable, "-B", str(broken / "lead_context.py")],
            input=json.dumps(self.PROMPT),
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))

    @pytest.mark.spec("Hook input errors allow")
    def test_claude_hook_commands_allow_silently_without_python3(self):
        hooks = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
        for event in ("SessionStart", "UserPromptSubmit", "Stop"):
            command = hooks[event][0]["hooks"][0]["command"]
            done = subprocess.run(
                command,
                shell=True,
                input="{}",
                capture_output=True,
                text=True,
                timeout=20,
                env={"PATH": "/nonexistent", "CLAUDE_PLUGIN_ROOT": str(PLUGIN)},
            )
            self.assertEqual((done.returncode, done.stdout), (0, ""), event)

    @pytest.mark.spec("Session generation")
    def test_hook_shaped_process_computes_the_servers_generation(self):
        code = (
            "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); import hook_lib; "
            "from agent_runtime import hc_tools; "
            "env = {'HC_SCOPE': 's', 'HC_TERMINAL_ID': 't0', 'CODEX_THREAD_ID': 'thread-x'}; "
            "print(hook_lib.identity(env)[2]); print(hc_tools.dispatch_identity(env)[1])"
        ) % (str(HOOKS), str(PLUGIN / "packages" / "agent-runtime" / "src"))
        proc = subprocess.Popen(
            ["codex", "-c", '"$@"; exit $?', "codex", "/bin/sh", "-c", 'python3 -c "$0"', code],
            executable="/bin/bash",
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        expected = "%d:%s" % (proc.pid, lib.lstart(proc.pid))
        out, err = proc.communicate(timeout=30)
        self.assertEqual(out.split("\n")[:2], [expected, expected], err)


if __name__ == "__main__":
    unittest.main()
