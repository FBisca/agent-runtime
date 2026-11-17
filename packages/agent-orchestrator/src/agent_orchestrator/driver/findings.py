"""A worker's report, the gate it is held at, and the findings a review leaves open.

A report takes one gate: the ticket's scenarios must be covered, the evidence must have run, and the
review must have passed. A finding the review raised stays open until a later review resolves it, and
the gate that holds a unit reads the open ones from the record rather than from the report it is
looking at. The gate, its refusal, and the open findings are one module because they are one question:
what still stands between this unit and the next gate.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines and no class twenty-five public methods.
"""

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .. import contracts, invariants, narrate, verify
from ..invariants import next_path
from ..report_rules import require
from ..timeutil import iso as now
from .answers import ARCHIVE_REBUILDS, NO_TEST_MENU
from .book import ARCHIVE, unit_summary
from .report import cell, item_text, short
from .review import attempt_trail
from .state import write_atomic


class Findings:
    """The report gate and the findings a review leaves open, mixed into the Driver.

    A member decides what still stands between a unit and its next gate; it reads the record and the
    reports through the driver it belongs to.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def gate(self, task_id, packet, report):
        """The gate for a unit's report: ("PASS" | "FAIL" | "error", findings as items or the reason)."""
        # Say the report was picked up before the invariants read the checkout.
        narrate.say(
            "info",
            "verify",
            "%s reported %s: checking its report" % (task_id, report.get("status") or "?"),
            task=task_id,
        )
        # The archive is the driver's own unit: its pull request's CI is its gate. A ticket is gated by verify.
        if packet["unit"] == ARCHIVE:
            return "PASS", []
        return self.verify_ticket(task_id, packet, report)

    def verify_ticket(self, task_id, packet, report):
        """verify's invariants on a YAML plan's ticket: scope, integrity and covers."""
        ticket = packet["unit"].upper()
        try:
            findings = verify.invariants(
                self.model,
                ticket,
                report,
                repo=packet["checkout"],
                base=self.args.base,
                layout=self.layout,
            )
        except verify.VerifyError as error:
            return "error", [str(error)[:500]]
        if not findings:
            return "PASS", []
        log = next_path(self.run_dir / task_id, "verify", ".log")[0]
        log.write_text(
            "".join("FAIL %(check)s: %(excerpt)s\n" % f for f in findings), encoding="utf-8"
        )
        command = "verify %s --repo %s --base %s" % (
            ticket,
            shlex.quote(str(packet["checkout"])),
            shlex.quote(self.args.base),
        )
        return "FAIL", verify.items(findings, command, log)

    def fail_gate(self, task_id, report, source, items, **fix_details):
        """A gate failed: raise review_rounds, then a fix round, or the escalation hook on the 3rd failure.

        The round, the report, the next phase (a fix round, or the Lead's request) and the draft the pull request
        still owes are booked in one ledger write, before any git or gh call: a restart after a crash anywhere later
        finds the gate already failed once, and the draft it owes is in `fix`. `fix_details` is the gate's own word
        for the round: `checks_run`, `answer_ids`, and `rebase` (a conflict re-reads its base)."""
        unit = self.unit_of(task_id)
        path = self.report_file(task_id)
        owed = None
        if unit != ARCHIVE and self.unit_flag(unit, "pr_ready"):
            owed = self.pr_of(
                task_id
            )  # the PR was readied: it goes back to draft before anyone changes its code
        fix = {
            "source": source,
            "items": items,
            "attempt": None,
            "checks_run": list(fix_details.get("checks_run") or ()),
            "answer_ids": list(fix_details.get("answer_ids") or ()),
            "rebase": True if fix_details.get("rebase") else None,
            "draft": owed,
        }
        remaining = [short(item) for item in items]
        rebuild = (
            False  # an archive conflict is a base that moved: rebuilt before a person is asked
        )
        asked = None
        with self.book() as data:  # the driver's count: the report's review_rounds is never read
            rounds = data.setdefault("rounds", {})
            if (
                unit not in rounds
            ):  # a ledger of the old driver: start from the rounds its reports recorded
                rounds[unit] = unit_summary(data, unit)["failed_reviews"]
            rounds[unit] += 1
            fix["attempt"] = rounds[unit]
            self.log("round", unit=unit, task=task_id, source=source, attempt=rounds[unit])
            if unit == ARCHIVE:
                self.book_report(data, task_id, report, path, remaining=remaining)
                rebuild, asked = self._archive_failure(data, task_id, source, remaining)
            else:
                self.book_report(data, task_id, report, path, review_rounds=1, remaining=remaining)
                self._gate_findings(unit, task_id, source, items)
                asked = self._gate_refusal(data, task_id, unit, source, fix)
                if asked is None:
                    data["tasks"][task_id].update(gate="fix-pending", fix=fix, updated_at=now())
        self._settle_failure(task_id, unit, owed, fix, rebuild, asked)

    def _archive_failure(self, data, task_id, source, remaining):
        """An archive gate failed: the branch is rebuilt once when its base moved, else the Lead decides. (rebuilt, the
        request to announce or None)."""
        rebuilds = data.setdefault("rebuilds", {})
        if source == "conflict" and rebuilds.get(ARCHIVE, 0) < ARCHIVE_REBUILDS:
            rebuilds[ARCHIVE] = rebuilds.get(ARCHIVE, 0) + 1
            return True, None
        reason = (
            "%s: %s. `retry` rebuilds the archive branch from the current base and pushes it again, `skip` "
            "leaves the archive to you"
            % (
                "the archive pull request conflicts with its base"
                if source == "conflict"
                else "the archive failed its %s gate" % source,
                "; ".join(remaining),
            )
        )
        asked = self.book_request(data, task_id, source, reason)
        return False, (source, reason, asked) if asked is not None else None

    def _gate_findings(self, unit, task_id, source, items):
        """One `finding` event per item of the failed round, each with the kind a fix round reads."""
        for item in items:
            self.log_finding(
                unit,
                task_id,
                source,
                item.get("kind", "blocking") if isinstance(item, dict) else "blocking",
                item,
            )

    def _gate_refusal(self, data, task_id, unit, source, fix):
        """(kind, reason, the requested unit) once the unit has spent its fix rounds, else None. A unit that already
        holds an open request is asked nothing (its unit is None), and one under the cap takes a fix round instead."""
        if fix["attempt"] <= self.fix_cap(unit):
            return None
        last = "; ".join(short(item) for item in fix["items"])
        menu = None
        reason = "%s failed its gates %d times; last: %s" % (unit, fix["attempt"], last)
        if source == "verify" and self.covers_without_test(unit, fix["items"]):
            menu = NO_TEST_MENU
            reason = (
                "%s failed its covers gate %d times because the ticket's Paths can hold no test, "
                "so the gate can never pass: add a test file, or a directory that holds its tests, "
                "to its paths (`amend`); last: %s" % (unit, fix["attempt"], last)
            )
        asked = self.book_request(data, task_id, "gate", reason, fix=fix, menu=menu)
        return ("gate", reason, asked) if asked is not None else None

    def _settle_failure(self, task_id, unit, owed, fix, rebuild, asked):
        """What follows the round's single ledger write: the archive rebuilt or undrafted, a readied pull request back
        in draft, and the Lead asked when the round raised a request (a unit of None: one was already open)."""
        if rebuild:
            # The lead's `retry`, verbatim: the branch is rebuilt from the current base, so the spec section and its
            # number are derived from what actually merged.
            self.retry_archive(task_id)
            return
        if unit == ARCHIVE:
            self.undraft_archive(task_id)
        elif owed and self.undraft(task_id, owed):
            self.mark(task_id, fix=dict(fix, draft=None))
        if asked is not None and asked[2] is not None:
            self.announce_request(task_id, *asked)

    def covers_without_test(self, unit, items):
        """Whether a verify failure is a covers failure on a ticket whose Paths hold no test (the lint's predicate)."""
        ticket = next(
            (t for t in (self.model.tickets if self.model else []) if t.id == unit.upper()), None
        )
        return (
            ticket is not None
            and any(isinstance(i, dict) and i.get("check") == "covers" for i in items)
            and verify.holds_no_test(ticket, self.repo)
        )

    def fix_cap(self, unit):
        """The fix rounds a unit may use: its `Max fix rounds:` plan line, else --max-fix-rounds."""
        return ((self.last_plan or {}).get("caps") or {}).get(
            unit.upper(), self.args.max_fix_rounds
        )

    def gate_name(self):
        """The gate that reads a plan: verify."""
        return "verify"

    def log_finding(self, unit, task_id, source, kind, item):
        """One `finding` event: a blocking item that goes to a fix round, or a deferred one that goes to deferred.md."""
        found = item.get("id") if isinstance(item, dict) else None
        text = item_text(item) if isinstance(item, dict) else item
        self.log(
            "finding",
            unit=unit,
            task=task_id,
            source=source,
            finding_kind=kind,
            id=found,
            summary=cell(text or "", 200),
        )

    def defer(self, task_id, items, source_path):
        """Items that go to deferred.md and the retro only: logged as findings, appended with their source file."""
        unit = self.unit_of(task_id)
        for item in items:
            self.log_finding(unit, task_id, "review", item.get("kind", "deferred"), item)
        if not items:
            return
        deferred = self.run_dir / "deferred.md"
        if not deferred.exists():
            deferred.write_text(
                "# Deferred findings: %s (for the major review after the driver ships)\n\n"
                % self.slug,
                encoding="utf-8",
            )
        with deferred.open("a", encoding="utf-8") as stream:
            for item in items:
                stream.write(
                    "- %s %s%s: %s%s (%s)\n"
                    % (
                        unit.upper(),
                        item["id"],
                        " [%s]" % item["kind"] if item.get("kind") else "",
                        item_text(item),
                        " (blocking, but outside the re-review's delta)"
                        if item.get("scope")
                        else "",
                        source_path,
                    )
                )

    def hold_open_findings(self, task_id, task, open_items):
        """A review that passes while the unit still has open blocking findings does not advance it: the reviewer is
        asked to close each with a resolution it can evidence, or to keep each blocking. Past the warnings the Lead is
        asked; either way the gate holds before CI."""
        record = self.open_path(self.unit_of(task_id))
        reason = (
            "the review leaves blocking finding(s) %s open with no supported resolution, so the unit cannot go on to "
            "CI. Close each one with a `resolutions` entry {id, status fixed|disproven, commit (the commit reviewed), "
            "note, evidence {command, exit 0, excerpt, log_path}} whose command and exit also appear in a `checks_run` "
            "entry at that commit, or keep it blocking with its id. The open findings are in %s."
            % (", ".join(item["id"] for item in open_items), record)
        )
        return self.warn_sender(task_id, "review", task["review"]["path"], reason, accept=True)

    def open_path(self, unit):
        return self.run_dir / unit / "open-findings.json"

    def open_blockers(self, unit, exclude=None):
        """The unit's open blocking findings: the record on disk, or one replayed from its reviews when a run of the
        previous version left none. A record that cannot be read is replayed too: a damaged file never clears one."""
        path = self.open_path(unit)
        if path.exists():
            try:
                return contracts.load(path, contracts.validate_open_findings)["items"]
            except contracts.ContractError as error:
                self.log(
                    "open-findings",
                    unit=unit,
                    path=str(path),
                    state="unreadable",
                    error=str(error)[:300],
                )
        return self.replay_blockers(unit, exclude)

    def replay_blockers(self, unit, exclude=None):
        """The open set rebuilt from the unit's reviews, oldest first: the rule `record_findings` applies, so a restart
        and a re-read of the same review agree."""
        open_items = []
        for path, review in self.reviews_of(unit):
            if path == exclude:
                continue
            try:
                contracts.validate_review(review)
            except contracts.ContractError:
                continue
            open_items = self.folded_findings(
                open_items, contracts.review_items(review)[0], self.resolutions_of(review)
            )
        return open_items

    def record_blockers(self, unit, review_path, review, blocking):
        """Apply one accepted review to the unit's open set and write the record: a resolution closes its id, a
        blocking item keeps an id open with the payload it was first raised with. Re-reading a review that was already
        applied leaves the set as it was."""
        open_items = self.folded_findings(
            self.open_blockers(unit, exclude=review_path), blocking, self.resolutions_of(review)
        )
        path = self.open_path(unit)
        if open_items or path.exists():
            # No record for a unit that has never had a blocker; a record emptied by closure stays as the answer.
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_atomic(
                path, json.dumps(contracts.make_open_findings(unit, open_items), indent=2) + "\n"
            )
        return open_items

    @staticmethod
    def resolutions_of(review):
        """Only a contract 2 review closes a finding: contract 1 has no `resolutions`, and `validate_review` refuses a
        producer that writes one there, so a legacy review never empties the open set."""
        return review.get("resolutions") if review.get("contract") == 2 else None

    @staticmethod
    def folded_findings(open_items, blocking, resolutions):
        """One review applied to the open set: what it resolves closes, what it raises blocking opens (or stays open
        with the payload it was first raised with). `blocking` comes filtered by the review's delta."""
        closed = {r["id"] for r in resolutions or [] if isinstance(r, dict)}
        kept = [item for item in open_items if item.get("id") not in closed]
        known = {item.get("id") for item in kept}
        for item in blocking:
            if isinstance(item, dict) and item.get("id") not in known:
                kept.append(item)
                known.add(item.get("id"))
        return kept

    def reviews_of(self, unit):
        """The unit's reviews, oldest first, as (path, document); a review file that no longer reads is skipped."""
        with self.book() as data:
            paths = [
                path
                for task in data["tasks"].values()
                if task["packet"].get("unit") == unit
                for path in invariants.review_paths(task)
            ]
        found, seen = [], set()
        for path in paths:
            if not path or path in seen:
                continue
            seen.add(path)
            try:
                document = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(document, dict):
                found.append((path, document))
        return found

    def finding_ids(self, unit):
        """Every blocking id the unit's reviews have raised, open or closed: the ids a resolution may name."""
        ids = set()
        for _path, review in self.reviews_of(unit):
            try:
                contracts.validate_review(review)
            except contracts.ContractError:
                continue
            ids.update(item["id"] for item in contracts.review_items(review)[0])
        return ids

    def check_resolutions(self, unit, review):
        """A resolution names a blocking finding the unit actually had: one that names no earlier finding claims a
        closure that never applied."""
        if review.get("contract") == 1:
            return
        unknown = [
            r["id"]
            for r in review.get("resolutions") or []
            if r["id"] not in self.finding_ids(unit)
        ]
        require(
            not unknown,
            "review: resolution(s) %s name no blocking finding this unit has had; a resolution closes an earlier "
            "finding" % ", ".join(unknown),
        )

    def unit_trail(self, unit):
        """(last reviewed commit, newest findings.json, newest report.json) over the unit's attempts; "" when none yet."""
        return attempt_trail(self, unit)

    def earlier_ids(self, unit, current_review):
        """The ids of every finding the unit has had before this review: fix-round findings and earlier review items."""
        found = set()
        with self.book() as data:
            tasks = [t for t in data["tasks"].values() if t["packet"].get("unit") == unit]
        paths = [self.run_dir / t["packet"]["task_id"] / "findings.json" for t in tasks]
        paths += [
            Path(path)
            for t in tasks
            for path in invariants.review_paths(t)
            if path and path != current_review
        ]
        for path in paths:
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(document, dict):
                continue
            for item in (
                (document.get("items") or [])
                + (document.get("major") or [])
                + (document.get("deferred") or [])
            ):
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    found.add(item["id"])
        return found

    @staticmethod
    def references(item):
        """The finding ids an item says it is about: its `finding`, `findings`, `ref`, `refs` or `references` field."""
        found = set()
        for key in ("finding", "findings", "ref", "refs", "references"):
            value = item.get(key)
            values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
            found.update(v for v in values if isinstance(v, str))
        return found

    def scope_filter(self, task_id, packet, delta, blocking):
        """(blocking items to gate on, deferred copies of the rest). In a re-review a blocking item whose file the delta
        did not change and which is not about an earlier finding is outside the scope: it is deferred and logged as a
        `scope` event, never gated. When the delta cannot be read nothing is filtered."""
        checkout = Path(packet["checkout"])
        diff = subprocess.run(
            ["git", "-C", str(checkout), "diff", "--name-only", delta["from"] + "..HEAD"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if diff.returncode != 0:
            return blocking, []
        changed = set(diff.stdout.split())
        unit = packet["unit"]
        earlier = self.earlier_ids(unit, self.task(task_id)["review"]["path"])
        kept, outside = [], []
        for item in blocking:
            file = str(item.get("file") or "")
            if file.startswith(str(checkout) + "/"):
                file = file[len(str(checkout)) + 1 :]
            file = file[2:] if file.startswith("./") else file
            if file in changed or item["id"] in earlier or self.references(item) & earlier:
                kept.append(item)
                continue
            outside.append(
                dict(item, kind="deferred", answer_required=False, scope="outside-delta")
            )
            self.log(
                "scope",
                unit=unit,
                task=task_id,
                id=item["id"],
                file=file,
                delta_from=delta["from"],
                why="blocking item outside the delta, about no earlier finding",
            )
        return kept, outside
