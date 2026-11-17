"""The durable record: one owner for the ledger every process of a run writes.

A run's ledger — `<state>/<slug>.json` — is what outlives a drive: its version, its kind marker, its
tasks, the ticket statuses beside them. The Lead's commands, the evidence runner and every round of the
driver read and write that one file, so the place that knows how it is locked, what an attempt is
called and which transition it may take is here, and a member the package is split into asks `Book`
instead of reaching for the file itself.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines, and because a rule the whole engine shares has one home.
"""

import copy
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import timeutil
from ..invariants import checked_write as ledger
from ..report_rules import require, require_report
from .records import ROUTE, require_completable, routed, whole

# The unit (and task id) of the driver's own archive: the only unit with no ticket row to write.
ARCHIVE = "archive"


def owned_paths(packet):
    """The checkout paths one attempt owns, resolved, and refused when it names something that would
    reach outside the checkout or cover more than a literal path."""
    root = Path(packet["checkout"]).resolve()
    owned = packet.get("owns")
    require(isinstance(owned, list) and owned, "owns must contain relative files or directories")
    paths = []
    for item in owned:
        require(
            isinstance(item, str) and item and not any(c in item for c in "*?[]"),
            "owns requires literal paths, not glob patterns",
        )
        rel = Path(item)
        require(
            not rel.is_absolute() and ".." not in rel.parts, "ownership must stay inside checkout"
        )
        target = (root / rel).resolve()
        require(target.is_relative_to(root), "ownership follows a symlink outside checkout")
        paths.append(target)
    return paths


# The packet fields a claim needs before it can reserve an attempt, and the backends it may name.
PACKET_TEXT = ("task_id", "worker", "plan", "objective", "checkout", "backend")
BACKENDS = ("codex", "claude", "pi")


def checked_texts(packet, key, required, invalid):
    """One packet field that is a non-empty list of non-empty strings, refused by which rule it broke."""
    require(isinstance(packet.get(key), list) and packet[key], required)
    require(all(isinstance(c, str) and c.strip() for c in packet[key]), invalid)


def checked_route(packet):
    """A routed packet names every route cell, its model and its unit; a legacy one keeps its metadata."""
    for key, allowed in ROUTE.items():
        require(packet.get(key) in allowed, key + " must be one of " + ", ".join(allowed))
    require(
        isinstance(packet.get("model"), str) and packet["model"].strip(),
        "a routed packet needs its model",
    )
    require(
        isinstance(packet.get("unit"), str) and packet["unit"].strip(),
        "a routed packet needs its unit",
    )


def checked_packet(packet):
    """The shape a packet has before it is written down: every field a claim, a gate and a summary read."""
    require(isinstance(packet, dict), "packet must be an object")
    for key in PACKET_TEXT:
        require(isinstance(packet.get(key), str) and packet[key].strip(), "missing " + key)
    require(packet["backend"] in BACKENDS, "backend must be codex, claude or pi")
    checked_texts(packet, "acceptance", "acceptance is required", "invalid acceptance criterion")
    require(isinstance(packet.get("checks"), list), "checks must be a list")
    require(
        all(isinstance(c, str) and c.strip() for c in packet["checks"]), "invalid check command"
    )
    require(
        isinstance(packet.get("depends_on", []), list)
        and all(isinstance(c, str) for c in packet.get("depends_on", [])),
        "invalid dependency list",
    )
    if routed(packet):
        checked_route(packet)


def checked_free(packet, tasks, paths):
    """The record has room for this attempt: its dependencies are done, and no unfinished attempt holds
    its worker or a path its own paths would cover."""
    for dep in packet.get("depends_on", []):
        require(
            dep in tasks and tasks[dep]["status"] == "COMPLETE", "dependency not complete: " + dep
        )
    for task in tasks.values():
        if task["status"] in ("COMPLETE", "RELEASED"):
            continue
        other = task["packet"]
        require(other["worker"] != packet["worker"], "worker already has an unfinished assignment")
        for left in paths:
            for right in owned_paths(other):
                require(
                    not (left.is_relative_to(right) or right.is_relative_to(left)),
                    "ownership overlaps task " + other["task_id"],
                )


def snapshot_of(checkout):
    """The checkout's snapshot, taken through the process seam the driver's package publishes.

    A `checkpoint` reads the tree it books, and the tests answer that read in process by patching
    `driver.checkout_snapshot`. The record therefore asks the package for it at call time: a name
    bound here at import time would answer the real process and ignore the seam a test installed.
    """
    from . import checkout_snapshot

    return checkout_snapshot(checkout)


def merged_snapshot_of(checkout):
    """The snapshot of a checkout the merge already consumed — None when the tree is gone, because a
    snapshot taken after a merge is evidence rather than a precondition. Read through the same seam."""
    from . import merged_snapshot

    return merged_snapshot(checkout)


def claim(data, packet):
    """Reserve one attempt in the ledger, or answer with the attempt that already holds its task id."""
    checked_packet(packet)
    snapshot = snapshot_of(packet["checkout"])
    packet = dict(packet, checkout=snapshot["checkout"])
    paths = owned_paths(packet)
    task_id = packet["task_id"]
    tasks = data["tasks"]
    if task_id in tasks:
        require(tasks[task_id]["packet"] == packet, "task ID already has a different assignment")
        return {"action": "existing", "task": tasks[task_id]}
    checked_free(packet, tasks, paths)
    tasks[task_id] = {
        "packet": packet,
        "status": "RESERVED",
        "runtime_id": None,
        "started_at": timeutil.iso(),
        "checkout_at_claim": snapshot,
    }
    return {"action": "reserved", "task": tasks[task_id]}


def bind(data, task_id, runtime_id):
    """Name the runtime agent that holds one attempt: from here its reports are its own."""
    task = data["tasks"][task_id]
    require(task["status"] not in ("COMPLETE", "RELEASED"), "assignment is closed")
    require(runtime_id.strip(), "runtime ID is empty")
    require(
        task["runtime_id"] in (None, runtime_id),
        "assignment already belongs to another runtime agent",
    )
    for other_id, other in data["tasks"].items():
        if other_id != task_id and other["status"] not in ("COMPLETE", "RELEASED"):
            require(
                other["runtime_id"] != runtime_id,
                "runtime agent already has an unfinished assignment",
            )
    task.update(runtime_id=runtime_id, status="ACTIVE")
    return task


def checkpoint(data, task_id, report, merged=False):
    """Record one worker's report. A COMPLETE report meets every rule the gate asks before it is stored;
    a BLOCKED one names its blocker and the next action, so neither can be settled by silence."""
    task = data["tasks"][task_id]
    require(isinstance(report, dict), "report must be an object")
    require(task["status"] not in ("COMPLETE", "RELEASED"), "assignment is closed")
    require(report.get("task_id") == task_id, "report task ID does not match")
    require_report(report)
    require(
        "review_rounds" not in report or whole(report["review_rounds"]),
        "review_rounds must be a whole number",
    )
    status = report["status"]
    if status == "COMPLETE":
        require(task["runtime_id"], "bind the runtime agent before completion")
        require_completable(task, report)
        task["finished_at"] = timeutil.iso()
    elif status == "BLOCKED":
        require(
            report.get("blocker") and report["remaining"],
            "blocked result needs a blocker and next action",
        )
    task.update(
        status=status,
        report=report,
        updated_at=timeutil.iso(),
        # A live worker with no tree is a real fault, so only the merge path tolerates a missing one.
        checkout_at_checkpoint=(merged_snapshot_of if merged else snapshot_of)(
            task["packet"]["checkout"]
        ),
    )
    return task


def settle_merged(data, task_id, report):
    """A merged unit is COMPLETE whatever its report says: the merge is final. The checks stay as the worker reported
    them; the caller records why the rules were not met."""
    task = data["tasks"][task_id]
    require(task["status"] not in ("COMPLETE", "RELEASED"), "assignment is closed")
    task["finished_at"] = timeutil.iso()
    task.update(
        status="COMPLETE",
        report=report,
        updated_at=timeutil.iso(),
        checkout_at_checkpoint=merged_snapshot_of(task["packet"]["checkout"]),
    )
    return task


def release(data, task_id, reason, cutoff=False):
    """Let one attempt go, with the reason it was let go: a released attempt stays readable."""
    task = data["tasks"][task_id]
    require(reason.strip(), "release requires a reason")
    require(task["status"] != "COMPLETE", "completed task cannot be released")
    task.update(status="RELEASED", release_reason=reason, cutoff=cutoff, updated_at=timeutil.iso())
    return task


def unit_summary(data, unit):
    """Failed reviews and cutoffs summed over every attempt at one logical unit."""
    attempts = [t for t in data["tasks"].values() if t["packet"].get("unit") == unit]
    require(attempts, "no attempts for unit " + unit)
    counted = (data.get("rounds") or {}).get(
        unit
    )  # the driver's own count; a run of the old driver has only the reports'
    failed = (
        counted
        if counted is not None
        else sum((t.get("report") or {}).get("review_rounds", 0) for t in attempts)
    )
    cutoffs = sum(1 for t in attempts if t.get("cutoff"))
    return {
        "unit": unit,
        "attempts": [task_summary(t) for t in attempts],
        "failed_reviews": failed,
        "cutoffs": cutoffs,
        "escalate": failed >= 2 or cutoffs >= 2,
    }


def task_summary(task):
    summary = {
        "task_id": task["packet"]["task_id"],
        "worker": task["packet"]["worker"],
        "status": task["status"],
        "runtime_id": task["runtime_id"],
    }
    for key in ("unit", *ROUTE, "model"):
        if key in task["packet"]:
            summary[key] = task["packet"][key]
    return summary


class Book:
    """The run's ledger, held by the Driver: one lock, one event log, one surface over the record.

    Calling it is a locked block — what the block changes is written when it ends, with one `state`
    event per task whose gate or status moved. The reads and the marks below are what a step, a
    collaborator and the Lead's commands use instead of touching the file, so no caller has to know
    how the record is locked or where it lives.
    """

    def __init__(self, driver):
        self.driver = driver

    @property
    def path(self):
        return self.driver.ledger_path

    @contextmanager
    def __call__(self):
        """The ledger, plus one `state` event for every task whose gate or status the block changed. The line is
        appended before the ledger is written, so the log is never behind the state it describes."""
        with ledger(self.path) as data:
            before = {
                task_id: (task.get("gate"), task["status"])
                for task_id, task in data["tasks"].items()
            }
            yield data
            for task_id, task in data["tasks"].items():
                old, new = before.get(task_id, (None, None)), (task.get("gate"), task["status"])
                if old == new:
                    continue
                packet = task["packet"]
                route = {
                    k: packet[k] for k in ("size", "difficulty", "class", "model") if k in packet
                }
                self.driver.log(
                    "state",
                    unit=packet.get("unit") or task_id,
                    task=task_id,
                    gate=[old[0], new[0]],
                    status=[old[1], new[1]],
                    route=route if task_id not in before and route else None,
                )

    def task(self, task_id):
        """One attempt as the record holds it, as a copy: a caller changes it under the lock or not at all."""
        with self() as data:
            return copy.deepcopy(data["tasks"][task_id])

    def unit_of(self, task_id):
        """The logical unit one attempt belongs to; a task id that names itself when the record holds none."""
        with self() as data:
            task = data["tasks"].get(task_id)
        return (task["packet"].get("unit") or task_id) if task else task_id

    def mark(self, task_id, **fields):
        """Set fields on one attempt, stamped with when it was marked."""
        with self() as data:
            data["tasks"][task_id].update(fields, updated_at=timeutil.iso())

    def write_status(self, ticket_id, status):
        """The plan ticket's Status cell; the archive unit has no row to write."""
        if ticket_id == ARCHIVE.upper():
            return
        with self() as data:
            data.setdefault("ticket_status", {})[ticket_id] = status

    def awaiting(self):
        """Tickets whose PR is not merged yet, or whose request the Lead has not answered: the run stays up."""
        with self() as data:
            return any(
                t.get("gate") in ("ready-for-ci", "awaiting-merge", "requested")
                for t in data["tasks"].values()
            )


class BookSurface:
    """The published surface of the durable record.

    Each name answers with the one `Book` the Driver holds, so a caller and a test keep asking the
    Driver while the record keeps one owner.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def awaiting(self):
        """Tickets whose PR is not merged yet, or whose request the Lead has not answered: the run stays up."""
        return self.book.awaiting()

    def task(self, task_id):
        """One attempt as the record holds it, as a copy."""
        return self.book.task(task_id)

    def unit_of(self, task_id):
        """The logical unit one attempt belongs to."""
        return self.book.unit_of(task_id)

    def mark(self, task_id, **fields):
        """Set fields on one attempt, and say when it was set."""
        return self.book.mark(task_id, **fields)

    def write_status(self, ticket_id, status):
        """The plan ticket's Status cell; the archive unit has no row to write."""
        return self.book.write_status(ticket_id, status)
