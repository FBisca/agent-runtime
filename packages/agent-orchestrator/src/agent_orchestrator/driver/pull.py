"""The pull request lane: one non-blocking poll, its checks, its conflicts, its merge and what a merge settles.

A worker opens a draft pull request with its report. `Pull` keeps it a draft while the review runs, polls
its checks once a round (nothing waits, no fix round is raised for checks that are not there yet), marks it
ready once the last review passed on green checks, brings it up to date with a base that moved, and takes a
conflict to a fix round of its own. The checks it saw are the evidence a CI fix round quotes, and a merge -
by the driver or by hand - completes the unit, writes its status, defers the findings nobody settled and
keeps or removes its worktree.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand lines,
and because everything the driver asks GitHub, and everything it writes down once GitHub answered, is one
lane with one home.
"""

import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .. import contracts, narrate, timeutil
from ..ci_log import FAILED_BUCKETS, PENDING_BUCKETS, logs_readable, placeholder, read_log
from ..invariants import PARKED, next_path
from .book import ARCHIVE, checkpoint, settle_merged
from .records import merge_state, newest_attempts
from .report import item_text
from .runtime import head_of


def engine():
    """The package this module is split from, asked when GitHub is spoken to.

    `gh` goes through the one seam the suite answers (`driver.gh_process`) and a merge enters the plan's
    next state through `driver.enter_plan_state`; a test patches those names on the package. A name bound
    here at import time would answer the real one and ignore the seam the test installed, so the package is
    asked each time, the way `Gates` and `Spawning` ask it.
    """
    return sys.modules[__name__.rpartition(".")[0]]


# The tail of a failing job's log that a fix round shows; the full log is at log_path.
CI_EXCERPT_CHARS = 2048


def job_log(run_log, job):
    """The lines of a `gh run view --log-failed` log that belong to one job: each opens with the job's name and a tab.
    A log with no such line is taken whole (it names no job)."""
    lines = [line for line in run_log.splitlines() if line.split("\t", 1)[0] == job]
    return "\n".join(lines) if lines else run_log


def log_tail(text, limit=CI_EXCERPT_CHARS):
    """The last `limit` characters of a log, starting at a whole line."""
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    return tail.split("\n", 1)[1] if "\n" in tail else tail


def _checks_state(drive, code, rows):
    """What one `gh pr checks` read says: green, pending, none, failed or error, from the buckets it printed.

    A failed check whose run's logs are still a placeholder is `pending`: there is no evidence to quote yet. The
    exit codes `gh pr checks` uses are 0 (all passed), 1 (some failed) and 8 (still running); anything else is an
    error rather than a verdict.
    """
    buckets = {str(row.get("bucket") or "").lower() for row in rows}
    if buckets & FAILED_BUCKETS:
        return "failed" if logs_readable(drive, rows) else "pending"
    if buckets & PENDING_BUCKETS:
        return "pending"
    if code == 0 and rows:
        return "green"
    if code in (0, 1, 8) and not rows:
        return "none"
    if code in (0, 1, 8):
        return "pending"
    return "error"


class Pull:
    """The run's pull request lane, held by the Driver: one method per way GitHub is asked or answered.

    A member asks the driver it belongs to for everything else - the record, the plan, the log, the gate a
    fix round is handed - so the lane reads as the one thing it asks, and a member added here carries no
    second copy of a rule the engine already owns.
    """

    def __init__(self, driver):
        self.driver = driver

    def __getattr__(self, name):
        """The engine behind the lane: the record, the log and the report are the Driver's own, and only
        what the driver asks GitHub lives here."""
        return getattr(self.driver, name)

    # ---- PR, CI and merge: one non-blocking poll in the loop -----------------------------------------------
    # The worker opens a draft pull request with its report. The driver keeps it a draft while the review runs,
    # polls its checks on every round (nothing waits, no fix round is raised for checks that are not there yet), and
    # marks it ready (`gh pr ready`) once the last review passed with green checks. From then on the unit waits for
    # the merge: it holds no worker slot and the driver only polls `gh` for it.

    def gh(self, argv, timeout=60, cwd=None):
        """Run `gh` and return (exit code, stdout, stderr). A `gh` that cannot run is an error, not a guess."""
        try:
            result = engine().gh_process(argv, timeout=timeout, **({"cwd": cwd} if cwd else {}))
        except subprocess.TimeoutExpired:
            return 1, "", "gh timed out"
        except OSError as error:
            return 1, "", str(error)
        return result.returncode, result.stdout, result.stderr

    def closed_prs(self, unit):
        """The pull requests this unit's gate has already seen closed: recorded when it happened, never guessed."""
        with self.book() as data:
            return {
                task["pr_closed"]
                for task in data["tasks"].values()
                if task.get("packet", {}).get("unit") == unit and task.get("pr_closed")
            }

    def pr_url(self, task_id):
        """The unit's pull request: a fix round opens none, so fall back to an earlier attempt's report."""
        task = self.task(task_id)
        reports = []
        if task.get("report_path"):
            try:
                reports.append(contracts.load(task["report_path"], contracts.validate_report))
            except contracts.ContractError:
                pass
        with self.book() as data:
            reports += [
                t["report"]
                for t in data["tasks"].values()
                if t["packet"].get("unit") == task["packet"]["unit"] and t.get("report")
            ]
        named = [r["pr"] for r in reports if r.get("pr")]
        # A pull request the merge gate has already seen closed is not this unit's: the Lead closed it to send the
        # work back, so a later attempt's branch needs one of its own. The ledger records it (`pr_closed`), so this
        # fallback answers without spending another `gh` call.
        closed = self.closed_prs(task["packet"]["unit"])
        url = next((u for u in named if u not in closed), None)
        if url is None and named:
            raise ValueError(
                "every pull request this unit's reports name was closed (%s): open a new one for this branch's "
                "head and report it as `pr`" % ", ".join(named)
            )
        match = re.fullmatch(r"https?://[^/]+/([^/]+/[^/]+)/pull/(\d+)/?", url or "")
        if match is None:
            raise ValueError("report.json names no pull request URL to check (pr: %r)" % url)
        return url, "%s#%s" % match.groups()

    def pr_of(self, task_id):
        """The unit's pull request URL, or None when no report names one."""
        try:
            return self.pr_url(task_id)[0]
        except (contracts.ContractError, ValueError):
            return None

    def pr_view(self, url) -> dict[str, Any]:
        """The pull request as `gh` sees it: {state, isDraft, mergeStateStatus, mergeable, headRefOid}. A `gh` that
        fails is no state at all: the driver polls again rather than guessing."""
        code, out, err = self.gh(
            [
                "pr",
                "view",
                url,
                "--json",
                "state,isDraft,mergeStateStatus,mergeable,mergeCommit,headRefOid",
            ]
        )
        if code != 0:
            return {"error": (err or out).strip()[:200]}
        try:
            state = json.loads(out)
        except ValueError:
            return {"error": "gh pr view printed no JSON"}
        return state if isinstance(state, dict) else {"error": "gh pr view printed no object"}

    def pr_checks(self, url):
        """The pull request's checks as (state, rows): green, pending, none or failed. `gh` prints one JSON row per
        check; a bucket says how it went. A check that failed while its run's logs are still a placeholder is pending,
        not a verdict: there is no evidence to quote yet. An exit code `gh` does not use for checks is an error."""
        code, out, _err = self.gh(["pr", "checks", url, "--json", "name,bucket,link,description"])
        try:
            rows = json.loads(out or "[]")
        except ValueError:
            rows = []
        rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        return _checks_state(self.driver, code, rows), rows

    def units(self):
        """Every unit of the run with the task that is still open for it."""
        with self.book() as data:
            tasks = [copy.deepcopy(t) for t in data["tasks"].values()]
        units = {}
        for task in tasks:
            unit = task["packet"].get("unit") or task["packet"]["task_id"]
            units.setdefault(unit, []).append(task)
        return {unit: group[-1] for unit, group in units.items()}

    def unit_flag(self, unit, key):
        """True when any attempt of the unit carries the flag: the pull request belongs to the unit, not to a task."""
        with self.book() as data:
            return any(
                task["packet"].get("unit") == unit and task.get(key)
                for task in data["tasks"].values()
            )

    def poll(self):
        """One non-blocking look at every unit's pull request: was it merged by hand, is it still a draft, how are
        its checks. A unit with no checks yet, or with checks still running, is looked at again on a later round: no
        fix round is raised and no other unit waits. The view taken here is the one `step_ci` acts on this round."""
        # The per-round cache is the Driver's: `gates.step_ci` reads the view taken here, and `ci_log` keeps the
        # failed-step logs beside it, so both live on the object the gate steps and the log helper are given.
        self.driver.views, self.driver.run_logs = {}, {}
        for unit, task in self.units().items():
            task_id = task["packet"]["task_id"]
            gate = task.get("gate")
            if (
                task["status"] in ("COMPLETE", "RELEASED")
                or gate in PARKED
                or gate in ("cleanup", "done")
            ):
                continue
            url = self.pr_of(task_id)
            if not url:
                continue
            if gate == "awaiting-merge":
                # main moves after the PR is ready: red checks there are a fix round (`step_merge` reads the view)
                self.poll_checks(task_id, url)
                continue
            state = self.pr_view(url)
            if state.get("error"):
                continue
            self.driver.views[url] = state
            if state.get("state") == "MERGED":
                self.merged_early(task_id, state)
                continue
            if gate in ("converged", "reviewing", "reviewed") and not self.unit_flag(
                unit, "pr_ready"
            ):
                self.ensure_draft(task_id, url, state)
            if gate in ("converged", "reviewing", "reviewed", "ready-for-ci"):
                self.poll_checks(task_id, url, state.get("headRefOid"))

    def ensure_draft(self, task_id, url, state):
        """The pull request is a draft while the review runs: a worker that opened it ready is put back."""
        unit = self.unit_of(task_id)
        if self.unit_flag(unit, "draft_done"):
            return
        if not state.get("isDraft"):
            code, _out, _err = self.gh(["pr", "ready", url, "--undo"])
            self.log(
                "draft", unit=unit, task=task_id, pr=url, outcome="draft" if code == 0 else "failed"
            )
            narrate.say("info", "fix", "%s back to draft while it is fixed" % url, task=task_id)
        self.mark(task_id, draft_done=True)

    def undraft(self, task_id, url):
        """The pull request goes back to draft before its code changes again, so nobody merges it half-fixed. On
        success readiness is cleared across the unit: `gh pr ready` runs again when the unit is back at ready-for-ci.
        False when `gh` refused; the caller holds the round and tries again on a later round."""
        unit = self.unit_of(task_id)
        code, out, err = self.gh(["pr", "ready", url, "--undo"])
        self.log(
            "draft", unit=unit, task=task_id, pr=url, outcome="draft" if code == 0 else "failed"
        )
        if code != 0:
            narrate.say(
                "warn",
                "fix",
                "could not put %s back to draft (%s); trying again next round"
                % (url, (err or out).strip()),
                task=task_id,
            )
            return False
        self.unready(unit)
        narrate.say("info", "fix", "%s back to draft while it is fixed" % url, task=task_id)
        return True

    def unready(self, unit):
        """Forget that the unit's pull request was readied: `unit_flag` ORs every attempt, so every one is cleared."""
        with self.book() as data:
            for task in data["tasks"].values():
                if task["packet"].get("unit") == unit:
                    task.pop("pr_ready", None)
                    task.pop("draft_done", None)

    def poll_checks(self, task_id, url, head=None):
        """The pull request's checks, once per round, and the head they were read on. The state is kept on the task;
        nothing waits for it."""
        state, rows = self.pr_checks(url)
        task = self.task(task_id)
        if (
            task.get("ci_state") != state
            or task.get("ci_rows") != rows
            or task.get("ci_head") != head
        ):
            self.mark(task_id, ci_state=state, ci_rows=rows, ci_head=head)
        self.log(
            "ci",
            unit=self.unit_of(task_id),
            task=task_id,
            gate=task.get("gate"),
            state=state,
            pr=url,
        )
        return state

    def merge_moved(self, task_id, url, view):
        """What an open pull request's merge state asks for: a conflict round, an update, or another poll. None when
        it can merge as it is and the caller goes on; True when the gate moved, False when nothing moves yet."""
        kind = merge_state(view)
        if kind == "conflict":
            self.conflict(task_id, url, view)
            return True
        if kind == "unknown":
            if view.get("error"):
                narrate.say(
                    "warn",
                    "merge",
                    "could not read whether %s can merge: %s; trying again" % (url, view["error"]),
                    task=task_id,
                )
            else:
                narrate.say(
                    "wait",
                    "merge",
                    "waiting for GitHub to compute whether %s can merge" % url,
                    task=task_id,
                )
            return False
        if kind == "behind":
            return self.update_branch(task_id, url, view) is not False
        return None

    def update_branch(self, task_id, url, view):
        """The pull request is behind its base: `gh pr update-branch` brings it up to date. A clean update needs no
        review (nobody wrote code): the unit is back at ready-for-ci, its checks run again and the PR stays ready. A
        failed update is a conflict round only when the PR now conflicts; any other failure goes to the Lead, since no
        rebase fixes an access or network error."""
        before = view.get("headRefOid")
        code, out, err = self.gh(["pr", "update-branch", url])
        unit = self.unit_of(task_id)
        if code == 0:
            self.log("update-branch", unit=unit, task=task_id, pr=url, outcome="updated")
            narrate.say(
                "info",
                "merge",
                "%s was behind its base; branch updated, CI runs again (no new review)" % url,
                task=task_id,
            )
            self.mark(
                task_id,
                gate="ready-for-ci",
                ci_state=None,
                ci_rows=None,
                ci_head=None,
                updated_from=before,
            )
            return False  # the new commits run CI again: polled on a later round
        excerpt = (err or out).strip()[:1500] or "gh pr update-branch exited %d" % code
        fresh = self.pr_view(url)
        if merge_state(fresh) == "conflict":
            self.log("update-branch", unit=unit, task=task_id, pr=url, outcome="conflict")
            evidence = ("update-branch", "gh pr update-branch %s" % url, code, excerpt)
            return self.conflict(task_id, url, fresh, evidence)
        self.log("update-branch", unit=unit, task=task_id, pr=url, outcome="failed")
        return self.request(
            task_id,
            "ci",
            "gh pr update-branch failed for %s and the pull request does not conflict: %s. Fix the access or "
            "update the branch by hand, then decide how the unit goes on." % (url, excerpt[:300]),
        )

    def conflict(self, task_id, url, view, evidence=None):
        """The pull request conflicts with its base: a fix round of its own, where a fresh worker rebases and the
        result goes to a delta re-review. The evidence is what `gh` said: the merge state, or the failed update."""
        unit = self.unit_of(task_id)
        status = "mergeStateStatus %s, mergeable %s" % (
            view.get("mergeStateStatus"),
            view.get("mergeable"),
        )
        check, command, code, excerpt = evidence or (
            "merge-state",
            "gh pr view %s --json mergeStateStatus,mergeable" % url,
            0,
            "the pull request conflicts with its base: %s" % status,
        )
        if evidence:
            excerpt = "%s (%s)" % (excerpt, status)
        log = next_path(self.run_dir / task_id, check, ".log")[0]
        log.write_text(excerpt + "\n", encoding="utf-8")
        self.log("conflict", unit=unit, task=task_id, pr=url, state=status)
        report = contracts.load(self.task(task_id)["report_path"], contracts.validate_report)
        item = {
            "id": "BR1",
            "kind": "blocking",
            "source": "conflict",
            "check": check,
            "command": command,
            "exit": code,
            "excerpt": excerpt,
            "log_path": str(log),
        }
        return self.fail_gate(task_id, report, "conflict", [item], rebase=True)

    def ci_items(self, task_id, url, rows):
        """The failing CI checks as evidence items. Each carries the capped tail of its own job's log (`gh run view
        --log-failed`, fetched once per run); the job's whole log is written to its `log_path`."""
        failing = [row for row in rows if str(row.get("bucket") or "").lower() in FAILED_BUCKETS]
        items = []
        for number, row in enumerate(failing, 1):
            name = row.get("name") or "ci"
            text = str(row.get("description") or "").strip()
            run = re.search(r"/runs/(\d+)", str(row.get("link") or ""))
            if run:
                logged = job_log(read_log(self.driver, run.group(1)), name)
                text = text if placeholder(logged) else logged or text
            items.append(self._ci_item(task_id, url, "CI%d" % number, name, text))
        if not items:
            items.append(self._ci_item(task_id, url, "CI1", "ci", "the checks failed"))
        return items

    def _ci_item(self, task_id, url, item_id, check, text):
        """One CI finding: the job's full text at `log_path`, its capped tail as the excerpt."""
        log = next_path(self.run_dir / task_id, "ci", ".log")[0]
        log.write_text(text + "\n", encoding="utf-8")
        return {
            "id": item_id,
            "kind": "blocking",
            "source": "ci",
            "check": check,
            "command": "gh pr checks %s" % url,
            "exit": 1,
            "excerpt": log_tail(text) or "the check failed",
            "log_path": str(log),
        }

    def put_status(self, task_id, status):
        """The ticket's Status cell; the archive unit has no row."""
        if self.task(task_id)["packet"]["unit"] != ARCHIVE:
            self.write_status(self.ticket_name(task_id), status)

    def ticket_name(self, task_id):
        return self.task(task_id)["packet"]["unit"].upper()

    def mark_merged(self, task_id, **fields):
        """The unit's cleanup gate, and for the archive unit the plan's ARCHIVED, in one transaction: a crash between
        them cannot leave a merged archive with an ACCEPTED plan."""
        with self.book() as data:
            data["tasks"][task_id].update(fields, gate="cleanup", updated_at=timeutil.iso())
            if data["tasks"][task_id]["packet"]["unit"] == ARCHIVE:
                engine().enter_plan_state(data, "ARCHIVED")

    def complete_merged(self, task_id, report):
        """The ledger's COMPLETE for a merged unit. A merge is final, so a report that fails the COMPLETE rules does
        not hold it back: the unit completes with the checks as reported, deferred.md says what went unverified, and no
        Lead is asked. False when the ledger refuses for another reason (a closed task); the Lead is asked then."""
        unverified = None
        gone = self.task(task_id)["packet"]["checkout"]
        if not Path(gone).exists():
            self.log("merged-checkout-missing", task=task_id, checkout=gone)
            narrate.say("warn", "merge", "merged, but its checkout %s is gone" % gone, task=task_id)
        try:
            with self.book() as data:
                try:
                    checkpoint(data, task_id, report, merged=True)
                except ValueError as error:
                    unverified = " ".join(str(error).split())
                    settle_merged(data, task_id, report)
        except ValueError as error:
            self.request(task_id, "merge", "merged, but the ledger rejected COMPLETE: %s" % error)
            return False
        if unverified:
            unit = self.unit_of(task_id)
            self._defer_unverified(task_id, unverified)
            self.log("merged-unverified", unit=unit, task=task_id, reason=unverified)
        self.flag_unverified_evidence(task_id)
        return True

    def merged_early(self, task_id, state):
        """The pull request was merged while the unit was still working: a merge is final. The unit is complete, its
        agents stop, and the findings it never settled go to deferred.md tagged merged-early."""
        task = self.task(task_id)
        unit = task["packet"]["unit"]
        url = task.get("pr") or self.pr_of(task_id)
        items = self._open_findings(unit)
        if items:
            self._defer_merged(unit, items, self.run_dir / unit)
        self.log("merged-early", unit=unit, task=task_id, pr=url, findings=len(items))
        self.close_request(task_id, "the pull request was merged before the unit finished")
        if not self.complete_merged(task_id, self.merged_report(task_id, url)):
            return
        commit = (state.get("mergeCommit") or {}).get("oid", "")
        self.put_status(
            task_id,
            "COMPLETE %s · merged %s%s"
            % (timeutil.iso()[:10], url, " (%s)" % commit[:7] if commit else ""),
        )
        if unit == ARCHIVE:
            verdict, lines = self.archive_evidence(task_id, task["packet"])
            self.log(
                "archive-evidence-merged-early", task=task_id, verdict=verdict, items=len(lines)
            )
        self.mark_merged(task_id, pr=url, merge_commit=commit)
        self.stop_unit_agents(unit, "merged")
        narrate.say("done", "merge", "merged early: %s" % url, task=task_id)

    def _reported_checks(self, task_id):
        """The checks of the newest attempt of the unit that holds a report: a fix round's own attempt usually has
        none yet, and the merge must not erase the evidence its worker already stored. [] only when none reported."""
        with self.book() as data:
            unit = data["tasks"][task_id]["packet"].get("unit") or task_id
            attempts = [
                tid for tid, t in data["tasks"].items() if (t["packet"].get("unit") or tid) == unit
            ]
            reports = {tid: data["tasks"][tid].get("report") for tid in attempts}
        ordered = [task_id] + [tid for tid in reversed(attempts) if tid != task_id]
        for tid in ordered:
            if reports.get(tid):
                return list(reports[tid].get("checks") or [])
        return []

    def merged_report(self, task_id, url):
        """The report a unit that was merged before it finished completes with: the merge settled it."""
        task = self.task(task_id)
        try:
            head = head_of(task["packet"]["checkout"])
        except (ValueError, OSError, subprocess.TimeoutExpired):
            head = "unknown"
        return {
            "contract": contracts.VERSION,
            "task_id": task_id,
            "status": "COMPLETE",
            "summary": "the pull request was merged before the unit finished",
            "acceptance_met": True,
            "files": [],
            "decisions": [],
            "interfaces": [],
            "remaining": [],
            "checks": self._reported_checks(task_id),
            "review_rounds": 0,
            "usage": "unknown",
            "head": head,
            "pr": url,
        }

    def _open_findings(self, unit):
        """The findings a unit has not settled: the newest fix round's items, or the ones a fix round is about to
        get."""
        _, findings, _ = self.unit_trail(unit)
        if findings:
            try:
                document = json.loads(Path(findings).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                document = None
            if isinstance(document, dict) and document.get("items"):
                return list(document["items"])
        with self.book() as data:
            tasks = [t for t in data["tasks"].values() if t["packet"].get("unit") == unit]
        # The newest attempt's fix items, by the order the record holds, as the trail above reads it.
        for task in newest_attempts(tasks):
            items = (task.get("fix") or {}).get("items") or []
            if items:
                return list(items)
        return []

    def _deferred_file(self):
        """deferred.md, created with its header on first use."""
        deferred = self.run_dir / "deferred.md"
        if not deferred.exists():
            deferred.write_text(
                "# Deferred findings: %s (for the major review after the driver ships)\n\n"
                % self.slug,
                encoding="utf-8",
            )
        return deferred

    def _defer_merged(self, unit, items, source):
        """The findings a merged-early unit never settled: deferred, tagged with the merge that ended it."""
        with self._deferred_file().open("a", encoding="utf-8") as stream:
            for item in items:
                stream.write(
                    "- %s %s [merged-early]: %s (%s)\n"
                    % (unit.upper(), item.get("id", "?"), item_text(item), source)
                )

    def _defer_unverified(self, task_id, reason):
        """A unit that merged without meeting the COMPLETE rules: the human reads what went unverified."""
        with self._deferred_file().open("a", encoding="utf-8") as stream:
            stream.write("- [merged-unverified] %s: %s\n" % (task_id, reason))

    def keep(self, task_id, reason):
        self.mark(task_id, gate="done", cleanup="kept: " + reason)
        narrate.say("warn", "cleanup", "worktree kept: %s" % reason, task=task_id)
