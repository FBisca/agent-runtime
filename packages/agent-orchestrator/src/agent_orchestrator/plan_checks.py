"""The refusals an authoring command makes about a plan's paths.

`plan check`, `plan approve` and `plan amend` make them; the driver's load never runs them, so a plan
approved before a rule existed keeps running. A path decides whether it can hold a test, and a discarding
folder whether it may be deleted.
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
