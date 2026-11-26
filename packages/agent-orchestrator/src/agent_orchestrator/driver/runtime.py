"""The driver's processes: one home for every process the engine starts.

`run` is a command the lead's checkout owns (git, the gate commands); `runtime_process` and
`gh_process` are the two external CLIs the driver speaks through, and `runtime_call` is a dispatch
tool call made through the runtime's own client. Each is a seam: a test answers it in process, so a
driver run starts no process per call, and a collaborator the package is split into starts its
processes the same way the driver does.

They live beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines, and because a call every collaborator makes has one home.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from ..report_rules import require
from ..runtime_client import CliRuntimeClient
from ..runtime_client import RuntimeError as RuntimeCallError

# A call that walks or writes the whole tree (`worktree add`, `status`) scales with the repository: a large one
# outruns `run()`'s default, which stays short so the small calls still fail fast.
TREE_TIMEOUT = 600


def run(argv, timeout=15):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, result.stderr.strip() or "Command failed: " + argv[0])
    return result.stdout.strip()


def checkout_snapshot(path):
    root = Path(path).resolve(strict=True)
    # one `git rev-parse` for the three answers: the root, the head and the branch
    identity = run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel", "HEAD", "--abbrev-ref", "HEAD"]
    ).splitlines()
    require(len(identity) == 3, "git rev-parse answered %r" % identity)
    actual = Path(identity[0]).resolve()
    require(root == actual, "checkout must be the repository/worktree root")
    return {
        "checkout": str(root),
        "head": identity[1],
        "branch": identity[2],
        # This is an inventory, NOT a content fingerprint. Re-read affected source.
        "worktree_status": run(
            ["git", "-C", str(root), "status", "--porcelain=v1"], timeout=TREE_TIMEOUT
        ),
    }


def merged_snapshot(path):
    """The checkout's snapshot, or None when the tree is gone: after a merge the snapshot is evidence, not a
    precondition."""
    try:
        return checkout_snapshot(path)
    except FileNotFoundError:
        return None


def head_of(checkout):
    """The checkout's HEAD sha."""
    return run(["git", "-C", str(checkout), "rev-parse", "HEAD"])


def resolve_runtime():
    """Ask the agent-runtime CLI which runtime this session runs in; never fail inspect.

    Resolution order: `$AGENT_RUNTIME_HOME/server/agent_runtime.py`, then `agent-runtime` on PATH.
    """
    home = os.environ.get("AGENT_RUNTIME_HOME")
    script = Path(home).expanduser() / "server" / "agent_runtime.py" if home else None
    if home and not (script and script.is_file()):
        print(
            f"warning: AGENT_RUNTIME_HOME={home} has no server/agent_runtime.py; falling back to agent-runtime on PATH",
            file=sys.stderr,
        )
    argv = [sys.executable, str(script)] if script and script.is_file() else ["agent-runtime"]
    try:
        return json.loads(run(argv + ["resolve", "--json"]))
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return {"runtime": "unknown"}


def runtime_process(argv, timeout=None):
    """One `agent-runtime` process. The tests answer it in process (a fake module), so a driver run starts no
    process per call: the seam is the process, not the answer."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def gh_process(argv, timeout=None, cwd=None):
    """One `gh` process; `cwd` names the repository for a call whose argv does not (`pr create`)."""
    return subprocess.run(["gh", *argv], capture_output=True, text=True, timeout=timeout, cwd=cwd)


def ci_process(argv, **options):
    """One local CI run, started and not waited for. The tests answer it in process: the seam is the process."""
    return subprocess.Popen(argv, **options)


def runtime_call(lead, tool, arguments, timeout=120):
    """One dispatch tool call, through the runtime's public CLI.

    The seam is the `RuntimeClient` protocol: the driver never imports the runtime's modules and
    never reads its store. The process the client speaks through is the seam the tests answer, so a
    driver run starts no process per call. A refusal is a `ValueError`, the driver's word for it.
    """
    try:
        return CliRuntimeClient(lead, process=runtime_process).call(
            tool, arguments, timeout=timeout
        )
    except RuntimeCallError as error:
        raise ValueError(str(error)) from None
