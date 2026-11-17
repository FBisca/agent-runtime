"""The handlers behind the command line: one per command that is not a lane of its own.

`driver/cli.py` builds the parser; these are what its subcommands call. `status` reads the plan and the
ledger, `run` starts and locks a drive, `await` waits on a drive's mark, `complete` records a person's
ticket, and `launch` hands a plan to a drive in a fresh process. Each
one takes the parsed arguments and answers the exit code, so the parser stays a description of what a
caller may type.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .. import events, evidence, invariants, timeutil, workbench
from .. import ledger as ledgers
from .. import plan as yaml_plan
from ..invariants import checked_write as ledger
from ..plan import PlanError
from ..report_rules import require
from ..runtime_client import approval_refusal
from .answers import decide
from .archive import report_crash
from .book import checkpoint, claim, release, task_summary, unit_summary
from .engine import Driver
from .lifecycle import metrics
from .records import context_verdict, split_row
from .report import render_status
from .routing import (
    CHECKS_WARNING,
    forwarded,
    is_yaml,
    live_terminals,
    load_approved,
    pane_name,
    read_repo_config,
    repo_root,
    require_yaml,
)
from .state import (
    Refused,
    await_lock_path,
    driver_gone,
    legacy_imports,
    old_ledger_of,
    read_ledger_file,
    run_dir_of,
    run_finished,
    run_lock,
    split_status,
    state_layout,
    take_wake,
)
from .watch import read_json


def _engine():
    """The package this module is split from, asked when a handler asks the engine for a seam.

    A test answers these seams by patching `driver.<name>` - `resolve_runtime`,
    `acceptance_context`, `checkout_snapshot`, `bind`, `run` and the clock `await`
    polls with - and a name bound here at import time would ignore the seam.
    """
    return sys.modules[__name__.rpartition(".")[0]]


def legacy_markdown_view(plan_path):
    """(slug, ticket ids, ticket statuses, plan state) of a legacy Markdown plan, for `status` and the workbench
    listing only: a Markdown plan is no longer run, so nothing else reads it, and it never writes. Tickets come
    only from the table under `## Tickets` whose header has an ID and a Status column."""
    text = plan_path.read_text(encoding="utf-8")
    head = text.split("\n## ", 1)[0]
    slug = re.search(r"Slug:\s*`([^`]+)`", head)
    state = re.search(r"Status:\s*([A-Za-z-]+)", head)
    ids, statuses = _markdown_tickets(text.splitlines())
    return (
        slug.group(1) if slug else yaml_plan.slug_of(plan_path),
        ids,
        statuses,
        state.group(1).upper() if state else "DRAFT",
    )


def _ticket_columns(cells, ids):
    """(id index, status index) the ticket table's header names, or None — a second table is not it."""
    if ids:
        return None
    heads = [cell.lower() for cell in cells]
    if ("#" in heads or "id" in heads) and "status" in heads:
        return (heads.index("#" if "#" in heads else "id"), heads.index("status"))
    return None


def _markdown_tickets(lines):
    """(ids, statuses) from the one ticket table under `## Tickets` whose header names an ID and a Status."""
    ids, statuses, columns, in_tickets = [], {}, None, False
    for line in lines:
        if line.startswith("#"):
            in_tickets = line.lstrip("#").strip().lower() == "tickets"
            columns = None
            continue
        cells = split_row(line)
        if cells is None:
            columns = columns if ids else None
            continue
        if not in_tickets:
            continue
        if columns is None:
            columns = _ticket_columns(cells, ids)
            continue
        if set("".join(cells)) <= set("-: ") or len(cells) <= max(columns):
            continue
        ids.append(cells[columns[0]])
        statuses[cells[columns[0]]] = cells[columns[1]]
    return ids, statuses


def plan_status(args, model=None):
    """`status <plan> --json`: the plan's state and history, and each ticket's state, reason and pull request, read
    from the ledger. It writes nothing, takes no lock and needs no run: before one, the plan is APPROVED (or DRAFT)
    and every ticket TO-DO.

    A caller that already holds the YAML plan model passes it: the workbench reads an approved plan's ledger under
    the approved snapshot's slug and ticket ids, not the edited file's."""
    plan_path = Path(args.plan).resolve(strict=True)
    repo, slug, ids, statuses, state, history = _plan_header(args, plan_path, model)
    ledger_path, run_dir = _plan_state_paths(args, plan_path, repo, slug)
    data, statuses, state, history = _plan_ledger(ledger_path, repo, statuses, state, history)
    return {
        "slug": slug,
        # The plan is an object (path, state, history) for a reader of the run; the flat keys stay for the existing ones.
        "plan": {"path": str(plan_path), "state": state, "history": [h["state"] for h in history]},
        "repo": str(repo) if repo else None,
        "run_dir": str(run_dir),
        "ledger": str(ledger_path),
        "state": state,
        "history": history,
        "tickets": _ticket_rows(ids, statuses, data),
    }


def _plan_header(args, plan_path, model):
    """(repo, slug, ticket ids, statuses, state, history) as the plan file holds them, before any ledger."""
    if not is_yaml(plan_path):
        slug, ids, statuses, state = legacy_markdown_view(plan_path)
        return None, slug, ids, statuses, state, []
    repo = repo_root(plan_path, args.repo)
    if model is None:
        model = yaml_plan.load(plan_path, repo=repo, state_dir=args.state_dir, verify=False)
    state = "APPROVED" if model.approval else "DRAFT"
    history = [{"state": state, "at": model.approval.at}] if model.approval else []
    return repo, model.slug, [t.id for t in model.tickets], {}, state, history


def _plan_state_paths(args, plan_path, repo, slug):
    """(ledger path, run directory) of the plan: the old key for a legacy plan, the layout's for a YAML one."""
    if repo is None:
        # A legacy plan's ledger stays at the old `<state>/<slug>.json` key.
        state_root = Path(args.state_dir).expanduser()
        return state_root / (slug + ".json"), state_root / slug
    ledger_path, run_dir = state_layout(args.state_dir, plan_path, repo, slug)
    if not ledger_path.exists():
        ledger_path = old_ledger_of(args.state_dir, repo, slug) or ledger_path
    return ledger_path, run_dir


def _plan_ledger(ledger_path, repo, statuses, state, history):
    """(ledger data, statuses, state, history) with the plan's own moves read, or the file's when it has none."""
    if not ledger_path.is_file():
        return {}, statuses, state, history
    data = read_ledger_file(ledger_path)
    if repo is None:
        return data, statuses, state, history
    # A legacy plan's Status column stays its source: its ledger never carried the ticket states.
    moves = data.get("plan") or {}
    return (
        data,
        data.get("ticket_status") or {},
        moves.get("state") or state,
        moves.get("history") or history,
    )


def _ticket_rows(ids, statuses, data):
    """One row per ticket: its state, the reason it carries and the pull request that closed it."""
    rows = []
    for ticket_id in ids:
        status, reason = split_status(statuses.get(ticket_id))
        prs = [
            task.get("pr")
            for task in (data.get("tasks") or {}).values()
            if (task.get("packet") or {}).get("unit") == ticket_id.lower() and task.get("pr")
        ]
        rows.append(
            {"id": ticket_id, "state": status, "reason": reason, "pr": prs[-1] if prs else None}
        )
    return rows


def complete_person_ticket(args):
    """`complete <plan> <T#> --note TEXT`: the Lead records a person-owned ticket done. The status goes into the
    ledger under its lock, so a live driver keeps it and the next run archives with it."""
    refusal = approval_refusal(verb="complete")
    if refusal:
        # The runtime's wording is about approval; its reason is the part before the dash.
        raise Refused(
            "complete: %s — recording a person's ticket belongs to the Lead (the person in their own session): "
            "a worker: ask the Lead to run complete; a Lead runs it from a terminal with no agent host, or from "
            "its hc Lead terminal" % refusal.split(" — ")[0]
        )
    note = " ".join(args.note.split())
    if not note:
        raise Refused("complete: needs a --note saying what was done, kept in the ticket's status")
    model, _, ledger_path, _, seen = _engine().acceptance_context(args)
    ticket = _person_ticket(model, args.ticket)
    require(ledger_path.is_file(), "complete: %s has no run yet; run it first" % model.slug)
    status = "COMPLETE %s · %s" % (timeutil.now().date().isoformat(), note)
    _record_person_ticket(ledger_path, seen, ticket, status)
    return {"ticket": ticket.id, "status": status}


def _person_ticket(model, named):
    """The person-owned ticket `complete` records, refused when it is missing or an agent's."""
    ticket = next((t for t in model.tickets if t.id.lower() == named.lower()), None)
    if ticket is None:
        raise Refused(
            "complete: no ticket %s in %s; its tickets are %s"
            % (named, model.slug, ", ".join(t.id for t in model.tickets))
        )
    if ticket.owner.strip().lower() == "agent":
        raise Refused(
            "complete: %s is an agent ticket; the driver's workers complete it, and `complete` records only a "
            "person-owned ticket" % ticket.id
        )
    return ticket


def _record_person_ticket(ledger_path, seen, ticket, status):
    """The ticket's status, written under the ledger's lock; a stale snapshot is refused."""
    try:
        with ledger(ledger_path, snapshot=seen, keys=("plan",)) as data:
            data.setdefault("ticket_status", {})[ticket.id] = status
    except ledgers.LedgerStale as error:
        raise Refused("complete: %s" % error) from None


def launch(args, run_args):
    """Check the plan, then start `run` in a new Herdr terminal. A live driver of the same name is left alone."""
    plan_path = Path(args.plan).resolve(strict=True)
    repo = _launch_repo(args, plan_path)
    require_yaml(plan_path)
    # The same check `run` makes first, so an unapproved or drifted contract starts no pane. A plan whose `approval`
    # span was lost is the plan the ledger holds the approval of: the check recovers it, as the drive does.
    slug = load_approved(
        plan_path,
        repo=repo,
        state_dir=forwarded(run_args, "--state-dir") or yaml_plan.DEFAULT_STATE,
        require_approval=True,
        recover=True,
    ).slug
    name = pane_name(slug)
    if name in live_terminals():
        return {"pane": name, "started": False}
    return _launched(name, repo, plan_path, run_args)


def _launch_repo(args, plan_path):
    """The repository a _launched run is rooted at: the one named, or the plan's own checkout."""
    if args.repo:
        return Path(args.repo).resolve()
    return Path(
        _engine().run(["git", "-C", str(plan_path.parent), "rev-parse", "--show-toplevel"])
    ).resolve()


# Pane and session variables stay out of what the driver inherits: the pane sets its own terminal id, and a Claude
# session's variables must not leak into Herdr tabs.
_NOT_FORWARDED = ("HC_TERMINAL_ID", "CLAUDECODE", "CLAUDE_CODE_")

# Runs in the pane: loads the env file (the first argument), deletes it, then execs the driver.
_ENV_BOOTSTRAP = (
    "import json, os, sys; p = sys.argv[1]; e = json.load(open(p)); os.unlink(p); os.environ.update(e); "
    "os.execv(sys.argv[2], sys.argv[2:])"
)


def _forwardable_env():
    return {k: v for k, v in os.environ.items() if not k.startswith(_NOT_FORWARDED)}


def _launched(name, repo, plan_path, run_args):
    """Run `agent-orchestrator run` in its own Herdr pane, handing the Lead's terminal over to it. The pane gets the
    Herdr server's environment, so the Lead's goes through a mode 0600 file the pane deletes at start: a value on argv
    would show in `ps`."""
    fd, env_path = tempfile.mkstemp(prefix="agent-orchestrator-env-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_forwardable_env(), handle)
        # The pane starts after the launcher returns, before its bootstrap opens the file: on success the bootstrap
        # owns the deletion.
        return _launch_pane(name, repo, plan_path, run_args, env_path)
    except BaseException:
        Path(env_path).unlink(missing_ok=True)
        raise


def _launch_pane(name, repo, plan_path, run_args, env_path):
    argv = [
        "hc",
        "run",
        "--bg",
        "--name",
        name,
        "--cwd",
        str(repo),
        "--json",
        "--",
        sys.executable,
        "-c",
        _ENV_BOOTSTRAP,
        env_path,
        sys.executable,
        "-m",
        "agent_orchestrator",
        "run",
        str(plan_path),
        "--repo",
        str(repo),
        *run_args,
    ]
    parent = os.environ.get("HC_TERMINAL_ID")
    # The run's own HC_TERMINAL_ID is its pane: the Lead's terminal has to be handed over.
    if parent:
        argv += ["--notify-terminal", parent]
    result = subprocess.run(
        argv[:3] + ["--parent", parent] + argv[3:] if parent else argv,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if not parent or result.returncode == 0:
        return _launched_output(name, result)
    nesting_error = "hc run failed (exit %d): %s" % (
        result.returncode,
        (result.stderr or result.stdout).strip(),
    )
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    return _launched_output(name, result, nesting_error)


def _launched_output(name, result, nesting_error=None):
    """The answer `launch` prints: the pane, whether it started, and why a nesting hand-over was retried."""
    if result.returncode != 0:
        raise ValueError(
            "hc run failed (exit %d): %s"
            % (result.returncode, (result.stderr or result.stdout).strip())
        )
    out = {"pane": name, "started": True}
    if nesting_error is not None:
        out.update(nested=False, nesting_error=nesting_error)
    return out


def rebuild_metrics(args):
    """`metrics <plan>`: metrics.json rebuilt from the run's events.jsonl, which survives a crash. A run dir written
    before the log existed is read from its ledger instead."""
    slug, ledger_path, run_dir = _engine().plan_home(args)
    rows = events.read(run_dir / "events.jsonl")
    if not rows:
        require(
            ledger_path.exists(),
            "no events.jsonl or ledger for %s under %s" % (slug, run_dir.parent),
        )
        rows = [
            dict(row, kind="import") for row in legacy_imports(ledgers.snapshot(ledger_path) or {})
        ]
    result = events.rollup(rows, slug)
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (run_dir / "metrics.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def rebuild_evidence(args):
    """`evidence <plan>`: the run's attempts.jsonl rebuilt from its ledger and its events.jsonl, which survive a crash.
    The old metrics.json is left alone: the evidence is written beside it."""
    slug, ledger_path, run_dir = _engine().plan_home(args)
    require(ledger_path.exists(), "no ledger for %s under %s" % (slug, run_dir.parent))
    records = evidence.attempts(
        ledgers.snapshot(ledger_path) or {}, events.read(run_dir / "events.jsonl"), slug
    )
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    evidence.write(run_dir / "attempts.jsonl", records)
    return records


def await_wake(args):
    """`await <plan>`: print the next wake the Lead has not seen and return; `finished -> <run>/retro.md` once the run
    has ended and every wake is printed; `crashed -> <log line>` when its driver crashed and `driver gone without a crash
    log` when it was killed. Waits (polling, no tokens) while there is none of these, which includes a run whose driver
    has not started yet."""
    run_dir = Path(run_dir_of(args))
    lock_path = await_lock_path(args)
    while True:
        line = take_wake(run_dir)
        if line:
            return line
        if run_finished(run_dir):
            return "finished -> %s" % (run_dir / "retro.md")
        gone = driver_gone(run_dir, lock_path)
        if gone:
            return gone
        _engine().time.sleep(args.poll_seconds)


def drive(args):
    require(args.max_workers >= 1, "--max-workers must be at least 1")
    require(args.max_fix_rounds >= 0, "--max-fix-rounds must not be negative")
    require(args.context_reserve >= 0, "--context-reserve must not be negative")
    require(args.idle_minutes >= 0, "--idle-minutes must not be negative")
    require(args.working_minutes >= 0, "--working-minutes must not be negative")
    require_yaml(args.plan)
    driver = Driver(args)
    slug = driver.slug = driver.read_plan()["slug"]
    with run_lock(driver.repo, slug, driver.plan_path):
        return drive_locked(driver)


def drive_locked(driver):
    """The run, under its lock. An exception that escapes it, from startup, the loop or the finalization, is logged as
    `crashed <reason>` and the Lead is woken before it goes on to end the process: `await` then reports the crash instead
    of polling a run nobody drives."""
    try:
        driver.start()
        try:
            driver.loop()
            if not driver.stranded:  # a run that leaves a task it cannot settle has not finished
                driver.log("finish")
        finally:
            try:
                driver.report_metrics()
            finally:
                driver.report_evidence()
    except Exception as error:
        report_crash(driver, error)
        raise
    return {
        "slug": driver.slug,
        "lead": driver.lead,
        "spawned": driver.spawned,
        "errors": driver.errors,
    }


def dispatch(args, extra):
    """The parsed command line answered: one branch per command group, and `launch` alone takes the
    options it forwards to `run`, which validates them."""
    config = read_repo_config(args)
    if args.command == "plan" and args.plan_command == "check":
        if config is not None and config.checks is None:
            print(CHECKS_WARNING % ".orchestrate/config.yaml", file=sys.stderr)
    if args.command == "launch":
        return launch(args, extra)  # unknown options are forwarded to `run`, which validates them
    if args.command == "plan":
        return _plan_command(args)
    if args.command == "plans":
        return _plans_command(args)
    if args.command in _READS:
        return _READS[args.command](args)
    if args.command in _RUNS:
        return _RUNS[args.command](args)
    if args.command in _INSPECTS:
        return _INSPECTS[args.command](args)
    return _ledger_command(args)


def _status_command(args):
    """`status <plan> [--json]`: the state and its tickets drawn for a person, the whole record for a script."""
    status = plan_status(args)
    return status if args.json else render_status(status)


def _context_command(args):
    """`context --sample FILE`: what the runtime last measured, against the reserve this run holds."""
    return context_verdict(read_json(args.sample), args.reserve_tokens, args.max_age_seconds)


def _plans_command(args):
    """`plans [--json] [--path DIR]`: the plans the repository holds, one path per line by default."""
    found = yaml_plan.discover(args.repo, args.path)
    if args.json:
        return found
    return "\n".join(entry["path"] for entry in found)


def _plan_command(args):
    """`plan schema|check|approve`: the plan grammar, its validation and its approval."""
    if args.plan_command == "schema":
        return yaml_plan.Plan.model_json_schema(by_alias=True)
    if args.plan_command == "approve":
        return _approved(args)
    model = load_approved(args.plan, repo=args.repo, state_dir=args.state_dir)
    return {"valid": True, "slug": model.slug}


def _approved(args):
    """`plan approve`, refused to a spawned worker: the plan belongs to the Lead."""
    caller = _engine().resolve_runtime()
    if isinstance(caller, dict) and caller.get("role") == "worker":
        raise PlanError(
            "approval: caller is a spawned worker, parent %s — approve belongs to the Lead "
            "(the person in their own session); ask the Lead to run plan approve"
            % caller.get("parent", "unknown")
        )
    return {
        "approval": yaml_plan.approve(
            args.plan, repo=args.repo, state_dir=args.state_dir, base=args.base, reason=args.reason
        )
    }


def _inspect_command(args):
    """`inspect`: this checkout, the executables on PATH, the runtime and what is known of the context."""
    return {
        **_engine().checkout_snapshot(args.checkout),
        "executables": {c: shutil.which(c) for c in ("codex", "claude", "pi")},
        "agent_runtime": _engine().resolve_runtime(),
        "native_agent_capabilities": "inspect this session's tool schema",
        "context": "unknown; supply current child runtime telemetry",
    }


def _metrics_command(args):
    """`metrics`: a plan's `metrics.json` rebuilt from its events, or a sample file rolled up."""
    if args.plan:
        return rebuild_metrics(args)
    require(args.samples, "metrics needs a plan file (rebuild its metrics.json) or --samples")
    return metrics(read_json(args.samples))


def _show_command(args):
    """`show`: a snapshot read, which takes no lock, so it never waits for the driver or makes it wait."""
    found = ledgers.snapshot(args.state)
    data = ledgers.validate(found, args.state) if found is not None else ledgers.new()
    if args.unit:
        return unit_summary(data, args.unit)
    return (
        data["tasks"][args.task] if args.task else [task_summary(t) for t in data["tasks"].values()]
    )


def _ledger_command(args):
    """`claim|bind|checkpoint|release|show|inspect|metrics`: the record's own commands, under its lock."""
    if args.command == "ledger":
        return invariants.check_command(args.path)
    if args.command == "show":
        return _show_command(args)
    if args.command == "inspect":
        return _inspect_command(args)
    if args.command == "metrics":
        return _metrics_command(args)
    with ledger(args.state) as data:
        if args.command == "claim":
            result = claim(data, read_json(args.packet))
            return {"action": result["action"], **task_summary(result["task"])}
        if args.command == "bind":
            return task_summary(_engine().bind(data, args.task, args.runtime_id))
        if args.command == "checkpoint":
            return task_summary(checkpoint(data, args.task, read_json(args.report)))
        require(args.stopped, "release requires --stopped")
        return task_summary(release(data, args.task, args.reason, args.cutoff))


# One answer per command that reads, runs, inspects or books: the group a command belongs to is the
# one thing `dispatch` decides, so a command added later names its reader instead of adding a branch.
_READS = {
    "status": _status_command,
    "complete": complete_person_ticket,
}
_RUNS = {"run": drive, "await": await_wake, "decide": decide}
_INSPECTS = {"context": _context_command, "evidence": rebuild_evidence, "workbench": workbench.run}
