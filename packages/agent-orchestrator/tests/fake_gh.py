"""The fake `gh`: the GitHub CLI the driver polls, answered in process.

The driver asks `gh` through one seam (`gh_process` in `orchestrate.py`), so the tests answer it here instead of
starting a process per call. The executable the fixture puts on PATH is a wrapper around this module.

Every call is logged with the plan and events as they stand. `pr view` answers `view-early.json` when scripted;
otherwise a draft is OPEN and the next `prs.json` state is used after that PR is made ready. Each queue's last
answer repeats. `pr checks` uses `checks-<number>.json`, falling back to `checks.json`. Draft flags are per PR.
`pr update-branch` uses `update.json`; `run view` returns a canned failed log. A draft that is never made ready
raises after 100 polls, exposing a missing transition without hanging the suite.
"""

import json
import os
import re
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


def process(argv, timeout=None):
    """The answer to one `gh` argv (without the `gh` itself)."""
    home = os.environ["FAKE_RUNTIME_HOME"]
    events_path = os.environ.get("FAKE_EVENTS", "")
    events = []
    if events_path and os.path.exists(events_path):
        with open(events_path, encoding="utf-8") as stream:
            events = stream.read().splitlines()
    with open(os.environ["FAKE_PLAN"], encoding="utf-8") as plan:
        plan_text = plan.read()
    with open(os.path.join(home, "gh.log"), "a", encoding="utf-8") as log:
        log.write(json.dumps({"argv": argv, "plan": plan_text, "events": events}) + "\n")
    match = re.search(r"/pull/(\d+)", argv[2] if len(argv) > 2 else "")
    number = match.group(1) if match else "0"
    ready = os.path.join(home, "ready-%s.flag" % number)
    if argv[:2] == ["pr", "checks"]:
        name = "checks-%s.json" % number
        if not os.path.exists(os.path.join(home, name)):
            name = "checks.json"
        code, out, err = (pop(home, name, [0, "[]", ""]) + ["", ""])[:3]
        return Completed(argv, code, out, err)
    if argv[:2] == ["pr", "ready"]:
        if "--undo" in argv:
            if os.path.exists(ready):
                os.remove(ready)
        else:
            with open(ready, "w", encoding="utf-8") as stream:
                stream.write("1")
        return Completed(argv, 0, "")
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
            state = "OPEN"
    row = {"state": state} if isinstance(state, str) else dict(state)
    row.setdefault("isDraft", not os.path.exists(ready))
    row.setdefault("mergeStateStatus", "CLEAN")
    row.setdefault("mergeable", "MERGEABLE")
    row.setdefault("mergeCommit", {"oid": "f" * 40} if row["state"] == "MERGED" else None)
    return Completed(argv, 0, json.dumps(row))


def main(argv):
    """The executable on PATH: answer one call and exit with its code."""
    result = process(argv)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode
