"""The rules a run is routed by: the route config it loads and the class and step routes it looks up.

A run's route config (a JSON profile: `--routes-file`) says which backend, model and effort each class and
each gate step runs on, and the driver reads it rather than any model table. A run that resumes keeps the
routes its ledger recorded. `reroute` rewrites that record while the run runs: the driver adopts it at its next
round (`adopt_reroute`), and a packet routed before it is routed again when a fresh agent is spawned for it
(`current_route`).
"""

from typing import Any

from .. import ledger as ledgers
from .. import narrate, routes
from ..plan import PlanError
from .records import ROUTE, routed


class Routes:
    """The routing, mixed into the Driver: what runs where.

    A member reads the route config the run holds and answers the class or step route a spawn asks for.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    step_routes: Any
    added_steps: Any
    route_source: Any
    route_revision = (
        0  # the reroute revision of the routes this run holds; a run never rerouted holds 0
    )

    def gate_cli(self):
        """The backend the review gates spawn on: the brain backend when one was named, the worker backend
        otherwise."""
        return getattr(self, "brain_cli", None) or self.args.cli

    def recorded_routes(self):
        """The routes an existing run's ledger recorded, or None for a run that has not started.

        A run that started and has no complete record cannot be recovered from whatever policy is there now:
        it stops here, before any worker, unless --routes-file names the config that migrates it."""
        data = ledgers.snapshot(self.ledger_path)
        if data is None or not ("lead" in data or "routes" in data):
            return None
        pinned, why = routes.read_record(data.get("routes"))
        if pinned is None:
            if getattr(self.args, "routes_file", None):
                return None  # the explicit config migrates the run and records the complete routes
            raise PlanError(
                "cannot resume %s: %s, and the policy now in force may not be what it ran on. Pass "
                "--routes-file with the typed route config to migrate it; the complete resolved routes "
                "are then recorded in the ledger before the run resumes" % (self.slug, why)
            )
        return pinned

    def pin_routes(self, pinned):
        """The recorded routes answer the run; an explicit argument that says otherwise stops it.

        An argument that matches the record is accepted and one the command line omits is not a conflict. The
        ledger is not written until the run is clear to resume."""
        args = self.args
        chosen, chosen_steps = _overrides(args)
        via = {route.klass: "--route" for route in chosen}
        via.update({route.step: "--step" for route in chosen_steps})
        classes = {route.klass: route for route in chosen}
        steps = {route.step: route for route in chosen_steps}
        classes, steps, via, dropped = _explicit_routes(
            args, pinned, chosen, chosen_steps, classes, steps, via
        )
        brain = self.brain_cli or pinned.brain_cli
        added = _gate_steps(pinned, steps, brain)
        found = routes.conflicts(
            pinned, classes, steps, cli=args.cli, brain_cli=self.brain_cli, via=via
        )
        found += [
            "--routes-file: step %s is recorded as %s, the file names no such step"
            % (name, routes.describe(pinned.steps[name]))
            for name in dropped
        ]
        if found:
            raise PlanError(
                "cannot resume %s: the arguments conflict with the routes its ledger recorded (%s). Drop "
                "the conflicting argument to keep the recorded routes, or change them with `agent-orchestrator "
                "reroute %s --route|--step NAME=BACKEND/MODEL[@EFFORT] --reason TEXT`; the ledger is unchanged"
                % (self.slug, "; ".join(found), self.plan_path)
            )
        args.cli = args.cli or pinned.cli
        self.brain_cli = self.brain_cli or pinned.brain_cli
        self.routes, self.step_routes = dict(pinned.classes), dict(pinned.steps) | added
        self.route_revision = pinned.revision
        self.added_steps = added
        self.routes_file, self.route_source = pinned.path, pinned.source
        self._brain_gates_refused()

    def _brain_gates_refused(self):
        """A record whose medium route is not the brain backend cannot spawn its gates, and the refusal names why."""
        if not self.brain_cli:
            return
        wanted = [s for s in ("reviewer",) if s not in self.step_routes]
        medium = self.routes["medium"]
        if wanted and medium.backend != self.brain_cli:
            raise PlanError(
                "cannot resume %s: the recorded medium route runs on %s, not on the brain backend %s the "
                "%s gate(s) would spawn on. Pass --step %s=%s/MODEL[@EFFORT] for them, or --routes-file "
                "with a medium route on %s to migrate"
                % (
                    self.slug,
                    medium.backend,
                    self.brain_cli,
                    "/".join(wanted),
                    wanted[0],
                    self.brain_cli,
                    self.brain_cli,
                )
            )

    def load_routes(self):
        """The class -> route map this run uses, from the typed route config the run selected. An invalid route
        config stops the run here, before any worker; it never falls back to another file.

        The step routes come with it: the config's own `steps` list, then the command line's overrides,
        which replace the class or the step they name."""
        try:
            config = routes.load(self.routes_file)
            resolved = routes.resolve(config)
            steps = {step.step: step for step in config.steps}
        except routes.RouteError as error:
            raise PlanError("invalid route config %s: %s" % (self.routes_file, error)) from None
        try:
            resolved = routes.apply_overrides(
                resolved, routes.overrides(getattr(self.args, "route", None))
            )
        except routes.RouteError as error:
            raise PlanError("invalid route override: %s" % error) from None
        try:
            steps = routes.apply_step_overrides(
                steps, routes.step_overrides(getattr(self.args, "step", None))
            )
        except routes.RouteError as error:
            raise PlanError("invalid step override: %s" % error) from None
        return resolved, steps

    def require_gate_routes(self):
        """A brain backend needs a route for each gate it spawns: a step route, or a medium route on that backend.

        A run that cannot route its gates stops before any worker, with the arguments that would route them."""
        brain = self.brain_cli
        if not brain:
            return
        wanted = [step for step in ("reviewer",) if step not in self.step_routes]
        medium = self.routes["medium"]
        if wanted and medium.backend != brain:
            raise PlanError(
                "the medium route runs on %s, not on the brain backend %s the %s gate(s) would spawn on. "
                "Pass --step %s=%s/MODEL[@EFFORT] for them, or give the route config a medium route on %s"
                % (
                    medium.backend,
                    brain,
                    "/".join(wanted),
                    wanted[0],
                    brain,
                    brain,
                )
            )

    def step_route(self, step):
        """The step's own route, when the run names one."""
        return (getattr(self, "step_routes", None) or {}).get(step)

    def step_backend(self, step, default):
        """The backend one step spawns on: its own route, or the run's answer."""
        route = self.step_route(step)
        return route.backend if route else default

    def step_model(self, step, default):
        """The model one step spawns on: its own route, or the run's answer."""
        route = self.step_route(step)
        return route.model if route else default

    def step_effort(self, step, default):
        """The effort one step spawns on: its own route, or the run's answer."""
        route = self.step_route(step)
        return (route.effort or default) if route else default

    def gate_model(self, klass):
        """The model a review gate spawns on: the brain's medium model when a brain backend was named.

        A typed route config answers the brain model: its medium class must run on the brain backend, and a run
        whose medium route does not stopped at start unless its gates have step routes."""
        brain = getattr(self, "brain_cli", None)
        if not brain:
            return self.routes[klass].model
        route = self.routes.get("medium")
        if route is None or route.backend != brain:
            raise PlanError(
                "the route config has no medium route on the brain backend '%s'" % brain
            )
        return route.model

    def gate_step(self, step, klass):
        """The backend and model one gate spawns on: the step's own route, or the brain's answer.

        A step route wins over the brain: the reviewer may sit on another backend, and on another
        model, than the implementer."""
        route = self.step_route(step)
        if route:
            return route.backend, route.model
        return self.gate_cli(), self.gate_model(klass)

    def gate_label(self, step, klass):
        """The class a review gate runs on, for the event line: the step's own name when a step route names it."""
        if self.step_route(step):
            return step
        return "medium" if getattr(self, "brain_cli", None) else klass

    def implementer_route(self, klass, effort):
        """(backend, model, effort) an implementer of `klass` spawns on now: the implementer step's route, else the
        class route; `effort` answers when neither names one."""
        route = self.routes[klass]
        return (
            self.step_backend("implementer", route.backend),
            self.step_model("implementer", route.model),
            self.step_effort("implementer", route.effort or effort),
        )

    def route_stamp(self):
        """The packet field that says which reroute revision routed it: none for a run never rerouted."""
        return {"route_revision": self.route_revision} if self.route_revision else {}

    def current_route(self, packet):
        """The packet fields that put a packet routed before the last reroute on the routes the run holds now, or
        nothing when it is current. A fresh agent is spawned from them; a running one keeps the route it has."""
        if not routed(packet) or packet.get("route_revision", 0) == self.route_revision:
            return {}
        backend, model, effort = self.implementer_route(packet["class"], packet["effort"])
        return {"backend": backend, "model": model, "effort": effort, **self.route_stamp()}

    def class_up(self, packet):
        """The packet fields that move a retry one model class up, on the routes the run holds now; at the top class
        the effort goes up a notch."""
        classes, efforts = ROUTE["class"], ROUTE["effort"]
        current = self.current_route(packet)
        packet = dict(packet, **current)
        at = classes.index(packet["class"])
        if at + 1 < len(classes):
            klass = classes[at + 1]
            backend, model, _ = self.implementer_route(klass, packet["effort"])
            return dict(current, **{"class": klass, "backend": backend, "model": model})
        return dict(
            current, effort=efforts[min(efforts.index(packet["effort"]) + 1, len(efforts) - 1)]
        )

    def adopt_reroute(self):
        """A reroute the ledger recorded since this run read its routes: every agent spawned from now on runs on
        it, and an agent already running keeps its route. 1 when one was adopted, else 0."""
        record = (ledgers.snapshot(self.ledger_path) or {}).get("routes")
        revision = routes.revision_of(record)
        if revision == self.route_revision:
            return 0
        pinned, why = routes.read_record(record)
        self.route_revision = revision  # an unreadable record is said once, not every round
        if pinned is None:
            self.log("error", gate="reroute", error=why)
            narrate.say(
                "warn", None, "cannot read the rerouted routes (%s); keeping the ones in use" % why
            )
            return 0
        changed = routes.changes(self.routes, self.step_routes, pinned.classes, pinned.steps)
        self.routes, self.step_routes = dict(pinned.classes), dict(pinned.steps)
        self.log("reroute-adopted", revision=revision, changes=changed)
        if not changed:
            return 1
        narrate.say(
            "info",
            None,
            "rerouted %s; agents already running keep theirs"
            % "; ".join(
                "the %s to %s (was %s)"
                % (
                    change.get("step") or "%s class" % change["class"],
                    change["new"],
                    change["old"] or "the default",
                )
                for change in changed
            ),
        )
        return 1


def _overrides(args):
    """(class routes, step routes) named on the command line, refused when one is not a route."""
    try:
        chosen = routes.overrides(getattr(args, "route", None))
        chosen_steps = routes.step_overrides(getattr(args, "step", None))
    except routes.RouteError as error:
        raise PlanError("invalid route override: %s" % error) from None
    return chosen, chosen_steps


def _explicit_routes(args, pinned, chosen, chosen_steps, classes, steps, via):
    """The routes an explicit `--routes-file` names, with the ledger's own steps checked against it."""
    explicit = getattr(args, "routes_file", None)
    if not explicit:
        return classes, steps, via, []
    try:
        config = routes.load(explicit)
        resolved = routes.resolve(config)
    except routes.RouteError as error:
        raise PlanError("invalid route config %s: %s" % (explicit, error)) from None
    # An explicit file is the whole routing of the run, so a step the ledger has and it lacks differs too.
    via = {name: "--routes-file" for name in (*routes.CLASSES, *routes.STEPS)} | via
    classes = routes.apply_overrides(resolved, chosen)
    steps = routes.apply_step_overrides({s.step: s for s in config.steps}, chosen_steps)
    return classes, steps, via, sorted(set(pinned.steps) - set(steps))


def _gate_steps(pinned, steps, brain):
    """The gate steps the command line adds, for a record whose own routes cannot reach its gates."""
    gates = (
        [s for s in ("reviewer",) if s not in pinned.steps]
        if brain and pinned.classes["medium"].backend != brain
        else []
    )
    return {name: steps.pop(name) for name in gates if name in steps}
