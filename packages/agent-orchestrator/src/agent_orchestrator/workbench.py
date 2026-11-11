"""The workbench's read side: `workbench plans` and `workbench plan` print what the plan loader and the ledger say.

Both commands are read-only: they take no lock, import no old ledger and create no file, so a person (or a pane)
may run them beside a live driver. Every answer is one JSON object with `schema_version`; a refusal is one too, with
an `error` that names its kind and the way out. A count, token or duration nothing measured is null, never 0.

The driver owns the ledger and the state layout, and imports this module; the helpers it owns are imported late.
"""

from pathlib import Path
from types import SimpleNamespace

from . import plan as yaml_plan
from .plan import DiscoveryError, PlanError

SCHEMA_VERSION = 1


class WorkbenchError(PlanError):
    """A refusal a reader tells apart by `kind`; `output` is the JSON object the command prints for it."""

    def __init__(self, kind, message, way_out, **extra):
        super().__init__(message)
        self.kind = kind
        self.output = {
            "schema_version": SCHEMA_VERSION,
            "error": {"kind": kind, "message": message, "way_out": way_out, **extra},
        }


def refusal(error, scope):
    """A discovery refusal as the workbench's error: its kind, the directories it names and the way out."""
    root = error.root
    if error.kind == "not_a_repo":
        return WorkbenchError(
            "not_a_repo",
            str(error),
            "run the command inside a repository, or pass --repo R",
        )
    if error.kind == "ambiguous":
        return WorkbenchError(
            "ambiguous",
            "several plan directories in %s: %s" % (root, ", ".join(error.directories)),
            "pass one plan path (agent-orchestrator workbench %s <plan>) or --repo R" % scope,
            directories=error.directories,
        )
    return WorkbenchError(
        error.kind, str(error), "pass the path of an existing plan or plan directory"
    )


def repository_of(args, path=None):
    """The repository of the command: `--repo`, else the one holding the plan (or directory) named, else the one
    holding the current directory."""
    try:
        if args.repo:
            return yaml_plan.repository_root(Path.cwd(), args.repo)
        if path is not None:
            where = Path(path).expanduser().resolve()
            return yaml_plan.repository_root(where if where.is_dir() else where.parent)
        return yaml_plan.repository_root(Path.cwd())
    except DiscoveryError as error:
        raise refusal(error, "plans") from None


def counts(tickets):
    """Ticket counts by state: `total` and `by_state`, the states sorted so the output is stable."""
    by_state = {}
    for ticket in tickets:
        by_state[ticket["state"]] = by_state.get(ticket["state"], 0) + 1
    return {"total": len(tickets), "by_state": dict(sorted(by_state.items()))}


def status_of(path, repo, args, source=None):
    """The ledger's answer for one plan, through the driver's own read-only `status` reader. `source` is the YAML plan
    model whose slug and ticket ids key the ledger: the approved one, not the edited file's."""
    from . import driver

    return driver.plan_status(
        SimpleNamespace(plan=str(path), repo=str(repo), state_dir=args.state_dir), source
    )


def approved_view(path, repo, args):
    """(revision, source model, ledger status) of a YAML plan: what the plan was approved as, and what the ledger
    says of it. Raises PlanError for a plan that does not load."""
    model = yaml_plan.load(path, repo=repo, state_dir=args.state_dir, verify=False)
    authored = yaml_plan.read_yaml(path)[0]
    revision, source = revision_of(model, authored, path, repo, args)
    return revision, source, status_of(path, repo, args, source)


def listing(entry, repo, args):
    """One plan's entry of `workbench plans`. A plan that does not load is an entry with an error and null counts, so
    one broken plan never hides the others."""
    markdown = entry["format"] == "markdown"
    shown = {
        "slug": entry["slug"],
        "format": entry["format"],
        "path": entry["path"],
        "legacy": markdown,
    }
    try:
        if markdown:
            status = status_of(entry["path"], repo, args)
        else:
            status = approved_view(Path(entry["path"]), repo, args)[2]
    except (PlanError, ValueError, KeyError, OSError) as error:
        kind = error.kind if isinstance(error, WorkbenchError) else "invalid_plan"
        shown.update(state=None, tickets=None, error={"kind": kind, "message": str(error)})
        return shown
    shown.update(slug=status["slug"], state=status["state"], tickets=counts(status["tickets"]))
    return shown


def plans(args):
    """`workbench plans [path] [--repo R]`: every plan of the repository, with its state and ticket counts."""
    repo = repository_of(args, args.path)
    try:
        found = yaml_plan.discover(repo, args.path)
    except DiscoveryError as error:
        raise refusal(error, "plans") from None
    if not found:
        raise WorkbenchError(
            "no_plans",
            "no plan files in %s" % repo,
            "pass an explicit plan path or --repo R, or write a plan with /start-planning",
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "repo": str(repo),
        "plans": [listing(entry, repo, args) for entry in found],
    }


def layers_of(tickets):
    """{ticket id: layer}: 0 for a ticket nothing blocks, else one more than its deepest blocker. The plan loader has
    already refused a cycle or a blocker that is no ticket, so the recursion ends."""
    blockers = {t.id: list(t.blocked_by) for t in tickets}
    layers = {}

    def layer(ticket_id):
        if ticket_id not in layers:
            layers[ticket_id] = 1 + max((layer(b) for b in blockers[ticket_id]), default=-1)
        return layers[ticket_id]

    for ticket_id in blockers:
        layer(ticket_id)
    return layers


def revision_of(model, authored, path, repo, args):
    """(revision object, the model the tickets come from). An approved plan is shown as approved: when the authored
    file or an oracle changed since, the approved snapshot's tickets are shown with `drift: true`."""
    approval = model.approval
    if approval is None:
        return (
            {
                "approved": False,
                "drift": False,
                "approved_digest": None,
                "current_digest": None,
                "approved_by": None,
                "approved_at": None,
            },
            model,
        )
    current = yaml_plan.digest(
        yaml_plan.identity(repo),
        approval.base,
        yaml_plan.canonical(authored),
        yaml_plan.payloads(yaml_plan.oracle_dir(path)),
    )
    drift = current != approval.digest
    source = model
    if drift:
        from . import driver

        snapshot = driver.snapshot_dir(approval, args.state_dir) / "plan.yaml"
        if not snapshot.is_file():
            raise WorkbenchError(
                "no_snapshot",
                "the plan changed after approval and its approved snapshot is missing at %s"
                % snapshot,
                "run agent-orchestrator plan approve %s" % path,
                path=str(path),
            )
        data, problems = yaml_plan.read_yaml(snapshot)
        if problems:
            raise WorkbenchError(
                "no_snapshot",
                "the approved snapshot %s is not a valid plan" % snapshot,
                "run agent-orchestrator plan approve %s" % path,
                path=str(path),
            )
        # The snapshot omits the approval block; the approved plan is still approved.
        source = yaml_plan.Plan.model_validate(data).model_copy(update={"approval": approval})
    return (
        {
            "approved": True,
            "drift": drift,
            "approved_digest": approval.digest,
            "current_digest": current,
            "approved_by": approval.by,
            "approved_at": approval.at,
        },
        source,
    )


def plan(args):
    """`workbench plan <plan>`: the plan's ticket graph, each ticket with its blocked_by edges, its layer and its
    ledger state, ordered by layer and then plan order."""
    from . import driver

    path = Path(args.plan).expanduser().resolve()
    if not path.is_file():
        raise WorkbenchError(
            "no_such_plan",
            "%s does not exist" % path,
            "pass the path of a plan file: agent-orchestrator workbench plans lists them",
            path=str(path),
        )
    if not driver.is_yaml(path):
        raise WorkbenchError(
            "legacy_plan",
            "%s is a Markdown plan: it has no ticket graph" % path,
            "rewrite it as plan/1 YAML and check it with agent-orchestrator plan check",
            path=str(path),
        )
    repo = repository_of(args, path)
    try:
        revision, source, status = approved_view(path, repo, args)
    except WorkbenchError:
        raise
    except PlanError as error:
        raise WorkbenchError(
            "invalid_plan",
            str(error),
            "fix the plan file and run agent-orchestrator plan check %s" % path,
            path=str(path),
        ) from None
    known = {t["id"]: t for t in status["tickets"]}
    layers = layers_of(source.tickets)
    tickets = []
    for index, ticket in enumerate(source.tickets):
        ledger = known.get(ticket.id) or {}
        tickets.append(
            (
                layers[ticket.id],
                index,
                {
                    "id": ticket.id,
                    "delivers": ticket.delivers,
                    "satisfies": list(ticket.satisfies),
                    "blocked_by": list(ticket.blocked_by),
                    "layer": layers[ticket.id],
                    "owner": ticket.owner,
                    "size": ticket.size,
                    "difficulty": ticket.difficulty,
                    "risk": list(ticket.risk),
                    "paths": list(ticket.paths),
                    "state": ledger.get("state", "TO-DO"),
                    "reason": ledger.get("reason"),
                    "pr": ledger.get("pr"),
                },
            )
        )
    shown = [row for _, _, row in sorted(tickets, key=lambda item: item[:2])]
    return {
        "schema_version": SCHEMA_VERSION,
        "slug": source.slug,
        "format": "yaml",
        "legacy": False,
        "path": str(path),
        "repo": str(repo),
        "goal": source.goal,
        "state": status["state"],
        "revision": revision,
        "counts": counts(shown),
        "tickets": shown,
    }


def run(args):
    """The answer of a `workbench` command, or a WorkbenchError."""
    if args.workbench_command == "plans":
        return plans(args)
    return plan(args)
