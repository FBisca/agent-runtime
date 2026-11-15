"""The spec sections an archive writes: one section per plan and one number per requirement.

The grammar and its reader live here rather than in `driver/__init__.py` because no module of the
package may pass a thousand lines, and because the archive's guard, `tests/test_spec_sections.py` and
the living contract read the same file the same way. `SpecSections` keeps the drive's own write with
them, so `driver.spec_text` still answers on a `Driver`.
"""

import re
from pathlib import Path
from typing import Any, Callable

# A spec section's grammar, as the archive writes it: the heading a plan owns, its requirement bullet, its row. The
# checks below read what the file holds, so a hand-resolved conflict or a second append is named, not merged in.
SPEC_HEADING = re.compile(r"^## (Requirements|Coverage): (.+?)\s*$")
SPEC_REQUIREMENT = re.compile(r"^- \*\*(R\d+)\*\*")
SPEC_ROW = re.compile(r"^\| (R\d+) \| (\S+) \|")
SPEC_MARKER = re.compile(r"^(<{7}|={7}|>{7}|\|{7})")


def spec_defects(text):
    """The spec's structural defects, one message each with its line: a conflict marker left in the file, a
    requirement numbered twice, a second section for a slug the file already has, a Coverage row naming a requirement
    no bullet in the file holds. Empty for a spec the archive wrote and nobody resolved by hand."""
    defects, numbered, sections, rows = [], {}, {}, []
    covering = False
    for number, line in enumerate(text.splitlines(), 1):
        marker = SPEC_MARKER.match(line)
        if marker:
            defects.append("line %d: a conflict marker %s" % (number, marker.group(1)))
        heading = SPEC_HEADING.match(line)
        if heading:
            kind, slug = heading.group(1), heading.group(2)
            if (kind, slug) in sections:
                defects.append(
                    "line %d: a second %s section for %s (the first is at line %d)"
                    % (number, kind, slug, sections[(kind, slug)])
                )
            else:
                sections[(kind, slug)] = number
            covering = kind == "Coverage"
        elif line.startswith("## "):
            covering = False
        bullet = SPEC_REQUIREMENT.match(line)
        if bullet:
            rid = bullet.group(1)
            if rid in numbered:  # one requirement, one number: a second bullet reuses it
                defects.append(
                    "line %d: %s is already numbered at line %d" % (number, rid, numbered[rid])
                )
            else:
                numbered[rid] = number
        row = SPEC_ROW.match(line) if covering else None
        if row:
            rows.append((number, row.group(1)))
    for number, rid in rows:
        if rid not in numbered:
            defects.append(
                "line %d: a Coverage row for %s, which no requirement in this file holds"
                % (number, rid)
            )
    return defects


def without_coverage(text, slug):
    """`text` without this plan's own `Coverage` section: the table is the one thing an archive appends again, so a
    rebuild replaces it instead of leaving a second copy (the incident `docs(spec): keep one Coverage table`). The
    plan's `Requirements` section stays: a held scenario keeps the number it already has, and `spec_section` adds only
    the requirements the file lacks."""
    kept, dropping = [], False
    for line in text.splitlines(keepends=True):
        heading = SPEC_HEADING.match(line.rstrip("\n"))
        if heading:
            dropping = heading.group(1) == "Coverage" and heading.group(2) == slug
        if not dropping:
            kept.append(line)
    return "".join(kept)


def merge_requirements(text, slug):
    """`text` with this plan's `Requirements` sections folded into the first one: one section per plan, however many
    times it archives, so a plan whose section the file already holds does not open a second one for a new scenario.
    A later block contributes its bullets and nothing else, and one blank line is kept after the last one."""
    lines = text.splitlines(keepends=True)
    heads = [index for index, line in enumerate(lines) if line.startswith("## ")]
    bounds = [
        (start, heads[at + 1] if at + 1 < len(heads) else len(lines))
        for at, start in enumerate(heads)
    ]
    ours = [
        (start, end)
        for start, end in bounds
        if (heading := SPEC_HEADING.match(lines[start].rstrip("\n")))
        and (heading.group(1), heading.group(2)) == ("Requirements", slug)
    ]
    if len(ours) < 2:
        return text
    first_start, first_end = ours[0]
    bullets = [
        line for start, end in ours[1:] for line in lines[start + 1 : end] if line.startswith("- ")
    ]
    last = max(
        (index for index in range(first_start, first_end) if lines[index].startswith("- ")),
        default=first_start + 1,
    )
    dropped = {index for start, end in ours[1:] for index in range(start, end)}
    kept = [line for index, line in enumerate(lines) if index not in dropped]
    at = last + 1 - sum(1 for index in dropped if index <= last)
    if at < len(kept) and kept[at].startswith("## ") and kept[at - 1].strip():
        kept[at:at] = ["\n"]  # the blank line the folded block took with it
        at += 1
    return "".join(kept[:at] + bullets + kept[at:])


class SpecSections:
    """The archive's own write, kept off `Driver`'s body: the structure budget holds that class to the
    methods it already carried. The three names below are annotations, not defaults: the drive supplies
    them, and a check needs them here to read a `Driver` as this class."""

    slug: str
    flat: Callable[..., str]
    plan_model: Callable[..., Any]
    scenario_nodes: Callable[..., Any]

    def held_requirements(self, spec):
        """This plan's scenarios the spec already holds, each with its requirement: {"S3": "R220"}.
        One requirement may name several scenarios — `(from <slug>/S1, S2, S3)` — and it holds every one of them."""
        try:
            text = Path(spec).read_text(encoding="utf-8")
        except OSError:
            return {}
        found = re.findall(
            r"^- \*\*(R\d+)\*\*.*\(from %s/((?:S\w+(?:,\s*)?)+)\)" % re.escape(self.slug),
            text,
            re.MULTILINE,
        )
        return {sid: rid for rid, ids in found for sid in (part.strip() for part in ids.split(","))}

    def spec_section(self, spec_text, held):
        """The text appended to the spec: the requirements it lacks, then the Coverage table of every scenario."""
        slug, nodes = self.slug, self.scenario_nodes()
        number = max((int(n) for n in re.findall(r"\bR(\d+)\b", spec_text)), default=0)
        numbered = dict(held)
        parts = []
        new = [s for s in self.plan_model().scenarios if s.id not in held]
        if new:
            parts += ["## Requirements: %s" % slug, ""]
            for scenario in new:
                number += 1
                numbered[scenario.id] = "R%d" % number
                parts.append(
                    "- **R%d** WHEN %s, THEN %s (from %s/%s)"
                    % (
                        number,
                        self.flat(scenario.when),
                        self.flat(scenario.then),
                        slug,
                        scenario.id,
                    )
                )
            parts.append("")
        parts += [
            "## Coverage: %s" % slug,
            "",
            "| Requirement | Scenario | Test nodes |",
            "|---|---|---|",
        ]
        for scenario in self.plan_model().scenarios:
            human = scenario.evidence.human if scenario.evidence else None
            cellular = (
                "human: " + self.flat(human).replace("|", "\\|")
                if human
                else "<br>".join("`%s`" % node for node in nodes[scenario.id])
            )
            parts.append("| %s | %s | %s |" % (numbered[scenario.id], scenario.id, cellular))
        return "\n".join(parts) + "\n"

    def spec_text(self, before, held):
        """The spec as this archive writes it: its section re-derived from `before`, so a rebuild replaces its own
        `Coverage` table and folds new requirements into its own `Requirements` section. Text the write would leave
        badly formed is refused here, before the commit and the push: a spec section a hand-resolved conflict mangled
        is a person's to fix, not something to push again."""
        body = without_coverage(before, self.slug)
        section = self.spec_section(body, held)
        body = body.rstrip("\n") + "\n\n" if body.strip() else ""
        text = merge_requirements(body + section, self.slug)
        already = spec_defects(before)
        defects = [defect for defect in spec_defects(text) if defect not in already]
        if defects:
            raise ValueError(
                "the section would leave %s badly formed: %s. Fix the spec by hand and `retry`, or `skip` and "
                "archive it yourself" % (self.plan_model().spec, "; ".join(defects[:3]))
            )
        return text
