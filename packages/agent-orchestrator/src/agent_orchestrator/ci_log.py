"""The failed-step logs behind a pull request's checks: what `gh run view --log-failed` answers, and whether it is
evidence yet.

`gh pr checks` reports buckets, and a failed bucket can arrive while the workflow run is still writing its logs:
`gh run view --log-failed` then answers `<run> is still in progress; logs will be available when it is complete`. That
placeholder is not a verdict, so a check holding it stays pending for a later poll instead of raising a fix round, and
the finding that finally quotes a log quotes the failing step's own, never the placeholder.

`driver.py` is already over the structure budget at its base and may not grow, so this reading lives here and the
driver only calls it: it passes itself for the `gh` seam and for the per-round cache of run logs.
"""

import re

# The buckets `gh pr checks` reports for a check that failed or was abandoned, and for one still running.
FAILED_BUCKETS = {"fail", "failing", "cancel", "cancelled", "timed_out", "action_required"}
PENDING_BUCKETS = {"pending", "queued", "in_progress", "waiting", "requested"}

# What `gh run view --log-failed` answers before a run's logs are written: the whole answer, anchored, so a readable log
# that merely quotes the sentence is not mistaken for it. It is read ATTEMPTS times in one poll round because the run may
# finish between the `pr checks` answer and the `run view` that asks it for the logs.
PLACEHOLDER = re.compile(r"^run \d+ is still in progress; logs will be available[^\n]*$")
ATTEMPTS = 3


def placeholder(text):
    """True when a `gh run view --log-failed` answer is the placeholder of a run whose logs are not readable yet."""
    answer = str(text).strip()
    return "\n" not in answer and bool(PLACEHOLDER.match(answer))


def read_log(driver, run, fresh=False):
    """One run's failed-step log, once a round: the check and the findings share it; `fresh` re-reads a placeholder.

    `driver` owns the `gh` seam and the per-round `run_logs` cache, which `Driver.poll` resets before any read.
    """
    if fresh or run not in driver.run_logs:
        _code, out, err = driver.gh(["run", "view", run, "--log-failed"])
        driver.run_logs[run] = (out or err).strip()
    return driver.run_logs[run]


def logs_readable(driver, rows):
    """True once a failing check's log is readable: while every failing run answers the placeholder, it is pending."""
    links = [
        str(r.get("link") or "")
        for r in rows
        if str(r.get("bucket") or "").lower() in FAILED_BUCKETS
    ]
    runs = dict.fromkeys(re.findall(r"/runs/(\d+)", " ".join(links)))
    reads = (read_log(driver, run, fresh=True) for _ in range(ATTEMPTS) for run in runs)
    return not runs or any(not placeholder(text) for text in reads)
