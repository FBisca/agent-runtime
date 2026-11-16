"""Every pytest run in this repo writes its outcome to `.pytest/last.log`, the one place to read a run's failures.

An agent that backgrounds a run and polls its output, or re-runs a suite to see a traceback again, spends the
run twice. The log holds the counts, each failing node id, its traceback and its captured output, in the
traceback style the run asked for; the next run replaces it.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

LOG = Path(".pytest") / "last.log"


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if hasattr(config, "workerinput"):  # an xdist worker: the controller holds every report
        return
    stats = terminalreporter.stats
    counts = ", ".join(
        "%d %s" % (len(stats[key]), key)
        for key in ("failed", "passed", "skipped", "deselected", "xfailed", "xpassed", "error")
        if stats.get(key)
    )
    broken = [
        (word, report)
        for key, word in (("failed", "FAILED"), ("error", "ERROR"))
        for report in stats.get(key, [])
        if getattr(report, "longrepr", None)
    ]
    lines = [
        "pytest %s" % " ".join(config.invocation_params.args),
        "exit %d at %s" % (exitstatus, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        counts or "no tests ran",
        "",
        *("%s %s" % (word, report.nodeid or "collection") for word, report in broken),
    ]
    for word, report in broken:
        when = getattr(report, "when", "collect")
        lines += ["", "==== %s %s (%s)" % (word, report.nodeid or "collection", when)]
        lines.append(report.longreprtext)
        lines += ["--- %s ---\n%s" % (title, body) for title, body in report.sections]
    path = Path(config.rootpath) / LOG
    path.parent.mkdir(exist_ok=True)
    temp = path.with_name("%s.%d" % (path.name, os.getpid()))
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temp.replace(path)  # a reader never sees half a log
    terminalreporter.write_line(
        "full failures and captured output: %s" % path.relative_to(config.rootpath)
    )
