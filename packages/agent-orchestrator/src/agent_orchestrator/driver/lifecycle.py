"""The run itself: the round it takes, the re-approval it adopts, and what it cost.

A drive reads the plan, takes one round per gate, and reconciles what the record holds against what
the runtime lists: a task whose agent is gone is restarted, a ticket with nothing dispatched is
dispatched, and a plan edited under a live run drains rather than stopping. This module keeps that
round and the re-approval a lead's edit is adopted with, so a run's own movement reads in one place.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines and no class twenty-five public methods.
"""

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .. import approval_recovery, events, evidence, narrate, routes
from .. import ledger as ledgers
from ..invariants import PARKED
from ..invariants import checked_write as ledger
from ..plan import PlanError
from ..report_rules import require
from ..timeutil import iso as now
from .answers import IntentUnsettled, Unreadable
from .book import release, worker_name
from .prompts import render
from .records import is_agent, ready, satisfied, terminal_gone
from .report import cell, metrics_table, show
from .runtime import runtime_call
from .state import enter_plan_state, legacy_imports, state_layout
from .ticket_dispatch import CONTRACT


def metrics(samples):
    """Normalize per-request observations. Never price tokens using guessed rates."""
    require(isinstance(samples, list), "metrics input must be a list of per-request records")
    seen, groups = set(), {}
    for sample in samples:
        _group_sample(sample, seen, groups)
    return {
        "groups": _metric_rows(groups),
        "note": "partial observations are not full workflow cost; include orchestrator and workers; summed request duration is not parallel wall time",
    }


# The columns every group carries and every sample may observe; a field no sample observed is unknown, not zero.
_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "output_tokens",
    "cost_usd",
    "duration_seconds",
)


def _group_sample(sample, seen, groups):
    """One request record added to its group, refused when it is not a request or repeats another."""
    require(
        isinstance(sample, dict) and sample.get("kind") == "request",
        "only per-request records are accepted",
    )
    for key in ("id", "source", "backend", "model", "strategy", "actor"):
        require(isinstance(sample.get(key), str) and sample[key], "missing metric " + key)
    identity = (sample["source"], sample["id"])
    require(identity not in seen, "duplicate usage record")
    seen.add(identity)
    key = (sample["strategy"], sample["actor"], sample["backend"], sample["model"])
    group = groups.setdefault(
        key,
        {
            "requests": 0,
            "totals": dict.fromkeys(_FIELDS, 0),
            "observations": dict.fromkeys(_FIELDS, 0),
        },
    )
    group["requests"] += 1
    for field in _FIELDS:
        value = sample.get(field)
        if value is None:
            continue
        require(
            type(value) in (int, float) and math.isfinite(value) and value >= 0,
            "invalid metric " + field,
        )
        group["totals"][field] += value
        group["observations"][field] += 1


def _metric_rows(groups):
    """One row per group: a field no request observed reads as unknown rather than as zero."""
    output = []
    for key, group in groups.items():
        for field in _FIELDS:
            if not group["observations"][field]:
                group["totals"][field] = None
        output.append(dict(zip(("strategy", "actor", "backend", "model"), key), **group))
    return output


sum_usage = events.sum_usage


class Lifecycle:
    """The run's own movement, mixed into the Driver: the round, the re-approval, the reconciliation.

    A member moves the run one step and asks the driver it belongs to for the plan, the record, the
    workers and the runtime.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    last_plan: Any
    slug: Any
    brain_cli: Any
    route_source: Any
    step_routes: Any
    run_dir: Any
    lead: Any
    stranded: Any

    def start(self):
        """Open the run: its plan, its routes, its run directory and the ledger it will keep."""
        if getattr(self.args, "models_file", None):
            raise PlanError(
                "--models-file is no longer read: the driver routes by a typed route file, not by a model "
                "table. Pass --routes-file FILE (JSON or YAML naming a backend, model and effort per class), "
                "or keep the routes in %s/routes.json (routes-codex.json for --cli codex, routes-pi.json "
                "for --cli pi)" % routes.USER_POLICY
            )
        plan = self.last_plan = self.read_plan()
        self.slug = plan["slug"]
        self.brain_cli = getattr(self.args, "brain_cli", None)
        # The run directory is only named once the routes are settled: a run that stops here has not started.
        self.ledger_path, run_dir = state_layout(
            self.args.state_dir, self.plan_path, self.repo, self.slug
        )
        pinned = self.recorded_routes()
        self.added_steps = {}
        if pinned:
            self.pin_routes(pinned)
        else:
            self._start_routes()
        Path(self.args.state_dir).expanduser().mkdir(parents=True, exist_ok=True, mode=0o700)
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # A drive that starts on a plan that lost its span read it before the log existed.
        approval_recovery.say_restored(self)
        narrate.NARRATOR.roots = [self.run_dir]  # the pane names a run file by its place in the run
        route_record, legacy = self._start_record(pinned)
        self._start_announce(plan, route_record, legacy)
        # A park a dead drive left behind is not this run's: the plan it could not use is one this run holds.
        self.end_park()

    def _start_routes(self):
        """The routes a run with no recorded ones is routed by: the file it names, the repo's, the user's."""
        self.args.cli = self.args.cli or "claude"
        # The route config this run uses: the file the run names, then the repository's own policy, then the
        # user's profile for the run's backend. No model table answers: with none of them the run stops here.
        try:
            self.routes_file, self.route_source = routes.find_config(
                explicit=getattr(self.args, "routes_file", None),
                repo=self.repo,
                home=routes.user_config_dir(),
                backend=self.args.cli,
            )
        except routes.RouteError as error:
            raise PlanError(str(error)) from None
        self.routes, self.step_routes = self.load_routes()
        self.require_gate_routes()

    def _start_record(self, pinned):
        """(the ledger's route record, the pre-log rows) after the run's first write: its lead and its routes."""
        # The ledger first: it carries the kind marker before any file a worker can read exists.
        with ledger(self.ledger_path) as data:
            self.lead = data.setdefault("lead", "orchestrate:" + self.slug)
            if not pinned:  # a resumed run keeps the record it already has
                data["routes"] = routes.record(
                    self.routes,
                    self.route_source,
                    self.routes_file,
                    self.step_routes,
                    cli=self.args.cli,
                    brain_cli=self.brain_cli,
                )
            elif self.added_steps:  # steps the record lacked, given by the command line
                data["routes"]["steps"].update(
                    routes.record(self.routes, "", None, self.added_steps)["steps"]
                )
            legacy = legacy_imports(data) if not (self.run_dir / "events.jsonl").exists() else []
            # the ledger owns the plan's state; the plan file is never written
            if not data.get("plan"):
                enter_plan_state(data, "APPROVED", self.approval and self.approval.at)
            if data["plan"]["state"] == "APPROVED":
                enter_plan_state(data, "RUNNING")
            self.resume_plan_state(data)
            # The approval the drive gates on, so a run that starts again reads its base from here and not from the
            # plan file, which is what loses the span.
            ledgers.record_approval(data, self.held_approval())
            return data["routes"], legacy

    def _start_announce(self, plan, route_record, legacy):
        """The pane's banner, the contract the workers read, and the run's first log lines."""
        narrate.NARRATOR.banner(
            plan=self.slug,
            # a legacy markdown plan has no model, so no goal to show
            goal=self.model.goal if self.model else "",
            statuses=[ticket["Status"] for ticket in plan["tickets"]],
            repo=str(self.repo),
            base=self.args.base,
            max_workers=self.args.max_workers,
            gates=list(getattr(self.args, "gate", None) or []),
            routes=route_record,
        )
        contract = self.run_dir / "contract.md"
        if not contract.exists():
            contract.write_text(CONTRACT, encoding="utf-8")
        for row in legacy:  # a run dir written before events.jsonl: its history enters the log once
            self.log("import", **row)
        self.log(
            "start",
            pid=os.getpid(),
            cli=self.args.cli,
            max_workers=self.args.max_workers,
            route_source=self.route_source,
        )

    def next_packet(self, task_id, task, row, plan, old, new):
        """Write the attempt's next packet `task.v<n>.md`, set its ownership reservation to the new paths and resume
        its worker on the packet; True when a live worker was told. Two workers never share a checkout, so the new
        paths cannot clash with another attempt's reservation."""
        packet, unit = task["packet"], task["packet"]["unit"]
        scenarios = [plan["scenarios"][sid] for sid in satisfied(row, plan["scenarios"])[0]]
        fields = self.contract_fields(row, scenarios)
        successor = dict(packet, **fields)
        task_dir = self.run_dir / task_id
        task_dir.mkdir(exist_ok=True, mode=0o700)
        version = next(n for n in range(2, 1000) if not (task_dir / ("task.v%d.md" % n)).exists())
        packet_md = task_dir / ("task.v%d.md" % version)
        changes = [
            "- %s: %s -> %s" % (field, show(getattr(old, field)), show(getattr(new, field)))
            for field in ("paths", "satisfies", "delivers")
            if getattr(old, field) != getattr(new, field)
        ]
        packet_md.write_text(
            self.task_text(plan, row, successor, scenarios, task_dir) + self._amended_note(changes),
            encoding="utf-8",
        )
        with self.book() as data:
            data["tasks"][task_id]["packet"].update(
                fields
            )  # the ownership reservation: the new paths
            data["tasks"][task_id]["updated_at"] = now()
        name = packet["worker"]
        resumed = self.resume_worker(
            task_id,
            packet,
            "Your contract changed: the plan was re-approved. Read %s and follow it: it replaces your "
            "earlier task packet; keep the work already in this checkout." % packet_md,
        )
        self.log(
            "repacket",
            unit=unit,
            task=task_id,
            packet=packet_md.name,
            resumed=resumed,
            digest=self.approved_digest(),
        )
        narrate.say(
            "info",
            "build",
            "%s: new packet %s%s"
            % (name, packet_md.name, "" if resumed else " (no live worker to tell)"),
            task=task_id,
        )
        return resumed

    def _amended_note(self, changes):
        """What a re-packeted attempt is told changed: the fields the re-approval moved, old value to new."""
        return render(
            "amended-note", digest=self.approved_digest()[:12], changes="\n".join(changes)
        )

    def record_usage(self, assignments):
        """Keep the `usage` the runtime attaches to an assignment; nothing is ever estimated."""
        seen = {
            a["assignment_id"]: a["usage"]
            for a in assignments
            if a.get("assignment_id") and a.get("usage") is not None
        }
        if seen:
            with self.book() as data:
                changed = {i: u for i, u in seen.items() if data.get("usage", {}).get(i) != u}
                data.setdefault("usage", {}).update(seen)
                owners = {}
                for task_id, task in data["tasks"].items():
                    for agent in (task, task.get("review") or {}, task.get("decision") or {}):
                        owners[agent.get("assignment_id")] = task["packet"].get("unit") or task_id
            for assignment_id, usage in changed.items():
                self.log(
                    "usage",
                    unit=owners.get(assignment_id),
                    assignment_id=assignment_id,
                    usage=usage,
                )

    def report_metrics(self):
        metrics = events.rollup(events.read(self.run_dir / "events.jsonl"), self.slug)
        (self.run_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
        )
        print(metrics_table(metrics), file=sys.stderr, flush=True)
        return metrics

    def report_evidence(self):
        """The run's attempt evidence: one schema-versioned record per attempt, written beside metrics.json (which
        keeps its own shape). A measurement that was not taken is null, never 0."""
        data = ledgers.snapshot(self.ledger_path) or {}
        records = evidence.attempts(data, events.read(self.run_dir / "events.jsonl"), self.slug)
        return evidence.write(self.run_dir / "attempts.jsonl", records)

    def in_flight(self):
        """(task, role, agent name, assignment id) for every open, unparked task that has an agent working for it."""
        found = []
        with self.book() as data:
            for task_id, task in data["tasks"].items():
                gate, packet = task.get("gate"), task["packet"]
                if task["status"] in ("COMPLETE", "RELEASED") or gate in PARKED:
                    continue
                answers, review = task.get("answers") or {}, task.get("review") or {}
                if answers.get("state") == "working" and answers.get("assignment_id"):
                    found.append((task_id, "answerer", packet["worker"], answers["assignment_id"]))
                if gate == "reviewing" and review.get("assignment_id"):
                    found.append((task_id, "reviewer", review["name"], review["assignment_id"]))
                if not gate and task["status"] == "ACTIVE" and task.get("assignment_id"):
                    found.append((task_id, "worker", packet["worker"], task["assignment_id"]))
        return found

    def agent_gone(self, name):
        """(True, the answer) when the runtime says the agent is gone, (False, its state) when it is live. A `status`
        that fails, or answers `unknown` for any other reason, raises `Unreadable`: that is not an answer."""
        answer = runtime_call(self.lead, "status", {"name": name})
        state = answer.get("state")
        if state in ("exited", "finished"):
            return True, state
        if state != "unknown":
            return False, state
        error = (answer.get("native") or {}).get("error")
        if terminal_gone(error, name):
            return True, "unknown: not_found"
        raise Unreadable(
            "status answered unknown: %s" % (cell(error, 200) if error else "no reason given")
        )

    def bound_here(self, name):
        """True when this drive spawned or attached the agent: the runtime has had no `wait` round to list it yet."""
        return name in self.touched

    def strand(self, task_id, name, error):
        """The run cannot tell what became of this task's agent: the task stays open and the Lead hears of it."""
        unit = self.unit_of(task_id)
        self.stranded.add(task_id)
        text = (
            "stranded %s: the agent %s of task %s cannot be settled: %s -> nothing was released or spawned; "
            "look at the terminal, then restart the drive" % (unit, name, task_id, cell(error, 300))
        )
        self.wake("stranded", unit, text, key="%s:%s" % (task_id, error))  # once per task and error
        reason = "%s: %s" % (task_id, error)
        if (
            reason not in self.errors
        ):  # the exit code follows what is stranded now, not what the Lead was told first
            self.errors.append(reason)
        self.log("stranded", unit=unit, task=task_id, name=name, error=str(error)[:500])
        narrate.say("fail", None, "stranded, nothing will settle it: %s" % error, task=task_id)

    def reconcile(self, finishing=False):
        """Settle every open task whose assignment `wait` does not list. Returns how many it acted on; a task it
        cannot settle is left in `self.stranded`. At startup a live terminal is left alone (its message may still
        come); when the run would finish, a terminal still at work that the runtime does not list, and that a
        dead drive left behind, is a unit nobody waits on."""
        self.stranded = set()
        acted = 0
        for task_id, role, name, assignment_id in self.in_flight():
            if assignment_id in self.unfinished:
                continue
            try:
                lost, said = self.agent_gone(name)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                self.strand(task_id, name, str(error))
                continue
            if not lost:
                if finishing and not self.bound_here(name):
                    acted += self._finish_live(task_id, role, name, assignment_id, said)
                continue
            unit = self.unit_of(task_id)
            self.log(
                "orphaned",
                unit=unit,
                task=task_id,
                role=role,
                name=name,
                assignment_id=assignment_id,
                answer=said,
            )
            narrate.say(
                "warn",
                "review" if role != "worker" else "build",
                "%s %s is gone (runtime: %s)" % (role, name, said),
                task=task_id,
            )
            if role != "worker":  # the path an agent the runtime reported as exited takes
                self.on_agent_attention(
                    assignment_id, "exited", "the runtime answered " + said, quiet=True
                )
                acted += 1
            else:
                acted += self.redispatch(task_id, said)
        return acted

    def _finish_live(self, task_id, role, name, assignment_id, said):
        """A live terminal a dead drive left behind, when the run would finish: settle it or name why it stands.

        A turn that ended with its report on disk goes to the gates; anything else nothing will ever settle, so the
        Lead is told. Returns 1 when it acted, 0 otherwise.
        """
        ended = said in ("idle", "done")
        self.strand(
            task_id,
            name,
            "turn ended (%s) with no report for this attempt" % said
            if ended
            else "live but not an assignment of this run: %s" % said,
        )
        return 0

    def redispatch(self, task_id, said):
        """Release the orphaned attempt and put the unit's worker on a new one, in the same checkout and branch. A
        draining run starts no attempt: the orphan stays open and the next reconciliation after the drain takes it."""
        if self.drain:
            return 0
        task = self.task(task_id)
        unit, name = task["packet"]["unit"], task["packet"]["worker"]
        plan = self.last_plan
        tickets = (
            plan["tickets"] if plan else []
        )  # a plan is read before the loop starts; the type allows none
        ticket = next((t for t in tickets if t["#"].lower() == unit), None)
        with self.book() as data:
            taken = set(data["tasks"])
            number = next(n for n in range(1, 1000) if "%so%d" % (unit, n) not in taken)
            release(data, task_id, "orphaned: " + said)
        new_id = "%so%d" % (unit, number)
        note = "\n".join(
            [
                "This is a new attempt (%s) of %s: the agent of the earlier attempt (%s) was lost "
                "(the runtime answered %s)." % (new_id, unit.upper(), task_id, said),
                "Its work is in this checkout and on this branch: read `git status` and `git log`, and continue "
                "from where it stands. Report with task_id `%s`." % new_id,
                "",
            ]
        )
        try:
            if ticket is None:
                raise ValueError("the plan no longer has ticket %s" % unit.upper())
            self.dispatch(plan, ticket, new_id, note=note)
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            if ticket is None:
                self.strand(task_id, name, "re-dispatch as %s failed: %s" % (new_id, error))
            else:
                self.not_dispatched(ticket, error, "not re-dispatched as " + new_id)
            return 0
        narrate.say("start", "build", "dispatched again after %s" % said, task=new_id)
        return 1

    def dispatch_ready(self, plan):
        """Dispatch every ready ticket with no attempt in flight, up to the run's worker slots."""
        with self.book() as data:
            tasks = data["tasks"]
            unfinished = _slots_used(tasks)
        by_id = {t["#"]: t for t in plan["tickets"]}
        count = 0
        for ticket in plan["tickets"]:
            pick = self._dispatchable(ticket, by_id, tasks, unfinished)
            if pick is None:
                continue
            held, new_id = pick
            try:
                self.dispatch(plan, ticket, new_id)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                self.not_dispatched(ticket, error)
                unfinished += 0 if held else 1
                continue
            unfinished += 0 if held else 1
            # a failure the Lead has answered no longer fails the run
            self.errors[:] = [e for e in self.errors if not e.startswith(ticket["#"] + ": ")]
            count += 1
        return count

    def _dispatchable(self, ticket, by_id, tasks, unfinished):
        """(the attempt that holds this unit, the id this round would dispatch) for a ticket the round sends, else
        None. Read in the loop's order, because `restartable` strands a unit whose runtime cannot be asked."""
        task_id = ticket["#"].lower()
        if not is_agent(ticket):
            return None
        if task_id in self.failed and ticket["Status"].strip().upper() == "TO-DO":
            self.failed.discard(task_id)  # the Lead's answer to the wake: try it again
        if task_id in self.failed:
            return None
        if not ready(ticket, by_id, self.restartable(ticket, tasks)):
            return None
        attempts = {k: t for k, t in tasks.items() if t["packet"].get("unit") == task_id}
        pick = _next_attempt(task_id, attempts, tasks)
        if pick is None:  # the unit already has its attempt: complete, or in flight
            return None
        held, new_id = pick
        if held and held["runtime_id"]:
            return None
        if not held and unfinished >= self.args.max_workers:
            return None
        return held, new_id

    def restartable(self, ticket, tasks):
        """True when a ticket whose cell reads `IN PROGRESS` holds no live work: the ledger has an attempt of its
        unit, every one is RELEASED, and the runtime answers definitively that its worker is not live. A cell with
        no attempt in the ledger belongs to some other run and stays a lock; a failed lookup is not an answer: it
        strands the unit (a wake and a non-zero exit) and nothing is spawned."""
        if not ticket["Status"].strip().upper().startswith("IN PROGRESS"):
            return False
        unit = ticket["#"].lower()
        attempts = {k: t for k, t in tasks.items() if t["packet"].get("unit") == unit}
        if not attempts or any(t["status"] != "RELEASED" for t in attempts.values()):
            return False
        name = worker_name(self.slug, ticket["#"][1:])
        try:
            return self.agent_gone(name)[0]
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            self.strand(list(attempts)[-1], name, str(error))
            return False

    def not_dispatched(self, ticket, error, what="not dispatched"):
        """A dispatch that cannot happen asks the Lead, once per ticket and reason; the cell keeps the reason and
        the ticket stays out of this run's rounds until the Lead sets its Status to exactly TO-DO (a restarted drive
        reads a cell starting with TO-DO as TO-DO and tries it once more)."""
        task_id = ticket["#"].lower()
        if isinstance(error, IntentUnsettled):
            # The Lead's way out is the ticket's Status: setting it back to TO-DO is the decision to send again.
            self.release_intents(error.operations, "the Lead is told through the ticket's wake")
        self.failed.add(task_id)
        self.errors.append("%s: %s" % (ticket["#"], error))
        narrate.say("fail", "build", "%s: %s" % (what, error), task=ticket["#"])
        self.write_status(ticket["#"], "TO-DO · %s: %s" % (what, cell(error, 120)))
        self.wake(
            "not-dispatched",
            task_id,
            "%s %s: %s -> fix that, then set the ticket's Status cell back to exactly TO-DO and the next round tries it again"
            % (ticket["#"], what, cell(error, 300)),
            key="%s:%s" % (task_id, error),
        )

    def advance(self):
        """Run every pending gate stage once; each step changes the task's gate or raises. Returns the steps run."""
        return self.gates.advance()


def _slots_used(tasks):
    """The worker slots the record holds: a unit waiting for its merge or its cleanup holds none."""
    return sum(
        1
        for task in tasks.values()
        if task["status"] not in ("COMPLETE", "RELEASED")
        and task.get("gate") not in PARKED
        and task.get("gate") not in ("awaiting-merge", "cleanup", "done")
    )


def _next_attempt(task_id, attempts, tasks):
    """(the reserved attempt to dispatch, its id) for a unit with nothing in flight, or None when the unit has one.

    A dispatch that failed after its claim left a reserved attempt: it is dispatched again under its own id (claim is
    idempotent for it), not as a fresh one. Once every attempt is closed, the next attempt id is a new one.
    """
    open_ = {
        key: task
        for key, task in attempts.items()
        if task["status"] not in ("COMPLETE", "RELEASED")
    }
    if any(task["status"] == "COMPLETE" for task in attempts.values()) or any(
        task["runtime_id"] for task in open_.values()
    ):
        return None
    if open_:
        new_id, held = next(iter(open_.items()))
        return held, new_id
    if not attempts:
        return None, None
    new_id = next(
        "%sr%d" % (task_id, n) for n in range(1, 1000) if "%sr%d" % (task_id, n) not in tasks
    )
    return None, new_id
