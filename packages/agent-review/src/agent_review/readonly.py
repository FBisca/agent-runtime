"""How a reviewer is started: the runtime's answer for one family, asked here (D38, P2-D2).

A deep review may read and search and nothing else (D18), and the arguments that hold each harness family to
that are the runtime's answer, one per family, beside the family list it ranges over. This module asks that
answer rather than keeping a second table that would drift from it, and hands the review the arguments to
start with.

The ask happens when a deep review needs it, not when this package is imported: screening is answered with
PyYAML alone, and an environment where the runtime is absent can hold no family to anything, so a deep review
is refused there - naming the install that would answer it - instead of the whole invocation dying on an import.
"""

import importlib
from dataclasses import dataclass


class ReadOnlyError(ValueError):
    """A harness no control can hold to reading and searching: nothing is spawned with it."""


@dataclass(frozen=True)
class Invocation:
    """The arguments that start one family read-only, and the controls that hold it to that.

    The prompt is not among them: it travels on the reviewer's stdin, where no variadic option of the family's
    own parsing can read it as another value (see `agent_runtime.read_only`).
    """

    controls: tuple[str, ...]
    flags: tuple[str, ...]

    @property
    def argv(self) -> tuple[str, ...]:
        """The arguments after the family's own name, in the order they are passed."""
        return (*self.controls, *self.flags)


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


def invocation(family: str) -> Invocation:
    """How `family`'s CLI is asked one question read-only, or a refusal naming the families that can be."""
    answer = _answer()
    try:
        run = answer.run(family)
    except answer.ReadOnlyError as refusal:
        raise ReadOnlyError(str(refusal)) from None
    return Invocation(controls=tuple(run.controls), flags=tuple(run.flags))


def controls(family: str) -> tuple[str, ...]:
    """The arguments that hold `family` to reading and searching, as the report records them."""
    return invocation(family).controls


def enforcing() -> tuple[str, ...]:
    """The families that answer with a read-only run, in the runtime's own order."""
    return tuple(_answer().enforcing())


def cannot(family: str) -> str | None:
    """Why the runtime cannot hold `family` to reading and searching, in its own words, or None.

    A family the runtime knows and cannot hold has a reason of its own, and a caller reads it rather than
    paraphrasing the refusal; None says the runtime has not answered for the family at all.
    """
    return _answer().cannot(family)
