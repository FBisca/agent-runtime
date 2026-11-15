"""The deterministic safeguards: rules decided from the change itself, before the model is asked.

A safeguard is a fact about the diff, not a threshold: the rule, the changed path it matched and the text
it matched, carried as `escalation.reasons[]` in the report (SG-D2, SG-D3). A hit short-circuits the
screening - no port is built and `usage.requests` is 0 - so a safeguard reason and a threshold-based one
never share a report (SG-D10), and a person reads the change instead of a model's opinion of it.

`target-outside-root` fires on a delete the change adds on a target it computes, unless the change confines
that target. It proves that nothing in the change confines the target, never that the target lies outside
the root: the value a computed target holds is not in a diff (SG-D5). So:

- a literal target that stays inside the root needs no confinement, and one that climbs out of it fires;
- a computed target is confined when its function carries a containment test on it - the side of the comparison
  the test is made on (`<target>.is_relative_to(...)`, `<target>.resolve().is_relative_to(...)`,
  `<target>.relative_to(...)`, `<target>.resolve().parent == ...`, `samefile(<target>, ...)`,
  `commonpath([<target>, ...])`). The side a test compares against is the boundary it confines the target to,
  never a target of its own: `folder.is_relative_to(self.root)` confines `folder`, and deleting `self.root`
  is still a delete of a target no test in the change is over. A computed target is confined too when it is
  handed to a guard the same change adds whose body confines that parameter - bound by the call's own order, so
  the caller may spell the argument anything - and when the guard's body confines the attribute of `self` the
  delete names, which is the same target in both functions; any other computed target fires;
- confinement is per target, never per function, and never by a name that does not cross a call: a guard answers
  for the argument bound to a parameter it confines and for the attributes its body confines, never for a
  caller's local that happens to be spelled like one of its own. A second delete in the same function is its own
  target and is answered on its own, however many guards the function calls;
- a computed target the change itself writes or creates in that function is its own artifact: deleting what
  the change makes is not a delete of anything outside the change, and the historical defect's own test helper
  (`429625b`) recreates the plan file it unlinks;
- the deletes are named here rather than inferred from names, and a write is not one of them: whether a write
  destroys anything depends on what the target already held, which a diff does not show, and the change that
  fixed the case this rule was learned from writes a computed path itself (`109c98b`). A rule that guessed
  would fire on every test that writes its own fixture.

One reason per rule and path names the first call the change adds on it: a report says which file a person
must read, and the file is the unit a review is opened on.

`protected-path` fires on a changed path a pattern declares sensitive, on either side of a rename and for
an added, modified or deleted file alike. The patterns are configuration (SG-D4, SG-D6): the packaged
baseline ships the mandatory ones and a repository's `.agent-review.yaml` adds to them, so a reason names
where the pattern came from as well as the path it matched.
"""

import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from agent_review.change import ADDED, Change, DiffLine, FileChange, Hunk, indent_columns

TARGET_OUTSIDE_ROOT = "target-outside-root"
PROTECTED_PATH = "protected-path"

# Where a reason's evidence came from: this rule reads the change, a sensitive-path rule reads the mandatory
# baseline or a repository's own list (T2), and a member rule reads a declaration. The field is what P2
# routes on, so it is never free text.
DIFF = "diff"
BASELINE = "baseline"
REPOSITORY = "repository"

# A call that deletes its target: the whole function is spelled out so a `remove` in a sentence is not one.
_DELETIONS = ("shutil.rmtree(", "os.remove(", "os.unlink(", "os.rmdir(")
# A call that deletes the expression it is made on.
_METHODS = (".unlink(", ".rmdir(")
# The module whose deletions the primitives above already read whole, so its method spelling is not a second call.
_OS_MODULE = "os"
_QUOTED = re.compile(r"""^[rRbBuUfF]{0,2}("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')$""")
_PATH_WRAPPER = re.compile(r"^(?:pathlib\.)?Path\((.*)\)$")
_DOTTED = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
# What a containment test confines: the side of the comparison it is made on. The side it compares against is the
# boundary, and it is no target of its own.
_CONFINEMENTS = (
    re.compile(
        r"(?P<target>[A-Za-z_][\w.]*)\s*\.\s*(?:resolve\s*\(\s*\)\s*\.\s*)?"
        r"(?:is_relative_to|relative_to)\s*\("
    ),
    re.compile(r"(?P<target>[A-Za-z_][\w.]*)\s*\.\s*(?:resolve\s*\(\s*\)\s*\.\s*)?parent\s*=="),
    re.compile(r"(?P<target>[A-Za-z_][\w.]*)\s*\.\s*resolve\s*\(\s*\)\s*=="),
)
# A call whose own arguments are the targets it relates: `samefile(a, b)` is a test over both, and
# `commonpath([a, b])` over every path it joins. Neither compares against a boundary, so both sides are confined.
_RELATED = ("samefile(", "commonpath(")
# A `def` line, with the parameters a caller's arguments bind to.
_DEF = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(?P<name>\w+)\s*\((?P<parameters>[^)]*)")
# An argument a call names, so it binds its parameter by name rather than by position.
_KEYWORD = re.compile(r"^(?P<name>[A-Za-z_]\w*)\s*=(?!=)\s*(?P<value>.+)$")


@dataclass(frozen=True)
class ProtectedPath:
    """One pattern that declares a path sensitive, and whether the baseline or the repository declared it."""

    pattern: str
    source: str = BASELINE


@dataclass(frozen=True)
class RuleSet:
    """What configuration contributes to the rules: the protected paths, the baseline's first (SG-D4, SG-D6)."""

    protected_paths: tuple[ProtectedPath, ...] = ()


def rule_set(baseline_paths: Iterable[str] = (), repository_paths: Iterable[str] = ()) -> RuleSet:
    """The rules one run decides with: the mandatory baseline, and a repository's own list added to it.

    A repository's list extends the baseline and never replaces it, and a pattern the baseline already names
    keeps the baseline as its source: the mandatory list is what proves that path, whatever else repeats it.
    """
    protected = [ProtectedPath(pattern=pattern, source=BASELINE) for pattern in baseline_paths]
    named = {path.pattern for path in protected}
    protected.extend(
        ProtectedPath(pattern=pattern, source=REPOSITORY)
        for pattern in repository_paths
        if pattern not in named
    )
    return RuleSet(protected_paths=tuple(protected))


def matches(pattern: str, path: str) -> bool:
    """Whether `path`, from the repository root, is one the pattern declares sensitive.

    `**` crosses directories, `*` and `?` stay within one segment, and a trailing slash names a directory
    and everything under it. A pattern with no glob names exactly that path, so `pyproject.toml` is the
    root's own file and never a member's.
    """
    return re.fullmatch(_pattern(pattern), path) is not None


def _pattern(pattern: str) -> str:
    """`pattern` as the regular expression a repository path is matched whole against."""
    translated: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            translated.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            translated.append(".*")
            index += 2
        elif pattern[index] == "*":
            translated.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            translated.append("[^/]")
            index += 1
        else:
            translated.append(re.escape(pattern[index]))
            index += 1
    if pattern.endswith("/"):
        return "".join(translated) + ".*"
    return "".join(translated)


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
class _Guard:
    """A function the change adds, read as a guard: the parameters it takes and the spellings its body confines."""

    name: str
    parameters: tuple[str, ...]
    confines: set[str] = field(default_factory=set)

    @property
    def attributes(self) -> frozenset[str]:
        """What the guard confines that means the same target in a caller: an attribute of `self`."""
        return frozenset(spelling for spelling in self.confines if spelling.startswith("self."))


@dataclass
class _Added:
    """One function the change adds: the parameters its `def` line declares, and the lines it adds as its body."""

    parameters: tuple[str, ...] = ()
    body: list[str] = field(default_factory=list)


@dataclass
class _Scope:
    """What a rule must know about the whole change: each guard it adds, and the targets that guard confines."""

    guards: tuple[_Guard, ...] = ()


def decide(change: Change, rules: RuleSet | None = None) -> tuple[EscalationReason, ...]:
    """The reasons this change is escalated before the model is asked, empty when none fires."""
    rules = RuleSet() if rules is None else rules
    scope = _Scope(guards=_confining_helpers(change))
    reasons: list[EscalationReason] = []
    for file in change.files:
        reasons.extend(_protected_path(file, rules))
        found = _target_outside_root(file, scope)
        if found is not None:
            reasons.append(found)
    return tuple(reasons)


def _protected_path(file: FileChange, rules: RuleSet) -> list[EscalationReason]:
    """One reason per changed path a pattern declares sensitive, both sides of a rename included (D13)."""
    reasons: list[EscalationReason] = []
    for path in file.paths:
        matched = next(
            (candidate for candidate in rules.protected_paths if matches(candidate.pattern, path)),
            None,
        )
        if matched is None:
            continue
        reasons.append(
            EscalationReason(
                rule=PROTECTED_PATH, path=path, pattern=matched.pattern, source=matched.source
            )
        )
    return reasons


def way_out(reason: EscalationReason) -> str:
    """What a person should do about one reason, in the words a refusal owes them."""
    if reason.rule == TARGET_OUTSIDE_ROOT:
        return (
            f"read {reason.path}: the change deletes with `{reason.pattern}` on a target it computes, and "
            "nothing in the change confines that target to the root it is written under. Confine it before "
            "the call - `target.is_relative_to(root)` against the root this run reviews - or drop the call; "
            "the review cannot tell how far the target reaches, so a person decides."
        )
    if reason.rule == PROTECTED_PATH:
        holder = (
            "the mandatory sensitive-path baseline"
            if reason.source == BASELINE
            else "this repository's own sensitive-path list"
        )
        return (
            f"read {reason.path}: it matches `{reason.pattern}`, which {holder} protects, so the change "
            "goes to a full deep review - a person reads the path and says why the change reaches it "
            "before it merges."
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
            if target == _OS_MODULE:
                continue  # `os.unlink(x)` is the deletion the primitive already read: one deletion is one call
            calls.append(_destructive(text[start : invocation.close + 1], target))
    return tuple(calls)


def _destructive(call: str, target: str) -> DestructiveCall:
    """One call, with the target its line names and whether that target is spelled out."""
    return DestructiveCall(
        call=" ".join(call.split()), target=target.strip(), literal=_literal(target)
    )


def _target_outside_root(file: FileChange, scope: _Scope) -> EscalationReason | None:
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
    """Whether anything in the change confines `call`'s target to where it may reach (SG-D5).

    Confinement is per target: each line of the region is asked about this target's own spelling, and a guard
    answers only for the argument it is handed and for the attributes its own body confines.
    """
    if call.literal is not None:
        return not _escapes(call.literal)
    held = set(_names(call.target))
    if not held:
        return False  # a target the diff cannot name is not one a test or a guard in it can be shown to confine
    for line in hunk.region(index):
        if _DEF.match(line.text) is not None:
            continue  # a definition is not a call, and a def line is no test on any target
        if held & _expanded(_confined_spellings((line.text,))):
            return True
        if any(_creates(name, line) for name in held):
            return True
        if any(held & _confined_by_call(line.text, guard) for guard in scope.guards):
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


def _confined_by_call(text: str, guard: _Guard) -> set[str]:
    """The target spellings a call of `guard` in `text` confines: the attributes the guard confines, and each
    argument it hands to a parameter the guard confines.

    The argument binds to the parameter by the call's own order, never by the two sharing a spelling: a caller
    may hand `self.layout.folder` where the guard's parameter is `folder`. What a guard confines by its own local
    name crosses no call - two functions' locals are not the same target - so only arguments and attributes do.
    """
    found: set[str] = set()
    for invocation in _calls(text, guard.name):
        found |= guard.attributes
        for parameter, argument in _bound(invocation.arguments, guard.parameters):
            if parameter not in guard.confines:
                continue
            spelled = _spelled(argument)
            if spelled is not None:
                found.add(spelled)
    return found


def _calls(text: str, guard: str) -> tuple["_Invocation", ...]:
    """Each call `text` makes to `guard`, as the line spells it; nothing when `text` does not call it."""
    found: list[_Invocation] = []
    for match in re.finditer(rf"(?<![\w]){re.escape(guard)}\s*\(", text):
        invocation = _invocation(text, match.end() - 1)
        if invocation is not None:
            found.append(invocation)
    return tuple(found)


def _bound(arguments: tuple[str, ...], parameters: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Each parameter of a call with the argument handed to it: named when the call names it, else by position."""
    bound: list[tuple[str, str]] = []
    position = 0
    for argument in arguments:
        text = argument.strip()
        if not text:
            continue
        keyword = _KEYWORD.match(text)
        if keyword is not None:
            bound.append((keyword.group("name"), keyword.group("value").strip()))
            continue
        if position < len(parameters):
            bound.append((parameters[position], text))
        position += 1
    return tuple(bound)


def _confining_helpers(change: Change) -> tuple[_Guard, ...]:
    """Each guard the change adds, with the target spellings its body confines.

    A guard confines the targets its own body tests, and it confines nothing else: a second delete in a caller is
    answered on its own target. One hop of calls is followed - a guard that hands a target to a deeper guard
    confines that target in its caller too - which is what a diff shows without a data-flow analysis. A
    confinement that reaches deeper is not read, and the rule then fires.
    """
    bodies = _added_defs(change)
    guards = [
        _Guard(
            name=name, parameters=added.parameters, confines=set(_confined_spellings(added.body))
        )
        for name, added in bodies.items()
    ]
    growing = True
    while growing:
        growing = False
        for guard in guards:
            for line in bodies[guard.name].body:
                if _DEF.match(line) is not None:
                    continue  # a definition is not a call to the guard it declares
                for callee in guards:
                    adds = _confined_by_call(line, callee) - guard.confines
                    if adds:
                        guard.confines |= adds
                        growing = True
    return tuple(guard for guard in guards if guard.confines)


def _confined_spellings(lines: Iterable[str]) -> set[str]:
    """The target spellings these lines are tests over: the side of each comparison the test is made on.

    A relationship test confines the target it tests, never the boundary it compares against, and a call that
    relates its own arguments confines each of them. Reading one group too many is how a delete of the root a
    target is confined into once stayed quiet.
    """
    found: set[str] = set()
    for line in lines:
        for pattern in _CONFINEMENTS:
            for match in pattern.finditer(line):
                for group in pattern.groupindex:
                    value = match.group(group)
                    spelled = None if value is None else _spelled(value)
                    if spelled is not None:
                        found.add(spelled)
        found |= _related_targets(line)
    return found


def _related_targets(line: str) -> set[str]:
    """The targets a call like `samefile(a, b)` or `commonpath([a, b])` tests: each of its own arguments."""
    found: set[str] = set()
    for primitive in _RELATED:
        for at in _occurrences(line, primitive):
            invocation = _invocation(line, at + len(primitive) - 1)
            if invocation is None:
                continue
            for argument in invocation.arguments:
                inside = argument.strip()
                if inside.startswith("[") and inside.endswith("]"):
                    inside = inside[1:-1]
                for part in inside.split(","):
                    spelled = _spelled(part)
                    if spelled is not None:
                        found.add(spelled)
    return found


def _added_defs(change: Change) -> dict[str, _Added]:
    """Every function the change adds, with the parameters it declares and the lines it adds as its body."""
    bodies: dict[str, _Added] = {}
    for file in change.files:
        for hunk in file.hunks:
            for index, line in enumerate(hunk.lines):
                if line.kind != ADDED:
                    continue
                match = _DEF.match(line.text)
                if match is None:
                    continue
                added = bodies.setdefault(
                    match.group("name"), _Added(_parameters(match.group("parameters")))
                )
                indent = indent_columns(line.text)
                for following in hunk.lines[index + 1 :]:
                    if following.kind != ADDED:
                        if following.text.strip():
                            break
                        continue
                    if following.text.strip() and indent_columns(following.text) <= indent:
                        break
                    added.body.append(following.text)
    return bodies


def _parameters(text: str) -> tuple[str, ...]:
    """The parameter names a `def` line declares after `self`: what a caller's arguments bind to, by position."""
    names: list[str] = []
    for part in text.split(","):
        name = part.split(":")[0].split("=")[0].strip().lstrip("*")
        if name and name not in ("self", "cls"):
            names.append(name)
    return tuple(names)


def _names(target: str) -> tuple[str, ...]:
    """The spellings a containment test may use for this target, or none when it is not a name."""
    text = _spelled(target)
    if text is None:
        return ()
    if text.startswith("self."):
        return (text, text[len("self.") :])
    return (text,)


def _spelled(target: str) -> str | None:
    """The name `target` is written as, without `str(...)` or a trailing `.resolve()`; nothing when it is no name."""
    text = target.strip()
    if text.endswith(".resolve()"):
        text = text[: -len(".resolve()")]
    wrapped = re.match(r"^str\(\s*(.*?)\s*\)$", text)
    if wrapped:
        text = wrapped.group(1)
    return text if _DOTTED.match(text) else None


def _expanded(spellings: Iterable[str]) -> set[str]:
    """Each spelling with every way a delete of that target may be written: `self.folder` is also `folder`."""
    found: set[str] = set()
    for spelling in spellings:
        found.update(_names(spelling))
    return found


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
