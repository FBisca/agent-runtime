"""The tool harness: the fake hc, its in-process stand-in, and the ToolCase bases the tool tests share."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_mcp_server import FakeCli, InProcessSession, Session

RUNTIME_SRC = Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"
sys.path.insert(0, str(RUNTIME_SRC))


FAKE = """#!%(python)s
import json, os, sys, time
argv = sys.argv[1:]
with open(%(log)r, "a") as log:
    log.write(json.dumps(argv) + "\\n")
# Concurrent calls share the replies file: write it whole and swap it in, so a reader never sees it half written.
def save(replies):
    temp = "%%s.%%d" %% (%(replies)r, os.getpid())
    with open(temp, "w") as stream:
        json.dump(replies, stream)
    os.replace(temp, %(replies)r)
replies = json.load(open(%(replies)r))
key = argv[0] + ":" + argv[1] if len(argv) > 1 and argv[0] + ":" + argv[1] in replies else argv[0]
reply = replies.get(key, {"rc": 0, "out": ""})
if replies.get("profile_ready_at") and argv[0] == "read":
    if time.monotonic() < replies["profile_ready_at"]:
        reply = {"out": ""}
# Execute only opt-in test programs, using a simulated login environment. Log no full environment.
if argv[0] == "run" and replies.get("execute_profile"):
    import hashlib, secrets, subprocess
    login = dict(os.environ)
    login.update(replies.get("login_env", {}))
    login["PROFILE_TEST_SECRET"] = secrets.token_hex(24)
    login["ANTHROPIC_API_KEY"] = login["PROFILE_TEST_SECRET"]
    login["PROFILE_TEST_DIGEST"] = hashlib.sha256(login["PROFILE_TEST_SECRET"].encode()).hexdigest()
    if replies.get("exec_failure_path"):
        login["PATH"] = replies["exec_failure_path"] + ":/" + login["PROFILE_TEST_SECRET"]
    child = subprocess.run(argv[10:], env=login, text=True, capture_output=True)
    with open(%(overlay)r, "a") as log:
        log.write(child.stdout)
    artifacts = {"argv": %(log)r, "overlay": %(overlay)r}
    if argv[10].endswith("agent-runtime-profile"):
        artifacts["config"] = argv[12]
    with open(%(overlay)r + ".audit", "w") as audit:
        json.dump({name: login["PROFILE_TEST_SECRET"].encode() in open(path, "rb").read()
                   for name, path in artifacts.items()}, audit)
    if child.returncode:
        if login["PROFILE_TEST_SECRET"] in child.stderr:
            sys.stderr.write("secret-leaked ")
            sys.exit(1)
        if not replies.get("profile_exit"):
            sys.stderr.write(child.stderr)
            sys.exit(child.returncode)
        replies["read:w1"] = {"out": child.stderr}
        replies["get:w1"] = {"out": json.dumps({"address": "t9", "parent": "t0", "state": "finished"})}
        if replies["profile_exit"] == "delayed":
            replies["profile_ready_at"] = time.monotonic() + 0.3
            replies["get:w1"] = {"out": json.dumps({"address": "t9", "parent": "t0", "state": "live"})}
        reply = {"out": json.dumps({"first_message": {"sent": replies["profile_exit"] != "unsent"}})}
        save(replies)
# Opt-in live-name model: replies["live"] lists terminal names; hc run refuses a live name like real hc.
if "live" in replies and argv[0] in ("run", "rm"):
    name = argv[argv.index("--name") + 1] if argv[0] == "run" else argv[1]
    if argv[0] == "run" and name in replies["live"]:
        sys.stderr.write(json.dumps({"error": {"kind": "name_taken", "message": "name " + repr(name) + " is taken"}}))
        sys.exit(1)
    if argv[0] == "run" and reply.get("rc", 0) == 0:
        replies["live"].append(name)
    if argv[0] == "rm" and name in replies["live"]:
        replies["live"].remove(name)
    save(replies)
if reply.get("sleep"):
    time.sleep(reply["sleep"])
sys.stdout.write(reply.get("out", ""))
sys.stderr.write(reply.get("err", ""))
sys.exit(reply.get("rc", 0))
"""


class FakeHcProcess:
    """One `hc` call answered by the fake's own script in this process: the same log, replies and exit code as the
    program, without a process per call."""

    def __init__(self, argv, code, text):
        out, err, saved = io.StringIO(), io.StringIO(), sys.argv
        sys.argv = list(argv)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                exec(code, {"__name__": "__main__"})
            self.returncode = 0
        except SystemExit as exit:
            self.returncode = (
                exit.code if isinstance(exit.code, int) else int(exit.code is not None)
            )
        finally:
            sys.argv = saved
        self.args, self.pid = argv, 0
        self.out, self.err = out.getvalue(), err.getvalue()
        if not text:
            self.out, self.err = self.out.encode(), self.err.encode()

    def communicate(self, input=None, timeout=None):
        return self.out, self.err

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass

    terminate = kill

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@contextmanager
def hc_in_process(fake):
    """Every `hc` the code under test starts, bare or behind the `env -u` scrub, is answered in this process by the
    fake script at `fake`; any other program still starts for real. The process boundary has its own tests."""
    source = Path(fake).read_text().split("\n", 1)[1]  # the fake's body, without its shebang
    code = compile(source, str(fake), "exec")
    real = subprocess.Popen

    def popen(argv, *args, **kwargs):
        command = unwrap([str(a) for a in argv]) if isinstance(argv, (list, tuple)) else []
        if command[:1] and os.path.basename(command[0]) == "hc":
            text = kwargs.get("text") or kwargs.get("universal_newlines") or kwargs.get("encoding")
            return FakeHcProcess(["hc", *command[1:]], code, text)
        return real(argv, *args, **kwargs)

    with mock.patch.object(subprocess, "Popen", popen):
        yield


TOML = 'name = "implementer"\ndeveloper_instructions = """\nCODEX IMPLEMENTER RULES\n"""\n'


def raw(name, parent, state="idle", app=None):
    status = {"app": app, "state": state} if app else state
    return json.dumps({"address": "t9", "name": name, "parent": parent, "status": status})


FIXTURES = Path(__file__).resolve().parent / "fixtures"


PROVIDERS = [
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_AUTO_MODE_SERVER",
    "CLAUDE_CODE_AUTO_MODE_MODEL",
    "MINIMAX_API_KEY",
    "PI_API_KEY",
    "PI_PROVIDER",
    "PI_MODEL",
]


def scrub(keys):
    """The `env -u NAME...` prefix a built-in spawn runs under."""
    return ["env"] + [arg for key in sorted(keys) for arg in ("-u", key)]


def unwrap(argv):
    """The CLI argv after the built-in `env -u NAME...` scrub."""
    if argv[:1] != ["env"]:
        return argv
    i = 1
    while argv[i : i + 1] == ["-u"]:
        i += 2
    return argv[i:]


class ToolCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        (self.dir / "bin").mkdir()
        agents = self.dir / "home" / ".codex" / "agents"
        agents.mkdir(parents=True)
        (agents / "implementer.toml").write_text(TOML)
        self.log = self.dir / "log"
        self.log.write_text("")

    in_process = False

    def start(self, replies=None, env=None, new_cli: bool | FakeCli = False, process=False):
        replies = dict(replies or {})
        replies.setdefault("get:t0", {"out": raw("lead", None)})
        replies.setdefault("get:t1", {"out": raw("worker", "t0")})
        (self.dir / "replies").write_text(json.dumps(replies))
        hc = self.dir / "bin" / "hc"
        hc.write_text(
            FAKE
            % {
                "python": sys.executable,
                "log": str(self.log),
                "replies": str(self.dir / "replies"),
                "overlay": str(self.dir / "overlay"),
            }
        )
        hc.chmod(0o755)
        full = {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / "home"),
            "HC_TERMINAL_ID": "t0",
            "CODEX_THREAD_ID": "test-session",
        }
        full.update(env or {})
        # The tool's decision runs in process; a test that is about the pipe, the process ancestry or a
        # call in its own thread asks for the server process.
        session = (
            InProcessSession(full)
            if self.in_process and not (process or new_cli)
            else Session(full, new_cli=new_cli)
        )
        self.addCleanup(session.close)
        session.handshake()
        return session

    def call(self, session, name, arguments):
        result = session.send("tools/call", {"name": name, "arguments": arguments})["result"]
        return result, result["content"][0]["text"]

    def runs(self, command=None):
        calls = [json.loads(l) for l in self.log.read_text().splitlines()]
        return [c for c in calls if command is None or c[0] == command]


class InProcessToolCase(ToolCase):
    """Tool decisions that need no server process of their own run in this one."""

    in_process = True
