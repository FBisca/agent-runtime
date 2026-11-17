"""The plan's end of life: the park it waits in, the archive it is closed by, its reopening and the crash report.

A drive that can go no further parks. The ledger keeps one record of it - its kind, its cause, the
approval the drive holds, since when, the units and the way out - `attention.md` shows that record
beside the tickets that need a person, the Lead is woken with it, and a park with nothing in flight
asks again until it is answered. A plan whose tickets are all done is archived: a discard deletes its
folder, a promote deletes the folder (or copies a legacy plan's files), applies the plan's requirement
changes to its spec, commits, pushes and opens the pull request. A park the Lead answers is reopened from the plan, and the guard that catches an escaping error
logs it, wakes the Lead and says it on stderr.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines, and because what a plan does when it stops is one lane with one home.
"""

import copy
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

from .. import approval_recovery, contracts, events, narrate, spec_blocks
from .. import config as repo_config
from .. import ledger as ledgers
from .. import plan as yaml_plan
from ..invariants import PARKED
from .book import ARCHIVE
from .records import is_agent, is_done
from .runtime import TREE_TIMEOUT, head_of


def _engine():
    """The package this module is split from, asked when the lane acts.

    The lane reads rules and seams the package owns - `run`, `write_atomic`, `locate_state`,
    `enter_plan_state`, `ACCEPTED_STATES`, `MATRIX`, the archive retry's refusal - and a test answers a
    seam by patching `driver.<name>` on that package. A name bound here at import time would answer the
    real one and ignore the seam the test installed, so the package is asked each time, the way
    `Gates`, `Pull` and `Spawning` ask it.
    """
    return sys.modules[__name__.rpartition(".")[0]]


# The two asks of a park: fifteen minutes in, then every thirty, while nothing is in flight.
PARK_ASK_FIRST = 15 * 60.0  # seconds a parked drive waits for the Lead before it asks again
PARK_ASK_EVERY = 30 * 60.0  # seconds between the asks after the first, until the park ends

# The runtime id of a unit the driver does itself: no agent is behind the archive.
DRIVER_ID = "driver"

# What the archive unit is held to, in a worker's words: the change set applied and the approved bytes kept.
YAML_ARCHIVE_ACCEPTANCE = [
    "The plan's spec holds each added and modified requirement as the plan states it, and no removed one",
    "plan.yaml and its Markdown narrative are byte-identical to the approved copies",
]


class Archive:
    """The run's park and its archive, held by the Driver: one method per record it keeps and one per way out.

    A member asks the driver it belongs to for everything else - the ledger, the plan, the processes,
    the worktrees - so the lane reads as the one thing it changes, and a member added here carries no
    second copy of a rule the engine already owns.
    """

    def __init__(self, driver):
        self.driver = driver

    def __getattr__(self, name):
        """The engine behind the lane: the record, the plan and the processes are the Driver's own, and only
        what the park and the archive do lives here."""
        return getattr(self.driver, name)

    # ---- the park ----------------------------------------------------------------------------------------------------
    # what the ledger keeps, attention.md shows and the Lead is woken for

    def held_approval(self):
        """The approval the drive gates on, as the ledger and the park keep it: the plan file is what loses the span
        (a hand edit drops it), so the base is read from the record when a run starts again."""
        if self.approval is None:
            return {}
        return {"base": self.approval.base, "digest": self.approval.digest}

    def _park_snapshot(self, park):
        """The approved bytes a plan park names to restore from: the snapshot of the approval the park holds."""
        digest = (park or {}).get("digest")
        if not digest:
            return ""
        # The snapshot's layout is the plan's rule, not the driver's: ask it, with the digest the park holds.
        return str(
            approval_recovery.snapshot_path(SimpleNamespace(digest=digest), self.args.state_dir)
        )

    def _park_record(self):
        """The park of the plan the drive cannot use: what it holds, why it is parked and since when."""
        drain = self.drain or {}
        held = self.held_approval()
        return {
            "kind": "plan",
            "cause": drain.get("cause"),
            "error": drain.get("error"),
            "digest": held.get("digest"),
            "base": held.get("base"),
            "since": self.parked_since,
            "units": [],
        }

    def park_here(self):
        """Book the park the drive is in, and hand back the record it wrote: the wake, attention.md and the exit all
        read the same record."""
        record = self._park_record()
        return self._book_park(record)

    def _book_park(self, record):
        """The park in the ledger (with the approval the drive holds) and in attention.md, which is rebuilt from it."""
        record["way_out"] = narrate.park_way_out(
            record, self.plan_path, self._park_snapshot(record)
        )
        with self.book() as data:
            ledgers.record_approval(data, self.held_approval())
            ledgers.record_park(data, record)
        self.refresh_attention()
        return record

    def end_park(self):
        """The park is over: the ledger and attention.md stop naming it."""
        with self.book() as data:
            ended = ledgers.end_park(data)
        if ended is None:
            return None
        self.refresh_attention()
        return ended

    def park_due(self):
        """Whether the park owes the Lead another ask: fifteen minutes in, then every thirty, while nothing is in
        flight. A parked unit's assignment is open in the runtime but is no work (`parked_assignments`), so it never
        silences the park. The plan is never given up for it (Drain on an unapproved edit keeps its promise) and no worker starts."""
        if not self.drain or self.unfinished - self.parked_assignments():
            return False
        interval = PARK_ASK_EVERY if self.park_asks else PARK_ASK_FIRST
        return self.clock() - (self.asked_at or 0) >= interval

    def ask_parked(self):
        """Ask the Lead again about the park it has not answered, in the same words: silence is not progress.
        The count and the clock are the drive's own fields, so they are set where they live."""
        self.driver.park_asks += 1
        self.driver.asked_at = self.clock()
        park = self._park_record()
        park["way_out"] = narrate.park_way_out(park, self.plan_path, self._park_snapshot(park))
        narrate.say("ask", None, narrate.park_ask(park, again=True))
        self.wake("drain", None, narrate.park_ask(park, again=True))

    def parked_assignments(self):
        """Assignment ids that belong to a parked unit (paused, blocked or skipped): open in the runtime, not work."""
        with self.book() as data:
            parked = {
                agent.get("assignment_id")
                for task in data["tasks"].values()
                if task.get("gate") in PARKED
                for agent in (task, task.get("review") or {}, task.get("decision") or {})
            } - {None}
            return parked

    # ---- the archive -------------------------------------------------------------------------------------------------
    # the driver's own unit, no agent: the branch, the spec, the pull request

    def start_archive(self, plan):
        """The archive's own unit, started once every ticket is done and the plan is accepted: a discard deletes the folder, a promote claims the unit that builds the branch."""
        agents = [t for t in plan["tickets"] if is_agent(t)]
        # Verify starts on the agent tickets; the archive waits for every ticket, a person's included, because a
        # scenario only a person can prove has its test node from that person's `complete`.
        if not agents or not all(is_done(t) for t in plan["tickets"]) or ARCHIVE in self.failed:
            return 0
        if self.plan_state() not in _engine().ACCEPTED_STATES:
            return 0  # a plan is archived only once it is accepted
        with self.book() as data:
            held = data["tasks"].get(ARCHIVE)
        if not self._claimable(held):
            return 0
        try:
            if self._archive_mode() == "discard" and not self._folder_tracked():
                return self.discard_plan(plan)
            self._claim_archive()
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            self.failed.add(ARCHIVE)
            self.errors.append("archive: %s" % error)
            narrate.say("fail", "archive", "archive not started: %s" % error)
            return 0
        self.build_archive(ARCHIVE)
        return 1

    @staticmethod
    def _claimable(held):
        """Whether this run may take the archive unit: none is held, or the one held is a build this driver died in.
        A task bound to a worker is an archive of an earlier driver: it keeps its ledger task. A task bound to the
        driver that never reported is one this driver died building: it is built again."""
        if not held or not held["runtime_id"]:
            return True
        return bool(
            held["runtime_id"] == DRIVER_ID and held["status"] == "ACTIVE" and not held.get("gate")
        )

    def _archive_mode(self):
        """promote | discard. Only a plan in its own folder can discard: a legacy plan always promotes."""
        path = getattr(
            self, "plan_path", None
        )  # a driver that holds no plan (a unit test's) promotes
        if path is None or not self.layout.is_folder:
            return "promote"
        return self.plan_model().archive or yaml_plan.repo_config(self.repo).archive_mode

    def _plan_folder_rel(self):
        """The plan folder's path in the repository, or None for a legacy plan or a folder plan kept outside it. A
        folder that is the repository, or holds it, is refused: deleting it would delete the work."""
        return self.layout.folder_place()

    def _folder_tracked(self):
        """Whether the plan folder is tracked on the base branch, which is the tree an archive branch is built from."""
        rel = self._plan_folder_rel()
        if rel is None:
            return False
        remote, _, branch = self.args.base.partition("/")
        _engine().run(["git", "-C", str(self.repo), "fetch", "-q", remote, branch], timeout=120)
        listed = _engine().run(
            [
                "git",
                "-C",
                str(self.repo),
                "ls-tree",
                "-r",
                "--name-only",
                self.args.base,
                "--",
                rel,
            ],
            timeout=TREE_TIMEOUT,
        )
        return bool(listed)

    def _guard_plan_folder(self):
        """Refuse to delete a plan folder that is not directly under plans.dir. The config is read again here, not
        taken from the plan's load: it can change mid-run, and this is the check at the deletion itself."""
        layout = self.layout
        plans_dir = repo_config.load(self.repo).plans_dir
        if layout.folder is not None and not layout.directly_under(plans_dir):
            raise ValueError(yaml_plan.not_under_plans_dir(layout.folder, plans_dir))

    def discard_plan(self, plan):
        """A discard plan whose folder is not tracked: the driver deletes the folder and the plan is ARCHIVED. No
        worker, no branch, no pull request."""
        self._guard_plan_folder()
        folder = self.layout.folder
        shutil.rmtree(folder)
        self.driver.discarded = True  # the plan it last read is the plan from here on
        with self.book() as data:
            _engine().enter_plan_state(data, "ARCHIVED")
        self.log("archive-discarded", folder=str(folder))
        narrate.say("done", "archive", "plan discarded: %s deleted" % folder)
        return 1

    def remove_plan_folder(self, checkout):
        """Delete the plan folder from an archive checkout (a plan kept outside the repository has none there)."""
        rel = self._plan_folder_rel()
        if rel is not None:
            self._guard_plan_folder()
            shutil.rmtree(Path(checkout) / rel, ignore_errors=True)

    def _archive_owns(self):
        """What the archive unit writes: the spec, the files it copies, and the plan folder it deletes."""
        model = self.plan_model()
        owns = [model.spec] if model.spec and self._archive_mode() == "promote" else []
        if self._archive_mode() == "promote":
            owns += [rel for rel, _ in self.yaml_copies()]
        folder = self._plan_folder_rel()
        return owns + ([folder + "/"] if folder else [])

    def _claim_archive(self):
        """Reserve the archive's attempt under the driver's own runtime id, with what the archive writes as the paths it owns."""
        name = self.slug[: 32 - len("-archive")].rstrip("-_") + "-archive"
        klass, effort = _engine().MATRIX[("small", "simple")]
        checkout = self.worktree(name, "feat/" + name)
        plan_model = self.model
        assert plan_model is not None  # read_plan sets it before any unit is dispatched
        packet = {
            "task_id": ARCHIVE,
            "worker": name,
            "plan": str(self.plan_path),
            "objective": "Archive the plan into the living spec"
            if self._archive_mode() == "promote"
            else "Delete the discarded plan folder",
            "checkout": str(checkout),
            "backend": self.args.cli,
            "owns": self._archive_owns(),
            "acceptance": YAML_ARCHIVE_ACCEPTANCE,
            "checks": [],  # no local gate: the pull request's CI is the gate
            "depends_on": [],
            "unit": ARCHIVE,
            "size": "small",
            "difficulty": "simple",
            "class": klass,
            "effort": effort,
            "model": "driver",
        }
        with self.book() as data:
            _engine().claim(data, packet)
            task = data["tasks"][ARCHIVE]
            if not task["runtime_id"]:
                _engine().bind(data, ARCHIVE, DRIVER_ID)
        (self.run_dir / ARCHIVE).mkdir(exist_ok=True, mode=0o700)

    def build_archive(self, task_id):
        """Build, push and open the archive, then hand its report to the gates. Any trouble asks the Lead."""
        try:
            head = self._archive_branch(task_id)
            self._snapshot_spec(task_id)
            url = self._archive_pr(task_id)
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            return self.request(
                task_id,
                "archive",
                "the archive could not be built: %s. `retry` builds it again from the current base, `skip` "
                "leaves it to you" % " ".join(str(error).split())[:400],
            )
        report = {
            "contract": contracts.VERSION,
            "task_id": task_id,
            "status": "COMPLETE",
            "summary": "the driver archived the plan: %s"
            % (
                "plan files copied, requirement changes applied"
                if self._archive_mode() == "promote" and not self.layout.is_folder
                else "plan folder deleted, requirement changes applied"
                if self._archive_mode() == "promote"
                else "the discarded plan folder deleted"
            ),
            "acceptance_met": True,
            "files": self._archive_owns(),
            "decisions": [],
            "interfaces": [],
            "checks": [],
            "review_rounds": 0,
            "usage": "unknown",
            "head": head,
            "pr": url,
        }
        self.log("report", unit=ARCHIVE, task=task_id, report=report)
        self.mark(task_id, gate="reported", handed_in=report)
        self.log("archive-built", task=task_id, pr=url, head=head)
        narrate.say("done", "archive", "archive built: %s" % url, task=task_id)

    def _snapshot_spec(self, task_id):
        if self._archive_mode() == "promote":
            checkout = self.task(task_id)["packet"]["checkout"]
            shutil.copyfile(
                Path(checkout) / self.plan_model().spec,
                self.run_dir / task_id / "archived-spec",
            )

    def _archive_branch(self, task_id):
        """The archive branch, rebuilt from the current base: the plan folder deleted or the plan files copied, the
        requirement changes applied, one commit, pushed. The branch is the driver's own, so a rebuild replaces what was pushed."""
        packet = self.task(task_id)["packet"]
        path, branch = Path(packet["checkout"]), "feat/" + packet["worker"]
        remote = self.args.base.partition("/")[0]
        _engine().run(
            ["git", "-C", str(self.repo), "fetch", "-q", remote, self.args.base.partition("/")[2]],
            timeout=120,
        )
        _engine().run(["git", "-C", str(path), "reset", "-q", "--hard"], timeout=TREE_TIMEOUT)
        _engine().run(["git", "-C", str(path), "clean", "-fdq"], timeout=TREE_TIMEOUT)
        _engine().run(
            ["git", "-C", str(path), "checkout", "-q", "-B", branch, self.args.base],
            timeout=TREE_TIMEOUT,
        )
        # A plan in its own folder leaves the tree, whether it promotes or discards.
        self.remove_plan_folder(path)
        message = "docs(%s): delete the discarded plan folder" % self.slug
        if self._archive_mode() == "promote":
            message = self._archive_message()
            self._promote_into(path)
        _engine().run(["git", "-C", str(path), "add", "-A"], timeout=TREE_TIMEOUT)
        _engine().run(
            ["git", "-C", str(path), *self._identity(path), "commit", "-q", "-m", message],
            timeout=TREE_TIMEOUT,
        )
        _engine().run(
            ["git", "-C", str(path), "push", "-q", "--force", remote, "HEAD:refs/heads/" + branch],
            timeout=300,
        )
        return head_of(path)

    def _promote_into(self, path):
        """What a promote archive puts on its branch: the plan files copied, the spec written."""
        for rel, source in self.yaml_copies():
            self._copy_plan_file(path, rel, source)
        self._write_spec(path)

    @staticmethod
    def _copy_plan_file(path, rel, source):
        """One approved plan file, or every file of one of its directories, byte-identical at its destination."""
        if not source.is_dir():
            target = path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            return
        for child in sorted(p for p in source.rglob("*") if p.is_file()):
            target = path / rel.rstrip("/") / child.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(child.read_bytes())

    def _archive_message(self):
        """The archive commit: the plan's goal, and why each removed requirement went."""
        model = self.plan_model()
        lines = ["docs(%s): archive the plan into %s" % (self.slug, model.spec), "", model.goal]
        if model.requirements.removed:
            lines += [""] + [
                "Removed %s: %s" % (r.title, r.why) for r in model.requirements.removed
            ]
        return "\n".join(lines)

    def _write_spec(self, path):
        """The plan's requirement changes applied to the base's own spec text."""
        model = self.plan_model()
        spec = path / model.spec
        try:
            before = spec.read_text(encoding="utf-8")
        except FileNotFoundError:
            before = ""
        text = spec_blocks.apply(before, model.requirements, model.spec)
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(text, encoding="utf-8")

    @staticmethod
    def _identity(path):
        """The commit identity flags: none when git already knows an author, else the driver's own."""
        known = subprocess.run(
            ["git", "-C", str(path), "config", "user.email"], capture_output=True, text=True
        ).stdout.strip()
        if known:
            return []
        return [
            "-c",
            "user.name=agent-orchestrator",
            "-c",
            "user.email=agent-orchestrator@localhost",
        ]

    def _archive_pr(self, task_id):
        """The archive's pull request: the one an earlier build opened while it is still open, else a new draft."""
        existing = self.pr_of(task_id)
        if existing and self.pr_view(existing).get("state") == "OPEN":
            return existing
        packet = self.task(task_id)["packet"]
        code, out, err = self.gh(
            [
                "pr",
                "create",
                "--draft",
                "--base",
                self.args.base.partition("/")[2],
                "--head",
                "feat/" + packet["worker"],
                "--title",
                "docs(%s): archive the plan" % self.slug
                if self._archive_mode() == "promote"
                else "docs(%s): delete the discarded plan folder" % self.slug,
                "--body",
                self._archive_body() + self._run_notes(),
            ],
            timeout=120,
            cwd=packet["checkout"],
        )
        found = re.findall(r"https?://\S+/pull/\d+", out or "")
        if code != 0 or not found:
            raise ValueError(
                "gh pr create failed: %s" % ((err or out).strip()[:300] or "no url printed")
            )
        return found[-1]

    def _run_notes(self):
        """What the run recorded without blocking, for the plan's close: the notes workers handed in with a COMPLETE
        report (a flaky check they re-ran among them), read from the run's events."""
        lines = []
        for event in events.read(self.run_dir / "events.jsonl"):
            if event["kind"] == "report":
                lines += [
                    "- %s: %s" % (event.get("unit"), " ".join(str(note).split()))
                    for note in event["report"].get("notes") or []
                ]
        return "\n\n## Recorded during the run\n\n" + "\n".join(lines) + "\n" if lines else ""

    def _archive_body(self):
        """The archive pull request's description, by what the archive does with the plan."""
        from .pointers import reference

        spec = (
            reference(self.run_dir / ARCHIVE, "archived-spec", "read")
            if self._archive_mode() == "promote"
            else ""
        )
        if self._archive_mode() == "discard":
            return "Deletes the discarded plan folder %s; it changes nothing else." % reference(
                self.repo, self._plan_folder_rel(), "collection"
            )
        if self.layout.is_folder:
            where = reference(self.repo, self.layout.folder, "collection")
            return (
                "Archives the plan %s into the living spec: the folder is deleted and the plan's requirement "
                "changes are applied by the driver. Spec snapshot: `%s`." % (where, spec)
            )
        return (
            "Archives `%s` into the living spec: the plan files and the plan's requirement changes, "
            "written by the driver. Spec snapshot: `%s`."
            % (reference(self.repo, self.plan_path, "read"), spec)
        )

    def undraft_archive(self, task_id):
        """A readied archive pull request goes back to draft before the Lead is asked: nobody merges a failing one."""
        url = self.pr_of(task_id)
        if url and self.unit_flag(ARCHIVE, "pr_ready"):
            self.undraft(task_id, url)

    def retry_archive(self, task_id):
        """The Lead's `retry`: the archive branch is rebuilt from the current base and pushed again. A pull request
        that was readied goes back to draft first, so nobody merges a branch that is changing.

        The archive waits for an accepted plan: over a plan that is not accepted the retry builds nothing and opens no
        pull request, and the Lead is asked again, naming the plan's state and the way out."""
        state = self.plan_state()
        if not _engine().archive_retry_allowed(state):
            reason, way_out = _engine().archive_retry_refusal(state)
            self.request(task_id, "archive", "%s. %s" % (reason, way_out))
            return
        url = self.pr_of(task_id)
        if url and self.unit_flag(ARCHIVE, "pr_ready") and not self.undraft(task_id, url):
            return False
        self.unready(ARCHIVE)
        self.mark(task_id, status="ACTIVE", gate=None, ci_state=None, ci_rows=None, ci_head=None)
        self.build_archive(task_id)

    def yaml_copies(self):
        """(repo-relative destination, approved source) for the plan files the archive copies byte-identical.
        The narrative is copied only when the plan has one."""
        layout = self.layout
        if layout.is_folder:
            return []  # a plan folder is deleted, never copied
        copies = [(layout.plan_place, layout.path)]
        if layout.narrative.exists():
            copies.append((self._beside_plan(layout.narrative), layout.narrative))
        return copies

    def _beside_plan(self, path):
        """The destination of a plan file: the same directory as the plan, inside the archive checkout even when
        the plan itself sits outside the repository."""
        return (PurePosixPath(self.layout.plan_place).parent / Path(path).name).as_posix()

    # ---- reopening a park --------------------------------------------------------------------------------------------
    # the Lead answered through the plan: a fresh attempt, or a release

    def reopen_parked(self, plan):
        """The user answers a pause through the plan (S4). A parked ticket whose Status is now TO-DO gets a fresh
        attempt of the same unit; one now COMPLETE (done by hand) is released so its dependents and the archive
        proceed. Any other Status leaves it parked. The held worker is stopped first, when its pane is live."""
        by_id = {t["#"].lower(): t for t in plan["tickets"]}
        with self.book() as data:
            parked = [
                (task_id, copy.deepcopy(task))
                for task_id, task in data["tasks"].items()
                if task.get("gate") in PARKED and task["status"] not in ("COMPLETE", "RELEASED")
            ]
        count = 0
        for task_id, task in parked:
            ticket = by_id.get(task["packet"]["unit"])
            if not ticket or not is_agent(ticket):
                continue
            status = ticket["Status"].strip().upper()
            if not status.startswith(("TO-DO", "COMPLETE")):
                continue
            again = status.startswith("TO-DO")
            if self.pane_alive(task["packet"]["worker"]):
                self.stop_agent(task["packet"]["worker"], task["packet"]["unit"], "reopened")
            with self.book() as data:
                _engine().release(
                    data,
                    task_id,
                    "the user set %s to %s"
                    % (ticket["#"], "TO-DO: retry" if again else "COMPLETE: done by hand"),
                )
            narrate.say(
                "info",
                None,
                "reopened by you: fresh attempt" if again else "completed by hand",
                task=ticket["#"],
            )
            if not again:
                continue
            with self.book() as data:
                taken = set(data["tasks"])
            attempt = next(
                n for n in range(1, 1000) if "%sp%d" % (task["packet"]["unit"], n) not in taken
            )
            try:
                self.dispatch(plan, ticket, "%sp%d" % (task["packet"]["unit"], attempt))
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                self.not_dispatched(ticket, error, "not re-dispatched")
                continue
            count += 1
        return count


# ---- the crash report: what the guard logs and what `await` reads ------------------------------------


def crash_line(run_dir, text):
    """What the Lead is told of a crashed driver: where the log is and the line the guard wrote."""
    return "crashed -> %s: %s" % (run_dir / "events.jsonl", text)


def report_crash(driver, error):
    """Log the crash, wake the Lead and say it on stderr. It takes no ledger lock and books nothing in the ledger: the
    crash may be the ledger itself, and the guard must not wait for what failed. A step of it that fails is said on
    stderr and the rest still happens."""
    reason = (
        str(error)
        if isinstance(error, ledgers.LedgerBusy)
        else "%s: %s" % (type(error).__name__, error)
    )
    reason = " ".join(reason.split())[:500]
    text = "crashed " + reason
    print(text, file=sys.stderr, flush=True)
    try:
        if getattr(driver, "run_dir", None) is None:  # it crashed before it knew its run directory
            driver.run_dir = _engine().state_layout(
                driver.args.state_dir, driver.plan_path, driver.repo, driver.slug
            )[1]
            driver.run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        driver.log("crash", reason=reason, text=text)
        driver.wake(
            "crash",
            None,
            crash_line(driver.run_dir, text),
        )
    except (
        Exception
    ) as failed:  # the original error is the news; this one only says the Lead may not have heard
        print("could not log the crash: %s" % failed, file=sys.stderr, flush=True)


class ArchiveSurface:
    """The published surface of the park and the archive.

    Each name answers with the one `Archive` the Driver holds, so a gate step, the suite and the Lead's
    commands keep asking the Driver while the record keeps one owner.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def held_approval(self):
        """The approval the drive gates on, as the ledger and the park keep it: the plan file is what
        loses the span (a hand edit drops it), so the base is read from the record when a run starts
        again."""
        return self.archive.held_approval()

    def park_here(self):
        """Book the park the drive is in, and hand back the record it wrote: the wake, attention.md and
        the exit all read the same record."""
        return self.archive.park_here()

    def end_park(self):
        """The park is over: the ledger and attention.md stop naming it."""
        return self.archive.end_park()

    def park_due(self):
        """Whether the park owes the Lead another ask: fifteen minutes in, then every thirty, while
        nothing is in flight."""
        return self.archive.park_due()

    def ask_parked(self):
        """Ask the Lead again about the park it has not answered, in the same words: silence is not
        progress."""
        return self.archive.ask_parked()

    def parked_assignments(self):
        """Assignment ids that belong to a parked unit (paused, blocked or skipped): open in the
        runtime, not work."""
        return self.archive.parked_assignments()

    def start_archive(self, plan):
        """The archive's own unit, started once every ticket is done and the plan is accepted: a discard
        deletes the folder, a promote claims the unit that builds the branch."""
        return self.archive.start_archive(plan)

    def discard_plan(self, plan):
        """A discard plan whose folder is not tracked: the driver deletes the folder and the plan is
        ARCHIVED."""
        return self.archive.discard_plan(plan)

    def remove_plan_folder(self, checkout):
        """Delete the plan folder from an archive checkout (a plan kept outside the repository has none
        there)."""
        return self.archive.remove_plan_folder(checkout)

    def build_archive(self, task_id):
        """Build, push and open the archive, then hand its report to the gates."""
        return self.archive.build_archive(task_id)

    def undraft_archive(self, task_id):
        """A readied archive pull request goes back to draft before the Lead is asked: nobody merges a
        failing one."""
        return self.archive.undraft_archive(task_id)

    def retry_archive(self, task_id):
        """The Lead's `retry`: the archive branch is rebuilt from the current base and pushed again."""
        return self.archive.retry_archive(task_id)

    def yaml_copies(self):
        """(repo-relative destination, approved source) for the plan files the archive copies byte-
        identical."""
        return self.archive.yaml_copies()

    def reopen_parked(self, plan):
        """The user answers a pause through the plan (S4)."""
        return self.archive.reopen_parked(plan)
