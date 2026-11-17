"""The `agent-runtime call` command end-to-end: a fake hc on PATH that logs argv, a fake process table,
and the dispatch store in a temp dir. The class stays in this module because its 34 public methods
are the only ones over the class-methods ceiling, and a new path has no recorded base to inherit;
the resolver, the session identity and the status views live beside it."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from agent_runtime_harness import BIN, DEAD_GENERATION

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))
from agent_runtime import agent_runtime as app  # noqa: E402
from agent_runtime.dispatch_store import Store  # noqa: E402

pytestmark = pytest.mark.integration

FAKE_HC = """#!%(python)s
import json, sys
argv = sys.argv[1:]
with open(%(log)r, "a") as log:
    log.write(json.dumps(argv) + "\\n")
replies = json.load(open(%(replies)r))
reply = replies.get(" ".join(argv[:2]), replies.get(argv[0], {"rc": 0, "out": ""}))
sys.stdout.write(reply.get("out", ""))
sys.exit(reply.get("rc", 0))
"""

FAKE_PS = """#!/bin/sh
[ "$1 $2" = "-o ppid=,args=" ] && [ "$3" = "-p" ] || exit 1
awk -v pid="$4" '$1 == pid { found = 1; $1 = ""; sub(/^ /, ""); print } END { exit !found }' %(table)s
"""


class CallTests(unittest.TestCase):  # noqa: PLR0904
    """`agent-runtime call` against a fake hc on PATH that logs argv; the dispatch store lives in a temp dir."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.table = self.dir / "ps-table"
        self.set_ancestors(
            [(5000, "/bin/zsh -l"), (6000, "/Users/x/.local/bin/herdr-core hold --socket s")]
        )
        (self.bin / "ps").write_text(FAKE_PS % {"table": str(self.table)})
        (self.bin / "ps").chmod(0o755)
        self.log = self.dir / "log"
        self.log.write_text("")
        self.replies = self.dir / "replies"
        self.set_replies({})
        hc = self.bin / "hc"
        hc.write_text(
            FAKE_HC % {"python": sys.executable, "log": str(self.log), "replies": str(self.replies)}
        )
        hc.chmod(0o755)

    def set_ancestors(self, chain, end=1):
        """Fake process table for `call`: the test runner is its parent, then each (pid, args), nearest first;
        the last one's parent is `end`, which has no entry (pid 1 ends the walk, any other pid is unreadable)."""
        pids = [os.getpid()] + [pid for pid, _ in chain]
        names = ["python3 -m unittest"] + [args for _, args in chain]
        lines = [
            "%d %d %s" % (pid, (pids + [end])[index + 1], names[index])
            for index, pid in enumerate(pids)
        ]
        self.table.write_text("\n".join(lines) + "\n")

    def set_replies(self, replies):
        base = {
            "get t0": {
                "out": json.dumps({"address": "t0", "name": "lead", "parent": None, "pid": 6000})
            },
            "get t1": {"out": json.dumps({"address": "t1", "name": "w1", "parent": "t0"})},
            "get t9": {
                "out": json.dumps({"address": "t9", "name": "w1", "parent": "t0", "pid": 6000})
            },
            "get w1": {"out": json.dumps({"address": "t9", "name": "w1", "parent": "t0"})},
            "run": {"out": json.dumps({"first_message": {"sent": True}})},
        }
        base.update(replies)
        self.replies.write_text(json.dumps(base))

    def hc_calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def call(self, *args, hc_terminal: str | None = "t0", extra_env=None, process=False):
        env = {
            "PATH": "%s:/usr/bin:/bin" % self.bin,
            "HOME": str(self.dir / "home"),
            "XDG_STATE_HOME": str(self.dir / "state"),
            "XDG_CONFIG_HOME": str(self.dir / "config"),
        }
        if hc_terminal:
            env["HC_TERMINAL_ID"] = hc_terminal
        env.update(extra_env or {})
        if not process:
            # The call's decision runs in process; `process=True` is the executable as a host starts it
            out, err = io.StringIO(), io.StringIO()
            with (
                patch.dict(app.os.environ, env, clear=True),
                patch.object(sys, "stdin", io.StringIO("")),
                # the CLI's parent is the test runner, the first row of the fake process table
                patch.object(os, "getppid", return_value=os.getpid()),
                patch.object(app, "ps_entry", app.real_ps_entry),  # this table is the fake `ps`
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                code = app.main(["call", *args])
            return code, out.getvalue(), err.getvalue()
        done = subprocess.run(
            [sys.executable, "-B", str(BIN), "call", *args],
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        return done.returncode, done.stdout, done.stderr

    @pytest.mark.spec("Call command")
    def test_S1_hc_terminal_runs_the_tool_and_prints_its_json(self):
        """S13: the one test that starts the executable as a host does"""
        code, out, err = self.call(
            "status", "--lead", "L1", "--json", '{"name": "w1"}', process=True
        )
        self.assertEqual(code, 0, err)
        info = json.loads(out)
        self.assertEqual((info["name"], info["parent"]), ("w1", "t0"))

    @pytest.mark.spec("Call command")
    def test_S1_plain_text_tool_result_is_wrapped_as_json(self):
        spawn_args = {"kind": "worker", "name": "w1", "task": "do x", "cwd": str(self.dir)}
        code, out, err = self.call("spawn", "--lead", "L1", "--json", json.dumps(spawn_args))
        self.assertEqual(code, 0, err)
        code, out, err = self.call("stop", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assertEqual(code, 0, err)
        self.assertIn("stopped", json.loads(out)["result"])

    @pytest.mark.spec("Call command")
    def test_S2_unexpected_exception_prints_error_json_not_a_traceback(self):
        (self.dir / "state").write_text(
            "not a directory"
        )  # the store cannot open under XDG_STATE_HOME
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(out)["isError"])
        self.assertNotIn("Traceback", err)

    @pytest.mark.spec("Call command")
    def test_S2_tool_error_prints_result_json_and_exits_1(self):
        code, out, err = self.call("status", "--lead", "L1", "--json", "{}")
        self.assertEqual(code, 1, err)
        result = json.loads(out)
        self.assertTrue(result["isError"])
        self.assertIn("name", result["content"][0]["text"])

    @pytest.mark.spec("Call command")
    def test_S3_outside_hc_exits_2_with_require_hc_message_and_starts_nothing(self):
        code, out, err = self.call(
            "spawn", "--lead", "L1", "--json", '{"kind": "worker", "name": "w1"}', hc_terminal=None
        )
        self.assertEqual(code, 2)
        self.assertIn("worker tools", err)
        self.assertEqual(self.hc_calls(), [])

    @pytest.mark.spec("Call command")
    def test_S4_bad_json_or_unknown_tool_exits_2_with_usage_and_starts_nothing(self):
        for args in (
            ("status", "--json", "{not json"),
            ("status", "--json", "[1]"),
            ("bogus", "--lead", "L1"),
        ):
            with self.subTest(args=args):
                code, out, err = self.call(*args)
                self.assertEqual(code, 2)
                self.assertIn("usage", err)
                self.assertEqual(self.hc_calls(), [])

    @pytest.mark.spec("Call command")
    def test_S5_same_lead_in_two_processes_shares_assignments(self):
        spawn_args = {"kind": "worker", "name": "w1", "task": "do x", "cwd": str(self.dir)}
        code, out, err = self.call("spawn", "--lead", "L1", "--json", json.dumps(spawn_args))
        self.assertEqual(code, 0, err)
        assignment = json.loads(out)["assignment_id"]
        self.set_replies(
            {
                "get t9": {
                    "out": json.dumps({"address": "t9", "name": "w1", "parent": "t0", "pid": 6000})
                }
            }
        )
        code, out, err = self.call(
            "report",
            "--json",
            json.dumps({"text": "done", "kind": "complete", "assignment_id": assignment}),
            hc_terminal="t9",
            extra_env={"CODEX_THREAD_ID": "W1"},
        )
        self.assertEqual(code, 0, err)
        code, out, err = self.call(
            "wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}'
        )
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["reason"], "completed")
        self.assertEqual(
            [(m["assignment_id"], m["kind"], m["text"]) for m in result["messages"]],
            [(assignment, "complete", "done")],
        )

    @pytest.mark.spec("Call generation conflict")
    def test_S7_other_generation_with_unfinished_work_is_refused_and_the_live_one_keeps_working(
        self,
    ):
        spawn_args = {"kind": "worker", "name": "w1", "task": "do x", "cwd": str(self.dir)}
        code, out, err = self.call("spawn", "--lead", "L1", "--json", json.dumps(spawn_args))
        self.assertEqual(code, 0, err)
        assignment = json.loads(out)["assignment_id"]
        before = [c for c in self.hc_calls() if c[0] in ("run", "kill", "rm", "send")]
        code, out, err = self.call("status", "--lead", "L2", "--json", '{"name": "w1"}')
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("L1", err)
        self.assertEqual(
            [c for c in self.hc_calls() if c[0] in ("run", "kill", "rm", "send")], before
        )
        self.set_replies(
            {
                "get t9": {
                    "out": json.dumps({"address": "t9", "name": "w1", "parent": "t0", "pid": 6000})
                }
            }
        )
        code, out, err = self.call(
            "report",
            "--json",
            json.dumps({"text": "done", "kind": "complete", "assignment_id": assignment}),
            hc_terminal="t9",
            extra_env={"CODEX_THREAD_ID": "W1"},
        )
        self.assertEqual(code, 0, err)
        code, out, err = self.call(
            "wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}'
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["reason"], "completed")

    @pytest.mark.spec("Call generation conflict")
    def test_S7_other_generation_is_refused_while_the_live_one_has_an_unacknowledged_report(self):
        spawn_args = {"kind": "worker", "name": "w1", "task": "do x", "cwd": str(self.dir)}
        code, out, err = self.call("spawn", "--lead", "L1", "--json", json.dumps(spawn_args))
        self.assertEqual(code, 0, err)
        assignment = json.loads(out)["assignment_id"]
        self.set_replies(
            {
                "get t9": {
                    "out": json.dumps({"address": "t9", "name": "w1", "parent": "t0", "pid": 6000})
                }
            }
        )
        code, out, err = self.call(
            "report",
            "--json",
            json.dumps({"text": "done", "kind": "complete", "assignment_id": assignment}),
            hc_terminal="t9",
            extra_env={"CODEX_THREAD_ID": "W1"},
        )
        self.assertEqual(code, 0, err)
        code, out, err = self.call("status", "--lead", "L2", "--json", '{"name": "w1"}')
        self.assertEqual(code, 2)
        self.assertIn("L1", err)
        code, out, err = self.call(
            "wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}'
        )
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["reason"], "completed")
        self.assertEqual(
            [(m["assignment_id"], m["kind"], m["text"]) for m in result["messages"]],
            [(assignment, "complete", "done")],
        )

    @pytest.mark.spec("Call generation conflict")
    def test_S7_other_generation_may_start_once_the_live_one_has_nothing_unfinished(self):
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assertEqual(code, 0, err)
        code, out, err = self.call("status", "--lead", "L2", "--json", '{"name": "w1"}')
        self.assertEqual(code, 0, err)

    def test_the_cli_refusal_advices_only_what_can_be_followed(self):
        """LEAD_ID cannot carry a real generation (its start stamp has spaces): the advice the refusal
        prints must be usable."""
        spawn_args = {"kind": "worker", "name": "w1", "task": "do x", "cwd": str(self.dir)}
        code, _, err = self.call("spawn", "--lead", "L1", "--json", json.dumps(spawn_args))
        self.assertEqual(code, 0, err)
        code, out, err = self.call("status", "--lead", "L2", "--json", '{"name": "w1"}')
        self.assertEqual(code, 2, out + err)
        self.assertIn("--lead L1", err)  # a generation LEAD_ID can carry

        stamp = "%d:%s" % (os.getpid(), app.lstart(os.getpid()))  # a real generation
        store = Store({"XDG_STATE_HOME": str(self.dir / "state")})
        with store.transaction() as db:
            scope, lead, _ = db.execute(
                "SELECT scope,lead,generation FROM teams ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        owner = store.team(scope, lead, stamp)
        identity = store.register(owner, "w2")
        store.bind(identity["assignment_id"], "t8", scope)  # the owner holds work
        code, out, err = self.call("status", "--lead", "L2", "--json", '{"name": "w1"}')
        self.assertEqual(code, 2, out + err)
        self.assertIn(stamp, err)  # the refusal names the generation that owns the terminal
        self.assertNotIn("--lead %s" % stamp, err)  # an advice nobody can follow
        self.assertIn("own hc terminal", err)  # the way out

    def test_the_cli_live_lead_check_reads_the_same_ownership_rule(self):
        """A newer row that left nothing behind never hides the live lead's generation."""
        spawn_args = {"kind": "worker", "name": "w1", "task": "do x", "cwd": str(self.dir)}
        code, out, err = self.call("spawn", "--lead", "L1", "--json", json.dumps(spawn_args))
        self.assertEqual(code, 0, err)
        store = Store({"XDG_STATE_HOME": str(self.dir / "state")})
        with store.transaction() as db:
            scope, lead, _ = db.execute(
                "SELECT scope,lead,generation FROM teams ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        store.team(scope, lead, DEAD_GENERATION)  # a newer row that left nothing behind
        code, out, err = self.call("status", "--lead", "L2", "--json", '{"name": "w1"}')
        self.assertEqual(code, 2, out + err)
        self.assertIn("L1", err)

    def test_ancestry_S5_parented_terminal_forced_lead_owns_worker_and_receives_report(self):
        self.set_replies(
            {"get t0": {"out": json.dumps({"address": "t0", "parent": "outer", "pid": 6000})}}
        )
        spawn_args = {"kind": "worker", "name": "w1", "task": "do x", "cwd": str(self.dir)}
        code, out, err = self.call("spawn", "--lead", "L1", "--json", json.dumps(spawn_args))
        self.assertEqual(code, 0, out + err)
        assignment = json.loads(out)["assignment_id"]
        run = next(c for c in self.hc_calls() if c[0] == "run")
        self.assertEqual(run[run.index("--parent") + 1], "t0")
        code, out, err = self.call(
            "report",
            "--json",
            json.dumps({"text": "done", "kind": "complete", "assignment_id": assignment}),
            hc_terminal="t9",
            extra_env={"CODEX_THREAD_ID": "W1"},
        )
        self.assertEqual(code, 0, out + err)
        code, out, err = self.call(
            "wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}'
        )
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            [(m["assignment_id"], m["text"]) for m in json.loads(out)["messages"]],
            [(assignment, "done")],
        )

    def test_ancestry_S6_parented_terminal_without_lead_remains_worker(self):
        self.set_replies(
            {"get t0": {"out": json.dumps({"address": "t0", "parent": "outer", "pid": 6000})}}
        )
        code, out, err = self.call(
            "status", "--json", '{"name": "w1"}', extra_env={"CODEX_THREAD_ID": "W1"}
        )
        self.assertEqual(code, 1, out + err)
        self.assertIn("caller has no matching active assignment", out)

    def test_ancestry_S5_forced_lead_override_does_not_outlive_its_call(self):
        """The forced lead role belongs to the call that asked for it: a later call in the same process
        resolves the same terminal as a worker again."""
        self.set_replies(
            {"get t0": {"out": json.dumps({"address": "t0", "parent": "outer", "pid": 6000})}}
        )
        env = {
            "PATH": "%s:/usr/bin:/bin" % self.bin,
            "HOME": str(self.dir / "home"),
            "XDG_STATE_HOME": str(self.dir / "state"),
            "XDG_CONFIG_HOME": str(self.dir / "config"),
            "HC_TERMINAL_ID": "t0",
        }
        forced = SimpleNamespace(tool="status", json='{"name": "w1"}', lead="L1")
        plain = SimpleNamespace(tool="status", json='{"name": "w1"}', lead=None)
        chain = {
            os.getppid(): (5000, "/bin/zsh -l"),
            5000: (6000, "/Users/x/.local/bin/herdr-core hold"),
            6000: (1, "/sbin/launchd"),
        }
        out = io.StringIO()
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(app, "ps_entry", lambda pid: chain.get(pid)),
            redirect_stdout(out),
        ):
            self.assertEqual(app.cmd_call(forced), 0, out.getvalue())
            self.assertEqual(
                app.cmd_call(plain), 1, out.getvalue()
            )  # a worker, not the forced lead

    SERVER = (6000, "/Users/x/.local/bin/herdr-core hold --socket s")

    def assert_refused_without_dispatch(self, code, err, needle):
        self.assertEqual(code, 2, err)
        self.assertIn(needle, err)
        self.assertEqual([c for c in self.hc_calls() if c[:1] != ["get"]], [])

    def test_ancestry_S1_claude_or_codex_below_the_server_is_refused_naming_pid_and_name(self):
        for pid, args, name in (
            (7001, "claude --agent implementer", "claude"),
            (7002, "/opt/bin/codex exec", "codex"),
            (7003, "node /usr/lib/codex/bin/codex", "codex"),
        ):
            self.set_ancestors([(5000, "/bin/zsh -c call"), (pid, args), self.SERVER])
            code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
            self.assert_refused_without_dispatch(code, err, "%d (%s)" % (pid, name))
            self.assertEqual(out, "")

    def test_ancestry_S2_walk_reaching_the_server_with_no_llm_ancestor_runs_the_call(self):
        self.set_ancestors([(5000, "/bin/zsh -l"), (5001, "python3 driver.py"), self.SERVER])
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["name"], "w1")

    def test_ancestry_S2_llm_above_the_server_is_not_an_ancestor_below_it(self):
        self.set_ancestors([(5000, "/bin/zsh -l"), self.SERVER, (8000, "claude")])
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assertEqual(code, 0, err)

    def test_ancestry_S3_unreadable_process_table_refuses(self):
        (self.bin / "ps").write_text("#!/bin/sh\nexit 1\n")
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assert_refused_without_dispatch(code, err, "process")

    def test_ancestry_S3_walk_ending_on_an_unreadable_pid_refuses(self):
        self.set_ancestors([(5000, "/bin/zsh -l"), (5001, "python3 driver.py")], end=4242)
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assert_refused_without_dispatch(code, err, "process")

    def test_ancestry_S3_walk_longer_than_the_hop_limit_refuses(self):
        self.set_ancestors(
            [(5000 + n, "/bin/sh") for n in range(app.MAX_CALL_ANCESTOR_HOPS + 2)] + [self.SERVER]
        )
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assert_refused_without_dispatch(code, err, "process")

    def test_ancestry_S3_reaching_pid_1_without_reaching_the_terminal_refuses(self):
        self.set_ancestors([(5000, "/bin/zsh -l")])
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assert_refused_without_dispatch(code, err, "pid 1")

    def test_ancestry_S3_unreadable_terminal_pid_refuses(self):
        self.set_replies({"get t0": {"rc": 1}})
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assert_refused_without_dispatch(code, err, "pid 1")

    def test_ancestry_S4_shell_forms_that_hide_call_from_the_hook_are_refused_by_the_cli(self):
        # The CLI never sees the command text: whatever shell form launched it, the claude ancestor decides.
        for form in (
            "/bin/zsh -c $(printf call)",
            "/usr/bin/env agent-runtime call wait",
            "/usr/bin/nohup agent-runtime call",
        ):
            self.set_ancestors(
                [(5000, form), (5001, "/bin/zsh -c eval"), (7001, "claude"), self.SERVER]
            )
            code, out, err = self.call(
                "wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}'
            )
            self.assert_refused_without_dispatch(code, err, "7001 (claude)")

    def test_ancestry_S4_orphaned_nohup_shell_reparented_to_pid_1_is_refused(self):
        self.set_ancestors([(5000, "sh -c sleep 1; agent-runtime call wait")])
        code, out, err = self.call(
            "wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}'
        )
        self.assert_refused_without_dispatch(code, err, "pid 1")

    def test_ancestry_S4_process_named_like_the_server_does_not_stop_the_walk(self):
        self.set_ancestors([(5000, "herdr-core bash -c probe"), (7001, "claude"), self.SERVER])
        code, out, err = self.call(
            "wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}'
        )
        self.assert_refused_without_dispatch(code, err, "7001 (claude)")

    def test_ancestry_S4_hc_exec_daemon_reparented_to_pid_1_is_refused(self):
        self.set_ancestors([(5000, "/bin/sh -c probe"), (5001, "hc --hc-launchd")])
        code, out, err = self.call(
            "wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}'
        )
        self.assert_refused_without_dispatch(code, err, "pid 1")

    def test_ancestry_S4_claude_as_the_terminal_root_is_refused(self):
        self.set_ancestors([(5000, "/bin/zsh -c probe"), (6000, "claude --agent implementer")])
        code, out, err = self.call(
            "wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}'
        )
        self.assert_refused_without_dispatch(code, err, "6000 (claude)")


if __name__ == "__main__":
    unittest.main()
