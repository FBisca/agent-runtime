"""The change between two revisions, read as paths and hunks beside the frozen diff (D33).

The screening hands the provider the diff whole; the safeguards beside it read the same text as structure:
one entry per changed path, the hunks a person sees, and the lines the change adds. Nothing here decides
anything - the rules in `safeguards` do - and nothing here looks outside the diff: a fact a diff does not
carry is not read from the working tree, so the same two revisions always read the same way (SG-D5).

A path's status is what the diff says it is (`added`, `deleted`, `renamed`, `modified`), and a rename keeps
both names: a rule about where a change reaches has to see either side (D13).

A hunk carries three lines of context, so the function a line sits in is sometimes only partly in the diff.
`Hunk.region` answers with the lines of that function as the hunk shows them, and with the whole hunk when
the hunk shows no function: a rule that reads a confinement test must not read the test of a different
function as this one's.
"""

import re
from dataclasses import dataclass, field

ADDED = "added"
REMOVED = "removed"
CONTEXT = "context"

ADDED_STATUS = "added"
DELETED_STATUS = "deleted"
RENAMED_STATUS = "renamed"
MODIFIED_STATUS = "modified"

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
_DEF = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(?P<name>\w+)")
_OLD_PREFIX = "--- "
_NEW_PREFIX = "+++ "
_DEV_NULL = "/dev/null"


@dataclass(frozen=True)
class DiffLine:
    """One line of a hunk: what it is, the text the diff prefixes, and its number on its own side."""

    kind: str
    text: str
    number: int | None


@dataclass(frozen=True)
class Hunk:
    """One `@@` hunk: where it starts on each side, what git says it sits in, and its lines."""

    old_start: int
    new_start: int
    section: str
    lines: tuple[DiffLine, ...]

    @property
    def added(self) -> tuple[DiffLine, ...]:
        """The lines the hunk adds, in the order the change writes them."""
        return tuple(line for line in self.lines if line.kind == ADDED)

    def region(self, index: int) -> tuple[DiffLine, ...]:
        """The lines of the function the line at `index` sits in, as this hunk shows them.

        The function's start is not always in the hunk, and module-level lines have no function at all: the
        whole hunk answers then. Which region a confinement test lands in is the difference between a change
        that confines its target and one that confines something else, so the approximation is stated.
        """
        target = self.lines[index]
        indent = indent_columns(target.text)
        start = 0
        enclosing: int | None = None
        for position in range(index, -1, -1):
            if _DEF.match(self.lines[position].text) is None:
                continue
            candidate = indent_columns(self.lines[position].text)
            if candidate < indent:
                start, enclosing = position, candidate
                break
        if enclosing is None:
            return self.lines
        end = len(self.lines)
        for position in range(start + 1, len(self.lines)):
            line = self.lines[position]
            if line.text.strip() and indent_columns(line.text) <= enclosing:
                end = position
                break
        return self.lines[start:end]


@dataclass(frozen=True)
class FileChange:
    """One changed path: where it came from, what happened to it, and its hunks."""

    path: str
    old_path: str
    status: str
    hunks: tuple[Hunk, ...]

    @property
    def added(self) -> tuple[DiffLine, ...]:
        """Every line this path's change adds, in the diff's order."""
        return tuple(line for hunk in self.hunks for line in hunk.added)

    @property
    def paths(self) -> tuple[str, ...]:
        """Both names the change names for this path, the changed one first when they differ (D13)."""
        return (self.old_path,) if self.old_path == self.path else (self.path, self.old_path)


@dataclass(frozen=True)
class Change:
    """The change as the diff spells it: one entry per changed path, in the diff's order."""

    files: tuple[FileChange, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        """Every changed path, both sides of a rename included, in the diff's order (D13)."""
        seen: list[str] = []
        for file in self.files:
            seen.extend(path for path in file.paths if path not in seen)
        return tuple(seen)


@dataclass
class _File:
    """A file change while its header and hunks are still arriving."""

    path: str = ""
    old_path: str = ""
    status: str = MODIFIED_STATUS
    hunks: list[Hunk] = field(default_factory=list)
    lines: list[DiffLine] = field(default_factory=list)
    old_start: int = 0
    new_start: int = 0
    section: str = ""
    old_no: int = 0
    new_no: int = 0

    def flush(self) -> None:
        """Close the hunk being read, if one is open."""
        if self.lines or self.section or self.old_start or self.new_start:
            self.hunks.append(
                Hunk(
                    old_start=self.old_start,
                    new_start=self.new_start,
                    section=self.section,
                    lines=tuple(self.lines),
                )
            )
        self.lines, self.section, self.old_start, self.new_start = [], "", 0, 0

    def freeze(self) -> FileChange:
        self.flush()
        return FileChange(
            path=self.path or self.old_path,
            old_path=self.old_path or self.path,
            status=self.status,
            hunks=tuple(self.hunks),
        )


def read(diff: str) -> Change:
    """`diff` - the output of `git diff base..head` - read as the change it describes."""
    files: list[_File] = []
    current: _File | None = None
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            if current is not None:
                files.append(current)
            current = _File()
            continue
        if current is None:
            continue  # a preamble before the first path belongs to no change
        if raw.startswith(_OLD_PREFIX):
            current.old_path = _path(raw[len(_OLD_PREFIX) :])
        elif raw.startswith(_NEW_PREFIX):
            current.path = _path(raw[len(_NEW_PREFIX) :])
        elif raw.startswith("new file mode"):
            current.status = ADDED_STATUS
        elif raw.startswith("deleted file mode"):
            current.status = DELETED_STATUS
        elif raw.startswith("rename from "):
            current.old_path, current.status = raw[len("rename from ") :], RENAMED_STATUS
        elif raw.startswith("rename to "):
            current.path, current.status = raw[len("rename to ") :], RENAMED_STATUS
        elif match := _HUNK.match(raw):
            current.flush()
            current.old_start, current.old_no = int(match.group(1)), int(match.group(1))
            current.new_start, current.new_no = int(match.group(3)), int(match.group(3))
            current.section = match.group(5)
        elif raw.startswith("\\"):
            continue  # "\ No newline at end of file": a note about a line, not a line
        elif raw[:1] in ("+", "-", " "):
            current.lines.append(_line(raw, current))
    if current is not None:
        files.append(current)
    return Change(files=tuple(file.freeze() for file in files))


def _line(raw: str, current: _File) -> DiffLine:
    """One hunk line, with the number it holds on its own side."""
    marker, text = raw[:1], raw[1:]
    if marker == "+":
        current.new_no += 1
        return DiffLine(ADDED, text, current.new_no - 1)
    if marker == "-":
        current.old_no += 1
        return DiffLine(REMOVED, text, current.old_no - 1)
    current.old_no += 1
    current.new_no += 1
    return DiffLine(CONTEXT, text, current.new_no - 1)


def _path(spelled: str) -> str:
    """A path as a header spells it: without the a/ or b/ prefix, and nothing for /dev/null."""
    text = spelled.strip()
    if text == _DEV_NULL:
        return ""
    if len(text) > 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    if text[:2] in ("a/", "b/"):
        text = text[2:]
    return text


def indent_columns(text: str) -> int:
    """How far a line is indented, tabs counted as the four columns git shows them in."""
    expanded = text.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())
