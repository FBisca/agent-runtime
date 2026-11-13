"""The typed route schema: which backend, model and effort each model class runs on.

A new run is routed by a route config: a versioned document that says what each class runs on. The driver reads
no model table (not the "Model classes" table of `~/.claude/CLAUDE.md`, not its own): a run with no typed config
stops before any worker and says how to supply one. The schema is strict on purpose: an unknown backend or an
unknown class is an error, never a silent fall back to another billable model.

The schema is a plain dict, so the file format stays a separate question: JSON is read here, YAML when
PyYAML is installed. The rules below are the same either way.

A new run finds its config in this order: the file it names (`--routes-file`), then the repository's own policy
(`.orchestrate/routes.json`), then the user's profile for the run's backend (`~/.config/orchestrate/routes.json`
for Claude, `routes-codex.json` for Codex, `routes-pi.json` for Pi). The first file found is the only one read: an
invalid one fails the run, it never falls through to the next. A command line may override one class or step
outright (`--route small=codex/gpt-s@high`); the override is validated like a config route and wins over every
file. A run that resumes keeps the routes its ledger recorded instead (see `read_record`).
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = 1
SUPPORTED_VERSIONS = (1,)
CLASSES = ("small", "medium", "large")
BACKENDS = (
    "claude",
    "codex",
    "pi",
)  # the worker backends; each of them runs a worker, so each of them may be routed to
EFFORTS = ("low", "medium", "high", "xhigh", "max")
STEPS = ("implementer", "reviewer", "gaps")
# The archive has no agent, so it has no route: naming it is refused with the way out.
ARCHIVE_ROUTE_REFUSAL = (
    "the route names the step 'archive': the archive has no agent; remove the `archive` route"
)
ROUTE_FIELDS = ("class", "backend", "model", "effort")
# What a route field may name, and what to call it when it names something else.
ALLOWED = {"class": CLASSES, "backend": BACKENDS, "effort": EFFORTS, "step": STEPS}
NOUNS = {"class": "model class", "backend": "worker backend", "effort": "effort", "step": "step"}
CONFIG_SUFFIXES = (".json", ".yaml", ".yml")
REPO_POLICY = ".orchestrate"  # the repository's own policy directory
USER_POLICY = "~/.config/orchestrate"  # the user's default directory
# The user's profile per worker backend: the file stem under USER_POLICY. Claude's is the plain `routes`.
USER_PROFILES = {"claude": "routes", "codex": "routes-codex", "pi": "routes-pi"}


class RouteError(ValueError):
    """The route config is missing, malformed, or names something the driver cannot run."""


@dataclass(frozen=True)
class Route:
    """One model class on one backend. The effort is None when the size/difficulty matrix decides it."""

    klass: str
    backend: str
    model: str
    effort: str | None = None


@dataclass(frozen=True)
class StepRoute:
    """One step of a run on one backend: the step's own backend, model and effort.

    The effort is None when the step's own default answers it (the matrix for a worker, the
    gate's constant for a gate)."""

    step: str
    backend: str
    model: str
    effort: str | None = None


@dataclass(frozen=True)
class RouteConfig:
    """A validated route config: its version, the routes it names and the steps it names."""

    version: int
    routes: tuple
    steps: tuple = ()


def text(value):
    return isinstance(value, str) and bool(value.strip())


class RouteEntry(BaseModel):
    """One route of a config: the model class, the backend it runs on, the model and the effort."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    klass: Literal["small", "medium", "large"] = Field(alias="class")
    backend: Literal["claude", "codex", "pi"]
    model: str = Field(min_length=1)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


class StepEntry(BaseModel):
    """One step of a config: the step, the backend it runs on, the model and the effort.

    A step is one spawn site of a run: the implementer, the reviewer or the gaps reviewer. A step route is the run's answer for that step, whatever the class routes say."""

    model_config = ConfigDict(extra="forbid")

    step: Literal["implementer", "reviewer", "gaps"]
    backend: Literal["claude", "codex", "pi"]
    model: str = Field(min_length=1)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


class RouteDocument(BaseModel):
    """The typed route config: its version, the routes it names and the steps it names."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    routes: list[RouteEntry] = Field(min_length=1)
    steps: list[StepEntry] = Field(default_factory=list)


def _problem(error, position=None, kind="route"):
    """One deterministic message from a validation failure: the route, the field, the value it holds.

    A refusal names the reason and the way out: which route, which field, what it named, and what
    that field may name instead.
    """
    first = error.errors()[0]
    location = first.get("loc", ())
    field = str(location[-1]) if location else "route"
    kind_name = first.get("type", "")
    value = first.get("input")
    if position is None:
        indexes = [part for part in location if isinstance(part, int)]
        position = indexes[0] + 1 if indexes else None
    where = "%s #%d" % (kind, position) if position is not None else "the route config"
    if kind_name == "missing":
        if field == "routes":
            return "the route config needs a non-empty 'routes' list"
        return "%s needs a %s" % (where, field)
    if kind_name == "extra_forbidden":
        return "%s has unknown field(s): %s" % (where, field)
    if field in ALLOWED:
        return "%s names %r, which is not a %s (%s)" % (
            where,
            value,
            NOUNS[field],
            ", ".join(ALLOWED[field]),
        )
    message = str(first.get("msg", "is invalid"))
    return "%s %s %s" % (
        where,
        field,
        message[0].lower() + message[1:] if message else "is invalid",
    )


def _route_problem(error, position):
    """One deterministic message from a validation failure: the route, the field, the value it holds."""
    return _problem(error, position, "route")


def _step_problem(error, position):
    """One deterministic message from a validation failure: the step, the field, the value it holds."""
    return _problem(error, position, "step")


def validate_route(entry, position):
    """One route of a config, validated. Every failure names the route and the field."""
    try:
        route = RouteEntry.model_validate(entry)
    except ValidationError as error:
        raise RouteError(_route_problem(error, position)) from None
    return Route(
        klass=route.klass, backend=route.backend, model=route.model.strip(), effort=route.effort
    )


def validate_step(entry, position):
    """One step of a config, validated. Every failure names the step and the field."""
    try:
        step = StepEntry.model_validate(entry)
    except ValidationError as error:
        raise RouteError(_step_problem(error, position)) from None
    return StepRoute(
        step=step.step, backend=step.backend, model=step.model.strip(), effort=step.effort
    )


def validate(data):
    """The validated route config of a mapping, or a RouteError that says what is wrong with it."""
    if not isinstance(data, dict):
        raise RouteError("the route config must be an object")
    named = [e.get("step") for e in data.get("steps") or [] if isinstance(e, dict)]
    if "archive" in named:
        raise RouteError(ARCHIVE_ROUTE_REFUSAL)
    try:
        document = RouteDocument.model_validate(data)
    except ValidationError as error:
        raise RouteError(_problem(error)) from None
    version = document.version
    routes, seen = [], {}
    for position, entry in enumerate(document.routes, 1):
        route = validate_route(entry.model_dump(by_alias=True), position)
        if route.klass in seen:
            raise RouteError(
                "route #%d repeats the class '%s' (route #%d already names it)"
                % (position, route.klass, seen[route.klass])
            )
        seen[route.klass] = position
        routes.append(route)
    steps, seen_steps = [], {}
    for position, entry in enumerate(document.steps, 1):
        step = validate_step(entry.model_dump(), position)
        if step.step in seen_steps:
            raise RouteError(
                "step #%d repeats the step '%s' (step #%d already names it)"
                % (position, step.step, seen_steps[step.step])
            )
        seen_steps[step.step] = position
        steps.append(step)
    return RouteConfig(version=version, routes=tuple(routes), steps=tuple(steps))


def load_yaml(raw, path):
    """The mapping a YAML route config holds. YAML needs PyYAML; without it the error says so."""
    try:
        import yaml
    except ImportError:
        raise RouteError(
            "the route config %s is YAML and PyYAML is not installed: install it or use JSON" % path
        ) from None
    try:
        return yaml.safe_load(raw)
    except Exception as error:  # PyYAML raises its own error types
        raise RouteError("the route config %s is not valid YAML: %s" % (path, error)) from None


def load(path):
    """The validated route config of a file. JSON is read; YAML is read when PyYAML is installed."""
    file = Path(path).expanduser()
    try:
        raw = file.read_text(encoding="utf-8")
    except OSError as error:
        raise RouteError("cannot read the route config %s: %s" % (file, error)) from None
    if file.suffix.lower() in (".yaml", ".yml"):
        data = load_yaml(raw, file)
    else:
        try:
            data = json.loads(raw)
        except ValueError as error:
            raise RouteError("the route config %s is not valid JSON: %s" % (file, error)) from None
    return validate(data)


def config_files(directory, stem="routes"):
    """The route config files a directory may hold for one stem, in the order they are read."""
    if not directory:
        return []
    base = Path(directory).expanduser()
    return [str(base / (stem + suffix)) for suffix in CONFIG_SUFFIXES]


def user_config_dir():
    """The user's default route config directory, or nothing when there is no home to look in."""
    try:
        return str(Path(USER_POLICY).expanduser())
    except RuntimeError:
        return None


def missing_config(backend):
    """The refusal of a new run that has no typed route config: what was looked for and how to supply one."""
    profile = "%s/%s%s" % (USER_POLICY, USER_PROFILES[backend], CONFIG_SUFFIXES[0])
    return (
        "no typed route config for a new run on %s: looked for the repository policy %s/routes.json and the "
        "user profile %s. Pass --routes-file FILE, or create %s. A route file is JSON such as "
        '{"version": 1, "routes": [{"class": "small", "backend": "%s", "model": "MODEL", "effort": "low"}, '
        "...]} with a route for small, medium and large; the driver reads no model table"
        % (backend, REPO_POLICY, profile, profile, backend)
    )


def find_config(explicit=None, repo=None, home=None, backend="claude"):
    """The route config a new run uses, and where it came from.

    The order is: the file the run names, then the repository's policy, then the user's profile for the run's
    backend. A run with none of them is refused: there is no table to answer, and no default to guess."""
    if explicit:
        return str(explicit), "explicit"
    for directory, stem, source in (
        (repo and str(Path(repo) / REPO_POLICY), "routes", "repo"),
        (home, USER_PROFILES[backend], "user"),
    ):
        for path in config_files(directory, stem):
            if Path(path).exists():
                return path, source
    raise RouteError(missing_config(backend))


def _override_parts(text, what="route", noun="class"):
    """The (name, backend, model, effort) an override names; every failure names the value it named."""
    if not isinstance(text, str) or "=" not in text:
        raise RouteError(
            "a %s override reads '%s=backend/model[@effort]', got %r" % (what, noun, text)
        )
    name, _, rest = text.partition("=")
    model_part, _, effort_part = rest.partition("@")
    backend, slash, model = model_part.partition("/")
    name, backend, model = name.strip(), backend.strip(), model.strip()
    effort = effort_part.strip() or None
    if not name:
        raise RouteError("the %s override %r names no %s" % (what, text, noun))
    if not slash or not model:
        raise RouteError(
            "the %s override %r names no model; it reads '%s=backend/model[@effort]'"
            % (what, text, noun)
        )
    if backend not in BACKENDS:
        raise RouteError(
            "the %s override %r names the backend '%s', which is not a worker backend (%s)"
            % (what, text, backend, ", ".join(BACKENDS))
        )
    if effort is not None and effort not in EFFORTS:
        raise RouteError(
            "the %s override %r names the effort '%s', which is not an effort (%s)"
            % (what, text, effort, ", ".join(EFFORTS))
        )
    return name, backend, model, effort


def override(text):
    """One explicit route override, read from the command line: `class=backend/model[@effort]`.

    It is validated like a config route: an unknown class, an unknown backend or an unknown effort is an error,
    never a fall back to another billable model."""
    klass, backend, model, effort = _override_parts(text)
    if klass == "archive":
        raise RouteError("the route override %r: %s" % (text, ARCHIVE_ROUTE_REFUSAL))
    if klass not in CLASSES:
        raise RouteError(
            "the route override %r names the class '%s', which is not a model class (%s)"
            % (text, klass, ", ".join(CLASSES))
        )
    return Route(klass=klass, backend=backend, model=model, effort=effort)


def overrides(texts):
    """The route overrides a command line names, each one validated."""
    return [override(text) for text in texts or ()]


def step_override(text):
    """One explicit step override, read from the command line: `step=backend/model[@effort]`.

    A step is one spawn site of a run: `implementer`, `reviewer` or `gaps`."""
    step, backend, model, effort = _override_parts(text, "step", "step")
    if step == "archive":
        raise RouteError("the step override %r: %s" % (text, ARCHIVE_ROUTE_REFUSAL))
    if step not in STEPS:
        raise RouteError(
            "the step override %r names the step '%s', which is not a step (%s)"
            % (text, step, ", ".join(STEPS))
        )
    return StepRoute(step=step, backend=backend, model=model, effort=effort)


def step_overrides(texts):
    """The step overrides a command line names, each one validated."""
    return [step_override(text) for text in texts or ()]


def apply_overrides(resolved, chosen):
    """The resolved routes with the explicit overrides on top: an override replaces the class it names."""
    out = dict(resolved)
    for route in chosen or ():
        if route.klass not in out:
            raise RouteError(
                "the route override names the class '%s', which the run has no route for (%s)"
                % (route.klass, ", ".join(sorted(out)) or "none")
            )
        out[route.klass] = route
    return out


def apply_step_overrides(steps, chosen):
    """The step routes with the explicit overrides on top: an override replaces the step it names."""
    out = dict(steps or {})
    for step in chosen or ():
        out[step.step] = step
    return out


def record(resolved, source, path=None, steps=None, cli=None, brain_cli=None):
    """The route fields the ledger keeps: where the routes came from, what each class runs on, what each
    step runs on and the backends the gates fall back to (the run's `--cli` and `--brain-cli`)."""
    return {
        "source": source,
        "path": str(path) if path else None,
        "cli": cli,
        "brain_cli": brain_cli,
        "classes": {
            klass: {
                "class": route.klass,
                "backend": route.backend,
                "model": route.model,
                "effort": route.effort,
            }
            for klass, route in sorted(resolved.items())
        },
        "steps": {
            step: {
                "step": route.step,
                "backend": route.backend,
                "model": route.model,
                "effort": route.effort,
            }
            for step, route in sorted((steps or {}).items())
        },
    }


@dataclass(frozen=True)
class Recorded:
    """The routes a ledger recorded, read back: what a resumed run keeps whatever the policy now says."""

    source: str
    path: str | None
    classes: dict
    steps: dict
    cli: str
    brain_cli: str | None
    brain_known: bool  # the record names the brain backend (older records do not)


def read_record(record):
    """(Recorded, None) for a complete route record, (None, why) for a missing or incomplete one.

    Complete means every model class has a backend, a model and an effort field, and the steps are
    recorded; a record written before steps were recorded cannot say what its steps ran on."""
    if not isinstance(record, dict):
        return None, "the ledger has no route record"
    classes, steps = record.get("classes"), record.get("steps")
    if not isinstance(classes, dict):
        return None, "the route record has no classes"
    if not isinstance(steps, dict):
        return None, "the route record does not say what its steps run on"
    missing = [klass for klass in CLASSES if klass not in classes]
    if missing:
        return None, "the route record lacks the class(es): %s" % ", ".join(missing)
    resolved, step_routes = {}, {}
    try:
        for klass in CLASSES:
            data = classes[klass]
            if not isinstance(data, dict) or "effort" not in data or data.get("class") != klass:
                raise RouteError("the route record's class '%s' is incomplete" % klass)
            resolved[klass] = validate_route(data, CLASSES.index(klass) + 1)
        for name, data in steps.items():
            if not isinstance(data, dict) or "effort" not in data or data.get("step") != name:
                raise RouteError("the route record's step '%s' is incomplete" % name)
            step_routes[name] = validate_step(data, len(step_routes) + 1)
    except RouteError as error:
        return None, "the route record is not valid: %s" % error
    brain = record.get("brain_cli")
    cli = record.get("cli")
    if cli not in BACKENDS:
        backends = {route.backend for route in resolved.values()}
        cli = backends.pop() if len(backends) == 1 else resolved["medium"].backend
    return (
        Recorded(
            source=str(record.get("source") or "legacy"),
            path=record.get("path"),
            classes=resolved,
            steps=step_routes,
            cli=cli,
            brain_cli=brain if brain in BACKENDS else None,
            brain_known="brain_cli" in record,
        ),
        None,
    )


def describe(route):
    """A route as an operator writes it: `backend/model[@effort]`."""
    return "%s/%s%s" % (route.backend, route.model, "@" + route.effort if route.effort else "")


def conflicts(pinned, classes, steps, cli=None, brain_cli=None, via=None):
    """What an explicit command line says that the recorded routes do not: one sentence per difference.

    `classes` and `steps` are the explicit routes (class -> Route, step -> StepRoute); `cli` and `brain_cli` the
    backends the command line named, None when it named none. `via` names the argument that said each class or
    step. An argument that matches is not a conflict."""
    via = via or {}
    out = []
    for klass, route in sorted(classes.items()):
        if pinned.classes[klass] != route:
            out.append(
                "%s: class %s is recorded as %s, the argument says %s"
                % (
                    via.get(klass, "the arguments"),
                    klass,
                    describe(pinned.classes[klass]),
                    describe(route),
                )
            )
    for name, route in sorted(steps.items()):
        have = pinned.steps.get(name)
        if have != route:
            out.append(
                "%s: step %s is recorded as %s, the argument says %s"
                % (
                    via.get(name, "the arguments"),
                    name,
                    describe(have) if have else "no route",
                    describe(route),
                )
            )
    if cli is not None:
        backends = {route.backend for route in pinned.classes.values()}
        if cli != pinned.cli and backends != {cli}:
            out.append("--cli %s: the ledger records the run on %s" % (cli, pinned.cli))
    if brain_cli is not None and pinned.brain_known and brain_cli != pinned.brain_cli:
        out.append(
            "--brain-cli %s: the ledger records %s"
            % (brain_cli, pinned.brain_cli or "no brain backend")
        )
    return out


def resolve(config):
    """The class -> route map a run uses. A class the config leaves out is an error rather than a silent fall
    back to another billable model."""
    routes = {route.klass: route for route in config.routes}
    missing = [klass for klass in CLASSES if klass not in routes]
    if missing:
        raise RouteError("the route config has no route for the class(es): %s" % ", ".join(missing))
    return routes
