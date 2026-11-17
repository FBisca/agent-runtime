"""What each unit delivered, as the spec section the archive promotes it in.

An archive's spec section names the scenarios the plan holds and the test nodes that prove them. A node
is spoken by the unit's newest report, read from disk so one the Lead added after a request counts, and
a scenario with no node is a gap the archive asks about rather than a section written over. This module
keeps that reading, so what a plan delivered is derived from the record and never restated.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines and no class twenty-five public methods.
"""

from typing import Any

from .. import contracts
from .book import ARCHIVE


class Coverage:
    """What each unit delivered, mixed into the Driver: the archive's spec section is cited from here.

    A member reads the record's reports through the driver it belongs to.
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
        """{scenario id: its test nodes}: the tickets' `covers` and the nodes a person recorded."""
        covers = self.ticket_covers()
        return {
            s.id: list(dict.fromkeys(covers.get(s.id, []))) for s in self.plan_model().scenarios
        }

    @staticmethod
    def flat(text):
        return " ".join(str(text).split())
