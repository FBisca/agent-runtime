"""The Lead's attention: the wake that names what is owed, and the round that watches for it.

A run's units go quiet in a handful of ways - a worker that ended its turn mid-task, a park nothing can
leave, a request the Lead has not answered - and each is one reading here: what a unit showed at its
last tick, how long it has shown nothing new, and the reason the run is still in flight. One wake is
written per thing the Lead must answer, and `attention.md` is rebuilt from the ledger rather than
appended to, so a restarted drive shows the same picture it showed before.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines and no class twenty-five public methods, and because attention is what every round of a run
reads before it acts.
"""

import copy
import json
import subprocess
from typing import Any

from .. import contracts, events, narrate
from .. import ledger as ledgers
from ..invariants import PARKED
from ..report_rules import require
from .records import is_agent, ready
from .report import cell
from .verdict import ATTENTION, VERDICTS, attention

ACCEPTED_STATES = ("ACCEPTED",)

PAYLOAD_WARNINGS = 2  # how often a hand-in is refused to its sender before the Lead is asked


def read_json(path):
    try:
        with open(path, encoding="utf-8") as stream:
            raw = stream.read(65537)
    except OSError as error:
        raise ValueError("cannot read %s: %s" % (path, error)) from None
    require(len(raw) <= 65536, "Input exceeds 64 KiB; provide a compact record, not a transcript")
    try:
        return json.loads(raw)
    except ValueError as error:
        raise ValueError("%s is not valid JSON: %s" % (path, error)) from None


def archive_retry_refusal(state):
    """(reason, way out): the Lead's `retry` for the archive is refused while the plan is not accepted.

    The archive waits for an accepted plan: a retry over a plan that is not accepted would build a branch and open a
    pull request nobody may merge. The reason names the plan's state; the way out names how the plan gets accepted."""
    reason = "the plan's state is %s, not ACCEPTED: the archive's retry is refused" % (
        state or "unknown"
    )
    way_out = (
        "the archive waits for an accepted plan: a plan is accepted once every agent ticket is COMPLETE; "
        "`run` again once they are"
    )
    return reason, way_out


def archive_retry_allowed(state):
    """Whether the archive may be rebuilt: only over an accepted plan."""
    return state in ACCEPTED_STATES


def plan_state_of(ledger_path):
    """The plan's state in its ledger, or None when the ledger holds none."""
    try:
        data = ledgers.snapshot(ledger_path) or {}
    except (OSError, ValueError):
        return None
    return (data.get("plan") or {}).get("state")


def pane_name(slug):
    """hc names allow a-z 0-9 - _ (1-32), so the pane is `orchestrate-<slug>`, not `orchestrate:<slug>`."""
    return ("orchestrate-" + slug)[:32].rstrip("-")


class Attention:  # noqa: PLR0904
    """The Lead's attention, mixed into the Driver: one reading per way a unit goes quiet.

    A member asks the driver it belongs to for everything else - the record, the log, the workers - and
    returns what one round of attention concluded.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    received: Any
    nudged = False  # a worker was put back to work while the runtime's report was read: the wait has not seen it

    def idle(self):
        """Back off after an iteration that dispatched, advanced and handled nothing: the runtime's wait returns
        at once when nothing is unfinished, so a PR awaiting merge or a parked gate would otherwise spin."""
        self.log("idle", interval=self.args.idle_interval)
        self.sleep(self.args.idle_interval)

    def idle_ticks(self, assignments):
        """Look at each agent the run waits on: one that shows nothing new (its state, its gate, its CI) for
        `--idle-minutes` needs the Lead. An agent that ended is not waited for here: `check_ended` hears of it."""
        moment = self.clock()
        for unit, task in self.units().items():
            task_id = task["packet"]["task_id"]
            # The Lead already has a `requested` unit, and a merge is a person's step (the pr-ready wake asked).
            skipped = (
                task["status"] in ("COMPLETE", "RELEASED")
                or task.get("gate") in PARKED
                or task.get("gate") in ("requested", "awaiting-merge")
            )
            if skipped:
                # A skipped unit that returns to `working` starts a fresh window, not the one it left.
                self.working_since.pop(task_id, None)
                self.ticks.pop(task_id, None)
                continue
            seen = self.watch(
                task_id,
                self.unit_assignments(unit),
                [task.get("gate"), task.get("ci_state")],
                assignments,
                moment,
            )
            if seen is None:
                continue
            _, minutes, working = seen
            if minutes < self.args.idle_minutes:
                continue
            waiting = "ci" if task.get("gate") in ("ready-for-ci", "reviewed") else "agent"
            self.log("idle", unit=unit, task=task_id, waiting=waiting, minutes=int(minutes))
            owed = self.owed(task)
            self.request(task_id, "idle", self.idle_reason(task, unit, minutes, working, owed))

    def watch(self, key, mine, shown, assignments, moment):
        """One step of the idle watchdog for one watched thing. `mine` is its assignment ids and `shown` what it
        showed besides its agents' states. None while the agent is busy (a long test is one turn: nothing to ask)
        or while this is the first look; else (ticks it showed nothing new, minutes since it last did, minutes it
        has been `working`)."""
        working = self.working_for(key, mine, assignments, moment)
        if working is not None and working < self.args.working_minutes:
            self.ticks.pop(key, None)
            return None
        agents = sorted(
            [a.get("assignment_id"), a.get("state"), a.get("runtime_state")]
            for a in assignments
            if a.get("assignment_id") in mine
        )
        signature = json.dumps([*shown, agents], sort_keys=True)
        previous = self.ticks.get(key)
        if previous is None or previous[0] != signature:
            self.ticks[key] = [signature, 0, moment]
            return None
        streak = previous[1] + 1
        self.ticks[key] = [signature, streak, previous[2]]
        return streak, (moment - previous[2]) / 60, working

    def unit_assignments(self, unit):
        with self.book() as data:
            return {
                a.get("assignment_id")
                for a in (data.get("agents") or {}).values()
                if a.get("unit") == unit
            }

    def owed(self, task):
        """(role, agent name) of the agent a unit waits on, or None: the reviewer owes a verdict and a worker its
        report, both handed in through the runtime."""
        if task.get("gate") == "reviewing":
            return ("reviewer", task["review"]["name"])
        if not task.get("gate") and task["status"] == "ACTIVE":
            return ("worker", task["packet"]["worker"])
        return None

    def idle_reason(self, task, unit, minutes, working, owed):
        """What the Lead reads when a unit showed nothing for `--idle-minutes`: who it waits on, how long it has
        been quiet and the way out."""
        text = "idle: %s showed no new state or report for %d minutes" % (unit, minutes)
        if working is not None:
            text += ", working for %d minutes" % working
        if owed:
            text += "; %s %s is live but has not reported: look at its terminal, then " % owed
        else:
            text += "; "
        return text + "decide %s %s retry | answer | skip" % (self.plan_path, unit)

    def working_for(self, key, mine, assignments, moment):
        """How long the runtime has reported one of these assignments `working`, or None when it does not."""
        if not any(
            a.get("assignment_id") in mine and a.get("runtime_state") == "working"
            for a in assignments
        ):
            self.working_since.pop(key, None)
            return None
        since = self.working_since.setdefault(key, moment)
        return (moment - since) / 60

    def receive(self, result):
        """Turn the runtime's reports into gate stages. Agents are told apart by assignment id only: the runtime's
        messages carry no name, and a resumed worker keeps its name across assignments."""
        self.received = bool(result.get("messages"))
        self.nudged = False
        self.record_usage(result.get("assignments") or [])
        for message in result.get("messages") or []:
            kind, assignment_id = message.get("kind"), message.get("assignment_id")
            if not assignment_id or kind not in ("complete", "blocked", "decision", "failed"):
                continue
            text = message.get("text") or ""
            if kind != "complete":
                self.on_agent_attention(assignment_id, "exited" if kind == "failed" else kind, text)
                continue
            closing, held, handin, verdict = self.complete_message(assignment_id, text)
            if closing:
                self.resolved_by_worker(closing, assignment_id)
            if held:
                self.hold(held[0], assignment_id, "complete", held[1])
            if handin:  # the report the call carried is read now, not at a later round
                self.take_report(handin, text)
            if verdict:  # so is a verdict
                self.take_verdict(verdict, text)
        self.check_ended(result.get("assignments") or [])

    def step_outcome(self, task, assignment_id, text):
        """What one complete does to one task: ("handin", id), ("verdict", id), ("closing", id), ("held", (id, text)),
        or None when the complete is not this task's."""
        if task["status"] in ("COMPLETE", "RELEASED"):
            return None
        gate = task.get("gate")
        if gate == "reviewing" and task["review"].get("assignment_id") == assignment_id:
            return ("verdict", task["packet"]["task_id"])
        if not gate and task.get("assignment_id") == assignment_id:
            return ("handin", task["packet"]["task_id"])
        if gate == "requested" and self.escalated_reviewer(task, assignment_id):
            return ("closing", task["packet"]["task_id"])
        if gate == "requested" and task.get("assignment_id") == assignment_id:
            return ("held", (task["packet"]["task_id"], text))
        return None

    def complete_message(self, assignment_id, text):
        """(the request it resolved, the request it held, the hand-in to read, the verdict to read) after one agent's
        complete, booked in one write."""
        closing = held = handin = verdict = None
        with self.book() as data:
            for task in data["tasks"].values():
                outcome = self.step_outcome(task, assignment_id, text)
                if outcome is None:
                    continue
                what, value = outcome
                if what == "handin":
                    handin = value
                elif what == "verdict":
                    verdict = value
                elif what == "closing":
                    task["gate"] = (
                        "reviewing"  # the verdict the reviewer finished counts, decided or not
                    )
                    closing = verdict = value
                else:
                    held = value
                break
            else:
                narrate.say(
                    "warn",
                    None,
                    "a complete from assignment %s matches no open step; ignored" % assignment_id,
                )
        return closing, held, handin, verdict

    def resolved_by_worker(self, closing, assignment_id):
        """A reviewer's complete resolved the request before the Lead decided: the record says which one."""
        self.close_request(
            closing,
            "resolved by the worker: the reviewer reported complete before the Lead decided",
        )
        self.write_status(self.unit_of(closing).upper(), "IN PROGRESS")
        self.log(
            "request-resolved",
            unit=self.unit_of(closing),
            task=closing,
            by="worker",
            assignment_id=assignment_id,
        )

    def escalated_reviewer(self, task, assignment_id):
        """True when `assignment_id` is the unit's reviewer, the unit's open request is the idle request raised while
        that reviewer was working, and the Lead has not decided it: the verdict the reviewer finished still counts.
        Once the Lead decides, the unit has moved on and a late `complete` is not acted on (One idle watchdog)."""
        request = task.get("request") or {}
        review = task.get("review") or {}
        if not (
            request.get("kind") == "idle"
            and request.get("from_gate") == "reviewing"
            and review.get("assignment_id") == assignment_id
        ):
            return False
        try:
            document = contracts.load(request["path"], contracts.validate_request)
        except (contracts.ContractError, OSError, ValueError):
            return False
        return document["status"] == "open"

    def check_ended(self, assignments):
        """Attention from assignment states and the runtime's verdicts, as `wait` reports them: `failed`, `blocked`
        or `decision`, a `working` assignment whose turn ended (idle, done: `silent`) or needs a person
        (unreported, error), and an attempt the runtime watched produce nothing. The last is the answer to the
        question the idle tick asks from screen freshness, so it is acted on when `wait` delivers it: the tick would
        log a worker the runtime has given up on as waiting, for two ticks (20 minutes)."""
        for assignment_id, kind, text, silent in attention(assignments):
            self.on_agent_attention(assignment_id, kind, text, quiet=True, silent=silent)

    def on_agent_attention(self, assignment_id, kind, text="", quiet=False, silent=False):
        """An agent reported `blocked` or `decision`, or its assignment failed or ended with no report. Each event is
        handled once: a unit that already has a gate (reviewing, requested, ...) ignores it, except that a `blocked` or
        `decision` from the worker of a unit with an open request is held (S7). A reviewer whose assignment ended
        moves its gate on, so a missing file is reported as a contract error.

        `silent` is a live agent whose turn ended (runtime idle or done): it may be waiting on a background job, so
        it is not an exit. A worker whose report is already on disk advances; every other role keeps waiting for its
        own `complete`, and only the idle watchdog asks the Lead (One idle watchdog)."""
        hit = self.attention_hit(assignment_id)
        if not hit:
            return self.unclaimed_attention(assignment_id, kind, text, quiet=quiet, silent=silent)
        task_id, role, name = hit
        reason = "%s %s%s" % (name, ATTENTION[kind], ": " + text if text else "")
        return self.answer_attention(task_id, role, assignment_id, kind, reason, silent)

    def attention_hit(self, assignment_id):
        """(task id, role, name) of the step this assignment belongs to, or None when no open step holds it."""
        hit = None
        with self.book() as data:
            for task_id, task in data["tasks"].items():
                gate = task.get("gate")
                if task["status"] in ("COMPLETE", "RELEASED"):
                    continue
                if gate == "requested" and task.get("assignment_id") == assignment_id:
                    hit = (task_id, "held", task["packet"]["worker"])
                elif gate == "reviewing" and task["review"].get("assignment_id") == assignment_id:
                    hit = (task_id, "reviewer", task["review"]["name"])
                elif not gate and task.get("assignment_id") == assignment_id:
                    hit = (task_id, "worker", task["packet"]["worker"])
        return hit

    def unclaimed_attention(self, assignment_id, kind, text, quiet=False, silent=False):
        """An assignment no open step holds: one that was already handled."""
        if kind in VERDICTS or silent:
            # A verdict or a silent turn on an assignment no step of the drive owns: nothing here waits on it.
            return None
        if not quiet:
            narrate.say(
                "info",
                None,
                "assignment %s %s; already handled, ignored" % (assignment_id, ATTENTION[kind]),
            )

    def answer_attention(self, task_id, role, assignment_id, kind, reason, silent):
        """What the step does with the attention its own agent raised."""
        if role == "held":
            return (
                self.hold(task_id, assignment_id, kind, reason)
                if kind in ("blocked", "decision")
                else None
            )
        if role == "reviewer" and (silent or kind == "exited"):
            return self.review_ended(task_id, reason)
        if silent and role != "worker":
            return None
        if role == "worker":
            if silent:
                return None
            if kind == "exited" and not self.task(task_id).get("resumed_empty"):
                # the first time a worker ends without a report it is resumed; only a second time asks the Lead
                self.mark(task_id, resumed_empty=True)
                return self.nudge(
                    task_id,
                    "You ended without a report; hand it in. Your task is %s (task_id `%s`) and its work is in "
                    "your checkout: finish it if it is not finished, then hand in the report JSON as the text "
                    "of your agent-runtime `report` call."
                    % (self.run_dir / self.unit_of(task_id) / "task.md", task_id),
                )
            return self.request(task_id, kind, reason)
        self.request(task_id, role, reason)

    def wake(self, kind, unit, text, key=None):
        """Append one wake for the Lead, unless a wake of this kind with the same `key` is already there."""
        path = self.run_dir / "wakes.jsonl"
        if key is not None and any(
            w.get("kind") == kind and w.get("key") == key for w in events.read(path)
        ):
            return False
        events.append(path, kind, unit=unit, text=text, key=key)
        self.log("wake", wake=kind, unit=unit, text=text)
        self.alert(text)
        return True

    def alert(self, text):
        """Tell the terminal that launched the run about a new wake, so a Lead that ended its turn hears of it.
        `hc send`, never `--force` or `interrupt`: the notice queues behind the Lead's turn instead of ending it.
        The wake is already on disk, so a send that fails is logged and the run goes on."""
        # A driver built for one call (the wake tests) has no parsed args, and so no terminal to tell.
        terminal = getattr(getattr(self, "args", None), "notify_terminal", None)
        if not terminal:
            return
        line = "%s needs you: %s" % (pane_name(self.slug), " ".join(str(text).split()))
        try:
            result = subprocess.run(
                ["hc", "send", terminal, line[:1000]],
                capture_output=True,
                text=True,
                timeout=30,
            )
            error = None if result.returncode == 0 else (result.stderr or result.stdout).strip()
        except (OSError, subprocess.TimeoutExpired) as caught:
            error = str(caught)
        if error is not None:
            narrate.say("warn", None, "could not wake the Lead in %s: %s" % (terminal, error))
            self.log("notify-failed", terminal=terminal, error=error[:500])

    def refresh_attention(self):
        """Rewrite attention.md from the ledger's `needs you` tickets and its park. Appending left a completed
        ticket's line behind, so the file is rebuilt on every tick and never edited."""
        with self.book() as data:
            statuses = dict(data.get("ticket_status") or {})
            park = copy.deepcopy(data.get("park"))
        lines = [
            "- %s (person): %s" % (ticket_id, " ".join(text.split(":", 1)[1].split()))
            for ticket_id, text in statuses.items()
            if str(text).lower().startswith("needs you:")
        ]
        body = "# Needs you: %s\n\n`status <plan> --json` carries the same reason.\n\n" % self.slug
        text = body + "".join(line + "\n" for line in lines)
        if park:
            text += narrate.park_section(park)
        (self.run_dir / "attention.md").write_text(text, encoding="utf-8")

    def attend_people(self, plan):
        """A person-owned ticket that becomes ready reads `needs you: <what it needs>` and waits for the user."""
        by_id = {t["#"]: t for t in plan["tickets"]}
        count = 0
        for ticket in plan["tickets"]:
            if is_agent(ticket) or not ready(ticket, by_id):
                continue
            needs = cell(ticket["What it delivers"] or "your action")
            self.write_status(ticket["#"], "needs you: " + needs)
            narrate.say("ask", None, "needs you: %s" % needs, task=ticket["#"])
            # attention.md and the pane are not read by a Lead that ended its turn; only a wake reaches it.
            self.wake(
                "person", ticket["#"], "person %s: %s" % (ticket["#"], needs), key=ticket["#"]
            )
            count += 1
        return count
