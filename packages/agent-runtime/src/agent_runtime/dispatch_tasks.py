"""The tasks table: a team's units, what each depends on, and which assignment holds one.

Registration links a ready task to the new assignment in the same transaction, a report completes or
blocks it in the same transaction, and a dead assignment puts it back."""

import json
from typing import Any

from .dispatch_errors import StoreError
from .dispatch_limits import MAX_PAGE_BYTES, MAX_WORKERS

TASK_REPORT_SQL = (
    "UPDATE tasks SET status=? WHERE assignment=? AND status IN ('in_progress','blocked')"
)
TASKS_DONE_SQL = "SELECT id FROM tasks WHERE team=? AND status='complete'"
TASKS_LIST_SQL = "SELECT rowid AS cursor,* FROM tasks WHERE team=? AND rowid>? ORDER BY rowid"
TASKS_DEPS_SQL = "SELECT id,deps FROM tasks WHERE team=?"


class Tasks:
    def __getattr__(self, name) -> Any:
        """The store behind this mixin: the schema, the transactions and the other families are the
        Store's own, and only what this module owns lives here."""
        raise AttributeError(name)

    TASK_STATUSES = ("pending", "in_progress", "complete", "blocked")

    def _link_task(self, db, team, worker, assignment, task_id, transfer):
        """D8/D11: link a ready task to the new assignment inside the registration transaction."""
        if not isinstance(task_id, str) or not task_id:
            raise StoreError("task_id must be a task ID")
        row = db.execute(
            "SELECT status,owner,assignment,deps FROM tasks WHERE id=? AND team=?", (task_id, team)
        ).fetchone()
        if not row:
            raise StoreError("unknown task for this team")
        done = {r[0] for r in db.execute(TASKS_DONE_SQL, (team,))}
        if row["status"] == "pending":
            if any(d not in done for d in json.loads(row["deps"])):
                raise StoreError("unsatisfied dependencies")
        elif not (
            transfer and row["status"] in ("in_progress", "blocked") and row["owner"] == worker
        ):
            raise StoreError("task is not pending (status %s)" % row["status"])
        changed = db.execute(
            "UPDATE tasks SET status='in_progress',owner=?,assignment=? WHERE id=? AND status=?",
            (worker, assignment, task_id, row["status"]),
        ).rowcount
        if not changed:  # lost a concurrent claim
            raise StoreError("task was claimed concurrently")
        return {
            "task_id": task_id,
            "status": row["status"],
            "owner": row["owner"],
            "assignment": row["assignment"],
        }

    def _tasks(self, db, team, cursor=0, only=None):
        """Team-scoped rows after rowid cursor, at most MAX_PAGE_BYTES of JSON; second item is the next cursor."""
        done = {r[0] for r in db.execute(TASKS_DONE_SQL, (team,))}
        page, size, last = [], 2, None
        for r in db.execute(TASKS_LIST_SQL, (team, cursor)):
            deps = json.loads(r["deps"])
            item = {
                "task_id": r["id"],
                "subject": r["subject"],
                "status": r["status"],
                "owner": r["owner"],
                "assignment_id": r["assignment"],
                "depends_on": deps,
                "ready": r["status"] == "pending" and all(d in done for d in deps),
            }
            if only is not None and r["id"] != only:
                continue
            entry = len(json.dumps(item, separators=(",", ":")).encode()) + 1
            if page and size + entry > MAX_PAGE_BYTES:
                return page, str(last)
            page.append(item)
            size, last = size + entry, r["cursor"]
        return page, None

    def _deps(self, db, team, task, deps):
        if (
            not isinstance(deps, list)
            or len(deps) > MAX_WORKERS
            or not all(isinstance(d, str) and len(d) <= 64 for d in deps)
        ):
            raise StoreError("depends_on must be a list of at most 32 task IDs")
        known = {r[0]: json.loads(r[1]) for r in db.execute(TASKS_DEPS_SQL, (team,))}
        for dep in deps:
            if dep not in known:
                raise StoreError("missing dependency: %s" % dep)
        known[task] = deps
        seen, stack = set(), [task]
        while stack:  # a path from task back to itself is a cycle
            for dep in known[stack.pop()]:
                if dep == task:
                    raise StoreError("dependency cycle")
                if dep not in seen:
                    seen.add(dep)
                    stack.append(dep)
        return sorted(set(deps))
