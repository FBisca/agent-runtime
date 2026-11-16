"""Sending to the runtime: the one door every worker is started, resumed, handed over and swapped through.

A worker is named, never created twice: `ensure_agent` books an intent in the ledger under a fresh UUID that
is also the runtime's operation id, starts (or re-attaches to) the agent of that name, and binds what the
runtime answered. An intent with no binding means the drive died in between, and the next round asks the
runtime what became of the key instead of sending again. `hand_round` and `hand_over` close one attempt and
put a worker on its successor; `swap_due` and `handoff` replace an agent that ran short of context; and
`stop_agent` ends the ones a merged or paused unit no longer needs.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand lines,
and because everything that reaches the runtime to start or resume a worker is one rule with one home.
"""

import subprocess
import sys
import uuid

from .. import narrate, timeutil
from ..runtime_client import OPERATION_ADOPTED, operation_state, refuses_operation_ids
from .records import context_verdict
from .runtime import runtime_call


def engine():
    """The package this module is split from, asked when a send runs.

    A send calls helpers the engine publishes - `assignment_of`, and the record's `claim`, `bind` and
    `release` - and a test answers a seam by patching `driver.<name>` on that package. A name bound here at
    import time would answer the real one and ignore the seam the test installed, so the package is asked
    each time, the way `Gates` asks for its own.
    """
    return sys.modules[__name__.rpartition(".")[0]]


class Spawning:
    """The run's door to the runtime, held by the Driver: one method per way a worker is put to work.

    A member asks the driver it belongs to for everything else - the record, the plan, the log - so a
    transition reads as the one thing it sends, and a member added here carries no second copy of a rule
    the engine already owns.
    """

    def __init__(self, driver):
        self.driver = driver

    def __getattr__(self, name):
        """The engine behind the door: the record, the log and the Lead's request are the Driver's own, and
        only the sends themselves live here."""
        return getattr(self.driver, name)

    # ---- every spawn and resume goes out under an intent ----------------------------------------------------

    # A row in the ledger's `intents`, keyed by a fresh UUID that is also the runtime's operation id, booked
    # before the call. The readable `unit/attempt/operation` path is metadata for the person reading the ledger.
    # A restart that finds an intent still open asks the runtime what became of its key (`call operation`), and
    # never sends again for a delivery it cannot establish.

    def book_intent(self, operation, name, role, unit, attempt):
        """Book the intent for one spawn or resume and return its operation id."""
        operation_id = str(uuid.uuid4())
        path = "%s/%s/%s" % (unit, attempt, operation)
        with self.book() as data:
            data.setdefault("intents", {})[operation_id] = {
                "name": name,
                "role": role,
                "unit": unit,
                "attempt": attempt,
                "operation": operation,
                "path": path,
                "at": timeutil.iso(),
                "state": "open",
                "assignment_id": None,
            }
        self.log(
            "intent-booked",
            name=name,
            role=role,
            unit=unit,
            operation=operation,
            operation_id=operation_id,
            route=path,
        )
        return operation_id

    def settle_intent(self, operation_id, state, assignment_id=None, why=None):
        """Close an intent as `bound` (the runtime answered), `adopted` (a restart found its assignment), `absent` (the
        runtime never saw the key), `refused` (the runtime said no) or `released` (the Lead decided)."""
        with self.book() as data:
            row = (data.get("intents") or {}).get(operation_id)
            if row is None:
                return
            row["state"] = state
            row["settled_at"] = timeutil.iso()
            if assignment_id:
                row["assignment_id"] = assignment_id
            if why:
                row["why"] = str(why)[:300]
        self.log(
            "intent-settled", operation_id=operation_id, state=state, why=why and str(why)[:300]
        )

    def release_intents(self, operations, why):
        """The Lead decided, or the unit was given up: these intents no longer stand for a send that may land."""
        for operation_id in operations or ():
            self.settle_intent(operation_id, "released", why=why)

    def send(self, operation, name, role, unit, attempt, arguments):
        """One spawn or resume under its own intent; (the runtime's answer, its assignment id). A runtime that does
        not take operation ids is never sent the call without one: the Lead is asked instead."""
        # The package's own class: `spawn.py` cannot import it at module load, and `send` raises it.
        from . import IntentUnsettled

        operation_id = self.book_intent(operation, name, role, unit, attempt)
        try:
            result = runtime_call(self.lead, operation, dict(arguments, operation_id=operation_id))
        except subprocess.TimeoutExpired:
            raise  # it may have started: the intent stays open, and a restart looks the key up
        except (ValueError, OSError) as error:
            self.settle_intent(operation_id, "refused", why=error)
            if refuses_operation_ids(error):
                raise IntentUnsettled(
                    "the runtime does not take operation ids (%s), so nothing was sent to %s and nothing will be "
                    "sent without one: retry once the runtime supports them, or skip"
                    % (error, name)
                ) from None
            raise
        assignment_id = engine().assignment_of(result)
        self.settle_intent(operation_id, "bound", assignment_id=assignment_id)
        return result, assignment_id

    def reconcile_intent(self, name):
        """What became of an intent the driver left open for `name`: (assignment id, intent) when the runtime holds
        the assignment (it is adopted), None when nothing is open or the runtime never saw the key (the caller acts
        again). A delivery the runtime cannot vouch for raises `IntentUnsettled`: sending again could do it twice."""
        # The package's own class: `spawn.py` cannot import it at module load, and a lookup raises it.
        from . import IntentUnsettled

        with self.book() as data:
            found = sorted(
                (row["at"], key, dict(row))
                for key, row in (data.get("intents") or {}).items()
                if row["name"] == name and row["state"] in ("open", "held")
            )
        adopted = None
        for _, key, intent in found:
            if intent["state"] == "held":  # the Lead has been asked: no lookup each round
                raise IntentUnsettled(self.unsettled_text(intent, "held", None), [key])
            try:
                answer = runtime_call(self.lead, "operation", {"operation_id": key})
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                answer = {"state": "unknown", "reason": "the lookup failed: %s" % error}
            state = operation_state(answer)
            assignment_id = answer.get("assignment_id") if isinstance(answer, dict) else None
            self.log("intent-lookup", name=name, operation_id=key, state=state)
            if state == "absent":
                self.settle_intent(key, "absent")
            elif state in OPERATION_ADOPTED and isinstance(assignment_id, str) and assignment_id:
                self.settle_intent(key, "adopted", assignment_id=assignment_id)
                adopted = (assignment_id, intent)
            else:
                raise IntentUnsettled(
                    self.unsettled_text(intent, state, (answer or {}).get("reason")), [key]
                )
        return adopted

    @staticmethod
    def unsettled_text(intent, state, reason):
        return "the %s may or may not have reached %s (the runtime answers %s%s): retry or skip" % (
            intent["operation"],
            intent["name"],
            state,
            "; " + str(reason) if reason else "",
        )

    def adopt(self, name, role, unit, assignment_id, intent):
        """Bind an assignment a restart found under an intent, as the send that made it would have."""
        with self.book() as data:
            record = data.setdefault("agents", {}).setdefault(
                name, {"role": role, "unit": unit, "intent_at": intent["at"]}
            )
            record.update(assignment_id=assignment_id, bound_at=timeutil.iso(), how="adopt")
            address = record.get("address")
        self.touched.add(name)
        self.log("agent", name=name, role=role, unit=unit, how="adopt", assignment_id=assignment_id)
        return address

    def hold_intent(self, task_id, error):
        """An intent the driver cannot settle: the unit's Lead is asked, and a `retry` sends again under a new key."""
        with self.book() as data:
            for key in error.operations:
                row = (data.get("intents") or {}).get(key)
                if row and row["state"] == "open":
                    row["state"] = "held"
        self.request(task_id, "intent", str(error), menu=("retry", "skip"), intent=error.operations)

    def agent_live(self, name):
        """True when the runtime knows a live agent of this name. A lookup that fails is an error, not `absent`: the
        caller must not spawn on a guess."""
        return runtime_call(self.lead, "status", {"name": name}).get("state") not in (
            None,
            "exited",
            "unknown",
        )

    def open_intent(self, name):
        with self.book() as data:
            known = (data.get("agents") or {}).get(name)
        return bool(known) and known.get("assignment_id") is None

    def spawn_args(self, spawn, role):
        """The spawn arguments, plus the run's role and profile when the Lead named them.

        Without a named role, implementation workers explicitly select implementer; review gates use no role.
        A named profile replaces the CLI family in the spawn: the runtime resolves it to its own CLI and env.
        A named brain backend wins over the profile for the gates that spawn on it."""
        agent = getattr(self.args, "agent", None) or ("implementer" if role == "worker" else None)
        profile = getattr(self.args, "profile", None)
        brain = getattr(self, "brain_cli", None)
        if profile and not (brain and spawn.get("cli") == brain):
            spawn = dict(spawn, cli=profile)
        return dict(spawn, agent=agent) if agent else spawn

    def ensure_agent(self, name, role, unit, spawn, fresh=False, attempt=None):
        """Put one agent under `name` and return (address, assignment_id).

        `spawn` is the runtime's spawn arguments. A name the ledger already knows is looked up first and, when live,
        resumed with the same task instead of spawned (that is how a restart re-attaches). `fresh` says the caller
        already stopped the old agent of this name, so a bound record needs no lookup; an open intent is always
        looked up."""
        attempt = attempt or name
        adopted = self.reconcile_intent(name)
        if adopted:
            assignment_id, intent = adopted
            address = self.adopt(name, role, unit, assignment_id, intent)
            return address or assignment_id, assignment_id
        known = self._claim_agent(name, role, unit)
        recovering = bool(known) and known.get("assignment_id") is None
        live = self.agent_live(name) if known and (recovering or not fresh) else False
        if live and not recovering and self.swap_due(name, role, unit, known.get("assignment_id")):
            self.stop_agent(name, unit, "swap")
            live, spawn = False, dict(spawn, task=spawn["task"] + self.handoff(name, unit))
        result, assignment_id, how = self._send_agent(
            name, role, unit, spawn, attempt, (live, known)
        )
        returned = self._address_of(how, result, known)
        with self.book() as data:
            # Only what the runtime returned is an address; a reader must never take a fallback for a terminal.
            data["agents"][name].update(
                assignment_id=assignment_id, address=returned, bound_at=timeutil.iso(), how=how
            )
        self.touched.add(name)
        self.log("agent", name=name, role=role, unit=unit, how=how, assignment_id=assignment_id)
        # What a task binds under (`runtime_id`) must be set, so it falls back to the assignment or the name; that
        # key is not an address and the ledger does not call it one.
        return returned or result.get("assignment_id") or name, assignment_id

    def _claim_agent(self, name, role, unit):
        """The record a send starts from: the agent this name already had, or the intent this one books."""
        with self.book() as data:
            agents = data.setdefault("agents", {})
            known = agents.get(name)
            if not (known and known.get("assignment_id") is None):
                agents[name] = {
                    "role": role,
                    "unit": unit,
                    "intent_at": timeutil.iso(),
                    "assignment_id": None,
                }
        recovering = bool(known) and known.get("assignment_id") is None
        self.log("intent", name=name, role=role, unit=unit, recovering=True if recovering else None)
        return known

    @staticmethod
    def _address_of(how, result, known):
        """The address a send answers with: what the runtime returned, or the one a resumed terminal kept."""
        returned = result.get("address")
        if not returned and how == "attach":
            return (known or {}).get("address")
        return returned

    def _send_agent(self, name, role, unit, spawn, attempt, state):
        """One send under its intent: (the runtime's answer, its assignment id, how it was reached)."""
        live, known = state
        recovering = bool(known) and known.get("assignment_id") is None
        try:
            if live:
                (result, assignment_id), how = (
                    self.send(
                        "resume", name, role, unit, attempt, {"name": name, "task": spawn["task"]}
                    ),
                    "attach",
                )
            else:
                (result, assignment_id), how = (
                    self.send("spawn", name, role, unit, attempt, self.spawn_args(spawn, role)),
                    "spawn",
                )
        except subprocess.TimeoutExpired:
            raise  # it may have started: the open intent stays, and a restart looks the name up
        except (ValueError, OSError):
            if not recovering:  # the runtime refused: nothing started, so nothing is left to find
                self._forget_agent(name, known)
            raise
        return result, assignment_id, how

    def _forget_agent(self, name, known):
        """A refused send leaves the record as it found it: the agent it knew, or no agent at all."""
        with self.book() as data:
            if known:
                data["agents"][name] = known
            else:
                data["agents"].pop(name, None)

    @staticmethod
    def keep_address(data, task_id, name):
        """Record on the task the address the runtime returned for the agent `name` (None when it returned none)."""
        data["tasks"][task_id]["address"] = ((data.get("agents") or {}).get(name) or {}).get(
            "address"
        )

    def swap_due(self, name, role, unit, assignment_id):
        """True when the agent is below the context reserve, judged from the usage the runtime last reported for its
        assignment (D5b). With no usable measurement it is resumed (D5c) and the log says `context: unknown`."""
        with self.book() as data:
            usage = (data.get("usage") or {}).get(assignment_id)
        sample = (
            usage.get("context")
            if isinstance(usage, dict) and isinstance(usage.get("context"), dict)
            else usage
        )
        verdict = (
            context_verdict(sample, self.args.context_reserve)
            if isinstance(sample, dict)
            else {"status": "unknown"}
        )
        if verdict["status"] == "unknown":
            self.log("context", name=name, role=role, unit=unit, context="unknown")
            return False
        if verdict["status"] != "checkpoint":
            return False
        self.log(
            "swap",
            name=name,
            role=role,
            unit=unit,
            remaining_tokens=verdict["remaining_tokens"],
            reserve=self.args.context_reserve,
            model=verdict["model"],
        )
        narrate.say(
            "info",
            None,
            "%s is low on context (%d tokens left); swapping in a fresh %s"
            % (name, verdict["remaining_tokens"], role),
            task=unit,
        )
        return True

    def handoff(self, name, unit):
        """What a fresh agent that replaces `name` reads in place of the transcript: the unit's task, its last report and
        findings, and the commit range (the whole change, and what is new since the last review)."""
        reviewed, findings, report = self.unit_trail(unit)
        with self.book() as data:
            checkout = next(
                (
                    t["packet"]["checkout"]
                    for t in data["tasks"].values()
                    if t["packet"].get("unit") == unit
                ),
                "",
            )
        return "\n".join(
            [
                "",
                "You are a fresh agent replacing %s, which ran short of context. You have no earlier transcript; read:"
                % name,
                "- The unit's task: %s" % (self.run_dir / unit / "task.md"),
                "- Its last report: %s" % (report or "none yet"),
                "- Its last findings: %s" % (findings or "none yet"),
                "- Commit range: `git -C %s diff %s...HEAD`%s"
                % (
                    checkout,
                    self.args.base,
                    "; new since the last review: `%s..HEAD`" % reviewed if reviewed else "",
                ),
                "",
            ]
        )

    def stop_agent(self, name, unit, why):
        runtime_call(self.lead, "stop", {"name": name})
        self.log("stop", name=name, unit=unit, why=why)

    def stop_unit_agents(self, unit, why):
        """Stop the unit's implementer and reviewer panes (the ones still alive). A pane that will not stop is logged, not fatal."""
        with self.book() as data:
            names = [
                n
                for n, a in (data.get("agents") or {}).items()
                if a.get("unit") == unit and a.get("role") in ("worker", "reviewer")
            ]
        for name in names:
            if not self.pane_alive(name):
                continue
            try:
                self.stop_agent(name, unit, why)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                narrate.say(
                    "warn", None, "could not stop %s (%s): %s" % (name, why, error), task=unit
                )
                self.log("error", unit=unit, name=name, gate="stop", error=str(error)[:500])

    def pane_alive(self, name):
        try:
            return runtime_call(self.lead, "status", {"name": name}).get("state") not in (
                None,
                "exited",
                "unknown",
            )
        except (ValueError, OSError, subprocess.TimeoutExpired):
            return False

    def resume_worker(self, task_id, packet, text):
        """Resume the attempt's own worker with `text`, whatever state the attempt is in: True when its pane is live
        and was told, False when there is nobody to tell."""
        name, unit = packet["worker"], packet["unit"]
        if not self.pane_alive(name):
            return False
        answer = runtime_call(self.lead, "resume", {"name": name, "task": text})
        assignment_id = engine().assignment_of(answer)
        with self.book() as data:
            data["tasks"][task_id]["assignment_id"] = assignment_id
            if name in (data.get("agents") or {}):
                data["agents"][name]["assignment_id"] = assignment_id
                if answer.get("address"):
                    data["agents"][name]["address"] = answer["address"]
        self.touched.add(name)
        self.log(
            "agent", name=name, role="worker", unit=unit, how="resume", assignment_id=assignment_id
        )
        return True

    def hand_round(self, task, successor, task_md, text, fresh=False, answer=None):
        """Close the unit's open task, claim its successor and put a worker on it: the live pane is resumed, a gone
        pane (or a `fresh` round) gets a new worker from `text`."""
        task_id, new_id = task["packet"]["task_id"], successor["task_id"]
        with self.book() as data:
            before = data["tasks"][task_id]["status"]
            if before != "RELEASED":
                engine().release(data, task_id, "superseded by " + new_id)
            reserved = engine().claim(data, successor)["action"] == "reserved"
            bound = data["tasks"][new_id]["runtime_id"]
        if not bound:
            try:
                runtime_id, assignment_id = self.hand_over(
                    task, successor, task_md, text, fresh, answer
                )
            except (ValueError, OSError, subprocess.TimeoutExpired):
                # Nothing took the round over, so the worker's own task stays the unit's open task: a `complete` that
                # lands while the Lead is asked again must still find it (a released task is not looked at).
                with self.book() as data:
                    if before != "RELEASED":
                        data["tasks"][task_id].update(status=before, updated_at=timeutil.iso())
                        for key in ("release_reason", "cutoff"):
                            data["tasks"][task_id].pop(key, None)
                    if reserved and not data["tasks"][new_id]["runtime_id"]:
                        del data["tasks"][new_id]
                raise
            with self.book() as data:
                engine().bind(data, new_id, str(runtime_id))
                data["tasks"][new_id]["assignment_id"] = assignment_id
                self.keep_address(data, new_id, successor["worker"])

    def hand_over(self, task, successor, task_md, text, fresh=False, answer=None):
        """Put a worker on the successor attempt: the live pane resumed, a gone pane replaced."""
        name, unit = successor["worker"], successor["unit"]
        said = " The Lead's answer: %s" % answer if answer else ""
        spawn = self._spawn_task(successor, text + said)
        attempt = successor["task_id"]
        adopted = self.reconcile_intent(name)
        if (
            adopted
        ):  # a send this driver made before it died: the runtime holds it, so nothing is sent again
            assignment_id, intent = adopted
            address = self.adopt(name, "worker", unit, assignment_id, intent)
            return address or task["runtime_id"], assignment_id
        if self.open_intent(
            name
        ):  # the driver died while starting this worker: find it, do not start another
            return self.ensure_agent(name, "worker", unit, spawn, fresh=True, attempt=attempt)
        alive = self.pane_alive(name)
        if alive and not fresh and self._swap_replaces(name, unit, spawn):
            fresh = True
        if alive and not fresh:
            return self._resume_round(name, unit, attempt, task_md, said, task["runtime_id"])
        return self._replace_worker(name, unit, spawn, successor, alive)

    @staticmethod
    def _spawn_task(successor, text):
        """The runtime's spawn arguments for one attempt: where it works, and which route it runs on."""
        return {
            "kind": "worker",
            "name": successor["worker"],
            "cwd": successor["checkout"],
            "cli": successor["backend"],
            "model": successor["model"],
            "effort": successor["effort"],
            "task": text,
        }

    def _swap_replaces(self, name, unit, spawn):
        """True when the live agent is swapped rather than resumed: its task then carries what a fresh one reads."""
        with self.book() as data:
            last = ((data.get("agents") or {}).get(name) or {}).get("assignment_id")
        if not self.swap_due(name, "worker", unit, last):
            return False
        spawn["task"] += self.handoff(name, unit)
        return True

    def _resume_round(self, name, unit, attempt, task_md, said, runtime_id):
        """Resume the live pane on the successor's task: (the address it kept, its new assignment)."""
        resumed, assignment_id = self.send(
            "resume",
            name,
            "worker",
            unit,
            attempt,
            {"name": name, "task": "Read %s and follow it.%s" % (task_md, said)},
        )
        with self.book() as data:
            if name in (data.get("agents") or {}):
                data["agents"][name]["assignment_id"] = assignment_id
                if resumed.get("address"):
                    data["agents"][name]["address"] = resumed["address"]
        self.touched.add(name)
        self.log(
            "agent", name=name, role="worker", unit=unit, how="resume", assignment_id=assignment_id
        )
        return resumed.get("address") or runtime_id, assignment_id

    def _replace_worker(self, name, unit, spawn, successor, alive):
        """Stop the pane being replaced, then start a fresh worker on the successor's attempt."""
        if alive:
            try:
                self.stop_agent(name, unit, "replaced")
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                narrate.say(
                    "warn",
                    None,
                    "could not stop %s before replacing it: %s" % (name, error),
                    task=successor["task_id"],
                )
        address, assignment_id = self.ensure_agent(
            name, "worker", unit, spawn, fresh=True, attempt=successor["task_id"]
        )
        narrate.say(
            "start",
            None,
            "started a fresh worker %s (%s)"
            % (name, "replacing the pane" if alive else "the old pane is gone"),
            task=successor["task_id"],
        )
        return address, assignment_id
