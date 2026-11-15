"""Deterministic chunks: the change split inside the budget in force, every region carried once (D41).

The screening asks one request per chunk, and a chunk is built from what the diff already is: a change's
files, in the diff's order, each with its own header, and each of its hunks as one region. Nothing here
summarises, reorders or consults the model - the same frozen diff and the same budget always produce the
same chunks - and a hunk is never split across two chunks. A region carried by two chunks would belong to
neither: each answer would be about part of it, and `coverage.changed_regions == coverage.covered` would
stop meaning anything.

A hunk larger than the budget is carried whole, as a chunk of its own, and that chunk says it is over
budget. The regions that do fit keep packing in the diff's order around it: a hunk nobody can fit is no
reason to spend a second chunk on its small neighbours. The alternative - a change nobody can screen - is
worse than a chunk the provider may refuse, and a refusal is an incomplete execution that says so (D40).
The estimate is the provider's own unit to the best of a cheap measure: `CHARS_PER_TOKEN` is conservative
against the bytes-per-token the reference set measured, so the tool chunks smaller rather than larger.

The budget in force is the configuration's `chunk.max_diff_tokens` when a configuration declares it, and
the provider's per-question limit otherwise: the whole state travels with every question, so the
per-question limit is the binding one, and long context is the caller's larger budget (D41, JEV.md).

`coverage` counts the changed regions the chunks carry beside the regions the diff holds, so a report
proves what it screened rather than asserting it. A change wider than `cross_file.max_files` cannot be read
by a cross-file question at once, and the tool records that gap as a reason and recommends a full deep
review (D42, D43): the tool cannot see which file a question needed, so the cross-file limit is the one
observable form of that condition, and a clean screen is never read as a complete one.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from agent_review.change import Change, Hunk
from agent_review.questions import QuestionBank
from agent_review.safeguards import CONFIGURATION, COVERAGE_GAP, EscalationReason
from agent_review.screening import limits

# The most changed files a cross-file question may be expected to read at once when no configuration
# declares a limit: a wide but ordinary change, which the packaged baseline screens in one request.
DEFAULT_MAX_FILES = 40
# How many characters of diff one provider token is estimated to hold. The reference set measured ~3.3
# bytes per token on a large change, so a smaller number estimates more tokens than the provider counts:
# the tool chunks smaller rather than larger, and the provider's own refusal stays the authority (D40).
CHARS_PER_TOKEN = 3


@dataclass(frozen=True)
class Region:
    """One changed region: a hunk of a file, with the header that says which file and what happened to it.

    A file whose change holds no hunk - a pure rename, a mode change - is still a region, with no hunk: the
    model has to read that the file moved, and its header is all the diff says about it.
    """

    path: str
    header: tuple[str, ...]
    hunk: Hunk | None = None

    @property
    def lines(self) -> tuple[str, ...]:
        """The hunk's own lines as the diff spelled them, or nothing for a file with no hunk."""
        return () if self.hunk is None else self.hunk.raw

    def text(self, *, header: bool = True) -> str:
        """This region as diff text, with the file's header when the chunk does not already carry it."""
        return "\n".join((*self.header, *self.lines) if header else self.lines)


@dataclass(frozen=True)
class Chunk:
    """One request's worth of the change: the regions it carries, their text, and whether they fit.

    `within_budget` is the report's own honesty: a chunk holding one hunk larger than the budget carries it
    whole and says it is over budget, rather than claiming a fit it does not have.
    """

    index: int
    regions: tuple[Region, ...]
    text: str
    within_budget: bool

    @property
    def tokens(self) -> int:
        """The estimated tokens this chunk's diff costs, by the same measure the budget was read with."""
        return estimate(self.text)

    @property
    def files(self) -> tuple[str, ...]:
        """The changed paths this chunk carries, in the diff's order, each named once."""
        seen: list[str] = []
        for region in self.regions:
            if region.path not in seen:
                seen.append(region.path)
        return tuple(seen)

    @property
    def changed_regions(self) -> int:
        """How many hunks this chunk carries; a header-only file is carried without being a region."""
        return sum(1 for region in self.regions if region.hunk is not None)

    def as_dict(self) -> dict[str, Any]:
        """The chunk as `report.json` holds it: what it carried, and whether it fit the budget."""
        return {
            "index": self.index,
            "files": list(self.files),
            "regions": self.changed_regions,
            "tokens": self.tokens,
            "within_budget": self.within_budget,
        }


@dataclass(frozen=True)
class Gap:
    """A coverage gap: the change is wider than a cross-file question may read, so the screen is partial."""

    changed_files: int
    max_files: int

    @property
    def reason(self) -> str:
        """Why this screen does not cover the change's cross-file interactions, in a person's words."""
        return (
            f"the change touches {self.changed_files} files and cross_file.max_files is {self.max_files}, "
            "so no cross-file question can read them together and this screen does not cover how they "
            "interact"
        )

    def as_dict(self) -> dict[str, Any]:
        """The gap as `report.json` holds it: its reason, and the width and limit it was read from."""
        return {
            "reason": self.reason,
            "changed_files": self.changed_files,
            "max_files": self.max_files,
        }

    def as_escalation(self) -> EscalationReason:
        """The gap as the reason a person must read the change, naming the limit that was exceeded."""
        return EscalationReason(
            rule=COVERAGE_GAP,
            path="",
            pattern=f"cross_file.max_files={self.max_files}",
            source=CONFIGURATION,
            detail=self.reason,
        )


@dataclass(frozen=True)
class Coverage:
    """What a screening covered: the regions the diff holds, the regions the chunks carried, and any gap."""

    changed_regions: int
    covered: int
    gap: Gap | None = None

    def as_dict(self) -> dict[str, Any]:
        """The coverage as `report.json` holds it, the gap as null when the whole change was carried."""
        return {
            "changed_regions": self.changed_regions,
            "covered": self.covered,
            "gap": None if self.gap is None else self.gap.as_dict(),
        }


def estimate(text: str) -> int:
    """The tokens `text` is estimated to cost: a cheap, deterministic measure, never the provider's count."""
    return _tokens(len(text))


def _tokens(chars: int) -> int:
    """The estimated tokens `chars` of diff cost, rounded up so a part never fits by truncation."""
    return math.ceil(chars / CHARS_PER_TOKEN)


def budget(bank: QuestionBank, *, long_context: bool) -> int:
    """The most estimated tokens one chunk may carry: what is configured, or the limit in force (D41)."""
    declared = bank.chunk.get("max_diff_tokens")
    if isinstance(declared, int) and not isinstance(declared, bool):
        return declared
    # Every question travels with the whole state, so the per-question limit is the binding one here.
    return limits(long_context).per_question


def cross_file_limit(bank: QuestionBank) -> int:
    """The most changed files a cross-file question may read at once, and the limit a gap is read from."""
    declared = bank.cross_file.get("max_files")
    if isinstance(declared, int) and not isinstance(declared, bool):
        return declared
    return DEFAULT_MAX_FILES


def split(change: Change, diff: str, *, budget: int) -> tuple[Chunk, ...]:
    """`change` split into deterministic chunks of at most `budget` estimated tokens, in the diff's order.

    A change that fits stays exactly the text git produced. A hunk is never split: a region larger than the
    budget is carried whole and its chunk says it is over budget.
    """
    regions = _regions(change)
    if not regions:
        return (Chunk(index=1, regions=(), text=diff, within_budget=estimate(diff) <= budget),)
    groups = _packed(regions, budget)
    if len(groups) == 1:
        # The whole change in one request is the change as git wrote it, never a re-spelling of it.
        group = groups.pop()
        return (
            Chunk(
                index=1,
                regions=tuple(group),
                text=diff,
                within_budget=estimate(diff) <= budget,
            ),
        )
    return tuple(
        Chunk(
            index=index,
            regions=tuple(group),
            text=_text(group),
            within_budget=estimate(_text(group)) <= budget,
        )
        for index, group in enumerate(groups, start=1)
    )


def coverage(change: Change, chunks: Sequence[Chunk], bank: QuestionBank) -> Coverage:
    """What the chunks covered of the change, and the coverage gap a wide change records (D41, D42, D43)."""
    changed = sum(len(file.hunks) for file in change.files)
    carried = sum(chunk.changed_regions for chunk in chunks)
    limit = cross_file_limit(bank)
    gap = (
        Gap(changed_files=len(change.files), max_files=limit) if len(change.files) > limit else None
    )
    return Coverage(changed_regions=changed, covered=carried, gap=gap)


def _regions(change: Change) -> tuple[Region, ...]:
    """The change as its regions: one per hunk, or one header-only region for a file with no hunk."""
    regions: list[Region] = []
    for file in change.files:
        if not file.hunks:
            regions.append(Region(path=file.path, header=file.header))
            continue
        regions.extend(Region(path=file.path, header=file.header, hunk=hunk) for hunk in file.hunks)
    return tuple(regions)


def _packed(regions: Sequence[Region], budget: int) -> list[list[Region]]:
    """The regions packed greedily, in order, into groups of at most `budget` estimated tokens.

    A group that would exceed the budget is closed before the region that would exceed it. A region that
    alone exceeds the budget travels in a group of its own, and the regions that do fit keep packing in the
    diff's order around it: a hunk nobody can fit is no reason to spend a second chunk on its small
    neighbours. The groups are returned in the order of the first region each carries, so chunk order is
    the diff's order however the oversized regions fall.
    """
    groups: list[list[tuple[int, Region]]] = []
    current: list[tuple[int, Region]] = []
    previous: Region | None = None
    chars = 0
    for position, region in enumerate(regions):
        if _tokens(_chars(region, None)) > budget:
            groups.append([(position, region)])
            continue
        addition = _chars(region, previous)
        if current and _tokens(chars + addition) > budget:
            groups.append(current)
            current, chars = [], 0
            addition = _chars(region, None)
        current.append((position, region))
        previous = region
        chars += addition
    if current:
        groups.append(current)
    return [
        [region for _, region in group] for group in sorted(groups, key=lambda group: group[0][0])
    ]


def _chars(region: Region, previous: Region | None) -> int:
    """How many characters `region` adds after `previous`, with its header when that was another file.

    The newline that separates it from the region before it is counted with the region it follows, so a
    region's own cost is exactly the text it contributes and a solo region's estimate matches its chunk's.
    """
    header = previous is None or previous.path != region.path
    return len(region.text(header=header)) + (0 if previous is None else 1)


def _text(regions: Sequence[Region]) -> str:
    """The group's regions as one diff text, each file's header written once before its first hunk."""
    parts: list[str] = []
    previous = ""
    for region in regions:
        if region.path != previous:
            parts.extend(region.header)
            previous = region.path
        parts.extend(region.lines)
    return "\n".join(parts)
