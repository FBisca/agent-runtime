"""A worker's report, the gate it is held at, and the findings a review leaves open.

A report takes one gate: the ticket's scenarios must be covered, the evidence must have run, and the
review must have passed. A finding the review raised stays open until a later review resolves it, and
the gate that holds a unit reads the open ones from the record rather than from the report it is
looking at. The gate, its refusal, and the open findings are one module because they are one question:
what still stands between this unit and the next gate.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines and no class twenty-five public methods.
"""

import shlex
from typing import Any

from .. import events, narrate, verify
from ..invariants import next_path
from ..timeutil import iso as now
from .answers import ARCHIVE_REBUILDS
from .book import ARCHIVE, unit_summary
from .report import cell, item_text, short
from .review import attempt_trail


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
        """verify's invariants on a YAML plan's ticket: integrity, covers and red; its overlaps go to the Lead."""
        ticket = packet["unit"].upper()
        with self.book() as data:
            statuses = data.get("ticket_status") or {}
            done = {k for k, v in statuses.items() if str(v).strip().upper().startswith("COMPLETE")}
            earlier = [
                t["report"]
                for tid, t in data["tasks"].items()
                if tid != task_id and t["packet"].get("unit") == packet["unit"] and t.get("report")
            ]
        try:
            files = verify.changed(packet["checkout"], self.args.base)
            overlaps = verify.overlaps(self.model, ticket, files, done=done)
            findings = verify.invariants(
                self.model,
                ticket,
                verify.with_evidence(report, earlier),
                repo=packet["checkout"],
                base=self.args.base,
                layout=self.layout,
                files=files,
            )
        except verify.VerifyError as error:
            return "error", [str(error)[:500]]
        self.warn_overlaps(packet["unit"], task_id, overlaps)
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

    def warn_overlaps(self, unit, task_id, overlaps):
        """Tell the Lead, never the worker, of a file the unit changed that another unfinished ticket lists: one
        `overlap` event and one pane line per file and ticket, however many rounds see it again. The plan close
        lists them with the run's notes."""
        seen = {
            (e.get("unit"), e.get("file"), e.get("ticket"))
            for e in events.read(self.run_dir / "events.jsonl")
            if e["kind"] == "overlap"
        }
        for overlap in overlaps:
            if (unit, overlap["file"], overlap["ticket"]) in seen:
                continue
            self.log(
                "overlap",
                unit=unit,
                task=task_id,
                file=overlap["file"],
                ticket=overlap["ticket"],
                text=overlap["excerpt"],
            )
            narrate.say("warn", "verify", overlap["excerpt"], task=task_id)

    def fail_gate(self, task_id, report, source, items, **fix_details):
        """A gate failed: raise review_rounds, then a fix round, or the escalation hook on the 3rd failure.

        The round, the report, the next phase (a fix round, or the Lead's request) and the draft the pull request
        still owes are booked in one ledger write, before any git or gh call: a restart after a crash anywhere later
        finds the gate already failed once, and the draft it owes is in `fix`. `fix_details` is the gate's own word
        for the round: `rebase` (a conflict re-reads its base)."""
        unit = self.unit_of(task_id)
        owed = None
        if unit != ARCHIVE and self.unit_flag(unit, "pr_ready"):
            owed = self.pr_of(
                task_id
            )  # the PR was readied: it goes back to draft before anyone changes its code
        fix = {
            "source": source,
            "items": items,
            "attempt": None,
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
            # One gate failing again is what asks the Lead; a different gate failing is a new problem a fix round
            # may still solve. A ledger of an older driver has no per-gate count: it starts at 0.
            failures = data.setdefault("failures", {}).setdefault(unit, {})
            failures[source] = failures.get(source, 0) + 1
            fix["failures"] = failures[source]
            self.log("round", unit=unit, task=task_id, source=source, attempt=rounds[unit])
            if unit == ARCHIVE:
                self.book_report(data, task_id, report)
                rebuild, asked = self._archive_failure(data, task_id, source, remaining)
            else:
                self.book_report(data, task_id, report, review_rounds=1)
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
        """One `finding` event per item of the failed round."""
        for item in items:
            self.log_finding(unit, task_id, source, item)

    def _gate_refusal(self, data, task_id, unit, source, fix):
        """(kind, reason, the requested unit) once the unit has spent its fix rounds, else None. A unit that already
        holds an open request is asked nothing (its unit is None), and one under the cap takes a fix round instead."""
        if fix["failures"] <= self.fix_cap(unit):
            return None
        last = "; ".join(short(item) for item in fix["items"])
        reason = "%s failed its %s gate %d times; last: %s" % (unit, source, fix["failures"], last)
        if source == "verify" and self.covers_without_test(unit, fix["items"]):
            reason = (
                "%s failed its covers gate %d times because the ticket's Paths can hold no test, "
                "so the gate can never pass: edit the plan's paths to hold a test and re-approve it "
                "(`agent-orchestrator plan approve %s --reason <why>`), then decide `retry`; last: %s"
                % (unit, fix["failures"], self.plan_path, last)
            )
        asked = self.book_request(data, task_id, "gate", reason, fix=fix)
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

    def log_finding(self, unit, task_id, source, item):
        """One `finding` event: an item that goes to a fix round."""
        self.log(
            "finding",
            unit=unit,
            task=task_id,
            source=source,
            finding_kind="blocking",
            id=item["id"],
            summary=cell(item_text(item), 200),
            file=item.get("file"),
            line=item.get("line"),
            rule=item.get("rule"),
        )

    def unit_trail(self, unit):
        """(last reviewed commit, newest findings.json, newest report.json) over the unit's attempts; "" when none yet."""
        return attempt_trail(self, unit)
