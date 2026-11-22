"""Render a collection with the size a reader can check."""

import os
from pathlib import Path

from .. import config
from ..plan_layout import PlanLayout
from .prompts import block, render


def reference(base, value, role):
    """Resolve a typed reference before it is handed to a reader."""
    values = value if isinstance(value, (list, tuple)) else [value]
    paths = [Path(os.path.abspath(Path(base) / item)) for item in values]
    if len(paths) != 1:
        raise ValueError("%s: expected one reference; restore it or choose a valid path" % value)
    if role == "pattern":
        import glob

        matches = sorted(Path(p) for p in glob.glob(str(paths[0]), recursive=True))
        if not matches or any(not path.is_file() for path in matches):
            raise ValueError(
                "%s: expected matching files; restore them or choose another pattern" % value
            )
        return ", ".join(str(path) for path in matches)
    invalid = any(c in str(value) for c in "*?[]")
    path = paths[0]
    if role == "read":
        invalid |= not path.is_file()
    elif role == "write":
        invalid |= path.is_dir()
    elif role == "collection":
        invalid |= path.exists() and not path.is_dir()
    else:
        raise ValueError("unknown reference role: %s" % role)
    if invalid:
        raise ValueError(
            "%s: expected one %s file; restore it or choose a valid path" % (value, role)
        )
    if role == "collection":
        return "%d files in %s" % (sum(1 for p in path.rglob("*") if p.is_file()), path)
    return str(path)


def collection(path):
    return reference(Path.cwd(), path, "collection")


def named_collection(checkout, name):
    """The scope path as the packet spells it, with the size beside it: the name locates the work, the count is
    the evidence that the name still resolves. A count alone is not a pointer."""
    path = Path(checkout) / name
    if path.is_dir():
        return "%s: %s" % (name, reference(checkout, name, "collection"))
    return "%s: 0 files in %s" % (name, path.resolve())


def owned_collections(checkout, paths):
    """The scope paths that denote collections, using their displayed names."""
    result = []
    for name in paths:
        if (Path(checkout) / name).is_dir() or str(name).endswith("/"):
            result.append(named_collection(checkout, name))
    return ", ".join(result) or "none"


def owned_scope(checkout, paths):
    result = []
    for name in paths:
        path = Path(checkout) / name
        if path.is_dir() or str(name).endswith("/"):
            result.append(named_collection(checkout, name))
        else:
            result.append("write %s" % reference(checkout, name, "write"))
    return ", ".join(result)


def rulebook(repo, checkout):
    """The repository's rulebook as a file of the checkout, or None when its config names none. The reader of the
    config and the reader of the file are the same name, so a rulebook that is not there is refused, not skipped."""
    name = config.load(repo).rulebook
    return reference(checkout, name, "read") if name else None


BRIEF = "brief.md"  # a folder plan's narrative, beside plan.yaml
TDD_SKILL_ENV = "AGENT_RUNTIME_TDD_SKILL"


TDD_SKILL_PLACES = ("~/.agents/skills/tdd/SKILL.md", "~/.claude/skills/tdd/SKILL.md")


def tdd_skill_path(env=None):
    """The `tdd` skill's SKILL.md: the environment's path, else the places a host installs it.

    A backend with no skill loader still gets the admission and cost rules in the contract; this
    path is what a Claude or Pi worker reads before its first edit."""
    env = os.environ if env is None else env
    named = env.get(TDD_SKILL_ENV)
    if named:
        return named
    for place in TDD_SKILL_PLACES:
        path = Path(os.path.expanduser(place))
        if path.is_file():
            return str(path)
    return None


def tdd_instruction():
    from . import tdd_skill_path

    skill = tdd_skill_path()
    if not skill:
        return "Read the `tdd` skill first when available; otherwise follow the TDD rules in the contract."
    rules = Path(skill).with_name("rules.md")
    for path in (Path(skill), rules):
        reference(Path.cwd(), path, "read")
    return "Read the `tdd` skill first: %s; read its rules at %s." % (skill, rules)


def plan_note(plan_path, reading):
    """What a brief says of the plan it names. A folder plan keeps its decision log, interfaces and code touched in
    `brief.md` beside `plan.yaml`, so the brief is named by its resolved path (a brief that is not there is not
    named); a legacy plan's own Markdown holds the Decisions and Seams sections. `reading` is the worker's
    imperative form ("read ...") against the reviewer's description."""
    layout = PlanLayout(plan_path)
    path = layout.path
    if not layout.is_folder:
        return ("read " if reading else "") + "its Decisions and Seams"
    brief = path.with_name(BRIEF)
    if not brief.is_file():
        return "plan.yaml is the contract"
    where = reference(path.parent, BRIEF, "read")
    if reading:
        return (
            "plan.yaml is the contract; read %s for the decision log, interfaces and code touched"
            % where
        )
    return (
        "plan.yaml is the contract; %s holds the decision log, interfaces and code touched" % where
    )


def task_text(self, _plan, ticket, packet, scenarios, task_dir):
    from . import CHECK_INSTRUCTIONS

    task_id, checkout = packet["task_id"], packet["checkout"]
    return render(
        "task",
        contract=self.run_dir / "contract.md",
        ticket=ticket["#"],
        delivers=ticket["What it delivers"],
        task_id=task_id,
        plan=self.plan_path,
        plan_note=plan_note(self.plan_path, True),
        checkout=checkout,
        worker=packet["worker"],
        base=self.args.base,
        checkout_contents=collection(checkout),
        scope_contents=owned_collections(checkout, packet["owns"]),
        owns=owned_scope(checkout, packet["owns"]),
        tdd=tdd_instruction(),
        scenarios="\n".join(scenarios),
        checks="; ".join(packet["checks"] or ["the repository's full CI gate"]),
        check_instructions=CHECK_INSTRUCTIONS,
        rulebook=block("task-rulebook", rulebook=book)
        if (book := rulebook(self.repo, checkout))
        else "",
        full_suite=block("task-full-suite", full=packet["full"]) if packet.get("full") else "",
        pr_base=self.args.base.partition("/")[2],
    )
