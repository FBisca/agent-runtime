"""The dispatch loop: one object the plan, the ledger and the runtime are held by.

A drive reads the plan's approved snapshot, takes one round, and keeps no state of its own: the plan,
the ledger, the run directory and the runtime hold everything a restarted drive must know. What the
object publishes is composed from the modules beside it - the routing in `routing.py`, the report gate
and its findings in `findings.py`, the Lead's answers and attention, the archive, the pull request lane
and the workers - so this module holds only the object and the handful of reads that decide what the
round acts on.

It lives beside `__init__.py` rather than in it because the package is a facade: `__init__.py`
re-exports what the modules define and the engine lives where its collaborators live.
"""

import copy
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .. import approval_recovery, events, narrate
from .. import ledger as ledgers
from .. import plan as yaml_plan
from ..invariants import PARKED
from ..plan import PlanError
from ..timeutil import iso as now
from .acceptance import Acceptance
from .answers import Answers
from .archive import Archive, ArchiveSurface
from .book import Book, BookSurface
from .briefs import Briefs
from .coverage import Coverage
from .findings import Findings
from .gates import Gates
from .lifecycle import Lifecycle
from .pull import Pull, PullSurface
from .report import cell
from .routing import Routes, load_approved, require_yaml
from .runtime import runtime_call
from .spawn import Spawning, SpawnSurface
from .spec import SpecSections
from .state import state_layout
from .watch import Attention


def _engine():
    """The package this module is split from, asked when the plan's checkout is read.

    A test answers the git seam by patching `driver.run`; a name bound here at
    import time would answer the real process and ignore it.
    """
    return sys.modules[__name__.rpartition(".")[0]]


class Driver(
    Routes,
    Answers,
    Briefs,
    Attention,
    Acceptance,
    Lifecycle,
    Findings,
    Coverage,
    ArchiveSurface,
    PullSurface,
    SpawnSurface,
    BookSurface,
    SpecSections,
):
    """The dispatch loop. It keeps no state of its own: the plan, the ledger and the runtime hold it.

    The surface it publishes is composed from the modules beside it: `driver/routing.py` and its
    siblings hold the engine's collaborators, and this class binds them onto one object.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    args: Any
    plan_path: Any
    repo: Any
    errors: Any
    spawned: Any
    failed: Any
    stuck: Any
    stranded: Any
    released: Any
    unfinished: Any
    reconciled: Any
    touched: Any
    received: Any
    sleep: Any
    last_plan: Any
    discarded: Any
    parked_since: Any
    asked_at: Any
    park_asks: Any
    parked: Any
    recovered: Any
    ticks: Any
    last_tick: Any
    working_since: Any
    run_logs: Any
    _archive: Any
    book: Any
    gates: Any
    spawning: Any
    _pull: Any

    def __init__(self, args):
        self.args = args
        self.plan_path = Path(args.plan).resolve(strict=True)
        self.repo = (
            Path(args.repo).resolve()
            if args.repo
            else Path(
                _engine().run(
                    ["git", "-C", str(self.plan_path.parent), "rev-parse", "--show-toplevel"]
                )
            ).resolve()
        )
        self._run_state()
        self._plan_state()
        self._clock_state()
        # The plan's end of life: the park, the archive and the crash report go through the Archive below.
        self._archive = Archive(self)
        # The durable record: every read and write of the ledger goes through the Book below.
        self.book = Book(self)
        # The state machine: every gate a task waits at is moved by one step of the Gates below.
        self.gates = Gates(self)
        # The workers: every spawn, resume, hand-over and swap goes through the Spawning below.
        self.spawning = Spawning(self)
        # The pull request lane: every look at a pull request, its checks and its merge goes through the Pull below.
        self._pull = Pull(self)

    def _run_state(self):
        """What one round accumulates: the errors it books, the agents it touched, the work it has left."""
        self.errors = []
        self.spawned = []
        self.failed = set()
        self.stuck = set()
        self.stranded = (
            set()
        )  # tasks the last reconciliation could not settle: the run must not finish over them
        self.released = 0  # units an amendment released: work in the round it was released
        self.unfinished = set()  # the assignment ids the runtime's last `wait` listed as unfinished
        self.reconciled = False
        self.touched = (
            set()
        )  # the agents this drive spawned, attached or resumed: no dead drive left them
        self.receipt = None
        self.received = False
        self.sleep = time.sleep  # injectable so tests do not really sleep

    def _plan_state(self):
        """The plan the drive holds, and the drain or park it is in while the file changes under it."""
        self.last_plan = None
        self.discarded = (
            False  # the plan folder was deleted by this drive: the plan it last read is the plan
        )
        self.model: yaml_plan.Plan | None = None  # the approved plan/1 model of a YAML plan
        self.approval: yaml_plan.Approval | None = (
            None  # the approval block of the YAML plan the loop last read
        )
        self.drain = None  # {"cause", "error"} while the plan is edited or unloadable and no new worker starts

    def _clock_state(self):
        """The drive's own clock: the park it waits in, the ticks it counts and what a unit last showed."""
        self.parked_since = None  # when the park began (wall clock): the ledger's `since`
        self.asked_at = (
            None  # the drive's clock at the last ask: a park with nothing in flight asks again
        )
        self.park_asks = 0  # how many times the park has asked the Lead again
        self.parked = []  # the units the run ended with parked: the exit names them
        self.recovered = approval_recovery.Recovered()  # the approval a lost span was repaired with
        self.ticks = {}  # unit -> [what it showed at the last tick, how many ticks it showed nothing new]
        self.last_tick = self.clock()
        self.working_since = {}  # task id -> when its agent was first seen `working`, unbroken
        self.run_logs = {}  # run id -> its failed-step log, once a round: the findings reuse the readiness read

    @property
    def pull(self):
        """The pull request lane: every look at a pull request, its checks and its merge.

        The lane keeps no state of its own - the record, the log and the per-round view are the Driver's - so
        the one a Driver holds is created on the first ask, and a Driver a test stubs without running `__init__`
        still answers the names the package publishes.
        """
        lane = self.__dict__.get("_pull")
        if lane is None:
            lane = self.__dict__["_pull"] = Pull(self)
        return lane

    @property
    def archive(self):
        """The plan's end of life: the park it waits in, the archive that closes it, the park reopened.

        The lane keeps no state of its own - the record, the plan and the processes are the Driver's - so the
        one a Driver holds is created on the first ask, and a Driver a test stubs without running `__init__`
        still answers the names the package publishes.
        """
        lane = self.__dict__.get("_archive")
        if lane is None:
            lane = self.__dict__["_archive"] = Archive(self)
        return lane

    def log(self, kind, **fields):
        events.append(self.run_dir / "events.jsonl", kind, **fields)

    def read_plan(self):
        """The plan as the run gates and dispatches it: the approved snapshot, until an amendment is adopted.

        A plan edited since its approval, or one that no longer loads, does not stop a run in flight: the run drains
        on the last approved snapshot (`begin_drain`) until the file is back to its approved bytes or carries a valid
        approval of its own (`adopt_amendment`). Before the run holds an approved plan there is nothing to drain on, so the
        refusal stands."""
        if self.discarded and self.last_plan is not None:
            return (
                self.last_plan
            )  # its folder is gone by design; the approved model was read before
        started = getattr(self, "run_dir", None) is not None
        try:
            model = self._load_plan()
        except PlanError as error:
            model = approval_recovery.recover_plan(self)
            if model is None:
                if self.model is None or not started:
                    raise
                self.begin_drain(error)
                return self.legacy_of(self.model)
        return self._settle_plan(model, self.model is not None and started)

    def _load_plan(self):
        """The plan as the file holds it, refused when it no longer loads or its slug changed."""
        require_yaml(self.plan_path)
        model = load_approved(
            self.plan_path,
            repo=self.repo,
            state_dir=self.args.state_dir,
            require_approval=True,
        )
        if self.model is not None and model.slug != self.model.slug:
            raise PlanError(
                "the plan's slug changed from %s to %s: the slug names the run's ledger, so no amendment can "
                "change it" % (self.model.slug, model.slug)
            )
        return model

    def _settle_plan(self, model, settled):
        """Hold the plan just read: adopt it as an amendment, end a drain, or take it as the plan of a new run."""
        found = model.approval.digest if model.approval else None
        if settled and found != self.approved_digest():
            held = self.model
            self.model, self.approval = model, model.approval
            self.adopt_amendment(held, self.legacy_of(model))
        elif settled and self.drain:
            self.end_drain()
        elif not settled:
            if found := yaml_plan.discard_problems(model, self.plan_path, self.repo):
                raise PlanError("; ".join(found))  # a run starts only on a plan it may delete
            self.model, self.approval = model, model.approval
        return self.legacy_of(model)

    def legacy_of(self, model):
        """The loop's view of `model`, with the ticket statuses the ledger holds."""
        ledger_path = state_layout(self.args.state_dir, self.plan_path, self.repo, model.slug)[0]
        statuses = {}
        data = ledgers.snapshot(ledger_path)  # a read: it never waits for a writer
        if data is not None:
            statuses = dict(data.get("ticket_status", {}))
        return model.legacy(statuses)

    def approved_digest(self):
        """The digest of the approval this run gates against; the drain and adoption code only runs once a plan is
        held, so a missing one is a bug, not a state."""
        if self.approval is None:
            raise PlanError("the run holds no approved plan yet")
        return self.approval.digest

    def begin_drain(self, error):
        """Drain once per edit: one event and one wake, however many reads find the file still changed."""
        if self.drain:
            return
        cause = "edited" if str(error).startswith("approval: changed") else "unloadable"
        # The load error names `plan approve` as its way out; a live run's way out is `plan amend`.
        text = re.sub(
            r"(; )?approval: after fixing \S+, run agent-orchestrator plan approve \S+",
            "",
            str(error),
        )
        text = " ".join(re.sub(r" — run agent-orchestrator plan approve \S+", "", text).split())[
            :500
        ]
        self.drain = {"cause": cause, "error": text}
        self.parked_since = now()
        self.asked_at = self.clock()
        self.park_asks = 0
        park = self.park_here()
        self.log("drain", state="draining", cause=cause, error=text, digest=self.approved_digest())
        narrate.say("warn", None, "draining: the plan is %s: %s" % (cause, text))
        self.wake("drain", None, narrate.park_ask(park), key="drain")

    def end_drain(self):
        """The file is back to the approved bytes."""
        self.log(
            "drain",
            state="ended",
            cause=(self.drain or {}).get("cause"),
            digest=self.approved_digest(),
        )
        narrate.say("info", None, "the plan is back to its approved bytes: the drain ended")
        self.drain = None
        self.end_park()

    def loop(self):
        idle = False
        while True:
            acted, dispatched = self._loop_round()
            # A drain has no timeout: the run waits for the Lead to amend the plan or restore it.
            if idle and not dispatched and not acted and not self.awaiting() and not self.drain:
                recovered = self._loop_recover()
                if recovered is None:
                    return
                dispatched += recovered
            idle = self._loop_settle(dispatched, acted)

    def _loop_round(self):
        """One round: the plan read, its gates advanced and its tickets dispatched. (acted, dispatched)."""
        self.read_plan()  # Integrity precedes gates, which can also spawn workers.
        acted, self.released = self.advance() + self.released, 0  # a release is work too
        plan, valid = self.current_plan()
        if not valid:
            # A drain: running attempts, reviews and the merge bookkeeping go on, and no worker starts (a first
            # dispatch, a reopened unit, the archive, a verification of an outcome that may be about to change).
            self.refresh_attention()
            return acted, 0
        acted += self.accept(plan)
        dispatched = (
            self.reopen_parked(plan)
            + self.dispatch_ready(plan)
            + self.attend_people(plan)
            + self.start_archive(plan)
        )
        self.refresh_attention()
        return acted, dispatched

    def _loop_recover(self):
        """A quiet round: reconcile once. The work it recovered, or None when the run ends here."""
        dispatched = self.reconcile(finishing=True)
        if dispatched:
            return dispatched
        # A run that ends with units parked says why it ends; it did not run out of work.
        self.parked = self.park_at_exit()
        return None

    def _loop_settle(self, dispatched, acted):
        """Wait on the runtime, take what it reported, and answer whether the run has gone idle."""
        wait = {"target": "all", "timeout": self.args.wait_seconds}
        if self.receipt:
            wait["receipt"] = self.receipt
        result = runtime_call(self.lead, "wait", wait, timeout=self.args.wait_seconds + 60)
        self.receipt = result.get("receipt")
        self.read_plan()  # an edit made while the run waited is seen before anything acts on the answer
        if self.park_due():
            self.ask_parked()
        self.receive(result)
        self.idle_ticks(result.get("assignments") or [])
        self.unfinished = {i for i in result.get("unfinished") or [] if isinstance(i, str)}
        recovered = 0
        if not self.reconciled:  # a drive that starts on a ledger a dead one left looks at it once
            self.reconciled = True
            recovered = self.reconcile()
            dispatched += recovered
        # `unfinished` lists assignment ids. A parked unit's assignment stays open in the runtime and makes `wait`
        # return at once: it is not work, so a run with only parked units left ends.
        parked = self.parked_assignments()
        # An attempt recovered after this wait is one it could not list: the next wait settles it, not `status`.
        idle = not recovered and not [
            entry for entry in result.get("unfinished") or [] if entry not in parked
        ]
        if not (dispatched or acted or self.received):
            self.idle()
        return idle

    def adopt_amendment(self, held, plan):
        """The plan carries a valid approval other than the one this run holds: gate and dispatch against it from
        now on. `held` is the model the run held until now."""
        if self.model is None:
            raise PlanError("the run holds no plan to adopt an amendment into")
        digest = self.approved_digest()
        amendment = next((a for a in reversed(self.model.amendments) if a.digest == digest), None)
        self.log(
            "amended",
            prev_digest=held.approval.digest,
            digest=digest,
            reason=amendment.reason[:500] if amendment else None,
            tickets=amendment.tickets if amendment else None,
            drained=True if self.drain else None,
        )
        narrate.say("info", None, "adopted the amended plan %s" % digest[:12])
        self.drain = None
        self.last_plan = plan
        self.end_park()  # the plan the drive could not use came back as an amendment, so the park is over
        with self.book() as data:
            ledgers.record_approval(data, self.held_approval())
            self.resume_plan_state(
                data
            )  # a ticket the amendment added reopens a plan that was accepted
        self.release_amendment(self.resume_changed(held, plan), plan)

    def resume_changed(self, held, plan):
        """Each open attempt (working or parked) whose ticket's `paths`, `satisfies` or `delivers` the amendment
        changed gets its next packet and its worker is resumed on it; any other attempt is left alone."""
        handed = {}
        if self.model is None:
            return handed
        before = {t.id.lower(): t for t in held.tickets}
        changed = {}
        for ticket in self.model.tickets:
            old = before.get(ticket.id.lower())
            if old and any(
                getattr(old, field) != getattr(ticket, field)
                for field in ("paths", "satisfies", "delivers")
            ):
                changed[ticket.id.lower()] = (old, ticket)
        if not changed:
            return handed
        rows = {t["#"].lower(): t for t in plan["tickets"]}
        with self.book() as data:
            open_ = [
                (task_id, copy.deepcopy(task))
                for task_id, task in data["tasks"].items()
                if task["packet"].get("unit") in changed
                and task["status"] not in ("COMPLETE", "RELEASED")
                and (not task.get("gate") or task.get("gate") in PARKED)
                and task.get("runtime_id")
            ]
        for task_id, task in open_:
            unit = task["packet"]["unit"]
            try:
                handed[task_id] = self.next_packet(task_id, task, rows[unit], plan, *changed[unit])
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                handed[task_id] = (
                    None  # a parked unit stays parked: its worker still has the old contract
                )
                self.log("error", unit=unit, task=task_id, gate="amend", error=str(error)[:500])
                narrate.say("fail", None, "could not hand %s its new packet: %s" % (task_id, error))
                self.wake(
                    "amend-failed",
                    unit,
                    "amend-failed %s: its worker could not be given the amended contract: %s -> look at its "
                    "terminal" % (unit, cell(error, 300)),
                    key="%s:%s" % (task_id, self.approved_digest()),
                )
        return handed

    def current_plan(self):
        """(plan, open): the plan the run holds, and whether it may start work. A plan edited since its approval is
        drained, not dispatched: `open` is False and nothing new starts until the edit is amended or undone."""
        self.last_plan = self.read_plan()
        return self.last_plan, not self.drain

    @property
    def layout(self):
        """The plan's resolver: what its layout is, which files are its own, where they sit in the repository."""
        return yaml_plan.PlanLayout(self.plan_path, self.repo)

    def plan_state(self):
        with self.book() as data:
            return (data.get("plan") or {}).get("state")

    def plan_model(self):
        """The approved plan/1 model; an archive only runs for a YAML plan, which holds one."""
        assert self.model is not None
        return self.model

    def contract(self):
        """(model, approval) of the approved YAML plan this drive holds."""
        assert self.model is not None and self.approval is not None
        return self.model, self.approval

    clock = staticmethod(time.monotonic)
