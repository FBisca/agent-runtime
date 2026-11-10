"""The plan/1 contract and its local approval snapshots.

Digest inputs use eight-byte big-endian length prefixes. The plan input is
safe_dump(sort_keys=True) after dropping approval: comments, key order and YAML
presentation are preserved on disk but do not affect the contract digest.
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
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

DEFAULT_STATE = "~/.local/state/orchestrate"
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
    timeout: Annotated[int, Field(gt=0)] = 600
    env: list[Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]] = Field(
        default_factory=list
    )
    negative: RepoPath | None = None
    promote: bool = True

    @model_validator(mode="after")
    def evidence_kind(self):
        if bool(self.human) == bool(self.check):
            raise ValueError("choose exactly one of human or check + baseline")
        if self.check and self.baseline is None:
            raise ValueError("check requires baseline: pass or fail")
        if self.human and self.model_fields_set - {"human"}:
            raise ValueError("human evidence may only contain human; remove check options")
        return self

    @field_validator("place", "negative")
    @classmethod
    def paths(cls, value):
        return literal_path(value) if value is not None else value

    @field_validator("oracle")
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


class Plan(Mapping):
    schema_version: Literal["plan/1"] = Field(alias="schema")
    slug: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$")]
    goal: Text
    spec: RepoPath
    outcome: Annotated[list[ScenarioId], Field(min_length=1)]
    scenarios: Annotated[list[Scenario], Field(min_length=1)]
    tickets: Annotated[list[Ticket], Field(min_length=1)]
    approval: Approval | None = None

    @field_validator("spec")
    @classmethod
    def spec_path(cls, value):
        return literal_path(value)

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
        else Path(git(Path(path).parent, "rev-parse", "--show-toplevel")).resolve()
    )


def identity(repo):
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    return str((repo / common).resolve())


def semantic_problems(data, path, repo):
    """Inspect independent fields even when other fields fail Pydantic validation."""
    problems = []
    if not isinstance(data, dict):
        return problems

    def defect(loc, why, fix):
        problems.append(f"{loc}: {why} — {fix}")

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
    for i, scenario in enumerate(scenarios):
        ev = scenario.get("evidence") if isinstance(scenario, dict) else None
        if not isinstance(ev, dict):
            continue
        prefix = f"scenarios[{i}].evidence"
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
                if (
                    not placed.resolve().is_relative_to(repo)
                    or placed.exists()
                    or placed.is_symlink()
                ):
                    defect(
                        prefix + ".place",
                        f"placed oracle {placed} already exists or escapes repo",
                        "choose an unused location inside the repo",
                    )
    return problems


def canonical(data):
    return yaml.safe_dump(
        {k: v for k, v in data.items() if k != "approval"}, sort_keys=True
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
    old_files = payloads(snap.parent / "oracle")
    live_data, _ = read_yaml(path)
    live_files = payloads(oracle_dir(path))
    changed = []
    if canonical(live_data) != old_plan:
        changed.append(str(path))
    changed += [
        str(oracle_dir(path) / n)
        for n in sorted(old_files.keys() | live_files.keys())
        if old_files.get(n) != live_files.get(n)
    ]
    if digest(identity(repo), model.approval.base, old_plan, old_files) != model.approval.digest:
        changed.append(f"repo identity/base or snapshot {snap.parent}")
    if changed:
        raise PlanError("approval: changed " + ", ".join(changed) + " — " + way_out)
    # The driver consumes the snapshot, after checking the authored file against it.
    return Plan.model_validate(snap_data).model_copy(update={"approval": model.approval})


def load(path, *, repo=None, state_dir=DEFAULT_STATE, require_approval=False, verify=True):
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
    if model.approval and verify:
        return verify_snapshot(path, model, repo, state_dir)
    if require_approval and model.approval is None:
        raise PlanError(
            f"approval: {path} is unapproved — run agent-orchestrator plan approve {path}"
        )
    return model


def approved_text(text, approval):
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    existing = next(((k, v) for k, v in node.value if k.value == "approval"), None)
    if node.flow_style:
        entry = yaml.safe_dump(
            {"approval": approval}, default_flow_style=True, width=10000
        ).strip()[1:-1]
        if existing:
            key, value = existing
            return text[: key.start_mark.index] + entry + text[value.end_mark.index :]
        end = node.end_mark.index - 1
        return text[:end] + (", " if node.value else "") + entry + text[end:]
    eol = "\r\n" if "\r\n" in text else "\n"
    block = yaml.safe_dump({"approval": approval}, sort_keys=False).replace("\n", eol)
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


def approve(path, *, repo=None, state_dir=DEFAULT_STATE, base="origin/main"):
    path = Path(path).resolve()
    repo = repository(path, repo)
    load(path, repo=repo, verify=False)
    data, _ = read_yaml(path)
    plan_bytes = canonical(data)
    files = payloads(oracle_dir(path))
    base_sha = git(repo, "rev-parse", "--verify", base + "^{commit}")
    sha = digest(identity(repo), base_sha, plan_bytes, files)
    email = subprocess.run(
        ["git", "-C", str(repo), "config", "user.email"], capture_output=True, text=True, timeout=15
    ).stdout.strip()
    approval = {
        "by": email or os.environ.get("USER") or "unknown",
        "at": datetime.now(timezone.utc).isoformat(),
        "base": base_sha,
        "digest": sha,
    }
    # Only the top-level approval span is replaced; the author's comments survive.
    text = approved_text(path.read_bytes().decode("utf-8"), approval)
    approvals = Path(state_dir).expanduser() / "approvals"
    approvals.mkdir(parents=True, exist_ok=True)
    target = approvals / sha
    snap = target / "plan.yaml"
    if (
        not snap.is_file()
        or snap.read_bytes() != plan_bytes
        or payloads(target / "oracle") != files
    ):
        temporary = Path(tempfile.mkdtemp(prefix=".approval-", dir=approvals))
        try:
            (temporary / "plan.yaml").write_bytes(plan_bytes)
            for name, content in files.items():
                dest = temporary / "oracle" / name
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
    return approval


PLAN_SUFFIXES = (".plan.yaml", ".plan.md")


def plan_entries(directory):
    """The plan files directly in `directory`; nothing is created when it does not exist."""
    if not directory.is_dir():
        return []
    return sorted(
        (p for p in directory.iterdir() if p.is_file() and p.name.endswith(PLAN_SUFFIXES)),
        key=lambda p: p.name,
    )


def discover(repo=None, explicit=None):
    """Plans for the Lead, the skills and the panel: the explicit plan (or the plan directory it
    names), else the one directory of the repository's tracked plan files.

    Several directories refuse, naming each and the explicit-path way out; none is an empty list."""
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        if not path.exists():
            raise PlanError(f"plans: {path} does not exist — pass an existing plan or directory")
        directory = path if path.is_dir() else path.parent
        files = set(plan_entries(directory))
        if path.is_file():
            files.add(path)
    else:
        root = repository(Path.cwd(), repo)
        tracked = git(root, "ls-files").splitlines()
        dirs = sorted({str(Path(f).parent) for f in tracked if f.endswith(PLAN_SUFFIXES)})
        if len(dirs) > 1:
            raise PlanError(
                "plans: several plan directories in "
                + str(root)
                + ": "
                + ", ".join(dirs)
                + " — pass one plan path to choose: agent-orchestrator plans list --json <plan>"
            )
        files = set(plan_entries(root / dirs[0])) if dirs else set()
    return [
        {
            "path": str(p),
            "format": "yaml" if p.name.endswith(".plan.yaml") else "markdown",
            "slug": p.name.removesuffix(".plan.yaml").removesuffix(".plan.md"),
        }
        for p in sorted(files)
    ]
