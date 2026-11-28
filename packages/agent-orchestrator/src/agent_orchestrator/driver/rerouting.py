"""`reroute <plan> --route|--step NAME=BACKEND/MODEL[@EFFORT] --reason TEXT`: change a run's routes while it runs.

A run pins its routes in its ledger, and a restart keeps them: an argument on `run` that differs is refused. This
command is the one way to change them. It rewrites the ledger's route record under the ledger's lock, moves the
record's `revision` on and logs one `reroute` event per class or step it changed. A live driver adopts the new record
at its next round, for every agent it spawns after that; an agent already running keeps the route it started on. A
restart resumes on the rerouted record, because it is the pinned record now.
"""

from .. import events, routes
from ..invariants import checked_write as ledger
from ..plan import PlanError
from ..runtime_client import approval_refusal
from .state import JsonRefusal, Refused, plan_home

_USAGE = (
    "agent-orchestrator reroute <plan> (--route CLASS=BACKEND/MODEL[@EFFORT] | --step STEP=BACKEND/MODEL[@EFFORT])... "
    "--reason TEXT"
)


def reroute(args):
    """The run's route record rewritten and the change logged; a refusal is one JSON object on stderr."""
    try:
        return _rerouted(args)
    except Refused as refused:
        raise JsonRefusal(
            refused, error="reroute refused", way_out="fix the reason above and run reroute again"
        ) from None
    except PlanError as error:
        raise JsonRefusal(
            Refused(str(error), "fix the reason above and run reroute again"),
            error="reroute refused",
        ) from None


def _rerouted(args):
    refusal = approval_refusal(verb="reroute")
    if refusal:
        raise Refused(
            "reroute: %s" % refusal.split(" — ")[0],
            "changing a run's routes belongs to the Lead: a worker asks the Lead to run reroute",
        )
    chosen, chosen_steps = _named_routes(args)
    reason = " ".join((args.reason or "").split())
    if not reason:
        raise Refused("reroute needs a --reason saying why the routes change", "run " + _USAGE)
    slug, ledger_path, run_dir = plan_home(args)
    if not ledger_path.is_file():
        raise Refused(
            "%s has no run yet, so it has no routes to change" % slug,
            "start it with `agent-orchestrator run %s`, which takes --route, --step and --routes-file"
            % args.plan,
        )
    with ledger(ledger_path) as data:
        record = data.get("routes")
        pinned, why = routes.read_record(record)
        if pinned is None:
            raise Refused(
                "%s: %s" % (slug, why),
                "restart the run with --routes-file to record its complete routes, then reroute",
            )
        classes = routes.apply_overrides(pinned.classes, chosen)
        steps = routes.apply_step_overrides(pinned.steps, chosen_steps)
        _require_gate_routes(pinned, classes, steps)
        changed = routes.changes(pinned.classes, pinned.steps, classes, steps)
        revision = pinned.revision + 1 if changed else pinned.revision
        if changed:
            fresh = routes.record(classes, pinned.source, pinned.path, steps)
            data["routes"] = dict(
                record, classes=fresh["classes"], steps=fresh["steps"], revision=revision
            )
    if changed:
        run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for change in changed:
            events.append(
                run_dir / "events.jsonl", "reroute", reason=reason, revision=revision, **change
            )
    return {"plan": slug, "revision": revision, "changes": changed, "reason": reason}


def _named_routes(args):
    """(class routes, step routes) the command line names, validated as `run` validates them."""
    try:
        chosen = routes.overrides(args.route)
        chosen_steps = routes.step_overrides(args.step)
    except routes.RouteError as error:
        raise Refused(
            str(error), "name a route the driver can run, and run reroute again"
        ) from None
    if not chosen and not chosen_steps:
        raise Refused("reroute names no route to change", "pass --route or --step: " + _USAGE)
    return chosen, chosen_steps


def _require_gate_routes(pinned, classes, steps):
    """A brain run reviews on its medium class: a reroute that moves it off the brain needs a reviewer route."""
    brain = pinned.brain_cli
    if brain and "reviewer" not in steps and classes["medium"].backend != brain:
        raise Refused(
            "the medium route would run on %s, not on the brain backend %s the reviewer spawns on"
            % (classes["medium"].backend, brain),
            "add --step reviewer=%s/MODEL[@EFFORT], or keep the medium route on %s"
            % (brain, brain),
        )
