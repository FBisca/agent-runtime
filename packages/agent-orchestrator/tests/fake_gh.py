"""The fake `gh`: the GitHub CLI the driver polls, answered in process.

The driver asks `gh` through one seam (`gh_process` in `orchestrate.py`), so the tests answer it here instead of
starting a process per call. The executable the fixture puts on PATH is a wrapper around this module.

Every call is logged with the plan and events as they stand. `pr view` answers `view-early.json` when scripted;
otherwise a draft answers the next `view-draft.json` state (OPEN when there is none) and the next `prs.json` state is
used after that PR is made ready. Each queue's last answer repeats. `pr checks` uses `checks-ready.json` while the PR
is ready, when scripted, then `checks-<number>.json`, falling back to `checks.json`. Draft flags are per PR.
`pr ready --undo` uses `undo.json`, `pr update-branch` uses `update.json`, `pr create` prints the url in `create.json` (default pull/9); `run view` returns a canned failed log. A draft that is never made ready
raises after 100 polls, exposing a missing transition without hanging the suite.
"""

import json
import os
import re
import subprocess
import sys


class Completed:
    """What `subprocess.run` returns: the exit code and the two streams."""

    def __init__(self, argv, returncode=0, stdout="", stderr=""):
        self.args = argv
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def pop(home, name, default=None):
    """The next item of a scripted queue (the last one repeats); the default when there is none."""
    path = os.path.join(home, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as stream:
        queue = json.load(stream)
    if not queue:
        return default
    item = queue.pop(0) if len(queue) > 1 else queue[0]
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(queue, stream)
    return item


def process(argv, timeout=None, cwd=None):
    """The answer to one `gh` argv (without the `gh` itself)."""
    home = os.environ["FAKE_RUNTIME_HOME"]
    events_path = os.environ.get("FAKE_EVENTS", "")
    events = []
    if events_path and os.path.exists(events_path):
        with open(events_path, encoding="utf-8") as stream:
            events = stream.read().splitlines()
    # FAKE_PLAN names the file whose text each call records: the run's ledger, which owns the tickets' Status.
    try:
        with open(os.environ["FAKE_PLAN"], encoding="utf-8") as plan:
            plan_text = plan.read()
    except FileNotFoundError:
        plan_text = ""
    with open(os.path.join(home, "gh.log"), "a", encoding="utf-8") as log:
        log.write(json.dumps({"argv": argv, "plan": plan_text, "events": events}) + "\n")
    match = re.search(r"/pull/(\d+)", argv[2] if len(argv) > 2 else "")
    number = match.group(1) if match else "0"
    ready = os.path.join(home, "ready-%s.flag" % number)
    if argv[:2] == ["pr", "checks"]:
        name = "checks-%s.json" % number
        if os.path.exists(ready) and os.path.exists(os.path.join(home, "checks-ready.json")):
            name = "checks-ready.json"
        elif not os.path.exists(os.path.join(home, name)):
            name = "checks.json"
        code, out, err = (pop(home, name, [0, "[]", ""]) + ["", ""])[:3]
        return Completed(argv, code, out, err)
    if argv[:2] == ["pr", "ready"]:
        if "--undo" in argv:
            code, err = (pop(home, "undo.json", [0, ""]) + [""])[:2]
            if code:
                return Completed(argv, code, "", err)
            if os.path.exists(ready):
                os.remove(ready)
        else:
            with open(ready, "w", encoding="utf-8") as stream:
                stream.write("1")
        return Completed(argv, 0, "")
    if argv[:2] == ["pr", "create"]:
        # The printed url is the new PR; `create.json` scripts [exit, stdout, stderr] answers (the last repeats).
        code, out, err = (
            pop(home, "create.json", [0, "https://github.com/o/r/pull/9\n", ""]) + ["", ""]
        )[:3]
        return Completed(argv, code, out, err)
    if argv[:2] == ["pr", "update-branch"]:
        code, err = (pop(home, "update.json", [0, ""]) + [""])[:2]
        return Completed(argv, code, "", err)
    if argv[:2] == ["run", "view"]:
        return Completed(argv, 0, "FAILED STEP LOG TAIL for run " + argv[2])
    if argv[:2] != ["pr", "view"]:
        return Completed(argv, 2, "", "fake gh: unsupported " + " ".join(argv))
    state = pop(home, "view-early.json")
    if state is None:
        if os.path.exists(ready):
            state = pop(home, "prs.json", "MERGED")
        else:
            count_path = os.path.join(home, "draft-polls-%s" % number)
            with open(count_path, "a+", encoding="utf-8") as count:
                count.seek(0)
                polls = len(count.read())
                if polls >= 100:
                    raise RuntimeError("fake gh: PR %s stayed draft for 100 polls" % number)
                count.write(".")
            state = pop(home, "view-draft.json", "OPEN")
    row = {"state": state} if isinstance(state, str) else dict(state)
    row.setdefault("isDraft", not os.path.exists(ready))
    row.setdefault("mergeStateStatus", "CLEAN")
    row.setdefault("mergeable", "MERGEABLE")
    if row["state"] == "MERGED":
        # A merged PR keeps the merge commit it was first given: a scripted one sticks to later polls.
        remembered = os.path.join(home, "merge-commit-%s.json" % number)
        if "mergeCommit" in row:
            with open(remembered, "w", encoding="utf-8") as stream:
                json.dump(row["mergeCommit"], stream)
        elif os.path.exists(remembered):
            with open(remembered, encoding="utf-8") as stream:
                row["mergeCommit"] = json.load(stream)
        else:
            row["mergeCommit"] = {"oid": merged_head()}
    row.setdefault("mergeCommit", None)
    return Completed(argv, 0, json.dumps(row))


def merged_head():
    """The merge commit gh names for a merged PR. When the unit's checkout HEAD is what origin's main holds (its work
    was pushed there, as a fast-forward merge leaves it), that commit; otherwise a placeholder no repository has."""
    checkout = os.environ.get("FAKE_CHECKOUT", "")
    if not checkout or not os.path.isdir(checkout):
        return "f" * 40

    def git(*args):
        return subprocess.run(
            ["git", "-C", checkout, *args], capture_output=True, text=True
        ).stdout.split()

    head = git("rev-parse", "HEAD")
    main = git("ls-remote", "origin", "refs/heads/main")
    return head[0] if head and main and main[0] == head[0] else "f" * 40


def main(argv):
    """The executable on PATH: answer one call and exit with its code."""
    result = process(argv)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode
