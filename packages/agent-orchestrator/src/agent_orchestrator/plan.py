"""The plan/1 contract and its local approval snapshots.

Digest inputs use eight-byte big-endian length prefixes. The plan input is
safe_dump(sort_keys=True) after dropping approval: comments, key order and YAML
presentation are preserved on disk but do not affect the contract digest.
"""

import copy
import fcntl
import fnmatch
import functools
import hashlib
import os
import posixpath
import shutil
import subprocess
import tempfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from . import config, runner
from . import ledger as ledgers

DEFAULT_STATE = "~/.local/state/orchestrate"
# A plan in its own folder is always named this; a legacy plan is `<name>.plan.yaml` beside `<name>.oracle/`.
FOLDER_PLAN = "plan.yaml"
# The folder's subdirectories whose every file the approval digests.
FOLDER_PAYLOADS = ("acceptance", "negatives")
ARCHIVE_MODES = ("promote", "discard")
ScenarioId = Annotated[str, Field(pattern=r"^S\d+[a-z]?$")]
TicketId = Annotated[str, Field(pattern=r"^T\d+$")]
Text = Annotated[str, Field(min_length=1)]


class PlanError(ValueError):
    """A contract refusal; the CLI exits 2 before dispatch."""


def literal_path(value):
    if (
        not value
        or Path(value).is_absolute()
        or ".." in Path(value).parts
        or any(c in value for c in "*?[]")
    ):
        raise ValueError("use a literal repo-relative path without globs, absolute paths or ..")
    return value


RepoPath = Annotated[
    Text, AfterValidator(literal_path), Field(description="Literal repository-relative path")
]


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)


class Evidence(Mapping):
    human: Text | None = None
    check: Text | None = None
    baseline: Literal["pass", "fail"] | None = None
    oracle: list[RepoPath] = Field(default_factory=list)
    place: RepoPath | None = None
    acceptance: list[RepoPath] = Field(
        default_factory=list,
        description="Folder plans: test files in the plan folder's acceptance/, placed for the check at "
        "<package>/<acceptance.dir>/<domain>/<file>",
    )
    package: RepoPath | None = Field(
        default=None,
        description="Folder plans: the repository directory (or .) whose acceptance dir the files are placed in",
    )
    timeout: Annotated[int, Field(gt=0)] = 600
    env: list[Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]] = Field(
        default_factory=list
    )
    negative: RepoPath | None = Field(
        default=None,
        description="A patch that must make the check fail; a legacy plan names an oracle file, a folder plan a "
        "path relative to the plan folder",
    )
    promote: bool = True

    @property
    def has_tests(self):
        """Whether the item brings test files of its own: legacy oracles or a folder plan's acceptance files."""
        return bool(self.oracle or self.acceptance)

    @model_validator(mode="after")
    def evidence_kind(self):
        if bool(self.human) == bool(self.check):
            raise ValueError("choose exactly one of human or check + baseline")
        if self.check and self.baseline is None:
            raise ValueError("check requires baseline: pass or fail")
        if self.human and self.model_fields_set - {"human"}:
            raise ValueError("human evidence may only contain human; remove check options")
        return self

    @field_validator("place", "negative", "package")
    @classmethod
    def paths(cls, value):
        return literal_path(value) if value is not None else value

    @field_validator("oracle", "acceptance")
    @classmethod
    def oracle_paths(cls, values):
        return [literal_path(v) for v in values]


class Scenario(Mapping):
    id: ScenarioId
    when: Text
    then: Text
    evidence: Evidence | None = None


class Ticket(Mapping):
    id: TicketId
    delivers: Text
    satisfies: Annotated[list[ScenarioId], Field(min_length=1)]
    paths: Annotated[list[RepoPath], Field(min_length=1)]
    size: Literal["small", "regular"] | None = None
    difficulty: Literal["simple", "complex"] | None = None
    blocked_by: list[TicketId] = Field(default_factory=list)
    owner: Text = "agent"
    risk: list[
        Literal["security", "concurrency", "retry-idempotency", "migration", "external", "state"]
    ] = Field(default_factory=list)
    evidence_waiver: Text | None = None
    max_fix_rounds: Annotated[int, Field(ge=0)] | None = None
    repo: Text | None = None

    @model_validator(mode="after")
    def agent_route(self):
        if self.owner.strip().lower() == "agent":
            if self.size is None or self.difficulty is None:
                raise ValueError(
                    "agent tickets require size and difficulty; supply the missing route fields"
                )
            if self.repo is not None:
                raise ValueError(
                    "repo is only allowed for non-agent owners; remove repo or assign to the Lead"
                )
        return self

    @field_validator("size", mode="before")
    @classmethod
    def chunked(cls, value):
        if value == "big":
            raise ValueError("big needs chunked dispatch; split it")
        return value


class Approval(Mapping):
    by: Text
    at: Text
    base: Annotated[str, Field(pattern=r"^[0-9a-f]{40,64}$")]
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Amendment(Mapping):
    prev_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    at: Text
    by: Text
    reason: Text
    tickets: list[TicketId] = Field(default_factory=list)


class Plan(Mapping):
    schema_version: Literal["plan/1"] = Field(alias="schema")
    slug: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$")]
    goal: Text
    spec: RepoPath | None = Field(
        default=None,
        description="The living spec the archive writes; required unless the plan is a folder plan that discards",
    )
    domain: Annotated[str, Field(pattern=r"^[a-z0-9-]+$")] | None = Field(
        default=None,
        description="Folder plans: the slug of the folder under <package>/<acceptance.dir>/ that promoted tests "
        "join; required to promote",
    )
    archive: Literal["promote", "discard"] | None = Field(
        default=None,
        description="Folder plans: what archive does with the plan folder; the repo config's archive.mode when "
        "absent",
    )
    outcome: Annotated[list[ScenarioId], Field(min_length=1)]
    scenarios: Annotated[list[Scenario], Field(min_length=1)]
    tickets: Annotated[list[Ticket], Field(min_length=1)]
    approval: Approval | None = None
    amendments: list[Amendment] = Field(
        default_factory=list,
        description="The history `plan amend` appends to: neither it nor the approval is part of the digest",
    )

    @field_validator("spec")
    @classmethod
    def spec_path(cls, value):
        return literal_path(value) if value is not None else value

    def legacy(self, statuses=None):
        """Keep the existing loop's routes and blocking semantics in one place."""
        statuses = statuses or {}
        return {
            "slug": self.slug,
            "status": "APPROVED" if self.approval else None,
            "scenarios": {
                s.id: f"- **{s.id}** WHEN {s.when}, THEN {s.then}" for s in self.scenarios
            },
            "tickets": [
                {
                    "line": i,
                    "#": t.id,
                    "What it delivers": t.delivers,
                    "Satisfies": ",".join(t.satisfies),
                    "Blocked by": ",".join(t.blocked_by) or "none",
                    "Paths": ",".join(t.paths),
                    "Size": t.size,
                    "Difficulty": t.difficulty,
                    "Owner": t.owner,
                    "Status": statuses.get(t.id, "TO-DO"),
                }
                for i, t in enumerate(self.tickets)
            ],
            "has_table": True,
            "caps": {t.id: t.max_fix_rounds for t in self.tickets if t.max_fix_rounds is not None},
            "cap_problems": [],
        }


def yaml_path(loc):
    result = ""
    for item in loc:
        result += f"[{item}]" if isinstance(item, int) else ("." if result else "") + str(item)
    return result or "plan"


class UniqueLoader(yaml.SafeLoader):
    """SafeLoader with duplicate diagnostics tied to the original YAML paths."""

    def __init__(self, stream):
        super().__init__(stream)
        self.paths: dict[int, tuple] = {}
        self.problems: list[str] = []

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key, _ in node.value:
            name = self.construct_object(key, deep=deep)
            if name in seen:
                self.problems.append(
                    f"{yaml_path(self.paths[id(node)] + (name,))}: duplicate key — remove the duplicate"
                )
            seen.add(name)
        return super().construct_mapping(node, deep=deep)


def read_yaml(path):
    try:
        raw = Path(path).read_text(encoding="utf-8")
        loader = UniqueLoader(raw)
        try:
            node = loader.get_single_node()
            loader.paths, loader.problems = {}, []

            def walk(n, loc):
                if id(n) in loader.paths:
                    return
                loader.paths[id(n)] = loc
                if isinstance(n, yaml.MappingNode):
                    for k, v in n.value:
                        walk(v, loc + (k.value,))
                elif isinstance(n, yaml.SequenceNode):
                    for i, v in enumerate(n.value):
                        walk(v, loc + (i,))

            if node is None:
                return None, ["plan: empty YAML — write a plan/1 mapping"]
            walk(node, ())
            return loader.construct_document(node), loader.problems
        finally:
            loader.dispose()
    except (OSError, UnicodeError, yaml.YAMLError, TypeError) as error:
        # Parser diagnostics can include command/env values; expose only the location.
        mark = getattr(error, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark else ""
        raise PlanError(
            f"{path}: unreadable or invalid YAML{where} — fix the file and run plan check"
        ) from None


def is_folder_plan(path):
    return Path(path).name == FOLDER_PLAN


def oracle_dir(path):
    path = Path(path)
    stem = path.name.removesuffix(".plan.yaml")
    return path.with_name(stem + ".oracle")


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


def is_test_path(path, patterns):
    """Whether `path` can hold a test: its file name matches a pattern or one of its directories is `tests`."""
    parts = Path(path).parts
    name = "" if str(path).endswith("/") else parts[-1] if parts else ""
    directories = parts if str(path).endswith("/") else parts[:-1]
    return "tests" in directories or any(fnmatch.fnmatchcase(name, p) for p in patterns)


def holds_no_test(paths, patterns):
    """True when none of `paths` is a directory (a trailing slash) or a test file: no test can be added there."""
    return not any(str(p).endswith("/") or is_test_path(p, patterns) for p in paths)


def lint_problems(model, repo):
    """Problems `plan check`, `plan approve` and `plan amend` refuse; the driver's load never runs this, so a plan
    approved before a rule existed keeps running."""
    patterns = repo_config(repo).test_patterns
    return [
        f"tickets[{i}] {ticket.id}: satisfies {', '.join(ticket.satisfies)} but its paths hold no test file "
        "or directory, so the covers gate could never pass — add a test file, or a directory that holds its "
        "tests, to its paths"
        for i, ticket in enumerate(model.tickets)
        if ticket.owner.strip().lower() == "agent" and holds_no_test(ticket.paths, patterns)
    ]


def semantic_problems(data, path, repo):
    """Inspect independent fields even when other fields fail Pydantic validation."""
    problems = []
    if not isinstance(data, dict):
        return problems

    def defect(loc, why, fix):
        problems.append(f"{loc}: {why} — {fix}")

    folder = is_folder_plan(path)
    scenarios = data.get("scenarios")
    tickets = data.get("tickets")
    scenarios = scenarios if isinstance(scenarios, list) else []
    tickets = tickets if isinstance(tickets, list) else []
    ids = {}
    for collection, rows in (("scenarios", scenarios), ("tickets", tickets)):
        seen = set()
        for i, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            sid = row["id"]
            if sid in seen:
                defect(f"{collection}[{i}].id", f"duplicate id {sid}", "use a unique id")
            seen.add(sid)
        ids[collection] = seen
    evidence_ids = {
        s.get("id")
        for s in scenarios
        if isinstance(s, dict)
        and isinstance(s.get("id"), str)
        and isinstance(s.get("evidence"), dict)
        and (s["evidence"].get("check") or s["evidence"].get("human"))
    }
    for i, sid in enumerate(
        data.get("outcome", []) if isinstance(data.get("outcome"), list) else []
    ):
        if not isinstance(sid, str) or sid not in ids["scenarios"]:
            defect(f"outcome[{i}]", f"{sid} is not a scenario", "name a declared scenario id")
        elif sid not in evidence_ids:
            defect(
                f"outcome[{i}]",
                f"{sid} has no evidence",
                "attach check + baseline or human evidence",
            )
    edges = {}
    for i, ticket in enumerate(tickets):
        if not isinstance(ticket, dict):
            continue
        prefix = f"tickets[{i}]"
        for field, target in (("satisfies", "scenarios"), ("blocked_by", "tickets")):
            values = ticket.get(field, [])
            if not isinstance(values, list):
                continue
            for j, value in enumerate(values):
                if not isinstance(value, str) or value not in ids[target]:
                    noun = "ticket" if target == "tickets" else "scenario"
                    defect(
                        f"{prefix}.{field}[{j}]",
                        f"{value} is not a {noun}",
                        f"name an existing {noun} id or remove it",
                    )
        if isinstance(ticket.get("id"), str):
            edges[ticket["id"]] = (
                (i, [b for b in ticket.get("blocked_by", []) if isinstance(b, str)])
                if isinstance(ticket.get("blocked_by", []), list)
                else (i, [])
            )
        if (
            ticket.get("repo") is not None
            and str(ticket.get("owner", "agent")).strip().lower() == "agent"
        ):
            defect(
                prefix + ".repo",
                "repo is only allowed for a non-agent owner",
                "remove repo or assign the ticket to the Lead",
            )
        satisfies = ticket.get("satisfies", [])
        satisfies = satisfies if isinstance(satisfies, list) else []
        if str(ticket.get("owner", "agent")).strip().lower() == "agent":
            for field in ("size", "difficulty"):
                if ticket.get(field) is None:
                    defect(
                        prefix + "." + field,
                        "missing agent route field",
                        "supply size and difficulty",
                    )
        if (
            ticket.get("risk")
            and not ticket.get("evidence_waiver")
            and not any(isinstance(s, str) and s in evidence_ids for s in satisfies)
        ):
            defect(
                prefix + ".risk",
                "risk-tagged ticket has no evidence",
                "satisfy an evidence-bearing scenario or add evidence_waiver",
            )
        for j, value in enumerate(
            ticket.get("paths", []) if isinstance(ticket.get("paths"), list) else []
        ):
            if isinstance(value, str):
                try:
                    literal_path(value)
                except ValueError as error:
                    problems.append(f"{prefix}.paths[{j}]: {error}")
    state = {}

    def visit(sid, stack):
        state[sid] = "open"
        i, blockers = edges[sid]
        for b in blockers:
            if state.get(b) == "open":
                defect(
                    f"tickets[{i}].blocked_by",
                    "dependency cycle: " + " -> ".join(stack + [b]),
                    "remove a dependency to break the cycle",
                )
            elif b in edges and b not in state:
                visit(b, stack + [b])
        state[sid] = "done"

    for sid in edges:
        if sid not in state:
            visit(sid, [sid])
    # D14: a risk-tagged ticket that is not waived must prove its scenarios with a negative control, since a
    # baseline pass alone cannot show that the oracle would catch the behaviour breaking.
    risk_gated = {
        s
        for ticket in tickets
        if isinstance(ticket, dict) and ticket.get("risk") and not ticket.get("evidence_waiver")
        for s in (ticket.get("satisfies") if isinstance(ticket.get("satisfies"), list) else [])
        if isinstance(s, str)
    }
    for i, scenario in enumerate(scenarios):
        ev = scenario.get("evidence") if isinstance(scenario, dict) else None
        if not isinstance(ev, dict):
            continue
        prefix = f"scenarios[{i}].evidence"
        if (
            ev.get("check")
            and ev.get("baseline") == "pass"
            and ev.get("negative") is None
            and scenario.get("id") in risk_gated
        ):
            defect(
                prefix,
                "risk-tagged ticket relies on baseline-pass evidence with no negative control",
                "add a negative patch that breaks the behaviour (set negative to its oracle file), "
                "set baseline: fail, or add evidence_waiver to the ticket",
            )
        if bool(ev.get("human")) == bool(ev.get("check")):
            defect(
                prefix,
                "evidence needs exactly one of check or human",
                "choose human or check + baseline",
            )
        if ev.get("check") and ev.get("baseline") is None:
            defect(prefix + ".baseline", "check needs baseline", "set pass or fail")
        if ev.get("human") and any(
            k in ev
            for k in (
                "check",
                "baseline",
                "oracle",
                "place",
                "acceptance",
                "package",
                "negative",
                "timeout",
                "env",
                "promote",
            )
        ):
            defect(
                prefix,
                "human evidence cannot carry check options",
                "keep only human or use check + baseline",
            )
        declared = ev.get("env", [])
        for name in declared if isinstance(declared, list) else []:
            if isinstance(name, str) and (name in ("HOME", "PATH") or name.startswith("XDG_")):
                defect(
                    prefix + ".env",
                    f"{name} is reserved: the runner owns the check's HOME, PATH and XDG_* variables",
                    "drop the name; the runner supplies a scratch value",
                )
        if folder:
            continue  # a folder plan's evidence is read by folder_problems
        names = ev.get("oracle", [])
        names = names if isinstance(names, list) else []
        if names and not ev.get("place"):
            defect(
                prefix + ".place",
                "oracle needs a placement directory",
                "set a directory inside the repo",
            )
        place = ev.get("place")
        destination = None
        if isinstance(place, str):
            try:
                literal_path(place)
                destination = (repo / place).resolve()
                destination.relative_to(repo)
            except ValueError:
                defect(
                    prefix + ".place",
                    "oracle place escapes the repo",
                    "use a directory inside the repo without ..",
                )
                destination = None
        for label, name in [(f"oracle[{j}]", n) for j, n in enumerate(names)] + (
            [("negative", ev["negative"])] if ev.get("negative") is not None else []
        ):
            if not isinstance(name, str):
                continue
            source = oracle_dir(path) / name
            try:
                literal_path(name)
                source.resolve().relative_to(oracle_dir(path).resolve())
                exists = source.is_file()
            except ValueError:
                exists = False
            if not exists:
                defect(
                    prefix + "." + label,
                    f"oracle file {name} missing or outside oracle dir",
                    "add the file to the oracle directory or remove the reference",
                )
            if label.startswith("oracle") and destination is not None:
                placed = destination / name.removesuffix(".oracle")
                # archive promotes the oracle onto its place byte for byte: that copy is the plan's own, not a clash
                promoted = (
                    exists
                    and placed.is_file()
                    and not placed.is_symlink()
                    and placed.read_bytes() == source.read_bytes()
                )
                if not placed.resolve().is_relative_to(repo) or (
                    (placed.exists() or placed.is_symlink()) and not promoted
                ):
                    defect(
                        prefix + ".place",
                        f"placed oracle {placed} already exists or escapes repo",
                        "choose an unused location inside the repo",
                    )
    if folder:
        problems += folder_problems(data, path, repo, scenarios)
    else:
        problems += legacy_problems(data, scenarios)
    return problems


# What a folder plan says in place of each legacy evidence key.
LEGACY_KEYS = {
    "oracle": "acceptance (files in the plan folder's acceptance/)",
    "place": "package (the destination follows from package, acceptance.dir and domain)",
    "promote": "the top-level archive: promote | discard",
}
SKIPPED = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
}


def legacy_problems(data, scenarios):
    """A legacy plan keeps its keys: the folder plan's are refused, each naming the legacy key to use."""
    problems = []
    for key in ("domain", "archive"):
        if key in data:
            problems.append(
                f"{key}: only a plan in its own folder (<folder>/plan.yaml) has this key — remove it, "
                "or move the plan into a folder"
            )
    if data.get("spec") is None:
        problems.append("spec: Field required — supply the required field")
    for i, scenario in enumerate(scenarios):
        ev = scenario.get("evidence") if isinstance(scenario, dict) else None
        for key, legacy in (("acceptance", "oracle"), ("package", "place")):
            if isinstance(ev, dict) and key in ev:
                problems.append(
                    f"scenarios[{i}].evidence.{key}: only a plan in its own folder has this key — "
                    f"use {legacy} (the legacy plan's {legacy} key), or move the plan into a folder"
                )
    return problems


def package_files(package, skip):
    """{basename: first path} of every file in `package`, minus VCS, caches and the `skip` trees: pytest imports a
    test module by its basename, so a promoted file needs a name no other module in the package has."""
    found = {}
    for here, dirs, names in os.walk(package):
        dirs[:] = sorted(
            d for d in dirs if d not in SKIPPED and Path(here, d).resolve() not in skip
        )
        for name in sorted(names):
            found.setdefault(name, Path(here, name))
    return found


def folder_problems(data, path, repo, scenarios):
    """The rules of a plan in its own folder: the folder keys, their files, and what a promote may not clobber."""
    problems = []

    def defect(loc, why, fix):
        problems.append(f"{loc}: {why} — {fix}")

    cfg = repo_config(repo)
    folder = Path(path).resolve().parent
    mode = data.get("archive") if data.get("archive") in ARCHIVE_MODES else cfg.archive_mode
    promote = mode == "promote"
    domain = data.get("domain")
    if promote and domain is None:
        defect(
            "domain",
            "a plan that promotes needs a domain",
            "set domain: <slug> (the folder under <package>/%s/ the tests join), or set archive: discard"
            % cfg.acceptance_dir,
        )
    if promote and data.get("spec") is None:
        defect(
            "spec",
            "a plan that promotes needs the living spec the archive writes",
            "set spec: <repo-relative path>, or set archive: discard",
        )
    skip = {folder, cfg.plans_dir.resolve()}
    indexes = {}
    for i, scenario in enumerate(scenarios):
        ev = scenario.get("evidence") if isinstance(scenario, dict) else None
        if not isinstance(ev, dict):
            continue
        prefix = f"scenarios[{i}].evidence"
        for key, new in LEGACY_KEYS.items():
            if key in ev:
                defect(f"{prefix}.{key}", "a plan in its own folder has no such key", f"use {new}")
        names = ev.get("acceptance", [])
        names = names if isinstance(names, list) else []
        package = ev.get("package")
        package_dir = None
        if isinstance(package, str):
            try:
                literal_path(package)
                package_dir = (repo / package).resolve()
                package_dir.relative_to(repo)
            except ValueError:
                defect(
                    f"{prefix}.package",
                    f"package {package} is outside the repository",
                    "name a directory inside the repository, or . for its root",
                )
                package_dir = None
        elif names:
            defect(
                f"{prefix}.package",
                "acceptance files need a package to be placed in",
                "set package: <directory inside the repository> or .",
            )
        for j, name in enumerate(names):
            if not isinstance(name, str):
                continue
            where = f"{prefix}.acceptance[{j}]"
            try:
                literal_path(name)
                source = (folder / "acceptance" / name).resolve()
                source.relative_to((folder / "acceptance").resolve())
                exists = source.is_file()
            except ValueError:
                exists = False
            if not exists:
                defect(
                    where,
                    f"acceptance file {folder / 'acceptance' / name} is missing or outside the plan's acceptance/",
                    "add the file to the plan's acceptance/ or remove the reference",
                )
                continue
            if not promote or package_dir is None or not isinstance(domain, str):
                continue
            if package_dir not in indexes:
                indexes[package_dir] = package_files(package_dir, skip)
            clash = indexes[package_dir].get(Path(name).name)
            if clash is not None:
                defect(
                    where,
                    f"the basename {Path(name).name} matches {clash.relative_to(repo)} in the package, and "
                    "pytest imports modules by basename",
                    "rename the acceptance file",
                )
            placed = package_dir / cfg.acceptance_dir / domain / name
            if placed.exists() or placed.is_symlink() or not placed.resolve().is_relative_to(repo):
                defect(
                    where,
                    f"the destination {placed} already exists or is outside the repository",
                    "choose another domain or rename the file",
                )
        negative = ev.get("negative")
        if isinstance(negative, str):
            try:
                literal_path(negative)
                source = (folder / negative).resolve()
                source.relative_to(folder)
                exists = source.is_file()
            except ValueError:
                exists = False
            if not exists:
                defect(
                    f"{prefix}.negative",
                    f"negative file {folder / negative} is missing or outside the plan folder",
                    "add the patch to the plan folder (negatives/) or remove the reference",
                )
    return problems


# What a person or `plan amend` writes about the contract, not the contract: neither enters the digest.
UNDIGESTED = ("approval", "amendments")


def canonical(data):
    return yaml.safe_dump(
        {k: v for k, v in data.items() if k not in UNDIGESTED}, sort_keys=True
    ).encode("utf-8")


def payloads(directory):
    return (
        {
            p.relative_to(directory).as_posix(): p.read_bytes()
            for p in sorted(directory.rglob("*"))
            if p.is_file()
        }
        if directory.exists()
        else {}
    )


def folder_files(path, data):
    """{folder-relative name: bytes} of a folder plan's acceptance/ and negatives/ and of each negative the plan
    names: what approval digests, snapshots and later compares. Interpreter caches are not the plan's."""
    folder = Path(path).parent
    names = set()
    for sub in FOLDER_PAYLOADS:
        base = folder / sub
        if base.is_dir():
            names.update(
                p.relative_to(folder).as_posix()
                for p in base.rglob("*")
                if p.is_file() and "__pycache__" not in p.relative_to(base).parts
            )
    for scenario in data.get("scenarios", []) if isinstance(data, dict) else []:
        named = scenario.get("evidence", {}).get("negative") if isinstance(scenario, dict) else None
        if isinstance(named, str) and (folder / named).is_file():
            names.add(posixpath.normpath(named))
    return {n: (folder / n).read_bytes() for n in sorted(names)}


def approved_files(path, data):
    """The files an approval of `path` covers, by the name the digest and the snapshot use."""
    return folder_files(path, data) if is_folder_plan(path) else payloads(oracle_dir(path))


def snapshot_files(snapshot, path):
    """The files of an approval snapshot directory: `oracle/` for a legacy plan, the folder-relative names for a
    folder plan (everything beside its plan.yaml)."""
    if not is_folder_plan(path):
        return payloads(snapshot / "oracle")
    return {n: b for n, b in payloads(snapshot).items() if n != "plan.yaml"}


def file_path(path, name):
    """Where a snapshot file name lives in the authored plan, for a message."""
    return Path(path).parent / name if is_folder_plan(path) else oracle_dir(path) / name


def destination(model, item, repo):
    """The repo-relative directory a folder plan's acceptance files are placed in for `item`:
    `<package>/<acceptance.dir>/<domain>`. A plan with no domain (it discards) has none."""
    parts = [item.package or ".", repo_config(repo).acceptance_dir] + (
        [model.domain] if model.domain else []
    )
    return posixpath.normpath(posixpath.join(*parts))


def runner_inputs(path, model, item, files, repo):
    """(oracles, place, negative) the runner takes for an evidence item: test file bytes by name, the directory they
    are placed in, and the negative patch as (name, bytes). One derivation for the baseline, the runs and the
    mutation checks, whichever layout the plan has."""
    negative = (item.negative, files[item.negative]) if item.negative else None
    if not is_folder_plan(path):
        return {name: files[name] for name in item.oracle}, item.place, negative
    return (
        {name: files["acceptance/" + name] for name in item.acceptance},
        destination(model, item, repo) if item.acceptance else None,
        negative,
    )


def placed_files(path, model, item, repo):
    """Repo-relative path of each test file an evidence item places."""
    if not is_folder_plan(path):
        return [
            ((item.place or "").rstrip("/") + "/" + name.removesuffix(".oracle")).lstrip("/")
            for name in item.oracle
        ]
    return [posixpath.join(destination(model, item, repo), name) for name in item.acceptance]


def digest(repo_identity, base, plan_bytes, files):
    parts = [repo_identity.encode(), base.encode(), plan_bytes]
    for name, content in sorted(files.items()):
        parts.extend((name.encode(), content))
    h = hashlib.sha256()
    for part in parts:
        h.update(len(part).to_bytes(8, "big"))
        h.update(part)
    return h.hexdigest()


def snapshot_path(approval, state_dir):
    # Filenames are not digest inputs: renaming a contract must reuse its snapshot.
    return Path(state_dir).expanduser() / "approvals" / approval.digest / "plan.yaml"


def verify_snapshot(path, model, repo, state_dir):
    way_out = f"run agent-orchestrator plan approve {path}"
    snap = snapshot_path(model.approval, state_dir)
    if not snap.is_file():
        raise PlanError(f"approval: snapshot missing at {snap} — {way_out}")
    snap_data, errors = read_yaml(snap)
    if errors or not isinstance(snap_data, dict):
        raise PlanError(f"approval: invalid snapshot {snap} — {way_out}")
    old_plan = snap.read_bytes()
    old_files = snapshot_files(snap.parent, path)
    live_data, _ = read_yaml(path)
    live_files = approved_files(path, live_data)
    changed = []
    if canonical(live_data) != old_plan:
        changed.append(str(path))
    changed += [
        str(file_path(path, n))
        for n in sorted(old_files.keys() | live_files.keys())
        if old_files.get(n) != live_files.get(n)
    ]
    if digest(identity(repo), model.approval.base, old_plan, old_files) != model.approval.digest:
        changed.append(f"repo identity/base or snapshot {snap.parent}")
    if changed:
        raise PlanError("approval: changed " + ", ".join(changed) + " — " + way_out)
    # The driver consumes the snapshot, after checking the authored file against it.
    return Plan.model_validate(snap_data).model_copy(update={"approval": model.approval})


def load(
    path, *, repo=None, state_dir=DEFAULT_STATE, require_approval=False, verify=True, lint=None
):
    """`lint` runs the refusals that only authoring commands make. It defaults to on for `plan check` (which
    verifies without requiring an approval) and off for the driver's load, which requires one."""
    lint = verify and not require_approval if lint is None else lint
    path = Path(path).resolve()
    data, problems = read_yaml(path)
    model = None
    try:
        model = Plan.model_validate(data)
    except ValidationError as error:
        for item in error.errors(include_input=False, include_url=False):
            fix = (
                "remove the unknown key"
                if item["type"] == "extra_forbidden"
                else "supply the required field"
                if item["type"] == "missing"
                else "use the declared plan/1 type or value"
            )
            problems.append(f"{yaml_path(item['loc'])}: {item['msg']} — {fix}")
    repo = repository(path, repo)
    problems += semantic_problems(data, path, repo)
    if problems:
        if isinstance(data, dict) and data.get("approval") and verify:
            problems.append(
                f"approval: after fixing {path}, run agent-orchestrator plan approve {path}"
            )
        raise PlanError("; ".join(problems))
    assert model is not None
    if lint and (found := lint_problems(model, repo)):
        raise PlanError("; ".join(found))
    if model.approval and verify:
        return verify_snapshot(path, model, repo, state_dir)
    if require_approval and model.approval is None:
        raise PlanError(
            f"approval: {path} is unapproved — run agent-orchestrator plan approve {path}"
        )
    return model


def replaced_text(text, name, new):
    """`text` with its top-level `name` set to `new` (appended when absent), every other byte kept: the
    author's comments survive."""
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    existing = next(((k, v) for k, v in node.value if k.value == name), None)
    if node.flow_style:
        entry = yaml.safe_dump({name: new}, default_flow_style=True, width=10000).strip()[1:-1]
        if existing:
            key, value = existing
            return text[: key.start_mark.index] + entry + text[value.end_mark.index :]
        end = node.end_mark.index - 1
        return text[:end] + (", " if node.value else "") + entry + text[end:]
    eol = "\r\n" if "\r\n" in text else "\n"
    block = yaml.safe_dump({name: new}, sort_keys=False).replace("\n", eol)
    if existing:
        key, value = existing
        start, end = key.start_mark.index, value.end_mark.index
        if text[end:].startswith(eol):
            end += len(eol)
    else:
        # An explicit YAML document terminator must stay after the new block.
        start = end = next(
            (
                e.start_mark.index
                for e in yaml.parse(text)
                if isinstance(e, yaml.DocumentEndEvent) and e.explicit
            ),
            len(text),
        )
    prefix = text[:start]
    return prefix + (eol if prefix and not prefix.endswith("\n") else "") + block + text[end:]


def approved_text(text, approval):
    return replaced_text(text, "approval", approval)


def evidence_key(path, model, item, files, repo):
    """What one check evidence item runs: the check, its oracle bytes (by hash), place, env names, timeout and
    negative. Two items with one key observe the same thing at one commit."""
    oracles, place, negative = runner_inputs(path, model, item, files, repo)
    return (
        item.check,
        tuple(sorted((name, runner.sha256(content)) for name, content in oracles.items())),
        place,
        tuple(item.env),
        item.timeout,
        (negative[0], runner.sha256(negative[1])) if negative else None,
    )


def checked_scenarios(model):
    return [s for s in model.scenarios if s.evidence is not None and s.evidence.check]


def baseline(path, model, repo, base_sha, approval_digest, files, carried=None):
    """Run every check item of the plan once at `base` and judge it against its `baseline:` (D24).

    Scenarios whose items would run the same thing share one run, and every run shares one worktree, so a plan pays
    for each distinct check and one venv, not for each scenario. `carried` maps a scenario ID to the Run of its
    unchanged item, re-issued from an earlier approval: it is judged like a fresh run and not run again. Returns
    the runs to persist, each with the calibration to record; raises PlanError naming every item that is not what
    it claims, before anything is written."""
    runs, refusals, seen, carried = [], [], {}, carried or {}
    checked = checked_scenarios(model)
    if not checked:
        return runs
    with ExitStack() as stack:
        shared = None
        for scenario in checked:
            item = scenario.evidence
            oracles, place, negative = runner_inputs(path, model, item, files, repo)
            key = evidence_key(path, model, item, files, repo)
            if scenario.id in carried:
                run = seen[key] = carried[scenario.id]
            elif key in seen:
                first = seen[key]
                run = runner.Run(copy.deepcopy(first.receipt), first.log)
                run.receipt["scenario"] = scenario.id
            else:
                if shared is None:
                    shared = stack.enter_context(runner.Workspace(repo, base_sha))
                run = seen[key] = runner.run_evidence(
                    repo,
                    base_sha,
                    scenario=scenario.id,
                    phase="baseline",
                    digest=approval_digest,
                    check=item.check,
                    oracles=oracles,
                    place=place,
                    env_names=item.env,
                    timeout=item.timeout,
                    negative=negative,
                    workspace=shared,
                )
            ok, observed, calibrated = runner.judge_baseline(
                run.receipt, item.baseline, item.has_tests
            )
            if not ok:
                refusals.append(
                    "%s: baseline expected %s at %s, observed %s — %s"
                    % (
                        scenario.id,
                        item.baseline,
                        base_sha[:12],
                        observed,
                        "make the oracle fail on an assertion at base and pass its controls, or set baseline: pass"
                        if item.baseline == "fail"
                        else "fix the check or set baseline: fail",
                    )
                )
            runs.append((run, {"calibrated": calibrated, "expected": item.baseline}))
    if refusals:
        raise PlanError("approval: " + "; ".join(refusals) + " — nothing was approved")
    return runs


def carried_baselines(path, repo, old, old_files, model, files, previous, sha, state_dir):
    """The baseline Runs `plan amend` re-issues instead of running: each check item whose evidence is the same in the
    approved snapshot `old` and in the edit, and whose receipt for the previous digest still passes the receipt
    ledger's tamper check, under the new digest `sha`. A changed or new item, or one whose receipt cannot be vouched
    for, is not here and runs."""
    from . import (
        verify,
    )  # verify reads plans, so it imports this module: the import waits until it is called

    ledger, receipts = state_paths(repo, model.slug, state_dir)
    found = ledgers.snapshot(ledger)  # a read: it never waits for the driver
    if not found:
        return {}
    try:
        was = Plan.model_validate(old)
    except ValidationError:
        return {}
    before = {s.id: s.evidence for s in checked_scenarios(was)}
    tree = git(repo, "rev-parse", previous.base + "^{tree}")
    carried = {}
    for scenario in checked_scenarios(model):
        item = scenario.evidence
        if scenario.id not in before:
            continue
        if evidence_key(path, was, before[scenario.id], old_files, repo) != evidence_key(
            path, model, item, files, repo
        ):
            continue
        kept = verify.carryable_baseline(found, scenario.id, receipts, previous.digest, tree)
        if kept is not None:
            carried[scenario.id] = runner.reissue(
                *kept, scenario=scenario.id, digest=sha, carried_from=previous.digest
            )
    return carried


def state_paths(repo, slug, state_dir):
    """(ledger path, receipts directory) of a plan: `<state>/<repo-key>/<slug>.json` and `.../<slug>/receipts` (D17).

    A ledger an older driver wrote under `<state>/<slug>.json` is imported first when this repository owns it, so
    creating the ledger here never hides it."""
    # The driver owns the state key and imports this module, so it is imported late.
    from . import driver

    state = Path(state_dir).expanduser()
    keyed = state / driver.repo_key(repo)
    ledger, run_dir = keyed / (slug + ".json"), keyed / slug
    driver.import_old_ledger(state, repo, slug, ledger, run_dir)
    return ledger, run_dir / "receipts"


@contextmanager
def plan_lock(repo, filename, who, refusal):
    """A non-blocking flock on `<git common dir>/agent-orchestrator/<filename>`, the protocol of `driver.run_lock`
    (it is written here because the driver's helper runs git through a function its callers replace). The file names
    its holder; `refusal` is the PlanError text for a held lock, given that holder."""
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    directory = common / "agent-orchestrator"
    directory.mkdir(exist_ok=True, mode=0o700)
    fd = os.open(directory / filename, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PlanError(refusal(lock.read().strip() or "another process")) from None
        lock.seek(0)
        lock.truncate()
        lock.write(
            "%s, pid %d since %s\n"
            % (who, os.getpid(), datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        )
        lock.flush()
        yield


def run_lock(repo, slug):
    """The plan's run lock, the file a driver holds for a whole run, so a live driver refuses approval and a driver
    refuses to start under one."""
    return plan_lock(
        repo,
        slug + ".lock",
        "plan approve",
        lambda holder: (
            "plan %s is being driven by %s; stop that driver (close its orchestrate-%s pane), then "
            "run plan approve again" % (slug, holder, slug)
        ),
    )


def amend_lock(repo, slug):
    """The lock of one `plan amend` at a time. It is not the run lock: a live driver holds that for its whole run
    and an amendment must go through it, so two amendments exclude each other here and a driver never needs it."""
    return plan_lock(
        repo,
        slug + ".amend.lock",
        "plan amend",
        lambda holder: (
            "plan %s is being amended by %s; wait for that plan amend to finish, then run it again"
            % (slug, holder)
        ),
    )


def approve(path, *, repo=None, state_dir=DEFAULT_STATE, base="origin/main"):
    """Approve a plan: run its baseline, then write the receipts, their ledger hashes, the snapshot and the block.

    Everything runs under the plan's run lock, so a live driver is never moved or raced; a held lock refuses before
    anything is written."""
    path = Path(path).resolve()
    repo = repository(path, repo)
    model = load(path, repo=repo, verify=False, lint=True)
    with run_lock(repo, model.slug):
        try:
            return approve_locked(path, repo, model, state_dir, base)
        except runner.RunnerError as error:
            raise PlanError(
                "approval: %s — the plan was not approved; fix that and run plan approve again"
                % error
            ) from None


def approve_locked(path, repo, model, state_dir, base):
    data, _ = read_yaml(path)
    plan_bytes = canonical(data)
    files = approved_files(path, data)
    base_sha = git(repo, "rev-parse", "--verify", base + "^{commit}")
    sha = digest(identity(repo), base_sha, plan_bytes, files)
    # A refusal here leaves no file behind: the runner writes nothing until the runs are accepted.
    runs = baseline(path, model, repo, base_sha, sha, files)
    if runs:
        ledger, receipts = state_paths(repo, model.slug, state_dir)
        for run, calibration in runs:
            runner.persist(run, receipts, calibration)
        runner.record_receipts(ledger, [run for run, _ in runs])
    approval = {
        "by": git_email(repo),
        "at": datetime.now(timezone.utc).isoformat(),
        "base": base_sha,
        "digest": sha,
    }
    # Only the top-level approval span is replaced; the author's comments survive.
    publish(
        path,
        approved_text(path.read_bytes().decode("utf-8"), approval),
        plan_bytes,
        files,
        sha,
        state_dir,
    )
    return approval


def git_email(repo):
    email = subprocess.run(
        ["git", "-C", str(repo), "config", "user.email"], capture_output=True, text=True, timeout=15
    ).stdout.strip()
    return email or os.environ.get("USER") or "unknown"


def publish(path, text, plan_bytes, files, sha, state_dir):
    """Write the approval snapshot of digest `sha`, then replace the plan file with `text`, in that order: a driver
    that reads the file in between finds the snapshot of the old approval and of the new one."""
    approvals = Path(state_dir).expanduser() / "approvals"
    approvals.mkdir(parents=True, exist_ok=True)
    target = approvals / sha
    snap = target / "plan.yaml"
    if (
        not snap.is_file()
        or snap.read_bytes() != plan_bytes
        or snapshot_files(target, path) != files
    ):
        temporary = Path(tempfile.mkdtemp(prefix=".approval-", dir=approvals))
        try:
            (temporary / "plan.yaml").write_bytes(plan_bytes)
            for name, content in files.items():
                dest = temporary / name if is_folder_plan(path) else temporary / "oracle" / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
            if target.exists():
                shutil.rmtree(target)
            temporary.rename(target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    fd, temporary = tempfile.mkstemp(prefix=".approval-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def amend(path, *, reason, repo=None, state_dir=DEFAULT_STATE, runtime_id=None):
    """Re-approve an approved plan after an edit, at the base its approval already has.

    The baselines run at that base, not at `origin/main`: a ticket that merged since may have turned a
    `baseline: pass` check red, and the plan's evidence is still judged against the tree it was approved on. The
    amendment holds its own lock, never the run lock, so a live driver is not stopped; a ticket the ledger marks
    COMPLETE is frozen, and nothing is written when the amendment is refused."""
    path = Path(path).resolve()
    repo = repository(path, repo)
    model = load(path, repo=repo, verify=False, lint=False)
    if model.approval is None:
        raise PlanError(
            f"approval: {path} is unapproved, so there is nothing to amend — run agent-orchestrator plan approve {path}"
        )
    if not reason.strip():
        raise PlanError("amend: --reason is empty — say why the plan changes")
    if found := lint_problems(model, repo):
        raise PlanError("; ".join(found))
    with amend_lock(repo, model.slug):
        try:
            return amend_locked(path, repo, model, state_dir, reason, runtime_id)
        except runner.RunnerError as error:
            raise PlanError(
                "approval: %s — the plan was not amended; fix that and run plan amend again" % error
            ) from None


def completed(repo, slug, state_dir):
    """The ticket IDs the plan's ledger marks COMPLETE."""
    ledger, _ = state_paths(repo, slug, state_dir)
    found = ledgers.snapshot(ledger)  # a read: it never waits for the driver
    statuses = (found or {}).get("ticket_status") or {}
    return {
        t for t, status in statuses.items() if str(status).strip().upper().startswith("COMPLETE")
    }


def frozen_problems(old, new, done):
    """What an edit may not do to the tickets in `done`: change or drop them, or change the `when` or `then` of a
    scenario they satisfy. A scenario's `evidence` may change."""
    before, after = ({t["id"]: t for t in d.get("tickets", [])} for d in (old, new))
    scenarios = (
        {s["id"]: s for s in d.get("scenarios", []) if isinstance(s, dict)} for d in (old, new)
    )
    old_scenarios, new_scenarios = scenarios
    problems = []
    for ticket in sorted(done & before.keys()):
        if ticket not in after:
            problems.append(f"{ticket} is COMPLETE and the edit drops it")
        elif after[ticket] != before[ticket]:
            problems.append(f"{ticket} is COMPLETE and the edit changes it")
        for sid in before[ticket]["satisfies"]:
            was, now = old_scenarios.get(sid), new_scenarios.get(sid)
            if was and (now is None or (was["when"], was["then"]) != (now["when"], now["then"])):
                problems.append(
                    f"{sid} is satisfied by COMPLETE {ticket} and the edit changes its when or then"
                )
    return problems


def amend_locked(path, repo, model, state_dir, reason, runtime_id):
    previous = model.approval
    way_out = f"run agent-orchestrator plan approve {path}"
    snap = snapshot_path(previous, state_dir)
    if not snap.is_file():
        raise PlanError(f"approval: snapshot of the approved plan missing at {snap} — {way_out}")
    old, errors = read_yaml(snap)
    old_bytes, old_files = snap.read_bytes(), snapshot_files(snap.parent, path)
    if (
        errors
        or not isinstance(old, dict)
        or digest(identity(repo), previous.base, old_bytes, old_files) != previous.digest
    ):
        raise PlanError(
            f"approval: the approval block of {path} does not match its snapshot {snap} — {way_out}"
        )
    data, _ = read_yaml(path)
    problems = frozen_problems(old, data, completed(repo, model.slug, state_dir))
    if problems:
        raise PlanError(
            "amend: "
            + "; ".join(problems)
            + " — a finished ticket is frozen: restore it and add a new ticket for the change"
        )
    plan_bytes = canonical(data)
    files = approved_files(path, data)
    base_sha = git(repo, "rev-parse", "--verify", previous.base + "^{commit}")
    sha = digest(identity(repo), base_sha, plan_bytes, files)
    if sha == previous.digest:
        raise PlanError(
            f"amend: {path} holds the approved contract, nothing to amend — edit the plan, then run plan amend again"
        )
    carried = carried_baselines(path, repo, old, old_files, model, files, previous, sha, state_dir)
    runs = baseline(path, model, repo, base_sha, sha, files, carried)
    if runs:
        ledger, receipts = state_paths(repo, model.slug, state_dir)
        for run, calibration in runs:
            runner.persist(run, receipts, calibration)
        runner.record_receipts(ledger, [run for run, _ in runs])
    email, at = git_email(repo), datetime.now(timezone.utc).isoformat()
    approval = {"by": email, "at": at, "base": base_sha, "digest": sha}
    was = {t["id"]: t for t in old.get("tickets", [])}
    entry = {
        "prev_digest": previous.digest,
        "digest": sha,
        "at": at,
        "by": f"{email} ({runtime_id})" if runtime_id else email,
        "reason": reason.strip(),
        "tickets": [t["id"] for t in data.get("tickets", []) if was.get(t["id"]) != t],
    }
    text = replaced_text(
        path.read_bytes().decode("utf-8"),
        "amendments",
        list(data.get("amendments") or []) + [entry],
    )
    publish(path, replaced_text(text, "approval", approval), plan_bytes, files, sha, state_dir)
    return {"approval": approval, "amendment": entry}


PLAN_SUFFIXES = (".plan.yaml", ".plan.md")


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
        (p for p in directory.iterdir() if p.is_file() and p.name.endswith(PLAN_SUFFIXES)),
        key=lambda p: p.name,
    )


def slug_of(path):
    """The key a plan's ledger lives under: a YAML plan's `slug:` field, else the file name without its suffixes.

    A Markdown plan, or a YAML plan whose file cannot be read, keeps the file-derived slug: a listing never fails on
    the plan it lists."""
    derived = path.name.removesuffix(".plan.yaml").removesuffix(".plan.md")
    if path.name == "plan.yaml":
        derived = path.parent.name  # a folder plan is named by its folder
    elif not path.name.endswith(".plan.yaml"):
        return derived
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
    return sorted(p / "plan.yaml" for p in directory.iterdir() if (p / "plan.yaml").is_file())


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
        dirs = sorted({str(Path(f).parent) for f in tracked if f.endswith(PLAN_SUFFIXES)})
        # A repo that writes its config has named its plans dir, so plan files elsewhere (test
        # fixtures) cannot make the listing ambiguous. The built-in default names nothing.
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
            "format": "markdown" if p.name.endswith(".plan.md") else "yaml",
            "slug": slug_of(p),
        }
        for p in sorted(files)
    ]
