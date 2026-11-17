"""Render a collection with the size a reader can check."""

import os
from pathlib import Path


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


def tdd_instruction():
    from . import tdd_skill_path

    skill = tdd_skill_path()
    if not skill:
        return "Read the `tdd` skill first when available; otherwise follow the TDD rules in the contract."
    rules = Path(skill).with_name("rules.md")
    for path in (Path(skill), rules):
        reference(Path.cwd(), path, "read")
    return "Read the `tdd` skill first: %s; read its rules at %s." % (skill, rules)


def task_text(self, _plan, ticket, packet, scenarios, task_dir):
    from . import CHECK_INSTRUCTIONS

    gate = packet["checks"] or ["the repository's full CI gate"]
    return "\n".join(
        [
            "Contract: %s" % (self.run_dir / "contract.md"),
            "Report: %s" % (task_dir / "report.json"),
            "",
            "# %s: %s" % (ticket["#"], ticket["What it delivers"]),
            "",
            "Report with task_id `%s`: the driver refuses a report whose task_id is not this one."
            % packet["task_id"],
            "",
            "Plan: %s (read its Decisions and Seams)." % self.plan_path,
            "Checkout: %s on branch feat/%s (new, from %s). Work only here."
            % (packet["checkout"], packet["worker"], self.args.base),
            "Checkout contents: %s" % collection(packet["checkout"]),
            "Scope contents: %s" % owned_collections(packet["checkout"], packet["owns"]),
            "Owns (edit nothing else): %s." % owned_scope(packet["checkout"], packet["owns"]),
            "Shared databases are read-only; schema changes go through migrations in your own branch.",
            "",
            "Scenarios, verbatim. Work red-first: one test per scenario, named with its S-ID, seen failing before the code exists; keep that failing output for the report's `red`.",
            tdd_instruction(),
            "",
            *scenarios,
            "",
            "Before you report COMPLETE, run: " + "; ".join(gate) + ".",
            CHECK_INSTRUCTIONS,
            *(
                [
                    "`%s` is the whole suite: do not run it; CI runs it." % packet["full"],
                ]
                if packet.get("full")
                else []
            ),
            "Then open a draft pull request against %s." % self.args.base.partition("/")[2],
            "",
        ]
    )
