"""The typed route schema: which backend, model and effort each model class runs on.

The driver used to read its classes from the "Model classes" table of `~/.claude/CLAUDE.md`. That table is
still read — it is the legacy config, kept for a transition — but a run may name a route config instead: a
versioned document that says what each class runs on. The schema is strict on purpose: an unknown backend or
an unknown class is an error, never a silent fall back to another billable model.

The schema is a plain dict, so the file format stays a separate question: JSON is read here, YAML when
PyYAML is installed. The rules below are the same either way.

A run finds its config in the plan's order: the file it names, then the repository's own policy, then the user's
default. A command line may override one class outright (`--route small=codex/gpt-s@high`); the override is
validated like a config route and wins over every file. With no config at all the legacy table answers.
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
LEGACY_WARNING = (
    "legacy routing config in use: the 'Model classes' table of %s; it is deprecated and will be "
    "phased out, pass --routes-file to name the typed route config"
)


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


def legacy_warning(path):
    """The warning a legacy run gets: the old config still answers, but it is on its way out."""
    return LEGACY_WARNING % path


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


def config_files(directory):
    """The route config files a directory holds, in the order they are read."""
    if not directory:
        return []
    base = Path(directory).expanduser()
    return [str(base / ("routes" + suffix)) for suffix in CONFIG_SUFFIXES]


def user_config_dir():
    """The user's default route config directory, or nothing when there is no home to look in."""
    try:
        return str(Path(USER_POLICY).expanduser())
    except RuntimeError:
        return None


def find_config(explicit=None, repo=None, home=None):
    """The route config a run uses, and where it came from.

    The order is the plan's: the file the run names, then the repository's policy, then the user's default.
    (None, None) means no config was found and the legacy 'Model classes' table answers."""
    if explicit:
        return str(explicit), "explicit"
    for directory, source in ((repo and str(Path(repo) / REPO_POLICY), "repo"), (home, "user")):
        for path in config_files(directory):
            if Path(path).exists():
                return path, source
    return None, None


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


def record(resolved, source, path=None, steps=None):
    """The route fields the ledger keeps: where the routes came from, what each class runs on and
    what each step runs on."""
    return {
        "source": source,
        "path": str(path) if path else None,
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


def legacy(backend, models):
    """The routes the legacy 'Model classes' table answers: one model per class, the effort left to the matrix."""
    return {
        klass: Route(klass=klass, backend=backend, model=model) for klass, model in models.items()
    }


def resolve(config, backend, legacy_models):
    """The class -> route map a run uses.

    Without a config the legacy table answers, unchanged. With one the config wins, and a class it leaves out
    is an error rather than a silent fall back to another billable model."""
    if config is None:
        return legacy(backend, legacy_models)
    routes = {route.klass: route for route in config.routes}
    missing = [klass for klass in CLASSES if klass not in routes]
    if missing:
        raise RouteError("the route config has no route for the class(es): %s" % ", ".join(missing))
    return routes
