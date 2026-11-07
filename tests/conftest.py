"""Pytest wiring for the suite: the markers and the default selection.

`uv run pytest` runs the fast suite (the tests marked `unit`): in-process, isolated, deterministic.
`uv run pytest -m integration` runs the tests that drive a real boundary (a subprocess, a socket, the
filesystem). `uv run pytest -n 4` runs everything.

The selection follows the command line: an explicit `-m` wins, and asking for parallelism (or for
durations) means the whole suite. A worker of xdist takes the selection from the controller.
"""

import pytest


def pytest_collection_modifyitems(config, items):
    """Keep the integration tests out of the default run, unless the command line asks for them."""
    if hasattr(config, "workerinput"):  # an xdist worker: the controller already selected
        return
    if config.getoption("markexpr", None):
        return  # an explicit marker expression wins
    if config.getoption("numprocesses", None) or config.getoption("durations", None):
        return  # parallelism or a profile of the whole suite
    if any(not argument.startswith("-") for argument in config.invocation_params.args):
        return  # a path or a node id was named: the caller picked the tests
    keep = []
    drop = []
    for item in items:
        (drop if item.get_closest_marker("integration") else keep).append(item)
    if not drop:
        return
    config.hook.pytest_deselected(items=drop)
    items[:] = keep
