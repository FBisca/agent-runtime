"""The refusals an authoring command makes about a plan's paths and its proof.

`plan check`, `plan approve` and `plan amend` make them; the driver's load never runs them, so a plan
approved before a rule existed keeps running. A path decides whether it can hold a test, a discarding
folder whether it may be deleted, and a scenario without evidence whether anything can prove it.
"""

import fnmatch
from pathlib import Path

from .plan_discovery import repo_config
from .plan_layout import PlanLayout, not_under_plans_dir


def is_test_path(path, patterns):
    """Whether `path` can hold a test: its file name matches a pattern or one of its directories is `tests`."""
    parts = Path(path).parts
    name = "" if str(path).endswith("/") else parts[-1] if parts else ""
    directories = parts if str(path).endswith("/") else parts[:-1]
    return "tests" in directories or any(fnmatch.fnmatchcase(name, p) for p in patterns)


def holds_no_test(paths, patterns):
    """True when none of `paths` is a directory (a trailing slash) or a test file: no test can be added there."""
    return not any(str(p).endswith("/") or is_test_path(p, patterns) for p in paths)


def discard_problems(model, path, repo):
    """The refusal of a folder plan that discards from a folder that is not directly under plans.dir, as a list. It
    is no part of the load every driver tick makes: a config that moves mid-run must reach the deletion's own check,
    not drain a run that holds an approved plan."""
    layout = PlanLayout(path, repo)
    cfg = repo_config(repo)
    if not layout.is_folder or (model.archive or cfg.archive_mode) != "discard":
        return []
    if layout.directly_under(cfg.plans_dir):
        return []
    return [not_under_plans_dir(layout.folder, cfg.plans_dir)]


def person_only_problems(model, repo):
    """The scenarios nothing can prove: no evidence, and only person-owned tickets satisfy them.

    A scenario with evidence is proven by it (a `human` item by a person, a `check` by the driver). A scenario an
    agent ticket satisfies is proven by that ticket's report, whose `covers` names the node. A scenario that
    carries no evidence and that only a person's ticket satisfies has no proof at all: the acceptance never asks
    for it, and the archive asks for a node at the very end. The lint refuses it early and names every way out:
    `human` evidence, an agent ticket, or a node the person records with `complete --cover`.

    A person-owned ticket that holds no test is out of the lint's reach: its work is not a test, so its proof is a
    node the person records by hand, wherever it lives (R57) — the lint cannot judge that node, and the
    person-ticket flow it would break is the one that flow is for. A person-owned ticket that could hold a test
    is in the lint's reach: the plan says how that scenario is proven."""
    patterns = repo_config(repo).test_patterns
    problems = []
    for scenario in model.scenarios:
        if scenario.evidence is not None:
            continue
        tickets = [t for t in model.tickets if scenario.id in t.satisfies]
        if not tickets or any(t.owner.strip().lower() == "agent" for t in tickets):
            continue
        if all(holds_no_test(ticket.paths, patterns) for ticket in tickets):
            continue
        problems.append(
            "%s: no evidence and only person-owned tickets satisfy it (%s), so nothing can prove it — attach "
            "`human` evidence to %s, let an agent ticket satisfy %s, or record a test node with "
            "`complete --cover %s=<node>`"
            % (
                scenario.id,
                ", ".join(t.id for t in tickets),
                scenario.id,
                scenario.id,
                scenario.id,
            )
        )
    return problems
