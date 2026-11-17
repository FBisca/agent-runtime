"""A spec's requirement blocks, and a plan's change set checked against them and applied to them.

A spec is current state: one `### Requirement: <title>` block per requirement, running to the next
requirement heading or the next higher heading. A plan adds, modifies or removes blocks by title;
`plan check` refuses a change the spec cannot take, and the archive applies it.
"""

import re
from pathlib import Path

HEADING = re.compile(r"^### Requirement: (.+?)\s*$")
BOUNDARY = re.compile(r"^#{1,3} ")


def blocks(text):
    """{title: (start, end)} line spans of each requirement block in `text`."""
    lines = text.splitlines(keepends=True)
    found, title, start = {}, None, 0
    for number, line in enumerate(lines + ["# end\n"]):
        if BOUNDARY.match(line):
            if title is not None:
                end = number
                while end > start and not lines[end - 1].strip():
                    end -= 1
                found[title] = (start, end)
            heading = HEADING.match(line)
            title, start = (heading.group(1), number) if heading else (None, number)
    return found


def read_titles(spec):
    try:
        return set(blocks(Path(spec).read_text(encoding="utf-8")))
    except FileNotFoundError:
        return set()


def change_problems(changes, spec, name):
    """Each change the spec at `spec` cannot take, naming the title, the spec and the fix."""
    titles = read_titles(spec)
    problems = [
        f"requirements.added: {r.title!r} already exists in {name} — move it to modified, or rename it"
        for r in changes.added
        if r.title in titles
    ]
    for kind, items in (("modified", changes.modified), ("removed", changes.removed)):
        problems += [
            f"requirements.{kind}: {r.title!r} does not exist in {name} — use its exact title from "
            f"{name}, or add it instead"
            for r in items
            if r.title not in titles
        ]
    return problems


def render(requirement):
    """One requirement block, as the spec holds it."""
    parts = [f"### Requirement: {requirement.title}", " ".join(requirement.shall.split()), ""]
    for scenario in requirement.scenarios:
        name = " ".join((scenario.name or scenario.when).split())
        parts += [
            f"#### Scenario: {name}",
            f"- WHEN {' '.join(scenario.when.split())}",
            f"- THEN {' '.join(scenario.then.split())}",
            "",
        ]
    return "\n".join(parts)


def apply(text, changes, name):
    """`text` with the change set applied: each modified block replaced whole, each removed block deleted, each
    added block appended. A change the spec cannot take is refused naming it, so nothing half-applied is written."""
    problems = []
    found = blocks(text)
    for kind, items in (("modified", changes.modified), ("removed", changes.removed)):
        problems += [f"{kind} {r.title!r}" for r in items if r.title not in found]
    problems += [f"added {r.title!r}" for r in changes.added if r.title in found]
    if problems:
        raise ValueError(
            "the plan's requirement changes do not fit %s: %s. Fix the spec or the plan and `retry`, or `skip` and "
            "archive it yourself" % (name, "; ".join(problems))
        )
    lines = text.splitlines(keepends=True)
    replacement = {r.title: render(r) for r in changes.modified}
    replacement.update({r.title: "" for r in changes.removed})
    for title, (start, end) in sorted(found.items(), key=lambda item: -item[1][0]):
        if title in replacement:
            new = replacement[title]
            if not new:  # a removed block takes its trailing blank lines with it
                while end < len(lines) and not lines[end].strip():
                    end += 1
            lines[start:end] = [new.rstrip("\n") + "\n"] if new else []
    body = "".join(lines).rstrip("\n")
    added = "\n".join(render(r) for r in changes.added).rstrip("\n")
    return "\n\n".join(part for part in (body, added) if part) + "\n"
