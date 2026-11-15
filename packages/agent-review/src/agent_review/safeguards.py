"""The deterministic safeguards: rules decided from the change itself, before the model is asked.

A safeguard is a fact about the diff, not a threshold: the rule, the changed path it matched and the text
it matched, carried as `escalation.reasons[]` in the report (SG-D2, SG-D3). A hit short-circuits the
screening - no port is built and `usage.requests` is 0 - so a safeguard reason and a threshold-based one
never share a report (SG-D10), and a person reads the change instead of a model's opinion of it.

`target-outside-root` fires on a delete the change adds on a target it computes, unless the change confines
that target. It proves that nothing in the change confines the target, never that the target lies outside
the root: the value a computed target holds is not in a diff (SG-D5). So:

- a literal target that stays inside the root needs no confinement, and one that climbs out of it fires;
- a computed target is confined when its function carries a containment test on it
  (`<target>.is_relative_to(...)`, `<target>.relative_to(...)`, `<target>.resolve().parent == ...`) or calls
  a guard the same change adds whose body confines; any other computed target fires;
- a computed target the change itself writes or creates in that function is its own artifact: deleting what
  the change makes is not a delete of anything outside the change, and the historical defect's own test helper
  (`429625b`) recreates the plan file it unlinks;
- the deletes are named here rather than inferred from names, and a write is not one of them: whether a write
destroys anything depends on what the target already held, which a diff does not show, and the change that
fixed the case this rule was learned from writes a computed path itself (`109c98b`). A rule that guessed
would fire on every test that writes its own fixture.

One reason per rule and path names the first call the change adds on it: a report says which file a person
must read, and the file is the unit a review is opened on.
"""

import posixpath
import re
from dataclasses import dataclass, field

from agent_review.change import ADDED, Change, DiffLine, Hunk, indent_columns

TARGET_OUTSIDE_ROOT = "target-outside-root"

# Where a reason's evidence came from: this rule reads the change, while a rule about sensitive paths reads a
# baseline or a repository's own list (T2). The field is what P2 routes on, so it is never free text.
DIFF = "diff"

# A call that deletes its target: the whole function is spelled out so a `remove` in a sentence is not one.
_DELETIONS = ("shutil.rmtree(", "os.remove(", "os.unlink(", "os.rmdir(")
# A call that deletes the expression it is made on.
_METHODS = (".unlink(", ".rmdir(")
_QUOTED = re.compile(r"""^[rRbBuUfF]{0,2}("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')$""")
_PATH_WRAPPER = re.compile(r"^(?:pathlib\.)?Path\((.*)\)$")
_DOTTED = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
_DEF = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(?P<name>\w+)")
# A containment test as the diff shows it, read in a guard body rather than on a named target.
_CONTAINMENT = re.compile(
    r"\.\s*(?:is_relative_to|relative_to)\s*\("
    r"|\.\s*parent\s*=="
    r"|samefile\s*\("
    r"|commonpath\s*\("
)


@dataclass(frozen=True)
class EscalationReason:
    """Why a change was escalated: the rule, the changed path, the text it matched, and where that came from."""

    rule: str
    path: str
    pattern: str
    source: str = DIFF

    def as_dict(self) -> dict[str, str]:
        """The reason as `report.json` holds it, in the contract's field order (SG-D3)."""
        return {
            "rule": self.rule,
            "path": self.path,
            "pattern": self.pattern,
            "source": self.source,
        }


@dataclass(frozen=True)
class DestructiveCall:
    """One delete or overwrite a change adds, and the target it acts on as the line spells it."""

    call: str
    target: str
    # The target when the change spells it out as a string; nothing when the change computes it.
    literal: str | None = None


@dataclass
class _Scope:
    """What a rule must know about the whole change: the guards it adds, so a call to one confines."""

    confining: set[str] = field(default_factory=set)


def decide(change: Change) -> tuple[EscalationReason, ...]:
    """The reasons this change is escalated before the model is asked, empty when none fires."""
    scope = _Scope(confining=_confining_helpers(change))
    reasons: list[EscalationReason] = []
    for file in change.files:
        found = _target_outside_root(file, scope)
        if found is not None:
            reasons.append(found)
    return tuple(reasons)


def way_out(reason: EscalationReason) -> str:
    """What a person should do about one reason, in the words a refusal owes them."""
    if reason.rule == TARGET_OUTSIDE_ROOT:
        return (
            f"read {reason.path}: the change deletes with `{reason.pattern}` on a target it computes, and "
            "nothing in the change confines that target to the root it is written under. Confine it before "
            "the call - `target.is_relative_to(root)` against the root this run reviews - or drop the call; "
            "the review cannot tell how far the target reaches, so a person decides."
        )
    return f"read {reason.path}: the {reason.rule} rule matched it, and a person decides before this merges."


def destructive_calls(text: str) -> tuple[DestructiveCall, ...]:
    """Every delete `text` makes, with the target it acts on.

    A line in a diff is read, not parsed as Python: the calls are the named ones, their arguments are split
    as written, and a receiver that is not a name is carried as a computed target with nothing to match.
    """
    calls: list[DestructiveCall] = []
    for primitive in _DELETIONS:
        for at in _occurrences(text, primitive):
            invocation = _invocation(text, at + len(primitive) - 1)
            if invocation is None or not invocation.arguments:
                continue
            target = invocation.arguments[0]
            if not target.strip():
                continue
            calls.append(_destructive(text[at : invocation.close + 1], target))
    for primitive in _METHODS:
        for at in _occurrences(text, primitive):
            invocation = _invocation(text, at + len(primitive) - 1)
            if invocation is None:
                continue
            receiver = _receiver(text, at)
            start = at if receiver is None else receiver.start
            target = "" if receiver is None else receiver.text
            calls.append(_destructive(text[start : invocation.close + 1], target))
    return tuple(calls)


def _destructive(call: str, target: str) -> DestructiveCall:
    """One call, with the target its line names and whether that target is spelled out."""
    return DestructiveCall(
        call=" ".join(call.split()), target=target.strip(), literal=_literal(target)
    )


def _target_outside_root(file, scope: _Scope) -> EscalationReason | None:
    """The first delete on an unconfined target this path adds, as one reason."""
    for hunk in file.hunks:
        for index, line in enumerate(hunk.lines):
            if line.kind != ADDED:
                continue
            for call in destructive_calls(line.text):
                if _confined(call, hunk, index, scope):
                    continue
                return EscalationReason(rule=TARGET_OUTSIDE_ROOT, path=file.path, pattern=call.call)
    return None


def _confined(call: DestructiveCall, hunk: Hunk, index: int, scope: _Scope) -> bool:
    """Whether anything in the change confines `call`'s target to where it may reach (SG-D5)."""
    if call.literal is not None:
        return not _escapes(call.literal)
    names = _names(call.target)
    for line in hunk.region(index):
        if any(_confines(name, line) for name in names):
            return True
        if any(_creates(name, line) for name in names):
            return True
        if any(_calls(line.text, guard) for guard in scope.confining):
            return True
    return False


def _creates(name: str, line: DiffLine) -> bool:
    """Whether `line` writes or creates `name` itself, so the target is the change's own artifact."""
    subject = re.escape(name)
    return (
        re.search(
            rf"(?<![\w.]){subject}\s*\.\s*(?:write_text|write_bytes|touch|mkdir)\s*\(", line.text
        )
        is not None
    )


def _confines(name: str, line: DiffLine) -> bool:
    """Whether `line` is a containment test on `name`, the target being what is confined."""
    subject = re.escape(name)
    return (
        re.search(
            rf"(?<![\w.]){subject}\s*\.\s*(?:is_relative_to|relative_to)\s*\("
            rf"|(?<![\w.]){subject}\s*\.\s*resolve\s*\(\s*\)\s*\.\s*parent\s*=="
            rf"|(?<![\w.]){subject}\s*\.\s*parent\s*==\s*.*\bresolve\s*\("
            rf"|samefile\s*\(\s*{subject}\b",
            line.text,
        )
        is not None
    )


def _confining_helpers(change: Change) -> set[str]:
    """The guards the change adds: functions whose added body confines, and those that call one.

    A change that factors its confinement into a guard is still confined, so a call from the region to such
    a guard answers for the target it is handed. One hop of calls is followed, which is what a diff shows
    without a data-flow analysis; a confinement that reaches deeper is not read, and the rule then fires.
    """
    bodies = _added_defs(change)
    confining = {
        name for name, body in bodies.items() if any(_CONTAINMENT.search(line) for line in body)
    }
    growing = True
    while growing:
        growing = False
        for name, body in bodies.items():
            if name in confining:
                continue
            if any(_calls(line, guard) for guard in confining for line in body):
                confining.add(name)
                growing = True
    return confining


def _added_defs(change: Change) -> dict[str, tuple[str, ...]]:
    """Every function the change adds, with the lines it adds as the body."""
    bodies: dict[str, list[str]] = {}
    for file in change.files:
        for hunk in file.hunks:
            for index, line in enumerate(hunk.lines):
                if line.kind != ADDED:
                    continue
                match = _DEF.match(line.text)
                if match is None:
                    continue
                indent = indent_columns(line.text)
                body: list[str] = []
                for following in hunk.lines[index + 1 :]:
                    if following.kind != ADDED:
                        if following.text.strip():
                            break
                        continue
                    if following.text.strip() and indent_columns(following.text) <= indent:
                        break
                    body.append(following.text)
                bodies.setdefault(match.group("name"), []).extend(body)
    return {name: tuple(lines) for name, lines in bodies.items()}


def _calls(text: str, name: str) -> bool:
    """Whether `text` calls `name`."""
    return re.search(rf"(?<![\w]){re.escape(name)}\s*\(", text) is not None


def _names(target: str) -> tuple[str, ...]:
    """The spellings a containment test may use for this target, or none when it is not a name."""
    text = target.strip()
    if text.endswith(".resolve()"):
        text = text[: -len(".resolve()")]
    wrapped = re.match(r"^str\(\s*(.*?)\s*\)$", text)
    if wrapped:
        text = wrapped.group(1)
    if not _DOTTED.match(text):
        return ()
    if text.startswith("self."):
        return (text, text[len("self.") :])
    return (text,)


def _literal(target: str) -> str | None:
    """The target `target` spells out as a string, or nothing when the change computes it."""
    text = target.strip()
    wrapped = _PATH_WRAPPER.match(text)
    if wrapped:
        text = wrapped.group(1).strip()
    quoted = _QUOTED.match(text)
    if quoted is None:
        return None
    return quoted.group(1)[1:-1]


def _escapes(literal: str) -> bool:
    """Whether a target the change spells out leaves the root it is written relative to."""
    text = literal.replace("\\", "/")
    if text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:/", text):
        return True
    return posixpath.normpath(text).startswith("..")


@dataclass(frozen=True)
class _Receiver:
    """An expression a method call is made on: where it starts, and as written."""

    start: int
    text: str


def _receiver(text: str, at: int) -> _Receiver | None:
    """The expression a method call is made on, as written before its dot."""
    before = text[:at].rstrip()
    match = re.search(
        r"[A-Za-z_][\w.\[\]]*(?:\([^()]*\))?(?:\s*[/+]\s*[A-Za-z_][\w.\[\]]*(?:\([^()]*\))?)*$",
        before,
    )
    if match is None:
        return None
    return _Receiver(start=len(before) - len(match.group(0)), text=match.group(0).strip())


def _occurrences(text: str, primitive: str) -> tuple[int, ...]:
    """Where `primitive` starts in `text`, each occurrence once."""
    found: list[int] = []
    at = text.find(primitive)
    while at != -1:
        found.append(at)
        at = text.find(primitive, at + 1)
    return tuple(found)


@dataclass(frozen=True)
class _Invocation:
    """One call as a line spells it: where its `(` closes, and its top-level arguments."""

    close: int
    arguments: tuple[str, ...]


def _invocation(text: str, opening: int) -> _Invocation | None:
    """The call whose `(` sits at `opening`, reading quotes and nesting as Python does."""
    depth = 0
    quote = ""
    escaped = False
    start = opening + 1
    arguments: list[str] = []
    for index in range(opening, len(text)):
        character = text[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
            continue
        if character in "\"'":
            quote = character
        elif character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
            if depth == 0:
                arguments.append(text[start:index])
                return _Invocation(close=index, arguments=tuple(arguments))
        elif character == "," and depth == 1:
            arguments.append(text[start:index])
            start = index + 1
    return None
