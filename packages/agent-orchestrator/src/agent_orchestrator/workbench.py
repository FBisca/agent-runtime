"""The workbench's read side: `workbench plans` and `workbench plan` print what the plan loader and the ledger say.

Both commands are read-only: they take no lock, import no old ledger and create no file, so a person (or a pane)
may run them beside a live driver. Every answer is one JSON object with `schema_version`; a refusal is one too, with
an `error` that names its kind and the way out. A count, token or duration nothing measured is null, never 0.

The driver owns the ledger and the state layout, and imports this module; the helpers it owns are imported late.
"""

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

from . import evidence, verify, workbench_needs
from . import plan as yaml_plan
from . import workbench_metrics as metrics
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
    if group == "active":
        items = workbench_needs.needs_of(
            shown["slug"], shown["state"], status["tickets"] if status else [], run
        )
    return shown, items


def without_twins(found):
    """The plan files of a listing without a Markdown plan whose YAML twin (same directory, same stem) is listed: the
    YAML plan is the plan, and its twin's state would contradict the ledger's."""
    yaml_stems = {
        (Path(e["path"]).parent, Path(e["path"]).name.removesuffix(".plan.yaml"))
        for e in found
        if e["format"] == "yaml"
    }
    return [
        e
        for e in found
        if e["format"] == "yaml"
        or (Path(e["path"]).parent, Path(e["path"]).name.removesuffix(".plan.md")) not in yaml_stems
    ]


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


def receipt_reason(stem, data, receipts_dir):
    """Why a receipt is not a pass, in its own words: its label (else its reason). None when the receipt cannot be
    read; its log and detail are never read."""
    path = Path(receipts_dir) / (stem + ".json")
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(raw).hexdigest() != (data.get("receipts") or {}).get(stem):
        return "%s.json no longer matches the sha256 the ledger holds" % stem
    try:
        receipt = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(receipt, dict) or receipt.get("result") == "PASS":
        return None
    return receipt.get("label") or receipt.get("reason")


def gates_of(ticket_id, source, data, receipts_dir):
    """The evidence that ran at the ticket's gate, in plan order: scenario, phase, result and the receipt's reason."""
    ran = (data.get("ticket_evidence") or {}).get(ticket_id) or {}
    order = [s.id for s in source.scenarios]
    wanted = sorted(ran, key=lambda sid: (order.index(sid) if sid in order else len(order), sid))
    return [
        {
            "scenario": sid,
            "phase": "ticket",
            "result": ran[sid].get("result"),
            "reason": receipt_reason(ran[sid].get("receipt") or "", data, receipts_dir),
        }
        for sid in wanted
    ]


def problem_of(data, digest, tree_of):
    """(freshness, why) of the integration run itself, else None: what `verify` checks before it reads a receipt."""
    pin = data.get("integration") or {}
    if not pin:
        return "missing", "the plan has not been verified on a pinned default-branch commit"
    if pin.get("digest") != digest:
        return "stale", "the integration run was for another approval digest"
    actual = tree_of(pin.get("sha"))
    if actual != pin.get("tree"):
        return "stale", "the pinned commit holds another tree than the recorded one"
    return None


def receipt_freshness(data, sid, phase, receipts_dir, digest, tree):
    """((freshness, why), receipt) of the newest receipt of one item and phase, by `verify`'s own reading: `fresh`
    counts for this digest and tree, `missing` is no receipt (or no file), `stale` is one that does not count."""
    stems = verify.receipt_stems(data, sid, phase)
    if not stems:
        return ("missing", "no %s receipt" % phase), None
    if not (Path(receipts_dir) / (stems[-1] + ".json")).is_file():
        return ("missing", "its receipt %s.json is missing" % stems[-1]), None
    receipt, why = verify.read_receipt(data, stems[-1], receipts_dir, digest, tree)
    if receipt is None:
        return ("stale", why), None
    return ("fresh", None), receipt


def item_freshness(source, scenario, data, receipts_dir, digest, tree_of):
    """`fresh`, `missing` or `stale` for one outcome item, and why: which receipt `verify` counts for it."""
    problem = problem_of(data, digest, tree_of)
    record = (data.get("acceptance") or {}).get(scenario.id) or {}
    if scenario.evidence.human:
        if problem:
            return problem
        if verify.holds_acceptance(record, digest, (data.get("integration") or {}).get("tree")):
            return "fresh", None
        if record:
            return "stale", "the acceptance was given for another approval digest or tree"
        return "missing", "no person has accepted it yet"
    if problem:
        return problem
    pin = data.get("integration") or {}
    found, receipt = receipt_freshness(
        data, scenario.id, "integration", receipts_dir, digest, pin.get("tree")
    )
    archived = (data.get("plan") or {}).get("state") == "ARCHIVED"
    if found[0] != "fresh" or not archived or scenario.id not in source.outcome:
        return found
    if receipt.get("result") != "PASS":
        return found
    # An archived plan's passing item also needs a receipt for the archive head.
    head = data.get("archive") or {}
    if not head or head.get("digest") != digest:
        return "stale", "the archive head was not verified for this approval digest"
    return receipt_freshness(data, scenario.id, "archive", receipts_dir, digest, head.get("tree"))[
        0
    ]


def outcome_of(source, repo, data, receipts_dir):
    """The plan's outcome as `verify <plan>` computes it (state and each item's state and reason) from the
    receipts and the ledger, with each item's freshness. Acceptance is verify's word, never inferred from tickets.
    None for a plan that was never approved: it has no contract to verify."""
    from . import driver

    approval = source.approval
    if approval is None:
        return None
    tree_of = driver.tree_in(repo)
    result = driver.acceptance(source, data, receipts_dir, digest=approval.digest, tree_of=tree_of)
    items = []
    for item in result["items"]:
        scenario = verify.scenario_of(source, item["scenario"])
        freshness, _ = item_freshness(
            source, scenario, data, receipts_dir, approval.digest, tree_of
        )
        items.append(
            {
                "scenario": item["scenario"],
                "state": item["state"],
                "freshness": freshness,
                "reason": item["reason"],
            }
        )
    return {
        "state": result["state"],
        "integration": (data.get("integration") or {}).get("sha"),
        "items": items,
    }


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
    ledger_path = Path(status["ledger"])
    data = driver.read_ledger_file(ledger_path) if ledger_path.is_file() else {}
    receipts_dir = Path(status["run_dir"]) / "receipts"
    unverified = data.get("unverified") or {}
    layers = layers_of(source.tickets)
    tickets = []
    for index, ticket in enumerate(source.tickets):
        ledger = known.get(ticket.id) or {}
        state = ledger.get("state", "TO-DO")
        gates = gates_of(ticket.id, source, data, receipts_dir)
        flagged = ticket.id in unverified
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
                    # Verified is the evidence's word, not the worker's: complete, merged past nothing, every gate
                    # that ran passed.
                    "verified": state == "COMPLETE"
                    and not flagged
                    and all(gate["result"] == "PASS" for gate in gates),
                    "unverified": flagged,
                    "unverified_reason": (unverified.get(ticket.id) or {}).get("reason"),
                    "attempts": attempts_of(ticket.id, data),
                    "gates": gates,
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
        "outcome": outcome_of(source, repo, data, receipts_dir),
        # A plan nobody drove has no run directory; one that has holds events, and its metrics are read from them.
        "run_dir": str(run_dir) if run_dir.is_dir() else None,
        "metrics": metrics.compute(run_dir, [t["id"] for t in shown]) if run_dir.is_dir() else None,
    }


def run(args):
    """The answer of a `workbench` command, or a WorkbenchError."""
    if args.workbench_command == "plans":
        return plans(args)
    return plan(args)
