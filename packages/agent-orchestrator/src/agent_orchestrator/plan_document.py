"""The plan/2 YAML document: what a plan, its requirement changes, its tickets and an approval are.

The models refuse an unknown key and a wrong type; `read_yaml` adds the duplicate-key and location
diagnostics the models cannot see, both as a `PlanError` the CLI exits 2 on before dispatch. Nothing
here reads the repository or runs a check. A plan changes its spec's requirements: it adds, modifies or
removes them by title, and each scenario sits under the requirement it proves. A scenario is its WHEN and
THEN; the ticket that satisfies it provides its evidence, as the tests its report's `covers` maps it to.
"""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

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


class Scenario(Mapping):
    id: ScenarioId
    name: Text | None = Field(
        default=None, description="The scenario's heading in the spec; its WHEN when absent"
    )
    when: Text
    then: Text


class Requirement(Mapping):
    """An added or modified requirement, stated whole: the archive writes it into the spec as it stands."""

    title: Text
    shall: Annotated[str, Field(min_length=1, pattern=r"\bSHALL\b")]
    scenarios: Annotated[list[Scenario], Field(min_length=1, max_length=3)]


class Removal(Mapping):
    title: Text
    why: Text


class Changes(Mapping):
    added: list[Requirement] = Field(default_factory=list)
    modified: list[Requirement] = Field(default_factory=list)
    removed: list[Removal] = Field(default_factory=list)


def scenario_entries(data):
    """(YAML path, mapping) of each scenario of a plan mapping, in plan order: each added, then each modified
    requirement's. Rows that are not mappings are skipped; the model refuses them with their own path."""
    changes = data.get("requirements") if isinstance(data, dict) else None
    changes = changes if isinstance(changes, dict) else {}
    entries = []
    for kind in ("added", "modified"):
        items = changes.get(kind)
        for i, item in enumerate(items if isinstance(items, list) else []):
            rows = item.get("scenarios") if isinstance(item, dict) else None
            for j, row in enumerate(rows if isinstance(rows, list) else []):
                if isinstance(row, dict):
                    entries.append((f"requirements.{kind}[{i}].scenarios[{j}]", row))
    return entries


def scenario_rows(data):
    """The scenario mappings of a plan mapping, in plan order."""
    return [row for _, row in scenario_entries(data)]


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
    reason: Text | None = None


class Plan(Mapping):
    schema_version: Literal["plan/2"] = Field(alias="schema")
    slug: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$")]
    goal: Text
    spec: RepoPath | None = Field(
        default=None,
        description="The living spec the archive writes; required unless the plan is a folder plan that discards",
    )
    archive: Literal["promote", "discard"] | None = Field(
        default=None,
        description="Folder plans: what archive does with the plan folder; the repo config's archive.mode when "
        "absent",
    )
    requirements: Changes = Field(
        description="The requirements this plan adds to, modifies in or removes from its spec, by title"
    )
    tickets: Annotated[list[Ticket], Field(min_length=1)]
    approval: Approval | None = None

    @property
    def scenarios(self):
        """Every scenario of the plan, in plan order: the added requirements', then the modified ones'."""
        return [
            s for r in self.requirements.added + self.requirements.modified for s in r.scenarios
        ]

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
                return None, ["plan: empty YAML — write a plan/2 mapping"]
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
