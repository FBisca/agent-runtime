"""Which harness families a read-only reviewer may be spawned through: the runtime's answer, asked here.

A deep review may read and search and nothing else (D18), and the arguments that hold each harness family to
that are the runtime's answer, one per family, beside the family list it ranges over (D38, P2-D2). This module
asks that answer rather than keeping a second table that would drift from it.

The ask happens when a deep review needs it, not when this package is imported: screening is answered with
PyYAML alone, and an environment where the runtime is absent can hold no family to anything, so a deep review
is refused there - naming the install that would answer it - instead of the whole invocation dying on an import.
"""

import importlib


class ReadOnlyError(ValueError):
    """A harness no control can hold to reading and searching: nothing is spawned with it."""


def _answer():
    """The runtime module that answers for each family, or a refusal naming what this environment lacks."""
    try:
        return importlib.import_module("agent_runtime.read_only")
    except ImportError:
        raise ReadOnlyError(
            "no harness can be held to a read/search-only review here: agent-runtime, which answers for "
            "every family it knows, is not installed in this environment; install agent-runtime beside "
            "agent-review, so a reviewer is spawned with the controls its harness enforces"
        ) from None


def capabilities() -> tuple[str, ...]:
    """What a read-only reviewer may do, in the runtime's own words."""
    return tuple(_answer().CAPABILITIES)


def controls(family: str) -> tuple[str, ...]:
    """The arguments `family`'s CLI needs to be read-only, or a refusal naming the families that can."""
    answer = _answer()
    try:
        return tuple(answer.controls(family))
    except answer.ReadOnlyError as refusal:
        raise ReadOnlyError(str(refusal)) from None


def enforcing() -> tuple[str, ...]:
    """The families that answer with read-only controls, in the runtime's own order."""
    return tuple(_answer().enforcing())
