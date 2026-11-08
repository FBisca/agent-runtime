#!/usr/bin/env python3
"""Wait for a pull request's checks to settle: the CI gate's wait, in this repo.

The driver gates a unit on its pull request's checks. The wait used to be a script outside this repo (`wait-checks`
on PATH), so the driver called whatever it found and the tests had to fake a whole executable to answer it. Here the
wait is a module the driver imports: `wait(ref, timeout)` answers like the script did — a `PASS <owner>/<repo>#<pr>`
or a `FAIL <owner>/<repo>#<pr>` line, the failing checks and the tail of the failed log — and the tests answer it
with a mock module instead of a process per call.

Command line: `python3 wait_checks.py <owner>/<repo>#<pr> [more refs...]`
Environment: `WAIT_CHECKS_INTERVAL` (seconds between polls, default 30), `WAIT_CHECKS_LOG_LINES` (log lines kept,
default 40).
Exit code: 0 when every ref passed, 1 when one failed, 2 on bad input.
"""

import json
import os
import re
import subprocess
import sys
import time

REGISTERING = "no checks reported"
REGISTER_ATTEMPTS = 12
REGISTER_INTERVAL = 10


class Result:
    """What the CI gate needs: the exit code, what the wait printed, and whether it ran out of time."""

    def __init__(self, returncode=0, stdout="", stderr="", timed_out=False):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out


def gh(argv, timeout=None):
    """One `gh` call: (exit code, stdout, stderr)."""
    result = subprocess.run(["gh", *argv], capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr


def resolve(ref):
    """`<owner>/<repo>#<pr>` (or a bare PR of the current repository) as (repo, pr)."""
    repo, pr = "", str(ref)
    if "#" in ref:
        repo, pr = ref.split("#", 1)
    if not re.fullmatch(r"\d+", pr):
        raise ValueError("bad PR %r" % ref)
    if not repo:
        repo = asked(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    elif "/" not in repo:
        repo = asked(["repo", "view", "--json", "owner", "-q", ".owner.login"]) + "/" + repo
    return repo, pr


def asked(argv, timeout=None):
    """One `gh` call that has to succeed: its stdout."""
    code, out, err = gh(argv, timeout=timeout)
    if code != 0:
        raise ValueError(err.strip() or "gh %s failed (exit %d)" % (" ".join(argv), code))
    return out.strip()


def remaining(deadline):
    """The seconds left before the deadline, or None when there is no deadline."""
    return None if deadline is None else max(0.0, deadline - time.monotonic())


def failing_checks(repo, pr):
    """The failing and cancelled checks as `  <name>: <bucket> <link>` lines."""
    code, out, _ = gh(["pr", "checks", pr, "-R", repo, "--json", "name,bucket,link"])
    if code != 0:
        return []
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return []
    return [
        "  %s: %s %s" % (row.get("name", ""), row.get("bucket", ""), row.get("link", ""))
        for row in rows
        if row.get("bucket") in ("fail", "cancel")
    ]


def failed_log(repo, pr, log_lines):
    """The tail of the first failed check's log, one indented line per line."""
    code, out, _ = gh(["pr", "checks", pr, "-R", repo, "--json", "bucket,link"])
    if code != 0:
        return []
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return []
    link = next((row.get("link") for row in rows if row.get("bucket") == "fail"), "")
    run = re.search(r"/runs/(\d+)", link or "")
    if not run:
        return []
    code, out, _ = gh(["run", "view", run.group(1), "-R", repo, "--log-failed"])
    return ["  " + line for line in out.splitlines()[-log_lines:]] if code == 0 else []


def wait(ref, timeout=None, interval=None, log_lines=None):
    """Wait for the checks of one pull request and answer like the `wait-checks` script did.

    A timeout is not a failure: the wait answers `timed_out`, and the caller polls again."""
    interval = int(os.environ.get("WAIT_CHECKS_INTERVAL", "30")) if interval is None else interval
    log_lines = (
        int(os.environ.get("WAIT_CHECKS_LOG_LINES", "40")) if log_lines is None else log_lines
    )
    repo, pr = resolve(ref)
    label = "%s#%s" % (repo, pr)
    deadline = None if timeout is None else time.monotonic() + timeout
    for attempt in range(REGISTER_ATTEMPTS):
        code, out, _ = gh(["pr", "checks", pr, "-R", repo])
        if code == 0 and REGISTERING not in out:
            break
        if deadline is not None and time.monotonic() + REGISTER_INTERVAL > deadline:
            return Result(1, "NONE %s: no checks reported yet\n" % label, timed_out=True)
        time.sleep(REGISTER_INTERVAL)
    else:
        return Result(
            1, "NONE %s: no checks reported after %d min\n" % (label, REGISTER_ATTEMPTS // 6)
        )
    try:
        code, _, err = gh(
            ["pr", "checks", pr, "-R", repo, "--watch", "--fail-fast", "--interval", str(interval)],
            timeout=remaining(deadline),
        )
    except subprocess.TimeoutExpired:
        return Result(1, "", timed_out=True)
    if code == 0:
        return Result(0, "PASS %s\n" % label)
    lines = ["FAIL %s" % label] + failing_checks(repo, pr) + failed_log(repo, pr, log_lines)
    return Result(1, "\n".join(lines) + "\n", stderr=err)


def main(argv):
    """The command line: wait for each ref in turn and print what each wait answered."""
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    results = []
    for ref in argv:
        try:
            results.append(wait(ref))
        except ValueError as error:
            print("wait-checks: %s" % error, file=sys.stderr)
            return 2
    for result in results:
        sys.stdout.write(result.stdout)
    return 0 if all(result.returncode == 0 for result in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
