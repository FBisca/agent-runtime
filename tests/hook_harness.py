"""The hook tests' harness: the plugin's paths, its entries loaded in process, and a fake CLI process.

`test_hooks.py` drives each entry two ways — in process, where the verdict is what is under test, and
under a fake `codex` process, where the stdin/stdout/exit boundary and the generation ancestry are. The
paths, the loaders and that fake process belong to more than one class, and a module that carries them
grows until nobody reads it whole. Nothing here is collected: the file is named without `test_`.
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import IO, cast
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"
PRE = HOOKS / "pre_tool_use.py"
START = HOOKS / "subagent_start.py"
CONTEXT = HOOKS / "lead_context.py"
STOP = HOOKS / "stop_guard.py"
SHIM = PLUGIN / "bin" / "agent-runtime-hook"
sys.path.insert(0, str(PLUGIN / "packages" / "agent-runtime" / "src"))

from agent_runtime import agent_runtime as lib  # noqa: E402

_MODULES: dict[Path, object] = {}


def _load(script):
    """The entry at `script` as a module, loaded once: a hook is imported, not re-executed per row."""
    if script not in _MODULES:
        spec = importlib.util.spec_from_file_location("hook_" + script.stem, script)
        if spec is None or spec.loader is None:  # a path that holds no python module
            raise ImportError("no loader for %s" % script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULES[script] = module
    return _MODULES[script]


PRE_MODULE = _load(PRE)
CONTEXT_MODULE = _load(CONTEXT)
HOOK_LIB = _load(HOOKS / "hook_lib.py")


def run_context_raw(payload, env, generation, raw=None):
    """The context hook's `main` in process: its answer as printed, with the generation pinned.

    The entry reads stdin, resolves its identity and prints; only that stdin/stdout/exit boundary needs a
    process, and the tests that prove it still start one. A table of payload shapes otherwise pays a
    fake CLI process, a python start and a process-table read per row.
    """
    out, err = io.StringIO(), io.StringIO()
    with (
        mock.patch.object(
            sys, "stdin", io.StringIO(raw if raw is not None else json.dumps(payload))
        ),
        mock.patch.object(sys, "stdout", out),
        mock.patch.object(sys, "stderr", err),
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(lib, "FORCED_GENERATION", generation),
    ):
        try:  # the entry's own fail-open: a broken import prints nothing either
            HOOK_LIB.fail_open(CONTEXT_MODULE.main)
        except Exception:  # noqa: BLE001 - an import error also prints nothing
            pass
    printed = out.getvalue()
    return (json.loads(printed) if printed.strip() else None), err.getvalue()


def run_pre_raw(raw, args=(), env=None):
    """The PreToolUse hook's `main` in process on raw stdin: its decision as printed, and its exit code."""
    out = io.StringIO()
    with (
        mock.patch.object(sys, "stdin", io.StringIO(raw)),
        mock.patch.object(sys, "stdout", out),
        mock.patch.dict(os.environ, env if env is not None else os.environ, clear=env is not None),
    ):
        code = PRE_MODULE.main(list(args))
    return code, out.getvalue()


def run_pre(payload, args=(), env=None):
    """Pure verdict logic runs in process; the boundary tests still start the hook as a process."""
    _, out = run_pre_raw(json.dumps(payload), args, env)
    return json.loads(out) if out.strip() else None


class FakeCliProcess:
    """One fake `codex` process that runs hook commands on request, so successive hooks (and seeding between
    them) share one generation. Each run returns (parsed stdout or None, stderr, returncode)."""

    def __init__(self, env, root):
        self.root, self.count = Path(root), 0
        self.env = dict(env)
        self.proc = subprocess.Popen(
            ["codex", "-c", 'while read -r n; do . "$DIR/s$n.sh"; echo ok; done', "codex"],
            executable="/bin/bash",
            env=dict(env, DIR=str(root)),
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.generation = "%d:%s" % (self.proc.pid, lib.lstart(self.proc.pid))
        self.stdin, self.stdout, self.stderr = (
            cast(IO[str], pipe) for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr)
        )

    def run(self, script, payload=None, raw=None, args=(), process=False):
        """script is a Python hook path, or a list for a command run as-is (the shim); args follow it.

        A hook path runs in process under this CLI's own generation, unless `process=True`: the tests that
        prove the stdin/stdout/exit boundary, and a broken import that must reach the interpreter, still
        start one. A list always starts a process, because the command it names is the boundary.
        """
        if not process and not isinstance(script, list):
            return self.in_process(script, payload, raw, args)
        self.count += 1
        n = self.count
        (self.root / ("p%d.json" % n)).write_text(raw if raw is not None else json.dumps(payload))
        command = (
            " ".join(script) if isinstance(script, list) else "%s -B %s" % (sys.executable, script)
        )
        (self.root / ("s%d.sh" % n)).write_text(
            '%s %s <"$DIR/p%d.json" >"$DIR/o%d" 2>"$DIR/e%d"; echo $? >"$DIR/r%d"\n'
            % (command, " ".join(args), n, n, n, n)
        )
        self.stdin.write("%d\n" % n)
        self.stdin.flush()
        assert self.stdout.readline().strip() == "ok"
        out = (self.root / ("o%d" % n)).read_text()
        return (
            json.loads(out) if out.strip() else None,
            (self.root / ("e%d" % n)).read_text(),
            int((self.root / ("r%d" % n)).read_text()),
        )

    def in_process(self, script, payload=None, raw=None, args=()):
        """The same hook entry in this test's process, its generation pinned to this fake CLI's."""
        module = _load(Path(script))
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(
                sys, "stdin", io.StringIO(raw if raw is not None else json.dumps(payload))
            ),
            mock.patch.object(sys, "stdout", out),
            mock.patch.object(sys, "stderr", err),
            mock.patch.dict(os.environ, self.env, clear=True),
            mock.patch.object(lib, "FORCED_GENERATION", self.generation),
        ):
            if Path(script) == CONTEXT:
                HOOK_LIB.fail_open(module.main)
            else:
                HOOK_LIB.fail_open(lambda: module.main(list(args)))
        printed = out.getvalue()
        return (json.loads(printed) if printed.strip() else None), err.getvalue(), 0

    def close(self):
        self.stdin.close()
        self.proc.wait(timeout=10)
        self.stdout.close()
        self.stderr.close()
