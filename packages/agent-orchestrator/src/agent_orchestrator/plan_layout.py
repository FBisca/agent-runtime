"""Where a plan lays its files out, and what a repository's listing says about them.

A plan in its own folder is `<folder>/plan.yaml`, a legacy plan `<stem>.plan.yaml` beside its narrative
and its oracle directory. Every command that reads, snapshots, places, guards or copies a plan's files
asks `PlanLayout`, so the file name is read in that class only. The git facts it is built on are here
too: an oracle's basename clashes with what the package already holds, and a folder that may be
deleted sits directly under plans.dir.
"""

import functools
import os
import posixpath
import subprocess
from pathlib import Path

from .plan_document import PlanError

# A plan in its own folder is always named this; a legacy plan is `<name>.plan.yaml` beside `<name>.oracle/`.
FOLDER_PLAN = "plan.yaml"


# What git lists for a tree: the files it tracks and the new ones it would carry, minus what it ignores.
LISTED = ("ls-files", "-z", "--cached", "--others", "--exclude-standard")


class PlanLayout:
    """The one place that knows how a plan lays out its files, for a plan in its own folder (`<folder>/plan.yaml`)
    and a legacy plan (`<stem>.plan.yaml` beside `<stem>.plan.md` and `<stem>.oracle/`). Built from the plan path and
    the repository; every command that reads, snapshots, places, guards or copies a plan's files asks it, so the
    file name is read in this class only. `repo` may be left out where only the path is asked."""

    YAML = ".plan.yaml"
    MARKDOWN = ".plan.md"

    def __init__(self, path, repo=None):
        self.path = Path(path)
        self.repo = Path(repo) if repo is not None else None
        self.kind = "folder" if self.path.name == FOLDER_PLAN else "legacy"

    @classmethod
    def of(cls, path, repo=None):
        return path if isinstance(path, cls) else cls(path, repo)

    @classmethod
    def is_plan_file(cls, path):
        """Whether `path` is named like a plan/1 YAML plan: `plan.yaml` or `<stem>.plan.yaml`."""
        name = Path(path).name
        return name == FOLDER_PLAN or name.endswith(cls.YAML)

    @classmethod
    def is_listed(cls, path):
        """Whether a plan listing shows `path`: a `<stem>.plan.yaml` or `<stem>.plan.md` plan (a folder plan is listed
        from its plans dir, not by name)."""
        return Path(path).name.endswith((cls.YAML, cls.MARKDOWN))

    @property
    def is_folder(self):
        return self.kind == "folder"

    @property
    def format(self):
        return "markdown" if self.path.name.endswith(self.MARKDOWN) else "yaml"

    @property
    def stem(self):
        """What names the plan: its folder's name for a folder plan, else the file name without its suffixes."""
        if self.is_folder:
            return self.path.parent.name
        return self.path.name.removesuffix(self.YAML).removesuffix(self.MARKDOWN)

    @property
    def folder(self):
        """The plan's own folder, or None for a legacy plan."""
        return self.path.parent if self.is_folder else None

    @property
    def oracle_dir(self):
        """A legacy plan's oracle directory beside it, or None for a folder plan."""
        return None if self.is_folder else self.path.with_name(self.stem + ".oracle")

    @property
    def narrative(self):
        """A legacy plan's Markdown narrative beside it, or None for a folder plan."""
        return None if self.is_folder else self.path.with_name(self.stem + self.MARKDOWN)

    @property
    def contract_files(self):
        """The paths that are the plan's contract: the folder plan's plan.yaml (the rest of its folder is read from
        `owns`), a legacy plan's file, narrative and oracle directory."""
        if self.is_folder:
            return [self.path]
        return [self.path, self.narrative, self.oracle_dir]

    @property
    def repository(self):
        """The repository the layout was built with; asking for a place in it without one is a mistake."""
        if self.repo is None:
            raise ValueError("this answer needs the repository: build PlanLayout(path, repo)")
        return self.repo

    def place(self, path):
        """`path` relative to the repository, or None when it is outside it."""
        repo = self.repository  # read outside the try: a missing repository is no "outside"
        try:
            return Path(path).relative_to(repo).as_posix()
        except ValueError:
            return None

    @property
    def plan_place(self):
        """Where the plan file sits in the repository; a legacy plan kept outside it is placed under docs/plans/,
        where an archive copies it. A folder plan outside the repository has no place (None)."""
        placed = self.place(self.path)
        if placed is None and not self.is_folder:
            return "docs/plans/" + self.path.name
        return placed

    def owns(self, name):
        """Whether the repository path `name` belongs to the plan: anything in a folder plan's folder, or a legacy
        plan's file or oracle directory. A folder plan kept outside the repository owns no repository path."""
        if self.is_folder:
            folder = self.place(self.folder)
            return folder is not None and name.startswith(folder + "/")
        plan = self.plan_place
        oracles = posixpath.dirname(plan) + "/" + self.oracle_dir.name
        return name == plan or name.startswith(oracles + "/")

    def holds_repo(self):
        folder = self.folder
        repo = self.repository
        return folder is not None and (repo == folder or repo.is_relative_to(folder))

    def may_delete_folder(self):
        """Whether the plan has a folder of its own that may be deleted: a folder that is the repository, or holds
        it, may not, because deleting it would delete the work."""
        return self.is_folder and not self.holds_repo()

    def directly_under(self, plans_dir):
        """Whether the plan folder's resolved parent is the resolved `plans_dir`: the only place a folder is deleted
        from. Resolving both means a folder nested deeper, or a symlink into `plans_dir`, is not directly under it."""
        folder = self.folder
        return folder is not None and folder.resolve().parent == Path(plans_dir).resolve()

    def folder_place(self):
        """The deletable folder's path in the repository; None for a legacy plan or a folder kept outside the
        repository. A folder that is or holds the repository is refused."""
        if not self.is_folder:
            return None
        if self.holds_repo():
            raise ValueError(
                "the plan folder %s is or holds the repository; move plan.yaml into a folder of its own"
                % self.folder
            )
        return self.place(self.folder)


def git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=15
    )
    if result.returncode:
        raise PlanError(f"repo/base: git {args[0]} failed — supply a valid --repo and --base")
    return result.stdout.strip()


def repository(path, repo=None):
    return (
        Path(repo).resolve()
        if repo is not None
        else Path(_toplevel(str(Path(path).parent.resolve()))).resolve()
    )


def identity(repo):
    return _identity(str(Path(repo).resolve()))


# A checkout's top level and git common dir do not change while it exists, and a driver tick asks for them dozens of
# times: one git process each was a large share of a tick. A failure raises, so it is never cached.
@functools.lru_cache(maxsize=256)
def _toplevel(directory):
    return git(directory, "rev-parse", "--show-toplevel")


@functools.lru_cache(maxsize=256)
def _identity(repo):
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    return str((Path(repo) / common).resolve())


def package_files(package, skip):
    """{basename: first path} of every file of `package` outside the `skip` trees: pytest imports a test module by
    its basename, so a promoted file needs a name no other module in the package has. What belongs to the package
    is what git lists: another checkout's or an ignored directory's copy is no module of it, and a tree outside a
    repository, which has no index to ask, is walked instead."""
    try:
        listed = git(package, *LISTED).split("\0")
        # An untracked directory git does not descend into is one entry ending in `/`, and holds no file.
        paths = [Path(package, name) for name in listed if name and not name.endswith("/")]
    except PlanError:
        paths = [Path(here, name) for here, _, names in os.walk(package) for name in names]
    return {
        path.name: path
        for path in paths
        if not any(t in (path.resolve(), *path.resolve().parents) for t in skip)
    }


def not_under_plans_dir(folder, plans_dir):
    """The refusal of a plan folder that may not be deleted, naming the folder, plans.dir and the way out."""
    resolved = Path(folder).resolve()
    return (
        f"plan folder {folder}: a plan that is deleted must sit directly under plans.dir ({plans_dir}), and its "
        f"folder resolves to {resolved} — move the folder to {plans_dir}/{resolved.name}, or set plans.dir in "
        f".orchestrate/config.yaml to {resolved.parent}"
    )
