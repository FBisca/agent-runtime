"""The state machine: the gate a unit waits at, and the one step that moves it on.

A unit crosses a handful of gates - the report it sent, the review it passed, the checks its pull
request ran, the merge, the cleanup - and each crossing is one method here, held by `Gates`, which
the Driver owns. `advance` is the round that reads every task's gate and dispatches it through
`STEPS`: the table and the steps it names live in one module, so a transition is read where it is
scheduled instead of a thousand lines apart.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines, and because the transitions are what every round of a run passes through.
"""

import json
import subprocess
import sys
from pathlib import Path

from .. import contracts, invariants, narrate, timeutil
from .book import ARCHIVE, release
from .records import merge_state, unit_attempts
from .report import cell, short
from .review import rereview_text


def _engine():
    """The package this module is split from, asked when a step runs.

    A step calls helpers the engine publishes - `run`, `worker_name`,
    `readable_request`, `unreadable_decision` - and a test answers a seam by patching `driver.<name>`
    on that package. A name bound here at import time would answer the real one and ignore the seam
    the test installed, so the package is asked each time, the way `Book` asks for its snapshots.
    """
    return sys.modules[__name__.rpartition(".")[0]]


# The gates a task is held at, and the one step that moves it on: what `advance` reads each round. A
# gate that is not here is one no round acts on - a parked unit, a task that is done, a task with none.
STEPS = {
    "reported": "step_reported",
    "converged": "step_converged",
    "reviewed": "step_reviewed",
    "fix-pending": "step_fix",
    "requested": "step_requested",
    "decided": "step_decided",
    "redo-pending": "step_redo",
    "ready-for-ci": "step_ci",
    "awaiting-merge": "step_merge",
    "cleanup": "step_cleanup",
}


def step_failed(drive, task_id, gate, error):
    """Book a gate step's failure: the unit is stuck at its gate, the run names the error and the reader hears it."""
    drive.stuck.add((task_id, gate))
    drive.errors.append("%s: %s" % (task_id, error))
    drive.log("error", unit=drive.unit_of(task_id), task=task_id, gate=gate, error=str(error)[:500])
    narrate.say("fail", narrate.step_of(gate), "step %s failed: %s" % (gate, error), task=task_id)


class Gates:
    """The run's state machine, held by the Driver: one method per gate, one table that dispatches them.

    A step asks the driver it belongs to for everything else - the record, the plan, the processes -
    so a transition reads as the one thing it changes, and a member added here carries no second copy
    of a rule the engine already owns.
    """

    def __init__(self, driver):
        self.driver = driver

    def __getattr__(self, name):
        """The engine behind the state machine: a step's mark, its request and its spawn are the
        Driver's own, and only the transitions themselves live here."""
        return getattr(self.driver, name)

    def advance(self):
        """Run every pending gate stage once; each step changes the task's gate or raises. Returns the steps run."""
        self._poll_quietly()
        steps = {gate: getattr(self, name) for gate, name in STEPS.items()}
        if (
            self.drain
        ):  # a fix round and a retry start a worker attempt: they wait for the drain to end
            del steps["fix-pending"], steps["redo-pending"]
        return self._run_steps(steps)

    def _poll_quietly(self):
        """One non-blocking look at every pull request before the gates move. A poll that fails is the
        run's error line, never a reason for the gates to stop."""
        try:
            self.poll()
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            self.errors.append("poll: %s" % error)
            self.log("error", gate="poll", error=str(error)[:500])
            narrate.say("fail", None, "could not poll the pull requests: %s" % error)

    def _run_steps(self, steps):
        """Every gated task's step, in one pass over the record: a step that is not ready yet is left
        for the next round, and one that fails is booked without stopping the rest."""
        # `send` raises the package's class, so this module must catch that one.
        from . import IntentUnsettled

        acted = 0
        with self.book() as data:
            ids = [task_id for task_id, task in data["tasks"].items() if task.get("gate") in steps]
        for task_id in ids:
            while True:
                gate = self.task(task_id).get("gate")
                if gate not in steps or (task_id, gate) in self.stuck:
                    break
                try:
                    if (
                        steps[gate](task_id) is False
                    ):  # nothing to act on yet: poll again next iteration
                        break
                except IntentUnsettled as error:
                    self.hold_intent(
                        task_id, error
                    )  # a request now, so the unit is not `stuck` after the decision
                    break
                except invariants.LedgerInvariant as error:
                    try:  # the Lead settles an illegal ledger: the refusal becomes a request naming the way out
                        self.request(task_id, gate, str(error))
                    except invariants.LedgerInvariant:
                        # A ledger already illegal before this write holds no request either: the refusal is the run's
                        # error, and `ledger check` names the repair.
                        step_failed(self, task_id, gate, error)
                    break
                except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                    if self.decision_unapplied(task_id, gate, error):
                        break  # the Lead is asked again, as for an unsettled intent: not an error of the run
                    step_failed(self, task_id, gate, error)
                    break
                acted += 1
        return acted

    def step_requested(self, task_id):
        """Wait for the Lead: a resolved request moves the unit on to `decided`. False while it is still open."""
        task = self.task(task_id)
        path = Path(task["request"]["path"])
        request = _engine().readable_request(path)
        if request is None:
            self.write_request(
                task_id
            )  # the driver died after the ledger and before the file, or mid-write
            request = contracts.load(path, contracts.validate_request)
        if request["status"] != "resolved":
            return False
        self.mark(task_id, gate="decided", decision={"path": request["decision"]})

    def step_reported(self, task_id):
        """The hand-in the driver already accepted (shape, task id, head): the gates read what it says."""
        task = self.task(task_id)
        report = task["handed_in"]
        self.log_clarifications(task_id, "report", report, "report:%s" % task_id)
        verdict, lines = self.gate(task_id, task["packet"], report)
        if verdict == "error":
            return self.request(task_id, self.gate_name(), lines[0])
        try:
            if verdict == "FAIL":
                return self.fail_gate(task_id, report, lines[0]["source"], lines)
            self.record_report(task_id, report)
        except ValueError as error:
            return self.request(task_id, "report", "the ledger rejected the report: %s" % error)
        # The archive is gated by its pull request's CI alone. Every other report, a fix round of any source included, goes to
        # the unit's reviewer: a delta re-review once the unit has been reviewed, else the first review.
        if task["packet"]["unit"] == ARCHIVE:
            return self.mark(task_id, gate="ready-for-ci")
        reviewed, findings, _ = self.unit_trail(task["packet"]["unit"])
        self.mark(
            task_id,
            gate="converged",
            delta={"from": reviewed, "findings": findings, "blocked": self.last_blocked(task)}
            if reviewed
            else None,
        )

    def last_blocked(self, task):
        """Whether the unit's newest verdict blocked: a re-review that blocks again goes to the Lead."""
        with self.book() as data:
            verdicts = [
                t["verdict"]
                for t in unit_attempts(data, task["packet"]["unit"])
                if t.get("verdict")
            ]
        return bool(verdicts) and verdicts[0]["verdict"] == "block"

    def step_converged(self, task_id):
        """The report is accepted: the reviewer starts, and CI is already running against the same commit. The first
        review gets the whole brief; a re-review resumes the reviewer that holds the change and sends it the fix diff
        only (a reviewer whose pane is gone gets the whole brief again)."""
        task = self.task(task_id)
        packet, delta = task["packet"], task.get("delta")
        unit, name = packet["unit"], _engine().worker_name(self.slug, packet["unit"][1:], "-review")
        review = self._review_slot(task_id, task.get("review"), unit, name)
        review_dir = Path(review["dir"])
        review_dir.mkdir(exist_ok=True, mode=0o700)
        task_md = review_dir / "task.md"
        again = bool(delta) and self.pane_alive(name)
        if not task_md.exists():
            task_md.write_text(self.review_text(packet), encoding="utf-8")
        backend, model = self.gate_step("reviewer", "large")
        if again:
            text = rereview_text(self, packet, delta, task_md)
        else:
            text = (
                "Read %s and follow it. Hand in the verdict as the text of your `report` call."
                % task_md
            )
        _, assignment_id = self.ensure_agent(
            name,
            "reviewer",
            unit,
            {
                "kind": "worker",
                "name": name,
                "task": text,
                "cwd": packet["checkout"],
                "cli": backend,
                "model": model,
                "effort": self.step_effort("reviewer", "high"),
            },
            attempt=review_dir.name,
        )
        self.mark(task_id, gate="reviewing", review=dict(review, assignment_id=assignment_id))
        narrate.say(
            "start",
            "review",
            "%s reviewer %s · %s · %s · %s"
            % (
                "resumed" if again else "started",
                name,
                self.gate_label("reviewer", "large"),
                model,
                self.step_effort("reviewer", "high"),
            ),
            task=task_id,
        )

    def _review_slot(self, task_id, review, unit, name):
        """The directory of this pass's brief. A pass the driver died in is repeated in the same one; a rework that
        keeps the task id (a Lead's answer) takes the next."""
        if review and "assignment_id" not in review:
            return review
        number = next(
            n
            for n in range(1, 1000)
            if not (self.run_dir / (unit + "-review" + ("" if n == 1 else str(n)))).exists()
        )
        record = {
            "name": name,
            "dir": str(self.run_dir / (unit + "-review" + ("" if number == 1 else str(number)))),
        }
        self.mark(task_id, review=record)
        return record

    def step_reviewed(self, task_id):
        """The verdict is in; the worker hears nothing until CI is in too, and then hears it once: the review's
        findings and the CI failures together. CI's deadline is the idle watchdog's, as every wait on a check."""
        task = self.task(task_id)
        packet, verdict = task["packet"], task["verdict"]
        try:
            url = self.pr_url(task_id)[0]
        except (contracts.ContractError, ValueError) as error:
            return self.request(task_id, "ci", str(error))
        state, head = task.get("ci_state"), task.get("ci_head")
        # CI counts when it ran on the commit that was reviewed: GitHub can still show the previous head's checks.
        if state not in ("green", "failed", "error") or (
            head and not head.startswith(task["report"]["head"][:7])
        ):
            narrate.say("wait", "ci", "review is in; waiting for CI on %s" % url, task=task_id)
            return False
        found = contracts.review_items(verdict)
        if found and (task.get("delta") or {}).get("blocked"):
            return self.request(
                task_id,
                "review",
                "%s is still blocked after its re-review; last: %s"
                % (packet["unit"], "; ".join(short(i) for i in found)),
            )
        if state == "error":
            return self.request(task_id, "ci", "gh pr checks failed for %s" % url)
        if state == "failed":
            return self.driver.pull.ci_failed(task_id, url, task, found)
        if found:
            return self.fail_gate(task_id, task["report"], "review", found)
        self.mark(task_id, gate="ready-for-ci")

    def step_ci(self, task_id):
        """The unit's merge state, then its checks. A conflict, a branch behind its base or a merge state GitHub has
        not computed yet is handled before `gh pr ready`, so a pull request that cannot merge is never readied. Green
        and the last review passed: the pull request is marked ready and the unit waits for the merge."""
        task = self.task(task_id)
        try:
            url = self.pr_url(task_id)[0]
        except (contracts.ContractError, ValueError) as error:
            return self.request(task_id, "ci", str(error))
        view = getattr(self, "views", {}).pop(url, None) or self.pr_view(url)
        kind = merge_state(view)
        if kind == "merged":
            return False  # the next poll completes it (merged_early)
        if kind == "closed":
            self.mark(task_id, pr_closed=url)  # a later attempt must not fall back to it
            return self.request(task_id, "merge", "%s was closed without a merge" % url)
        moved = self.merge_moved(task_id, url, view)
        if moved is not None:
            return moved
        if task.get("ci_state") != "green":
            return self._ci_wait(task_id, task, url)
        before = task.get("updated_from")
        if before and task.get("ci_head") in (None, before):
            # green read before GitHub moved the head is the old head's green, not the update's
            narrate.say(
                "wait", "ci", "waiting for CI on the updated branch of %s" % url, task=task_id
            )
            return False
        return self._ci_ready(task_id, task, url, view)

    def _ci_wait(self, task_id, task, url):
        """What a pull request whose checks are not green asks for: a fix round when they failed, the
        Lead when `gh` could not read them, another poll while they run. False as long as it waits."""
        state = task.get("ci_state")
        if state == "failed":
            return self.driver.pull.ci_failed(task_id, url, task)
        if state == "error":
            return self.request(task_id, "ci", "gh pr checks failed for %s" % url)
        narrate.say("wait", "ci", "waiting for CI on %s" % url, task=task_id)
        return False  # no fix round: the unit is polled again on a later round

    def _ci_ready(self, task_id, task, url, view):
        """A green pull request the last review passed: readied once, the Lead woken with the live head,
        the ticket's status written, and the unit left waiting for the merge."""
        if not task.get("pr_ready"):
            code, out, err = self.gh(["pr", "ready", url])
            if code != 0:
                return self.request(
                    task_id, "ci", "gh pr ready failed: %s" % (err or out).strip()[:200]
                )
            self.mark(task_id, pr_ready=True)
            narrate.say("done", "ci", "CI green; %s ready for review" % url, task=task_id)
        unit = self.unit_of(task_id)
        # an update-branch moves the head without a new report: the live head keys the wake, so it is sent again
        head = view.get("headRefOid") or self.task(task_id)["report"]["head"]
        self.wake("pr-ready", unit, "pr-ready %s: %s" % (unit, url), key="%s:%s" % (unit, head))
        self.put_status(task_id, "IN PROGRESS · awaiting merge (%s)" % url)
        self.mark(task_id, gate="awaiting-merge", pr=url, updated_from=None)

    def step_merge(self, task_id):
        """The unit waits for the merge, and the merge itself is the user's. The driver watches the pull request
        while it waits: main moves on, so a PR that now conflicts, is behind or has red checks is acted on."""
        url = self.task(task_id).get("pr")
        if not url:
            return self.request(task_id, "merge", "no pull request to wait for")
        state = self.pr_view(url)
        kind = merge_state(state)
        if kind == "closed":
            self.mark(task_id, pr_closed=url)  # a later attempt must not fall back to it
            return self.request(task_id, "merge", "%s was closed without a merge" % url)
        if kind != "merged":
            moved = self.merge_moved(task_id, url, state)
            if moved is not None:
                return moved
            task = self.task(task_id)
            if task.get("ci_state") == "failed":
                return self.driver.pull.ci_failed(task_id, url, task)
            return False
        stored = self.task(task_id)["report"]
        report = dict(
            stored,
            status="COMPLETE",
            acceptance_met=True,
            review_rounds=stored.get("review_rounds", 0),
        )
        if not self.complete_merged(task_id, report):
            return
        commit = (state.get("mergeCommit") or {}).get("oid", "")
        self.put_status(
            task_id,
            "COMPLETE %s · merged %s%s"
            % (timeutil.iso()[:10], url, " (%s)" % commit[:7] if commit else ""),
        )
        self.mark_merged(task_id, merge_commit=commit)
        narrate.say("done", "merge", "merged %s" % url, task=task_id)

    def step_cleanup(self, task_id):
        """A merge is final: stop the unit's implementer and reviewer panes, then remove the worktree only when nothing
        would be lost; else leave it and say why."""
        task = self.task(task_id)
        packet = task["packet"]
        self.stop_unit_agents(
            packet["unit"], "merged"
        )  # a merge is final: both panes stop whatever the worktree holds
        with self.book() as data:
            busy = [
                t["packet"]["task_id"]
                for t in data["tasks"].values()
                if (
                    t["packet"]["worker"] == packet["worker"]
                    or t["packet"]["checkout"] == packet["checkout"]
                )
                and t["status"] not in ("COMPLETE", "RELEASED")
            ]
        if busy:
            return self.mark(
                task_id, gate="done", cleanup="kept: worker still holds %s" % ", ".join(busy)
            )
        path, branch = Path(packet["checkout"]), task["checkout_at_claim"]["branch"]
        reason = self._cleanup_unpushed(path, branch)
        if reason:
            return self.keep(task_id, reason)
        _engine().run(["git", "-C", str(self.repo), "worktree", "remove", str(path)], timeout=60)
        _engine().run(["git", "-C", str(self.repo), "worktree", "prune"], timeout=60)
        self.mark(task_id, gate="done", cleanup="panes stopped, worktree removed")
        narrate.say("done", "cleanup", "panes stopped, worktree removed", task=task_id)

    def _cleanup_unpushed(self, path, branch):
        """Why the worktree must be kept, or None when nothing in it would be lost: a change that is not
        committed, and a branch whose commits are neither pushed nor merged, are both work."""
        dirty = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if dirty.returncode != 0 or dirty.stdout.strip():
            return "the checkout has uncommitted or untracked files"
        head = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        pushed = subprocess.run(
            ["git", "-C", str(path), "branch", "-r", "--contains", head],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.split()
        if not pushed:
            return "branch %s has commits that are neither pushed nor merged" % branch
        return None

    def step_fix(self, task_id):
        """Hand the failing items to the unit's worker in a new task directory; a gone pane gets a fresh worker."""
        task = self.task(task_id)
        packet, fix = task["packet"], task["fix"]
        if fix.get("draft"):
            if not self.undraft(task_id, fix["draft"]):
                return False  # a ready PR must not change under a reviewer's or a merger's eyes
            fix = dict(fix, draft=None)
            self.mark(task_id, fix=fix)
        unit, attempt = packet["unit"], fix["attempt"]
        new_id = self._fix_task_id(task, unit, attempt)
        new_dir = self.run_dir / new_id
        new_dir.mkdir(exist_ok=True, mode=0o700)
        findings, findings_path = self._fix_findings(task_id, fix, attempt, new_dir)
        if findings is None:
            return  # the Lead was asked: a findings file the contract refuses is never a crash
        task_md = new_dir / "task.md"
        if not task_md.exists():
            task_md.write_text(self.fix_text(task, new_id, new_dir, findings), encoding="utf-8")
        successor = dict(
            packet,
            task_id=new_id,
            objective="Fix round %d for %s: %s findings" % (attempt, unit.upper(), fix["source"]),
        )
        if fix.get("up"):
            successor.update(self.class_up(packet))
        text = self._fix_text(fix, unit, findings_path, task_md)
        self.hand_round(
            task,
            successor,
            task_md,
            text,
            fresh=fix.get("fresh") or fix.get("up") or fix.get("rebase"),
            answer=fix.get("answer"),
        )
        self.mark(task_id, gate="superseded", fix_task=new_id, held=[])
        self.replay_held(task, new_id)
        narrate.say(
            "start",
            "fix",
            "fix round for %s from %s findings" % (packet["worker"], fix["source"]),
            task=new_id,
        )

    def _fix_task_id(self, task, unit, attempt):
        """The round's task id: `<unit>r<attempt>`, or the next one the ledger does not hold (a restart can leave
        records under the round's number). Booked on the task before anything is sent, so a restarted step reuses it."""
        if task.get("fix_task"):
            return task["fix_task"]
        with self.book() as data:
            taken = set(data["tasks"])
        new_id = next(
            "%sr%d" % (unit, n)
            for n in range(attempt, attempt + 1000)
            if "%sr%d" % (unit, n) not in taken
        )
        self.mark(task["packet"]["task_id"], fix_task=new_id)
        return new_id

    def _fix_findings(self, task_id, fix, attempt, new_dir):
        """The round's findings file, written once and read back before anyone works from it. A file the
        contract refuses asks the Lead and answers (None, None); the worker never sees a broken round."""
        findings_path = new_dir / "findings.json"
        try:
            findings = contracts.make_findings(
                fix["source"],
                attempt,
                fix["items"],
            )
            if not findings_path.exists():
                findings_path.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
            contracts.load(findings_path, contracts.validate_findings)
        except contracts.ContractError as error:
            self.contract_error(task_id, "findings", error)
            return None, None
        return findings, findings_path

    def _fix_text(self, fix, unit, findings_path, task_md):
        """What the round's worker reads: the unit's brief, the findings and the round's brief. A round
        raised because the pull request conflicts says to rebase instead of to fix."""
        text = (
            "Read %s and follow it. Its work is already in this checkout; this is a fix round: read the findings at %s "
            "and the round's brief at %s, then fix only what the findings name."
            % (self.run_dir / unit / "task.md", findings_path, task_md)
        )
        if fix.get("rebase"):
            text = (
                "Read %s and follow it. The pull request conflicts with its base: this is a rebase round, not a fix round. Rebase the branch onto the base, resolve the "
                "conflicts, push the result to the same branch and open no new pull request. The round's brief is "
                "at %s." % (task_md, task_md)
            )
        return text

    def step_decided(self, task_id):
        """Apply the Lead's decision. A `complete` that arrived while the request was open and whose report is on disk
        is acted on instead of handing the unit to a worker again: the worker had already finished."""
        task = self.task(task_id)
        try:
            decision = contracts.load(task["decision"]["path"], contracts.validate_decision)
        except contracts.ContractError as error:
            return self.request(
                task_id, "decision", _engine().unreadable_decision(task["decision"]["path"], error)
            )
        action, reason, unit = decision["action"], decision["reason"], task["packet"]["unit"]
        narrate.say("info", "decide", "Lead decided %s: %s" % (action, reason), task=task_id)
        self.log("decision", unit=unit, task=task_id, action=action, reason=reason[:500])
        held = task.get("held") or []
        asked = task.get("request") or {}
        self.release_intents(asked.get("intent"), "the Lead decided %s" % action)
        with self.book() as data:
            if (data.get("main_red") or {}).get("task") == task_id:
                del data[
                    "main_red"
                ]  # the Lead has answered for main: the waiting tickets look again
                self.driver.main_failed = None
        if action in ("mark blocked", "skip"):
            return self._decide_paused(task_id, action, reason, held, unit)
        if unit == ARCHIVE:
            return self.retry_archive(task_id)
        self.write_status(unit.upper(), "IN PROGRESS")
        if self._decide_intent(task_id, asked, action):
            return
        finished = next((m for m in held if m["kind"] == "complete"), None)
        if finished:
            self.log("held-acted", unit=unit, task=task_id, held="complete", acted="reported")
            self.mark(task_id, gate=None, held=[])
            return self.take_report(task_id, finished["text"])
        if action == "answer":
            return self._decide_answer(task_id, task, asked, decision)
        return self._decide_handoff(task_id, task, action, decision, reason)

    def _decide_answer(self, task_id, task, asked, decision):
        """The Lead's `answer`: a request raised while the reviewer owed its review is answered to the
        reviewer, and every other answer goes to the unit's worker."""
        if asked.get("from_gate") == "reviewing" and task.get("review"):
            # The request was raised while the reviewer owed its review: the answer is for the reviewer.
            return self.step_review_answer(task_id, decision)
        return self.step_answer(task_id, decision)

    def _decide_intent(self, task_id, asked, action):
        """A held send the decision lets out again: it goes from the step that held it, under a new key,
        so the unit moves on instead of being handed to a worker a second time. True when it did."""
        if asked.get("kind") == "intent" and action == "retry" and asked.get("from_gate"):
            self.mark(task_id, gate=asked["from_gate"], held=[])
            return True
        return False

    def _decide_paused(self, task_id, action, reason, held, unit):
        """The Lead's `mark blocked` or `skip`: the attempt is closed, the messages its worker sent while
        the request was open are dropped, the unit's panes stop, and the ticket says which of the two."""
        blocked = action == "mark blocked"
        with self.book() as data:
            if blocked:
                data["tasks"][task_id].update(status="BLOCKED", blocker=reason)
            else:
                release(data, task_id, "skipped by the Lead: " + reason)
            data["tasks"][task_id].update(
                gate="blocked" if blocked else "skipped", updated_at=timeutil.iso(), held=[]
            )
        for message in held:
            self.log("held-dropped", unit=unit, task=task_id, held=message["kind"], why=action)
        self.stop_unit_agents(unit, "paused")
        return self.write_status(
            unit.upper(), ("BLOCKED" if blocked else "SKIPPED") + " · " + cell(reason)
        )

    def _decide_handoff(self, task_id, task, action, decision, reason):
        """A retry, a one-class-up retry or a fresh worker: a fix round already raised takes the flags on
        the pending round, and a unit holding none is handed over to a new attempt."""
        flags = {
            "fresh": action == "fresh worker",
            "up": action == "retry one class up",
            "answer": decision.get("answer"),
        }
        applying = dict(task["decision"], applying=action)  # a failed hand-over asks the Lead again
        if task.get("fix"):
            return self.mark(
                task_id, gate="fix-pending", fix=dict(task["fix"], **flags), decision=applying
            )
        self.mark(task_id, gate="redo-pending", redo=dict(flags, reason=reason), decision=applying)

    def step_answer(self, task_id, decision):
        """The Lead's `answer`: the unit's worker goes on under the same task id, so the id the brief names is the id
        its report must carry. Only `retry`, `fresh worker` and `retry one class up` start a new attempt."""
        task = self.task(task_id)
        packet, unit = task["packet"], task["packet"]["unit"]
        task_dir = self.run_dir / task_id
        task_dir.mkdir(exist_ok=True, mode=0o700)
        # named after the decision file, so a driver that died after the resume writes nothing twice
        suffix = Path(task["decision"]["path"]).name.removeprefix("decision").removesuffix(".json")
        brief = task_dir / ("answer%s.md" % suffix)
        if not brief.exists():
            brief.write_text(self.answer_text(task, decision), encoding="utf-8")
        text = (
            "Read %s and follow it. This is the task you were working on, answered by the Lead: it keeps its "
            "task id `%s`; the unit's brief is %s."
            % (brief, task_id, self.run_dir / unit / "task.md")
        )
        address, assignment_id = self.hand_over(
            task, packet, brief, text, answer=decision["answer"]
        )
        with self.book() as data:
            data["tasks"][task_id].update(
                runtime_id=str(address or task["runtime_id"]),
                assignment_id=assignment_id,
                status="ACTIVE",  # a report that failed its gate left PARTIAL; the worker is working again
                gate=None,
                held=[],
                answered_at=timeutil.iso(),
                updated_at=timeutil.iso(),
            )
            self.keep_address(data, task_id, packet["worker"])
        self.replay_held(task, task_id)
        narrate.say("info", "decide", "answer sent; %s resumed" % packet["worker"], task=task_id)

    def step_review_answer(self, task_id, decision):
        """The Lead's `answer` to a request raised while the reviewer owed its verdict: the reviewer goes on with the
        review it owes, and the implementer is left alone. A reviewer whose pane is gone is replaced by one told
        where its brief is."""
        task = self.task(task_id)
        unit = task["packet"]["unit"]
        self.resume_reviewer(
            task_id,
            "The Lead answered what stopped your review of %s. Their answer: %s. Hand in your verdict."
            % (unit.upper(), decision["answer"]),
        )
        self.mark(task_id, held=[], updated_at=timeutil.iso())
        narrate.say("info", "decide", "answer sent to %s" % task["review"]["name"], task=task_id)

    def step_redo(self, task_id):
        """Hand the unit back to a worker after the Lead's `retry`: a new task directory with the event and the reason."""
        task = self.task(task_id)
        packet, redo, unit = task["packet"], task["redo"], task["packet"]["unit"]
        with self.book() as data:
            taken = set(data["tasks"])
        number = next(
            n
            for n in range(1, 1000)
            if "%sd%d" % (unit, n) not in taken or "%sd%d" % (unit, n) == task.get("redo_task")
        )
        new_id = "%sd%d" % (unit, number)
        new_dir = self.run_dir / new_id
        new_dir.mkdir(exist_ok=True, mode=0o700)
        task_md = new_dir / "task.md"
        if not task_md.exists():
            task_md.write_text(self.redo_text(task, new_id, new_dir, redo), encoding="utf-8")
        successor = dict(
            packet,
            task_id=new_id,
            objective="Retry %s after: %s" % (unit.upper(), task["event"]["kind"]),
        )
        if redo["up"]:
            successor.update(self.class_up(packet))
        self.mark(task_id, redo_task=new_id)
        text = (
            "Read %s and follow it. Its work is already in this checkout; this is a retry: read the retry brief at %s "
            "and continue from where the work stands." % (self.run_dir / unit / "task.md", task_md)
        )
        self.hand_round(
            task,
            successor,
            task_md,
            text,
            fresh=redo["fresh"] or redo["up"],
            answer=redo.get("answer"),
        )
        self.mark(task_id, gate="superseded", held=[])
        self.replay_held(task, new_id)
        narrate.say(
            "start",
            "fix",
            "retry for %s after %s" % (packet["worker"], task["event"]["kind"]),
            task=new_id,
        )
