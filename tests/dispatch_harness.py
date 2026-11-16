"""The dispatch tests' shared pieces; pytest does not collect this module."""

# A generation whose process is gone: no process carries that pid, so the row is provably dead.
DEAD_GENERATION = "999999999:Wed Nov  4 10:00:00 2026"


def stop(process):
    """Kill a child and reap it in one cleanup.

    `addCleanup` runs last-in-first-out: a `wait` registered after a `kill` runs first and sits out the
    child's whole sleep, so both belong in one cleanup."""
    if process.poll() is None:
        process.kill()
    process.wait()
