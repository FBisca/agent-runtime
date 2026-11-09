"""A bounded benchmark runner: every run gets a budget, and a run that overruns it is terminated and recorded.

S3: WHEN a test run stalls or exceeds a declared budget, THEN the benchmark terminates it and records a failure
instead of running indefinitely.

The runner starts the command in its own process group, so the whole tree (the driver, the fakes it calls, the
commands they run) dies with it: a stall costs the budget, never the suite. Every run leaves one record: the
outcome, the reason, the budget it ran under and the source revision it ran on.
"""

import json
import os
import signal
import subprocess
import time

DEFAULT_BUDGET = 30.0


def read_records(path):
    """The records a benchmark run left behind."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream.read().splitlines() if line.strip()]


def kill_tree(proc):
    """The command's whole process group, dead: nothing it started outlives it."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    proc.wait(timeout=10)


def tree_gone(pgid, timeout=10.0):
    """Whether the process group is empty: nothing the run started outlived it.

    A killed tree is not gone the instant the signal lands: the processes stay in the group as zombies
    until the system reaps them, and under load that takes longer than a moment. The wait is bounded,
    so a tree that really outlived the run still fails the check.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def run_budgeted(
    argv,
    env=None,
    cwd=None,
    budget_seconds=DEFAULT_BUDGET,
    label="",
    revision=None,
    records=None,
    tail=4000,
):
    """Run `argv` under a budget and return its record.

    The record says `pass` when the command ended by itself with exit code 0, and `failure` when it exited with
    another code or overran the budget (in which case it is terminated first). `records` is a path: the record is
    appended to it as one JSON line, so a benchmark run leaves its evidence behind.
    """
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    pgid = os.getpgid(proc.pid)
    record = {
        "label": label,
        "revision": revision,
        "budget_seconds": budget_seconds,
        "argv": list(argv),
    }
    try:
        out, err = proc.communicate(timeout=budget_seconds)
        record["outcome"] = "pass" if proc.returncode == 0 else "failure"
        record["returncode"] = proc.returncode
        record["reason"] = "" if proc.returncode == 0 else "the run exited %s" % proc.returncode
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        out, err = proc.communicate()
        record["outcome"] = "failure"
        record["returncode"] = None
        record["reason"] = "the run exceeded its budget of %ss and was terminated" % budget_seconds
        record["tree_gone"] = tree_gone(pgid)
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    record["stdout"] = (out or "")[-tail:]
    record["stderr"] = (err or "")[-tail:]
    if records:
        with open(records, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def verify(records_path, revision, out_path=None):
    """Mark a source revision verified, but only on the records of runs that passed.

    The validator is deterministic: it reads the records, and writes the manifest only when every record of that
    revision passed. A run that failed, was terminated or never happened leaves the revision unverified. Returns the
    manifest, or None when the revision is not verified.
    """
    records = [
        record for record in read_records(records_path) if record.get("revision") == revision
    ]
    if not records or any(record.get("outcome") != "pass" for record in records):
        return None
    manifest = {
        "revision": revision,
        "verified": True,
        "runs": len(records),
        "artifacts": [
            {
                "label": record.get("label"),
                "argv": record.get("argv"),
                "elapsed_seconds": record.get("elapsed_seconds"),
            }
            for record in records
        ],
    }
    if out_path:
        with open(out_path, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, sort_keys=True)
    return manifest
