"""Which harness families can be held to read/search-only, and how each is asked one question (D38).

A deep review is read-only by contract (D18): it may read the source, its references and its tests, and it
may not modify a file, run a command or run a test. A prompt saying so is not a control - a family that
accepts a flag and silently drops it is the defect this module exists to prevent - so every family answers
here with the arguments its own CLI enforces, or with the reason it cannot, and a caller that is handed the
refusal spawns nothing.

The families are the runtime's own (`profiles.BUILTINS`), never a second list: a family added tomorrow is
asked like the rest and answers with its run or with a refusal naming what it cannot enforce, so none can
walk past this module. A family this module does not know is refused the same way - silence is not an answer
a caller can act on.

A run is the arguments that hold the family to reading and searching plus the flag that makes it answer one
question without a terminal, and the prompt always travels on stdin: a family's options can be variadic (both
tool options of `claude` are), so a positional prompt after them is read as another value of theirs, and both
families' print modes read the prompt from stdin and write the answer to stdout. A family whose CLI could not
read its prompt there would have to be added here with that said, and its invocation proven, rather than
assumed.

What each run is:

- `claude` runs in its `plan` permission mode with only the read/search tools allowed and every tool that
  writes a file or runs a command denied, so a shell call has nothing left to reach; `-p` prints the answer.
- `pi` is started with a built-in tool allowlist of `read`, `grep`, `find` and `ls`, and with its MCP and
  extension tools off: no `bash`, `edit` or `write` is declared, and no extension can add one back; `-p`
  prints the answer.
- `codex` has no such control. Its read-only sandbox stops a command writing a file or reaching the network,
  but it still runs the command, so it cannot prohibit the command or test execution a review must forbid and
  is refused rather than trusted to a sandbox that executes what D18 does not allow.
"""

from dataclasses import dataclass

from agent_runtime import profiles

# What a read-only reviewer is allowed to do, in the words the report names its capabilities with (D18).
CAPABILITIES = ("read", "search")


@dataclass(frozen=True)
class Run:
    """One family's read-only, one-question run: the arguments its CLI is given, and the prompt on stdin."""

    # What holds the family to reading and searching, in the order its own help documents it.
    controls: tuple[str, ...]
    # What makes it answer once and print the answer to stdout, instead of opening a terminal.
    flags: tuple[str, ...]

    @property
    def argv(self) -> tuple[str, ...]:
        """The arguments after the family's own name, in the order they are passed."""
        return (*self.controls, *self.flags)


# Each family the runtime can hold, as the run its own CLI needs. One table, so a family cannot answer with
# controls and then be started without the flag that makes it answer at all.
RUNS = {
    "claude": Run(
        controls=(
            "--permission-mode",
            "plan",
            "--allowedTools",
            "Read,Grep,Glob",
            "--disallowedTools",
            "Bash,Edit,Write,NotebookEdit",
        ),
        flags=("-p",),
    ),
    "pi": Run(
        controls=("--tools", "read,grep,find,ls", "--no-mcp", "--no-extensions"),
        flags=("-p",),
    ),
}

# Why a family the runtime knows cannot be held, in the family's own terms: the control it lacks, not a
# preference. A family absent from both tables is not one the runtime runs at all.
CANNOT = {
    "codex": (
        "its read-only sandbox stops a command writing a file, but it still runs the command, so it "
        "cannot prohibit command or test execution"
    ),
}


class ReadOnlyError(ValueError):
    """A harness that cannot be held to read/search-only: nothing is spawned with it."""


def run(family):
    """How `family`'s CLI is asked one question read-only, or a refusal naming the families that can be.

    Raises `ReadOnlyError` naming the capability the family cannot enforce and the families that can, so a
    caller refuses the harness before it starts the reviewer instead of trusting a control that is absent.
    """
    if family in RUNS:
        return RUNS[family]
    reason = cannot(family) or "agent-runtime does not run it, so there is no control to ask for"
    raise ReadOnlyError(
        "the %s harness cannot enforce a read/search-only review: %s; ask for one that can (%s) "
        "instead - a reviewer is never spawned with a control its harness drops"
        % (family, reason, ", ".join(enforcing()))
    )


def cannot(family):
    """Why `family` cannot be held to reading and searching, in the runtime's own terms, or None.

    A family the runtime knows and cannot hold has a reason of its own; None says the runtime has no answer
    for the family at all, which a caller reads as a defect rather than as a reason to refuse quietly.
    """
    return CANNOT.get(family)


def controls(family):
    """The arguments that hold `family` to reading and searching, as an audit reads them."""
    return run(family).controls


def enforcing():
    """The families the runtime knows that answer with a read-only run, in the runtime's own order."""
    return tuple(family for family in profiles.BUILTINS if family in RUNS)
