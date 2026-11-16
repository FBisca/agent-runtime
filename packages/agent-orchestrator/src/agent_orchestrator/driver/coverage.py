"""What each unit delivered, as the spec section the archive promotes it in.

An archive's spec section names the scenarios the plan holds and the test nodes that prove them. A node
is spoken by the unit's newest report, read from disk so one the Lead added after a request counts, and
a scenario with no node is a gap the archive asks about rather than a section written over. This module
keeps that reading, so what a plan delivered is derived from the record and never restated.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines and no class twenty-five public methods.
"""

import subprocess
from typing import Any

from .. import contracts, narrate
from .answers import IntentUnsettled
from .book import ARCHIVE


class Coverage:
    """What each unit delivered, mixed into the Driver: the archive's spec section is cited from here.

    A member reads the record's reports and the plan's oracles through the driver it belongs to.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def ticket_covers(self):
        """{scenario: [node, ...]}: what each unit ended with, plus the nodes `complete` recorded for a person ticket.

        The unit speaks with its newest report, read from disk so a node the Lead added after a request counts, with
        the ledger's copy as the fallback; an earlier attempt only fills a scenario that report does not name. A
        superseded attempt may claim a test the attempt that replaced it reverted, and a promoted spec has to cite
        what merged, not what was tried."""
        with self.book() as data:
            tasks = [t for t in data["tasks"].values() if t["packet"].get("unit") != ARCHIVE]
            person = list((data.get("person_covers") or {}).values())
        found: dict[str, list[str]] = {}

        def extend(covers):
            for sid, nodes in (covers or {}).items():
                for node in nodes:
                    if node not in found.setdefault(sid, []):
                        found[sid].append(node)

        spoken: set[str] = set()
        for task in sorted(tasks, key=lambda t: str(t.get("updated_at") or ""), reverse=True):
            unit = task["packet"]["unit"]
            if unit in spoken:
                continue
            spoken.add(unit)
            report = None
            if task.get("report_path"):
                try:
                    report = contracts.load(task["report_path"], contracts.validate_report)
                except (contracts.ContractError, ValueError):
                    report = None
            extend((report or task.get("report") or {}).get("covers"))
        for covers in person:
            extend(covers)
        return found

    def scenario_nodes(self):
        """{scenario id: its test nodes}: the tickets' `covers`, then the scenario's own placed oracle paths."""
        covers = self.ticket_covers()
        placed = {
            scenario.id: [rel for rel, _ in self.scenario_targets(scenario)]
            for scenario in self.plan_model().scenarios
        }
        return {
            s.id: list(dict.fromkeys(covers.get(s.id, []) + placed.get(s.id, [])))
            for s in self.plan_model().scenarios
        }

    @staticmethod
    def flat(text):
        return " ".join(str(text).split())

    def start_gaps(self, _plan):
        """Spawn the advisory gaps reviewer once, after the archive PR has merged (S6). Its failure never blocks
        anything. Returns 1 when it acted, 0 otherwise."""
        if self.plan_state() != "ARCHIVED":
            return 0
        with self.book() as data:
            if data.get("gaps"):
                return 0
            merges = [
                (tid, t.get("pr"), t.get("merge_commit"))
                for tid, t in data["tasks"].items()
                if t.get("merge_commit")
            ]
        name = self.gaps_name()
        path, task_md = self.run_dir / "gaps.md", self.run_dir / "gaps-task.md"
        task_md.write_text(self.gaps_brief(path, merges), encoding="utf-8")
        # The spawn takes the ledger for its own bookkeeping, so it happens outside this block: a nested block on
        # the one ledger waits for a lock this thread already holds.
        gaps = self.start_gaps_reviewer(name, path, task_md)
        with self.book() as data:
            data["gaps"] = gaps
        return 1

    def gaps_name(self):
        """The pane name of the gaps reviewer: the run's slug, shortened so the suffix fits a pane."""
        return self.slug[: 32 - len("-gaps")].rstrip("-_") + "-gaps"

    def gaps_brief(self, path, merges):
        """The task file the gaps reviewer reads: what merged, what to look for and what to write."""
        return "\n".join(
            [
                "# Gaps review of %s: advisory, paths only" % self.slug,
                "",
                "Read these yourself; nothing is pasted here.",
                "- Plan: %s (its Decisions, Scenarios, Risks and Out of scope)" % self.plan_path,
                "- Repository: %s; the built work is merged on %s" % (self.repo, self.args.base),
                *[
                    "- Merged %s: %s %s" % (tid, pr or "no PR url", (commit or "")[:12])
                    for tid, pr, commit in merges
                ],
                "",
                "Look for what the plan promised and the final code lacks: a scenario with no real test, a decision not "
                "reflected, a risk left open, behaviour nobody asked for. Findings become plan edits, so be specific.",
                "Advisory only: it blocks nothing. Touch nothing in the repository.",
                "",
                "Write %s for the user: one line per gap (missing, partial, contradicts, unrequested) with the file or "
                "scenario it concerns. If there are none, say so." % path,
                "Then call the agent-runtime `report` tool with kind=complete and one line: `<n> gaps - %s`."
                % path,
                "",
            ]
        )

    def start_gaps_reviewer(self, name, path, task_md):
        """The gaps reviewer's record: the attempt it started, or the failure that ended it before it began."""
        try:
            backend, model = self.gate_step("gaps", "medium")
            effort = self.step_effort("gaps", "medium")
            _, assignment_id = self.ensure_agent(
                name,
                "gaps",
                "gaps",
                {
                    "kind": "worker",
                    "name": name,
                    "task": "Read %s and follow it." % task_md,
                    "cwd": str(self.repo),
                    "cli": backend,
                    "model": model,
                    "effort": effort,
                },
            )
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            return self.gaps_failed(name, path, error)
        narrate.say(
            "start",
            "gaps",
            "started gaps reviewer %s · %s · %s · %s"
            % (name, self.gate_label("gaps", "medium"), model, effort),
        )
        return {"name": name, "path": str(path), "assignment_id": assignment_id, "ended": False}

    def gaps_failed(self, name, path, error):
        """A gaps reviewer that never started: the intents it left are released and the record says why."""
        if isinstance(error, IntentUnsettled):
            self.release_intents(error.operations, "no attempt is waiting on it")
        narrate.say("fail", "gaps", "gaps reviewer not started: %s" % error)
        return {"name": name, "path": str(path), "ended": True, "error": str(error)}
