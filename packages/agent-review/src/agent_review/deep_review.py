"""The one deep review an escalation asks for: its scope, its instructions and its read-only spawn (D12, D14).

An escalated change is read by one reviewer, never by a swarm (D14). The review is asked for with
`--deep-review <harness>`, and everything about it is configuration rather than the reviewer's choice: the
instruction sets it is given, and the rules and paths that make each set apply (D07, D29, D14).

The scope follows the reasons that fired, never a guess (P2-D3): a reason that names the question it judged
is a calibrated signal, which points where the reviewer should look, so a review of those alone is
`focused`; a reason about the change itself - a sensitive path, a safeguard, a coverage gap - is nothing a
question bounds, so the review is `full`. The rule is one sentence over the reasons: a review whose every
reason names its question is focused, and one reason about the change makes it full.

The instructions are composed the same way (P2-D5): a set that declares no `applies` joins every review, a
set matching a fired rule or a changed path joins too, and the report names the composed ids in declaration
order.

The reviewer is read-only (D18): the harness family answers with the arguments that hold it to reading and
searching, or refuses and nothing is spawned (`agent_runtime.read_only`). It is handed the frozen diff,
because a reviewer that may not run a command cannot ask git what changed.

What the reviewer answers is judged where the report is assembled: this stage spawns it once and records
what it was asked, so a spawn is checked without a model (P2-D1).
"""

import json
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent_review import readonly
from agent_review.errors import ReviewError
from agent_review.numbers import number
from agent_review.safeguards import EscalationReason, matches

FOCUSED = "focused"
FULL = "full"
# The canned reviewer's record of what it was asked for, written beside its answers (P2-D1).
SPAWNS = "spawns.json"
# One review's own budget, when a configuration declares none: ten minutes for the reviewer to stop in.
DEFAULT_TIMEOUT_SECONDS = 600.0
SETTINGS_FIELDS = ("timeout_seconds", "instructions")
INSTRUCTION_FIELDS = ("id", "text", "applies")
APPLIES_FIELDS = ("rules", "paths")


@dataclass(frozen=True)
class Instruction:
    """One specialized instruction set: what it says, and the rules or changed paths that make it join.

    A set that declares neither applies to every review, which is what makes `evidence-first` the set every
    invocation carries (P2-D5).
    """

    id: str
    text: str
    rules: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()

    def joins(self, fired: Iterable[str], changed_paths: Iterable[str]) -> bool:
        """Whether this set is composed for a review with these fired rules and these changed paths."""
        if not (self.rules or self.paths):
            return True
        if set(self.rules).intersection(fired):
            return True
        return any(matches(pattern, path) for pattern in self.paths for path in changed_paths)

    def as_dict(self) -> dict[str, str]:
        """The set as a reviewer is handed it: its id and its text."""
        return {"id": self.id, "text": self.text}


@dataclass(frozen=True)
class Settings:
    """What a configuration says a deep review is: how long it may take, and the sets it may be given."""

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    instructions: tuple[Instruction, ...] = ()


@dataclass(frozen=True)
class Target:
    """What a review looks at: the frozen change, both revisions, the changed paths and the checkout.

    They travel together because a reviewer is handed all four or none: the diff because it may not run a
    command, the revisions so the report names what was reviewed, the paths for the instruction sets that
    match a changed path, and the checkout the reviewer reads.
    """

    diff: str
    base: str
    head: str
    paths: tuple[str, ...]
    root: Path


@dataclass(frozen=True)
class Spawn:
    """One reviewer asked to investigate: what it may do, where it looks, and what it is told."""

    harness: str
    # The arguments that hold this harness to reading and searching, as the report of an audit reads them.
    controls: tuple[str, ...]
    base: str
    head: str
    scope: str
    instructions: tuple[Instruction, ...]
    signals: tuple[EscalationReason, ...]
    paths: tuple[str, ...]
    timeout_seconds: float
    diff: str
    root: Path

    @property
    def capabilities(self) -> tuple[str, ...]:
        """What the reviewer may do, which is what every read-only control leaves it."""
        return readonly.capabilities()

    @property
    def prompt(self) -> str:
        """What the reviewer is told: the change, its scope, the composed instructions and the signals.

        The frozen diff travels in the prompt because the reviewer may not run a command, so it cannot ask
        git what changed; naming the change only would leave the review reading a checkout blind.
        """
        lines = [
            f"Review the change {self.base}..{self.head} read-only: read and search {self.root}, and "
            "modify nothing, run no command and run no test.",
            f"Scope: {self._scope_instruction()}",
            "Instructions:",
        ]
        lines += [f"- {entry.id}: {entry.text}" for entry in self.instructions] or ["- none"]
        lines.append("Signals that fired:")
        lines += [f"- {signal.rule}{self._signal_subject(signal)}" for signal in self.signals]
        lines += ["Change:", self.diff]
        return "\n".join(lines)

    @staticmethod
    def _signal_subject(signal: EscalationReason) -> str:
        """The question or path a signal is about, with its detail, as one line."""
        subject = signal.question or signal.path
        if subject and signal.detail:
            return f" on {subject}: {signal.detail}"
        if subject:
            return f" on {subject}: {signal.pattern}"
        return f": {signal.detail or signal.pattern}"

    def _scope_instruction(self) -> str:
        if self.scope == FULL:
            return "the whole change, not only where the signals point."
        return "the flagged areas below, and the code they touch."

    def as_dict(self) -> dict[str, Any]:
        """The request as a canned reviewer records it: everything the report says, and the controls it ran."""
        return {
            "harness": self.harness,
            "controls": list(self.controls),
            "base": self.base,
            "head": self.head,
            "scope": self.scope,
            "capabilities": list(self.capabilities),
            "instructions": [entry.as_dict() for entry in self.instructions],
            "signals": [signal.as_dict() for signal in self.signals],
            "paths": list(self.paths),
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class Review:
    """What the review asked for and what it spent: the `deep_review` section of the one report (D39)."""

    harness: str
    scope: str
    instructions: tuple[Instruction, ...]
    signals: tuple[EscalationReason, ...]
    spawns: int = 1

    @property
    def capabilities(self) -> tuple[str, ...]:
        """What the reviewer was allowed to do, as the read-only answer gave it."""
        return readonly.capabilities()

    def as_dict(self) -> dict[str, Any]:
        """The section as `report.json` holds it, in the contract's field order."""
        return {
            "harness": self.harness,
            "scope": self.scope,
            "capabilities": list(self.capabilities),
            "instructions": [entry.id for entry in self.instructions],
            "spawns": self.spawns,
            "signals": [signal.as_dict() for signal in self.signals],
        }


class Reviewer(Protocol):
    """The one seam a deep review reaches: the harness's own CLI, or a canned reviewer replacing it."""

    # The family this reviewer is spawned through, named in the report beside what it was asked.
    harness: str
    # The arguments that hold that family to reading and searching, recorded so an audit reads them.
    controls: tuple[str, ...]

    # `...` is a protocol member's body: it carries the signature and no caller runs it.
    def spawn(self, request: Spawn) -> None: ...


class FixtureReviewer:
    """A canned reviewer behind the same seam, so every check stays offline and repeats exactly (P2-D1)."""

    def __init__(self, harness: str, controls: Sequence[str], directory: Path | str) -> None:
        self.harness = harness
        self.controls = tuple(controls)
        self.directory = Path(directory)
        self.recorded: list[dict[str, Any]] = []
        if not self.directory.is_dir():
            raise ReviewError(
                f"the deep-review fixture {self.directory} is not a directory; point "
                f"--deep-review-fixture at the directory the canned reviewer records {SPAWNS} in"
            )

    def spawn(self, request: Spawn) -> None:
        """Record what the reviewer was asked, so a check reads the spawn instead of reaching a model."""
        self.recorded.append(request.as_dict())
        path = self.directory / SPAWNS
        try:
            path.write_text(json.dumps(self.recorded, indent=2) + "\n", encoding="utf-8")
        except OSError as error:
            raise ReviewError(
                f"cannot record the deep review in {path}: {error.strerror or error}; point "
                "--deep-review-fixture at a directory this invocation may write"
            ) from None


class HarnessReviewer:
    """One read/search-only reviewer, run through its harness's own CLI with the controls it enforces."""

    def __init__(self, harness: str, controls: Sequence[str]) -> None:
        self.harness = harness
        self.controls = tuple(controls)

    def spawn(self, request: Spawn) -> None:
        """Start the reviewer with its read-only arguments and the prompt, prompt last (D38).

        A harness that cannot start, times out or exits nonzero stops the invocation rather than reporting a
        review that never happened; what an incomplete review's status is belongs to the report that judges
        the reviewer's answer (D20).
        """
        argv = [self.harness, *self.controls, request.prompt]
        try:
            done = subprocess.run(
                argv,
                cwd=request.root,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ReviewError(
                f"the {self.harness} reviewer did not answer within {request.timeout_seconds} s"
            ) from None
        except OSError as error:
            raise ReviewError(
                f"cannot start the {self.harness} reviewer: {error.strerror or error}; install the "
                "harness or name one that this checkout can run"
            ) from None
        if done.returncode != 0:
            raise ReviewError(
                f"the {self.harness} reviewer exited {done.returncode}: "
                f"{(done.stderr or done.stdout).strip()[-2000:]}"
            )


def settings(declared: Any, path: Path | str) -> Settings:
    """The `deep_review` block as the settings a review runs with, or a refusal naming the field by path.

    A declaration no stage can read is refused before anything is screened (D29, D30): a timeout of zero
    would stop the reviewer before it read anything, and an instruction set with an `applies` that names
    neither a rule nor a path would never join a review.
    """
    if declared is None:
        return Settings()
    if not isinstance(declared, Mapping):
        raise ReviewError(
            f"{path}: deep_review must be a mapping of settings, not {type(declared).__name__}"
        )
    unknown = sorted(set(declared) - set(SETTINGS_FIELDS))
    if unknown:
        raise ReviewError(
            f"{path}: deep_review has an unknown field {unknown[0]!r}; it takes "
            f"{', '.join(SETTINGS_FIELDS)}"
        )
    return Settings(
        timeout_seconds=_timeout(declared.get("timeout_seconds"), path),
        instructions=_instructions(declared.get("instructions"), path),
    )


def scope_of(reasons: Iterable[EscalationReason]) -> str:
    """`focused` when every reason names the question it judged, `full` when one is about the change (P2-D3)."""
    return FULL if any(not reason.question for reason in reasons) else FOCUSED


def composed(
    settings: Settings, reasons: Iterable[EscalationReason], paths: Iterable[str]
) -> tuple[Instruction, ...]:
    """The instruction sets this review is given, in the configuration's declaration order (P2-D5)."""
    fired = {reason.rule for reason in reasons}
    changed = tuple(paths)
    return tuple(entry for entry in settings.instructions if entry.joins(fired, changed))


def review(
    *,
    settings: Settings,
    escalation: Iterable[EscalationReason],
    target: Target,
    reviewer: Reviewer,
) -> Review:
    """Spawn the one reviewer this escalation asks for, and answer what it was asked (D14, D19, P2-D3).

    The scope and the instructions are decided here, from the reasons the tool recorded, so neither is the
    reviewer's choice; the reviewer is spawned once, with the frozen change in hand (D18, D20).
    """
    signals = tuple(escalation)
    request = Spawn(
        harness=reviewer.harness,
        controls=reviewer.controls,
        base=target.base,
        head=target.head,
        scope=scope_of(signals),
        instructions=composed(settings, signals, target.paths),
        signals=signals,
        paths=target.paths,
        timeout_seconds=settings.timeout_seconds,
        diff=target.diff,
        root=target.root,
    )
    reviewer.spawn(request)
    return Review(
        harness=request.harness,
        scope=request.scope,
        instructions=request.instructions,
        signals=signals,
    )


def _timeout(declared: Any, path: Path | str) -> float:
    """How long the reviewer may take, refused when a zero or unreadable number would stop it at once."""
    if declared is None:
        return DEFAULT_TIMEOUT_SECONDS
    seconds = number(declared, "deep_review.timeout_seconds", path)
    if seconds == 0:
        raise ReviewError(
            f"{path}: deep_review.timeout_seconds must be a positive number of seconds, not "
            f"{declared!r}; a reviewer given no time reads nothing"
        )
    return seconds


def _instructions(declared: Any, path: Path | str) -> tuple[Instruction, ...]:
    """The specialized sets a review may be given, in declaration order, refused by path when unreadable."""
    if declared is None:
        return ()
    if not isinstance(declared, list):
        raise ReviewError(
            f"{path}: deep_review.instructions must be a list of instruction sets, not "
            f"{type(declared).__name__}"
        )
    sets: list[Instruction] = []
    seen: dict[str, int] = {}
    for index, entry in enumerate(declared):
        instruction = _instruction(entry, f"deep_review.instructions[{index}]", path)
        if instruction.id in seen:
            raise ReviewError(
                f"{path}: deep_review.instructions[{index}].id repeats {instruction.id!r}, already "
                f"declared by deep_review.instructions[{seen[instruction.id]}]; the report names the "
                "composed sets by id, so each is declared once"
            )
        seen[instruction.id] = index
        sets.append(instruction)
    return tuple(sets)


def _instruction(entry: Any, name: str, path: Path | str) -> Instruction:
    """One instruction set, refused by field path when a stage cannot read it."""
    if not isinstance(entry, Mapping):
        raise ReviewError(
            f"{path}: {name} must be a mapping with {', '.join(INSTRUCTION_FIELDS)}, not "
            f"{type(entry).__name__}"
        )
    unknown = sorted(set(entry) - set(INSTRUCTION_FIELDS))
    if unknown:
        raise ReviewError(f"{path}: {name} has an unknown field {unknown[0]!r}")
    for required in ("id", "text"):
        if required not in entry:
            raise ReviewError(f"{path}: {name}.{required} is missing")
    identifier, text = entry["id"], entry["text"]
    for field_name, value in (("id", identifier), ("text", text)):
        if not isinstance(value, str) or not value.strip():
            raise ReviewError(
                f"{path}: {name}.{field_name} must be a non-empty string, not {value!r}"
            )
    rules, paths = _applies(entry.get("applies"), identifier, name, path)
    return Instruction(id=identifier, text=text, rules=rules, paths=paths)


def _applies(
    declared: Any, identifier: str, name: str, path: Path | str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The rules and changed paths that make one set join, or a refusal naming the field that is unreadable."""
    if declared is None:
        return (), ()
    if not isinstance(declared, Mapping):
        raise ReviewError(
            f"{path}: {name}.applies must be a mapping of rules and paths, not "
            f"{type(declared).__name__}"
        )
    unknown = sorted(set(declared) - set(APPLIES_FIELDS))
    if unknown:
        raise ReviewError(
            f"{path}: {name}.applies has an unknown field {unknown[0]!r}; it takes "
            f"{', '.join(APPLIES_FIELDS)}"
        )
    if not declared:
        raise ReviewError(
            f"{path}: {name}.applies declares nothing, so the set {identifier!r} would never join a "
            "review; name the rules or the paths that make it apply, or drop `applies` to join every one"
        )
    return (
        _names(declared.get("rules"), f"{name}.applies.rules", identifier, path),
        _names(declared.get("paths"), f"{name}.applies.paths", identifier, path),
    )


def _names(declared: Any, field_name: str, identifier: str, path: Path | str) -> tuple[str, ...]:
    """A list of rules or path patterns a set applies to, refused by path when it is not one."""
    if declared is None:
        return ()
    if not isinstance(declared, list):
        raise ReviewError(
            f"{path}: {field_name} must be a list for the instruction set {identifier!r}, not "
            f"{type(declared).__name__}"
        )
    for index, entry in enumerate(declared):
        if not isinstance(entry, str) or not entry.strip():
            raise ReviewError(
                f"{path}: {field_name}[{index}] must be a non-empty string for the instruction set "
                f"{identifier!r}, not {entry!r}"
            )
    return tuple(entry.strip() for entry in declared)
