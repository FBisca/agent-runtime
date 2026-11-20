"""Which plans a repository holds, for the Lead, the skills and the panel.

A plan in its own folder is listed from the plans dir the config names; a legacy plan from the one
plans directory (docs/plans, .plans or the config's plans dir) its tracked plan files sit in. Several
directories refuse, naming each and the explicit-path way out; none is an empty listing.
"""

import subprocess
from pathlib import Path

from . import config
from .plan_document import PlanError, read_yaml
from .plan_layout import FOLDER_PLAN, PlanLayout, git

# Where a repository keeps plan files besides the config's plans dir.
LEGACY_HOMES = ("docs/plans", ".plans")


class DiscoveryError(PlanError):
    """Plan discovery refused for a reason a reader can tell apart: `kind` is not_a_repo, ambiguous or no_such_path."""

    def __init__(self, kind, text, *, root=None, directories=()):
        super().__init__(text)
        self.kind, self.root, self.directories = kind, root, list(directories)


def plan_entries(directory):
    """The plan files directly in `directory`; nothing is created when it does not exist."""
    if not directory.is_dir():
        return []
    return sorted(
        (p for p in directory.iterdir() if p.is_file() and PlanLayout.is_listed(p)),
        key=lambda p: p.name,
    )


def slug_of(path):
    """The key a plan's ledger lives under: a YAML plan's `slug:` field, else the file name without its suffixes.

    A Markdown plan, or a YAML plan whose file cannot be read, keeps the file-derived slug: a listing never fails on
    the plan it lists."""
    layout = PlanLayout(path)
    derived = layout.stem  # a folder plan is named by its folder
    if not PlanLayout.is_plan_file(path):
        return derived  # a Markdown plan keeps the file-derived slug
    try:
        data = read_yaml(path)[0]
    except PlanError:
        return derived
    slug = data.get("slug") if isinstance(data, dict) else None
    return slug if isinstance(slug, str) and slug else derived


def repo_config(root):
    """The repo config as a PlanError, so every command refuses it the way it refuses a plan."""
    try:
        return config.load(root)
    except config.ConfigError as error:
        raise PlanError(f"config: {error}") from error


def folder_plans(directory):
    """`<directory>/<folder>/plan.yaml` for each folder that has one; nothing is created."""
    if not directory.is_dir():
        return []
    return sorted(p / FOLDER_PLAN for p in directory.iterdir() if (p / FOLDER_PLAN).is_file())


def repository_root(cwd, repo=None):
    """The repository `discover` reads: the one named, else the one holding `cwd`; not_a_repo when it is neither."""
    where = Path(repo).resolve() if repo is not None else Path(cwd).resolve()
    found = subprocess.run(
        ["git", "-C", str(where), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if found.returncode:
        raise DiscoveryError(
            "not_a_repo",
            f"plans: {where} is not inside a git repository — run inside one or pass --repo R",
            root=where,
        )
    return where if repo is not None else Path(found.stdout.strip()).resolve()


def discover(repo=None, explicit=None):
    """Plans for the Lead, the skills and the panel: the explicit plan (or the plan directory it
    names), else the one directory of the repository's tracked plan files.

    Several directories refuse, naming each and the explicit-path way out; none is an empty list."""
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        if not path.exists():
            raise DiscoveryError(
                "no_such_path",
                f"plans: {path} does not exist — pass an existing plan or directory",
                root=path,
            )
        directory = path if path.is_dir() else path.parent
        files = set(plan_entries(directory))
        if path.is_file():
            files.add(path)
    else:
        root = repository_root(Path.cwd(), repo)
        plans_dir = repo_config(root).plans_dir
        configured = folder_plans(plans_dir)
        tracked = git(root, "ls-files").splitlines()
        # A plan file lives in a plans directory: one elsewhere (a test fixture) is not a plan.
        homes = {(root / home).resolve() for home in LEGACY_HOMES} | {plans_dir.resolve()}
        dirs = sorted(
            {
                str(Path(f).parent)
                for f in tracked
                if PlanLayout.is_listed(f) and (root / f).parent.resolve() in homes
            }
        )
        # A repo that writes its config has named its plans dir, so the other home cannot make the
        # listing ambiguous. The built-in default names nothing.
        if len(dirs) > 1 and (root / config.CONFIG_PATH).is_file():
            chosen = [d for d in dirs if (root / d).resolve() == plans_dir.resolve()]
            dirs = chosen or dirs
        if len(dirs) > 1:
            raise DiscoveryError(
                "ambiguous",
                "plans: several plan directories in "
                + str(root)
                + ": "
                + ", ".join(dirs)
                + " — pass one plan path to choose: agent-orchestrator plans list --json <plan>",
                root=root,
                directories=dirs,
            )
        files = set(plan_entries(root / dirs[0])) if dirs else set()
        files.update(configured)
    return [
        {
            "path": str(p),
            "format": PlanLayout(p).format,
            "slug": slug_of(p),
        }
        for p in sorted(files)
    ]
