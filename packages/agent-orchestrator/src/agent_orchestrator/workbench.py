"""The workbench's read side: `workbench plans` and `workbench plan` print what the plan loader and the ledger say.

Both commands are read-only: they take no lock, import no old ledger and create no file, so a person (or a pane)
may run them beside a live driver. Every answer is one JSON object with `schema_version`; a refusal is one too, with
an `error` that names its kind and the way out. A count, token or duration nothing measured is null, never 0.

`workbench totals` sums the last seven days of every plan (see `workbench_totals`).

The driver owns the ledger and the state layout, and imports this module; the helpers it owns are imported late.
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

from . import evidence, workbench_needs, workbench_totals
from . import plan as yaml_plan
from . import workbench_metrics as metrics
from .plan import DiscoveryError, PlanError, PlanLayout
from .timeutil import now

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


def agents_of(data, ids):
    """[(ticket id, name, role, address)] of every ledger agent whose unit is a ticket of `ids`, in ledger order. The
    unit is the link, never the name; an agent of no ticket (the archive's) is left out."""
    by_unit = {ticket_id.lower(): ticket_id for ticket_id in ids}
    found = []
    for name, agent in (data.get("agents") or {}).items():
        agent = agent or {}
        unit = agent.get("unit")
        if not isinstance(unit, str) or unit.lower() not in by_unit:
            continue
        address = agent.get("address")
        found.append(
            (
                by_unit[unit.lower()],
                name,
                agent.get("role"),
                address if isinstance(address, str) and address.strip() else None,
            )
        )
    return found


def awaiting_merge_of(data, ids):
    """[{ticket, pr}] for each task the driver holds at the awaiting-merge gate, in ledger order."""
    by_unit = {ticket_id.lower(): ticket_id for ticket_id in ids}
    held = []
    for task in (data.get("tasks") or {}).values():
        unit = (task.get("packet") or {}).get("unit")
        if (
            task.get("gate") == "awaiting-merge"
            and isinstance(unit, str)
            and unit.lower() in by_unit
        ):
            held.append({"ticket": by_unit[unit.lower()], "pr": task.get("pr")})
    return held


def listing(entry, repo, args):
    """(one plan's entry of `workbench plans`, the items it holds for the person). A plan that does not load is an
    entry with an error and null counts, so one broken plan never hides the others."""
    from . import driver

    markdown = entry["format"] == "markdown"
    path = Path(entry["path"])
    shown = {
        "slug": entry["slug"],
        "format": entry["format"],
        "path": entry["path"],
        "legacy": markdown,
    }
    status = None
    try:
        if markdown:
            status = status_of(entry["path"], repo, args)
        else:
            status = approved_view(path, repo, args)[2]
    except (PlanError, ValueError, KeyError, OSError) as error:
        kind = error.kind if isinstance(error, WorkbenchError) else "invalid_plan"
        shown.update(state=None, tickets=None, error={"kind": kind, "message": str(error)})
    else:
        shown.update(slug=status["slug"], state=status["state"])
        # A Markdown plan whose table lists no ticket has nothing to count: no counts, not a zero.
        if status["tickets"] or not markdown:
            shown["tickets"] = counts(status["tickets"])
    if status:
        ledger, run_dir = Path(status["ledger"]), Path(status["run_dir"])
    else:
        ledger, run_dir = driver.locate_state(args.state_dir, path, repo, entry["slug"])
    group = workbench_needs.group_of(
        shown["state"], markdown, None if status else workbench_needs.ledger_state_of(ledger)
    )
    events = run_dir / "events.jsonl"
    # Only an active plan's log is read whole: what waits on the person is only asked of those.
    run = workbench_needs.scan(events) if group == "active" else None
    shown.update(group=group, last_activity=workbench_needs.last_activity(events, path, run))
    items = []
    shown.update(agents=[], awaiting_merge=[])
    if group == "active":
        items = workbench_needs.needs_of(
            shown["slug"], shown["state"], status["tickets"] if status else [], run
        )
        if status and ledger.is_file():
            data = driver.read_ledger_file(ledger)
            ids = [t["id"] for t in status["tickets"]]
            shown["agents"] = [
                {"name": name, "role": role, "ticket": ticket, "address": address}
                for ticket, name, role, address in agents_of(data, ids)
            ]
            shown["awaiting_merge"] = awaiting_merge_of(data, ids)
    return shown, items


def without_twins(found):
    """The plan files of a listing without a Markdown plan whose YAML twin (same directory, same stem) is listed: the
    YAML plan is the plan, and its twin's state would contradict the ledger's."""
    layouts = [PlanLayout(e["path"]) for e in found]
    keys = [(layout.path.parent, layout.kind, layout.stem) for layout in layouts]
    yaml_stems = {key for key, e in zip(keys, found) if e["format"] == "yaml"}
    return [e for key, e in zip(keys, found) if e["format"] == "yaml" or key not in yaml_stems]


def plans(args):
    """`workbench plans [path] [--repo R]`: every plan of the repository, with its state, ticket counts, group and last
    activity, active plans first and the newest first, and the `needs_me` items of the active ones."""
    repo = repository_of(args, args.path)
    try:
        found = without_twins(yaml_plan.discover(repo, args.path))
    except DiscoveryError as error:
        raise refusal(error, "plans") from None
    if not found:
        raise WorkbenchError(
            "no_plans",
            "no plan files in %s" % repo,
            "pass an explicit plan path or --repo R, or write a plan with /start-planning",
        )
    rows = [listing(entry, repo, args) for entry in found]
    rows.sort(key=lambda row: workbench_needs.order_key(row[0]))
    return {
        "schema_version": SCHEMA_VERSION,
        "repo": str(repo),
        "plans": [shown for shown, _ in rows],
        "needs_me": [item for _, items in rows for item in items],
    }


def totals_source(entry, repo, args):
    """The totals' inputs for one plan file, or None for a Markdown plan (never run). A plan that no longer loads (an
    archived one whose folder was deleted) is still read: its ledger names its tickets."""
    from . import driver

    if entry["format"] == "markdown":
        return None
    path = Path(entry["path"])
    try:
        status = approved_view(path, repo, args)[2]
    except (PlanError, ValueError, KeyError, OSError):
        status = None
    if status:
        ledger, run_dir = Path(status["ledger"]), Path(status["run_dir"])
        ids = [t["id"] for t in status["tickets"]]
        slug = status["slug"]
    else:
        slug = entry["slug"]
        ledger, run_dir = driver.locate_state(args.state_dir, path, repo, slug)
        ids = []
    data = {}
    if ledger.is_file():
        try:
            data = driver.read_ledger_file(ledger)
        except (PlanError, ValueError, OSError):
            data = {}
    if not ids:
        ids = list((data.get("ticket_status") or {}))
    return workbench_totals.Source(slug, run_dir, ids, data.get("tasks"))


def totals(args):
    """`workbench totals [path] [--repo R]`: the last seven days of the repository's plans, summed for the home cards
    and each card's dashboard. Reads the plans' ledgers and logs and writes nothing."""
    repo = repository_of(args, args.path)
    try:
        found = without_twins(yaml_plan.discover(repo, args.path))
    except DiscoveryError as error:
        raise refusal(error, "totals") from None
    if not found:
        raise WorkbenchError(
            "no_plans",
            "no plan files in %s" % repo,
            "pass an explicit plan path or --repo R, or write a plan with /start-planning",
        )
    sources = [s for s in (totals_source(entry, repo, args) for entry in found) if s]
    return {**workbench_totals.totals(sources, now()), "repo": str(repo)}


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


# The letter after a unit's id in an attempt's task id says why the attempt exists (driver: fix `r`, retry `d`,
# orphan `o`, reopen `p`, answer `a`); the unit's own id is its first worker.
ATTEMPT_KINDS = {
    "": "worker",
    "r": "fix",
    "d": "retry",
    "o": "orphan",
    "p": "reopen",
    "a": "answer",
}


def kind_of(task_id, unit):
    match = re.fullmatch(r"%s([a-z]?)\d*" % re.escape(unit), task_id.lower())
    return ATTEMPT_KINDS.get(match.group(1), "other") if match else "other"


def fallbacks_of(data):
    """The strings the driver wrote as an agent's address when the runtime returned none: an agent's name or an
    assignment id (of any attempt). None of them is a terminal, so a reader must not offer to open one."""
    names = set()
    for name, agent in (data.get("agents") or {}).items():
        names.add(name)
        names.add((agent or {}).get("assignment_id"))
    for task in (data.get("tasks") or {}).values():
        names.add(task.get("assignment_id"))
        names.add((task.get("packet") or {}).get("worker"))
        for part in ("review", "decision", "answers"):
            if isinstance(task.get(part), dict):
                names.add(task[part].get("assignment_id"))
    names.discard(None)
    return names


def address_of(task, fallbacks):
    """The address the runtime returned for this attempt, else None.

    A ledger written since the driver records only what the runtime returned holds it as `address`. An older one
    holds the binding key `runtime_id`, which is the address or, when the runtime returned none, the fallback the
    driver wrote. Nothing else (a terminal name, another attempt, a timestamp) is ever read as an address."""
    bound = task.get("runtime_id")
    found = task["address"] if "address" in task else bound
    # A binding key that is the fallback wins over a recorded address: the two disagree, so nothing is known.
    if not isinstance(found, str) or not found.strip() or bound in fallbacks:
        return None
    return found


def claimed_of(task):
    """Whether the worker claimed completion: its own report said complete, met its acceptance and passed every
    check. The ledger keeps the driver's checkpoint of that report (PARTIAL until the merge), so the worker's file is
    read for the claim and nothing of it is printed; without the file the ledger's copy is the claim.

    A claim is the worker's word, not evidence: `verified` is what says the gates passed."""
    path = task.get("report_path")
    if isinstance(path, str):
        try:
            report = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = None
        if isinstance(report, dict) and report.get("status"):
            return evidence.claimed({"report": report})
    return evidence.claimed(task)


def attempts_of(ticket_id, data):
    """Every ledger task of the ticket, oldest first (start time, then ledger order), the newest marked latest."""
    unit = ticket_id.lower()
    fallbacks = fallbacks_of(data)
    mine = [
        (task.get("started_at") or "", index, task_id, task)
        for index, (task_id, task) in enumerate((data.get("tasks") or {}).items())
        if (task.get("packet") or {}).get("unit") == unit
    ]
    shown = []
    for _, _, task_id, task in sorted(mine, key=lambda item: item[:2]):
        finished = task.get("finished_at")
        if not finished and task.get("status") in evidence.TERMINAL:
            finished = task.get("updated_at")
        shown.append(
            {
                "task_id": task_id,
                "kind": kind_of(task_id, unit),
                "status": task.get("status"),
                "started_at": task.get("started_at"),
                "finished_at": finished,
                "assignment_id": task.get("assignment_id"),
                "address": address_of(task, fallbacks),
                "claimed": claimed_of(task),
                "latest": False,
            }
        )
    if shown:
        shown[-1]["latest"] = True
    return shown


def revision_of(model, authored, path, repo, args):
    """(revision object, the model the tickets come from). An approved plan is shown as approved: when the authored
    file changed since, the approved snapshot's tickets are shown with `drift: true`."""
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
            "rewrite it as plan/2 YAML and check it with agent-orchestrator plan check",
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
    ledger_path = Path(status["ledger"])
    data = driver.read_ledger_file(ledger_path) if ledger_path.is_file() else {}
    agents = agents_of(data, [t.id for t in source.tickets])
    layers = layers_of(source.tickets)
    tickets = []
    for index, ticket in enumerate(source.tickets):
        ledger = known.get(ticket.id) or {}
        state = ledger.get("state", "TO-DO")
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
                    "state": state,
                    "reason": ledger.get("reason"),
                    "pr": ledger.get("pr"),
                    # Verified is the gate's word, not the worker's: a ticket completes only through verify's
                    # invariants and its pull request's CI.
                    "verified": state == "COMPLETE",
                    "attempts": attempts_of(ticket.id, data),
                    "agents": [
                        {"name": name, "role": role, "address": address}
                        for owner, name, role, address in agents
                        if owner == ticket.id
                    ],
                },
            )
        )
    shown = [row for _, _, row in sorted(tickets, key=lambda item: item[:2])]
    run_dir = Path(status["run_dir"])
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
        # A plan nobody drove has no run directory; one that has holds events, and its metrics are read from them.
        "run_dir": str(run_dir) if run_dir.is_dir() else None,
        "metrics": metrics.compute(run_dir, [t["id"] for t in shown]) if run_dir.is_dir() else None,
    }


def run(args):
    """The answer of a `workbench` command, or a WorkbenchError."""
    if args.workbench_command == "plans":
        return plans(args)
    if args.workbench_command == "totals":
        return totals(args)
    return plan(args)
