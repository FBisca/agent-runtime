"""Which harness families can be held to read/search-only, and the arguments that hold them (D38).

A deep review is read-only by contract (D18): it may read the source, its references and its tests, and it
may not modify a file, run a command or run a test. A prompt saying so is not a control - a family that
accepts a flag and silently drops it is the defect this module exists to prevent - so every family answers
here with the arguments its own CLI enforces, or with the reason it cannot, and a caller that is handed the
refusal spawns nothing.

The families are the runtime's own (`profiles.BUILTINS`), never a second list: a family added tomorrow is
asked like the rest and answers with its controls or with a refusal naming what it cannot enforce, so none
can walk past this module. A family this module does not know is refused the same way - silence is not an
answer a caller can act on.

What each control is:

- `claude` runs in its `plan` permission mode with only the read/search tools allowed and every tool that
  writes a file or runs a command denied, so a shell call has nothing left to reach.
- `pi` is started with a built-in tool allowlist of `read`, `grep`, `find` and `ls`, and with its MCP and
  extension tools off: no `bash`, `edit` or `write` is declared, and no extension can add one back.
- `codex` has no such control. Its read-only sandbox stops a command writing a file or reaching the
  network, but it still runs the command, so it cannot prohibit the command or test execution a review
  must forbid and is refused rather than trusted to a sandbox that executes what D18 does not allow.
"""

from agent_runtime import profiles

# What a read-only reviewer is allowed to do, in the words the report names its capabilities with (D18).
CAPABILITIES = ("read", "search")

# The arguments each enforceable family's CLI is given, in the order its own help documents them.
CONTROLS = {
    "claude": (
        "--permission-mode",
        "plan",
        "--allowedTools",
        "Read,Grep,Glob",
        "--disallowedTools",
        "Bash,Edit,Write,NotebookEdit",
    ),
    "pi": ("--tools", "read,grep,find,ls", "--no-mcp", "--no-extensions"),
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


def controls(family):
    """The arguments `family`'s CLI needs to be read/search-only.

    Raises `ReadOnlyError` naming the capability the family cannot enforce and the families that can, so a
    caller refuses the harness before it starts the reviewer instead of trusting a control that is absent.
    """
    if family in CONTROLS:
        return CONTROLS[family]
    reason = (
        CANNOT.get(family) or "agent-runtime does not run it, so there is no control to ask for"
    )
    raise ReadOnlyError(
        "the %s harness cannot enforce a read/search-only review: %s; ask for one that can (%s) "
        "instead - a reviewer is never spawned with a control its harness drops"
        % (family, reason, ", ".join(enforcing()))
    )


def enforcing():
    """The families the runtime knows that answer with read-only controls, in the runtime's own order."""
    return tuple(family for family in profiles.BUILTINS if family in CONTROLS)
