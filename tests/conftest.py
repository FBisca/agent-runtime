"""Pytest wiring for the suite: the markers and the default selection.

`uv run pytest` runs the fast suite (the tests marked `unit`): in-process, isolated, deterministic.
`uv run pytest -m integration` runs the tests that drive a real boundary (a subprocess, a socket, the
filesystem). `uv run pytest -n auto` runs everything.

The selection follows the command line: an explicit `-m` wins, and asking for parallelism (or for
durations) means the whole suite. A worker of xdist takes the selection from the controller.
"""

import socket
import subprocess

import pytest


class _RefusedInUnit(RuntimeError):
    """A unit test reached for a process or a socket."""


def _refuse(what):
    def refused(*args, **kwargs):
        raise _RefusedInUnit(
            f"a unit test tried to {what}: the fast suite stays in process. Mark the test "
            "`integration`, or inject the seam it reaches for."
        )

    return refused


@pytest.fixture(autouse=True)
def _no_process_or_socket_in_unit(request, monkeypatch):
    """Refuse a process or a network connection in a unit test; integration tests are untouched."""
    node = request.node
    if node.get_closest_marker("integration") or not node.get_closest_marker("unit"):
        yield
        return
    monkeypatch.setattr(subprocess, "Popen", _refuse("start a process (Popen)"))
    monkeypatch.setattr(socket.socket, "connect", _refuse("open a network socket"))
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse("open a network socket"))
    yield


def pytest_collection_modifyitems(config, items):
    """Keep the integration tests out of the default run, unless the command line asks for them."""
    if hasattr(config, "workerinput"):  # an xdist worker: the controller already selected
        return
    if config.getoption("markexpr", None):
        return  # an explicit marker expression wins
    if config.getoption("numprocesses", None) or config.getoption("durations", None):
        return  # parallelism or a profile of the whole suite
    # file_or_dir holds only the positional arguments: an option's value (`-p no:warnings`) is not one.
    if config.option.file_or_dir:
        return  # a path or a node id was named: the caller picked the tests
    keep = []
    drop = []
    for item in items:
        (drop if item.get_closest_marker("integration") else keep).append(item)
    if not drop:
        return
    config.hook.pytest_deselected(items=drop)
    items[:] = keep
