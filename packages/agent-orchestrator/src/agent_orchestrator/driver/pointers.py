"""Render a collection with the size a reader can check."""

from pathlib import Path


def collection(path):
    root = Path(path)
    count = sum(1 for entry in root.rglob("*") if entry.is_file())
    return "%d files in %s" % (count, root)


def owned_collections(checkout, paths):
    """The scope paths that denote collections, using their displayed names."""
    result = []
    for name in paths:
        path = Path(checkout) / name
        if path.is_dir() or str(name).endswith("/"):
            count = sum(1 for entry in path.rglob("*") if entry.is_file())
            result.append("%d files in %s" % (count, name.rstrip("/")))
    return ", ".join(result) or "none"


def tdd_instruction():
    from . import tdd_skill_path

    skill = tdd_skill_path()
    if not skill:
        return "Read the TDD rules in the contract first."
    rules = Path(skill).with_name("rules.md")
    for path in (Path(skill), rules):
        if not path.is_file():
            raise ValueError(
                "%s: expected a TDD instruction file; restore it or choose a valid skill path"
                % path
            )
    return "Read the `tdd` skill first: %s; read its rules at %s." % (skill, rules)


def task_text(self, _plan, ticket, packet, scenarios, task_dir):
    from . import PROXY_INSTRUCTION

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
            "Owns (edit nothing else): %s." % ", ".join(packet["owns"]),
            "Shared databases are read-only; schema changes go through migrations in your own branch.",
            "",
            "Scenarios, verbatim. Work red-first: one test per scenario, named with its S-ID, seen failing before the code exists.",
            tdd_instruction(),
            "",
            *scenarios,
            "",
            "Before you report COMPLETE, run: " + "; ".join(gate) + ".",
            PROXY_INSTRUCTION,
            *(
                [
                    "`%s` is the whole suite: do not run it; verify and CI run it."
                    % packet["full"],
                ]
                if packet.get("full")
                else []
            ),
            "Then open a draft pull request against %s." % self.args.base.partition("/")[2],
            "",
        ]
    )
