"""Versioned same-user dispatch store. No terminal text is a delivery mechanism.

Limits: 32 registered workers per team, 32 messages / 128 KiB per compact JSON
message array (UTF-8, ensure_ascii=True, separators=(',', ':'), including brackets
and commas), 65536 bytes per report (UTF-8 and JSON-encoded text). Oversize publication
fails explicitly; reads never truncate or acknowledge implicitly. A receipt names
exactly the returned IDs; passing it on the next read acknowledges that page.
Connections/transactions are per operation, allowing separate MCP processes.
"""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

import dispatch_events

MAX_WORKERS = MAX_MESSAGES = 32
MAX_TEXT_BYTES, MAX_BATCH_BYTES = 65536, 131072
REPORT_KINDS = ("progress", "complete", "blocked", "decision")


class StoreError(Exception):
    pass


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, env):
        root = Path(env.get("XDG_STATE_HOME") or Path(env.get("HOME") or Path.home()) / ".local/state")
        self.directory = root / "agent-runtime"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or self.directory.stat().st_uid != os.getuid():
            raise StoreError("dispatch directory must be owned by the current user")
        self.directory.chmod(0o700)
        self.path = self.directory / "dispatch.sqlite3"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise StoreError("incompatible dispatch schema version")
            for statement in (
                "CREATE TABLE IF NOT EXISTS teams (id TEXT PRIMARY KEY, scope TEXT, lead TEXT, generation TEXT, UNIQUE(scope,lead,generation))",
                "CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, team TEXT, name TEXT, address TEXT, active INTEGER, UNIQUE(team,name))",
                "CREATE TABLE IF NOT EXISTS assignments (id TEXT PRIMARY KEY, team TEXT, worker TEXT, state TEXT, current INTEGER)",
                "CREATE TABLE IF NOT EXISTS messages (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, team TEXT, recipient TEXT, assignment TEXT, body TEXT, acked INTEGER DEFAULT 0)",
                "CREATE INDEX IF NOT EXISTS inbox ON messages(team,recipient,acked,seq)",
                "CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, team TEXT, recipient TEXT, ids TEXT)",
                "CREATE TABLE IF NOT EXISTS callers (scope TEXT,address TEXT,generation TEXT,team TEXT,PRIMARY KEY(scope,address,generation))",
                # v2: shared tasks; rowid order is the durable ID sequence.
                "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, team TEXT, subject TEXT, status TEXT, owner TEXT, assignment TEXT, deps TEXT)",
            ):
                db.execute(statement)
            db.execute("PRAGMA user_version=2")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            # SQLite derives rollback-journal permissions from the private database.
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def team(self, scope, lead, generation):
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO teams VALUES (?,?,?,?)", (uid(), scope, lead, generation))
            return db.execute("SELECT id FROM teams WHERE scope=? AND lead=? AND generation=?",
                              (scope, lead, generation)).fetchone()[0]

    def register(self, team, name):
        with self.transaction() as db:
            row = db.execute("SELECT id FROM workers WHERE team=? AND name=?", (team, name)).fetchone()
            worker = row[0] if row else uid()
            if not row:
                count = db.execute("SELECT COUNT(*) FROM workers WHERE team=?", (team,)).fetchone()[0]
                if count >= MAX_WORKERS:
                    raise StoreError("team limit is 32 registered workers")
                db.execute("INSERT INTO workers VALUES (?,?,?,NULL,1)", (worker, team, name))
            assignment = uid()
            db.execute("INSERT INTO assignments VALUES (?,?,?,'pending',0)", (assignment, team, worker))
            return {"team_id": team, "worker_id": worker, "assignment_id": assignment}

    def _bind(self, db, assignment, address, scope):
        row = db.execute("SELECT a.worker,a.state,a.rowid AS sequence,t.rowid AS generation,t.scope,t.lead "
                         "FROM assignments a JOIN teams t ON t.id=a.team WHERE a.id=?", (assignment,)).fetchone()
        if not row or row["scope"] != scope:
            raise StoreError("assignment does not belong to this scope")
        if row["state"] != "pending":
            return  # A fast report may already have bound/completed this assignment.
        # Tables are append-only: registration order and lead-generation order are
        # durable authority, even when a pending launch never finished binding.
        newer_assignment = db.execute("SELECT 1 FROM assignments WHERE worker=? AND rowid>? LIMIT 1",
                                      (row["worker"], row["sequence"])).fetchone()
        newer_generation = db.execute("SELECT 1 FROM teams WHERE scope=? AND lead=? AND rowid>? LIMIT 1",
                                      (scope, row["lead"], row["generation"])).fetchone()
        newer_address = db.execute("SELECT 1 FROM workers w JOIN teams t ON t.id=w.team "
                                   "JOIN assignments a ON a.worker=w.id WHERE w.address=? AND t.scope=? "
                                   "AND a.current=1 AND a.rowid>? LIMIT 1",
                                   (address, scope, row["sequence"])).fetchone()
        if newer_assignment or newer_generation or newer_address:
            raise StoreError("assignment is stale; newer registration or generation owns binding")
        db.execute("UPDATE assignments SET current=0 WHERE worker=?", (row["worker"],))
        db.execute("UPDATE workers SET active=0 WHERE address=? AND team IN (SELECT id FROM teams WHERE scope=?)",
                   (address, scope))
        db.execute("UPDATE workers SET address=?,active=1 WHERE id=?", (address, row["worker"]))
        db.execute("UPDATE assignments SET state='working',current=1 WHERE id=?", (assignment,))

    def bind(self, assignment, address, scope, ack=None):
        with self.transaction() as db:
            if ack:
                self.acknowledge(db, *ack)
            self._bind(db, assignment, address, scope)

    def fail(self, assignment):
        with self.transaction() as db:
            db.execute("UPDATE assignments SET state='failed' WHERE id=? AND state IN ('pending','working')", (assignment,))

    def publish(self, scope, address, parent, assignment, text, kind, message_id=None, caller_name=None, ack=None, generation=None):
        if kind not in REPORT_KINDS:
            raise StoreError("kind must be progress, complete, blocked or decision")
        if max(len(text.encode("utf-8")), len(json.dumps(text).encode("utf-8"))) > MAX_TEXT_BYTES:
            raise StoreError("report limit is 65536 bytes; split into progress reports")
        if message_id is not None and (not isinstance(message_id, str) or not 1 <= len(message_id) <= 128):
            raise StoreError("message_id must be 1-128 characters")
        message_id = message_id or uid()
        with self.transaction() as db:
            if ack:
                self.acknowledge(db, *ack)
            row = db.execute("SELECT a.*,w.address,w.name,w.active,t.scope,t.lead FROM assignments a "
                             "JOIN workers w ON w.id=a.worker JOIN teams t ON t.id=a.team WHERE a.id=?",
                             (assignment,)).fetchone()
            if row and row["state"] == "pending" and (row["scope"], row["lead"], row["name"]) == (scope, parent, caller_name):
                self._bind(db, assignment, address, scope)
                row = db.execute("SELECT a.*,w.address,w.active,t.scope,t.lead FROM assignments a "
                                 "JOIN workers w ON w.id=a.worker JOIN teams t ON t.id=a.team WHERE a.id=?",
                                 (assignment,)).fetchone()
            if not row or not row["current"] or not row["active"] or row["state"] in ("pending", "failed") or (
                    row["scope"], row["address"], row["lead"]) != (scope, address, parent):
                raise StoreError("assignment is stale or does not belong to this caller")
            envelope = {"message_id": message_id, "team_id": row["team"], "worker_id": row["worker"],
                        "assignment_id": assignment, "sender": row["worker"], "recipient": row["lead"],
                        "kind": kind, "text": text}
            body = json.dumps(envelope, separators=(",", ":"))
            previous = db.execute("SELECT body FROM messages WHERE id=?", (message_id,)).fetchone()
            if generation:
                self._pin(db, scope, address, generation, row["team"])
            if previous:
                if previous[0] != body:
                    raise StoreError("message_id conflict")
                return envelope
            if row["state"] == "complete":
                raise StoreError("assignment already complete")
            db.execute("INSERT INTO messages(id,team,recipient,assignment,body) VALUES (?,?,?,?,?)",
                       (message_id, row["team"], row["lead"], assignment, body))
            db.execute("UPDATE assignments SET state=? WHERE id=?",
                       ({"complete": "complete", "blocked": "blocked", "decision": "decision"}.get(kind, "working"), assignment))
            if kind in ("complete", "blocked"):  # same transaction as the report; replays returned above
                db.execute("UPDATE tasks SET status=? WHERE assignment=? AND status IN ('in_progress','blocked')",
                           ("complete" if kind == "complete" else "blocked", assignment))
        dispatch_events.notify(self.directory)
        return envelope

    def actor(self, scope, address, parent, generation=None, assignment=None, name=None):
        team = self.team(scope, address, generation) if parent is None else None
        with self.transaction() as db:
            pending = False
            if parent is None:
                actor = {"team_id": team, "recipient": address, "worker_id": None, "assignment_id": None,
                         "scope": scope, "address": address, "parent": None}
            else:
                row = db.execute("SELECT a.id,a.state,w.id AS worker,t.id AS team FROM assignments a "
                                 "JOIN workers w ON w.id=a.worker JOIN teams t ON t.id=a.team "
                                 "WHERE t.scope=? AND t.lead=? AND ((w.address=? AND w.active=1 AND a.current=1) "
                                 "OR (a.id=? AND a.state='pending' AND w.name=?)) ORDER BY a.rowid DESC LIMIT 1",
                                 (scope, parent, address, assignment, name)).fetchone()
                if not row or (assignment and row["id"] != assignment):
                    raise StoreError("caller has no matching active assignment")
                actor = {"team_id": row["team"], "recipient": row["worker"], "worker_id": row["worker"],
                         "assignment_id": row["id"], "scope": scope, "address": address, "parent": parent}
                pinned = db.execute("SELECT team FROM callers WHERE scope=? AND address=? AND generation=?",
                                    (scope, address, generation)).fetchone()
                if not generation or (not pinned and not assignment):
                    raise StoreError("assignment_id required on first worker call to prove session")
                if pinned and pinned[0] != row["team"]:
                    raise StoreError("stale caller session cannot adopt another team generation")
                pending = row["state"] == "pending"
            self.authorize(db, actor, pending=pending)
            if actor["worker_id"] and not pending:
                self._pin(db, scope, address, generation, actor["team_id"])
            # A pending fast report proves the session only when publication commits.
            return actor

    def _pin(self, db, scope, address, generation, team):
        pinned = db.execute("SELECT team FROM callers WHERE scope=? AND address=? AND generation=?",
                            (scope, address, generation)).fetchone()
        if pinned and pinned[0] != team:
            raise StoreError("stale caller session cannot adopt another team generation")
        db.execute("INSERT OR IGNORE INTO callers VALUES (?,?,?,?)", (scope, address, generation, team))

    def authorize(self, db, actor, pending=False):
        latest = db.execute("SELECT id FROM teams WHERE scope=? AND lead=? ORDER BY rowid DESC LIMIT 1",
                            (actor["scope"], actor["parent"] or actor["address"])).fetchone()
        if not latest or latest[0] != actor["team_id"]:
            raise StoreError("stale team generation")
        if actor["worker_id"] and not pending:
            row = db.execute("SELECT 1 FROM workers w JOIN assignments a ON a.worker=w.id "
                             "WHERE w.id=? AND w.address=? AND w.active=1 AND a.id=? AND a.current=1",
                             (actor["worker_id"], actor["address"], actor["assignment_id"])).fetchone()
            if not row:
                raise StoreError("stale worker assignment")

    def receipt_owner(self, receipt):
        with self.transaction() as db:
            row = db.execute("SELECT team,recipient FROM receipts WHERE id=?", (receipt,)).fetchone()
            if not row:
                raise StoreError("invalid receipt")
            return row["team"], row["recipient"], receipt

    def has_worker(self, team, target):
        with self.transaction() as db:
            return db.execute("SELECT 1 FROM workers WHERE team=? AND active=1 AND (name=? OR id=?)",
                              (team, target, target)).fetchone() is not None

    def peer(self, actor, target, text, message_id=None, ack=None):
        if max(len(text.encode()), len(json.dumps(text).encode())) > MAX_TEXT_BYTES:
            raise StoreError("message limit is 65536 bytes")
        if message_id is not None and (not isinstance(message_id, str) or not 1 <= len(message_id) <= 128):
            raise StoreError("message_id must be 1-128 characters")
        with self.transaction() as db:
            self.authorize(db, actor)
            if ack:
                self.acknowledge(db, *ack)
            lead = db.execute("SELECT lead FROM teams WHERE id=?", (actor["team_id"],)).fetchone()[0]
            recipient = lead if target in ("lead", lead, actor["team_id"]) else None
            if recipient is None:
                row = db.execute("SELECT id FROM workers WHERE team=? AND active=1 AND (name=? OR id=?)",
                                 (actor["team_id"], target, target)).fetchone()
                if not row:
                    raise StoreError("recipient is not an active teammate")
                recipient = row[0]
            envelope = {"message_id": message_id or uid(), "team_id": actor["team_id"],
                        "worker_id": actor["worker_id"], "assignment_id": actor["assignment_id"],
                        "sender": actor["worker_id"] or actor["team_id"], "recipient": recipient,
                        "kind": "message", "text": text}
            body = json.dumps(envelope, separators=(",", ":"))
            previous = db.execute("SELECT body FROM messages WHERE id=?", (envelope["message_id"],)).fetchone()
            if previous and previous[0] != body:
                raise StoreError("message_id conflict")
            if not previous:
                # NULL distinguishes peer data from assignment reports during selection.
                db.execute("INSERT INTO messages(id,team,recipient,assignment,body) VALUES (?,?,?,NULL,?)",
                           (envelope["message_id"], actor["team_id"], recipient, body))
        dispatch_events.notify(self.directory)
        return envelope

    def stopped(self, team, name, ack=None):
        with self.transaction() as db:
            if ack:
                self.acknowledge(db, *ack)
            worker = db.execute("SELECT id FROM workers WHERE team=? AND name=?", (team, name)).fetchone()
            if not worker:
                raise StoreError("worker is not registered in this team")
            db.execute("UPDATE workers SET active=0 WHERE id=?", (worker[0],))
            db.execute("UPDATE assignments SET state='failed' WHERE worker=? AND state!='complete'", (worker[0],))
        dispatch_events.notify(self.directory)

    def acknowledge(self, db, team, recipient, receipt):
        if receipt is None:
            return
        row = db.execute("SELECT * FROM receipts WHERE id=?", (receipt,)).fetchone()
        if not row or (row["team"], row["recipient"]) != (team, recipient):
            raise StoreError("invalid or foreign receipt")
        for message_id in json.loads(row["ids"]):
            db.execute("UPDATE messages SET acked=1 WHERE id=?", (message_id,))

    def snapshot(self, team, recipient, target, receipt=None, ack=None):
        """Acknowledge the previous result and freeze membership before listening."""
        with self.transaction() as db:
            self.acknowledge(db, *(ack or (team, recipient, receipt)))
            return [dict(row) for row in db.execute(
                "SELECT a.id AS assignment_id,w.name,w.address,t.lead FROM assignments a "
                "JOIN workers w ON w.id=a.worker JOIN teams t ON t.id=a.team "
                "WHERE a.team=? AND (a.current=1 OR a.id=?) "
                "AND (? IN ('all','any') OR w.name=? OR a.id=?) ORDER BY a.rowid",
                (team, target, target, target, target))]

    def read(self, team, recipient, target, receipt=None, assignment_ids=None, issue_receipt=True, ready=None, inbox=False, ack=None):
        """Optional pure ready(batch) returns a final reason or None under this transaction.

        A pending snapshot creates no receipt; a final snapshot and its receipt
        commit together, so concurrent publications/acks cannot invalidate readiness.
        """
        with self.transaction() as db:
            self.acknowledge(db, *(ack or (team, recipient, receipt)))
            selection = "a.current=1"
            selected_params = []
            if assignment_ids is not None:
                selection = "a.id IN (%s)" % (",".join("?" for _ in assignment_ids) or "NULL")
                selected_params = list(assignment_ids)
            assignments = [dict(row) for row in db.execute(
                "SELECT a.id AS assignment_id,a.worker AS worker_id,CASE WHEN a.state='complete' THEN 'complete' "
                "WHEN a.current=0 OR w.active=0 THEN 'failed' ELSE a.state END AS state,w.name FROM assignments a "
                "JOIN workers w ON w.id=a.worker WHERE a.team=? AND " + selection + " ORDER BY a.rowid",
                [team] + selected_params)]
            if assignment_ids is None and target not in ("any", "all"):
                assignments = [a for a in assignments if target in (a["name"], a["assignment_id"])]
            # Historical reports remain readable for all/any; named reads select that worker's history.
            query = "SELECT body FROM messages WHERE team=? AND recipient=? AND acked=0"
            params = [team, recipient]
            if assignment_ids is not None and not inbox:
                query += " AND (assignment IN (%s) OR assignment IS NULL)" % (",".join("?" for _ in assignment_ids) or "NULL")
                params.extend(assignment_ids)
            elif assignment_ids is None and not inbox and target not in ("any", "all"):
                query += " AND assignment IN (SELECT id FROM assignments WHERE worker IN (SELECT id FROM workers WHERE team=? AND name=?) OR id=?)"
                params.extend((team, target, target))
            messages, size, more = [], 2, False  # Opening and closing array brackets.
            for row in db.execute(query + " ORDER BY seq", params):
                body = row[0]
                entry_size = len(body.encode("utf-8")) + (1 if messages else 0)
                if len(messages) >= MAX_MESSAGES or size + entry_size > MAX_BATCH_BYTES:
                    more = True
                    break
                messages.append(json.loads(body))
                size += entry_size
            unfinished = [a["assignment_id"] for a in assignments if a["state"] != "complete"]
            reason = "timeout"
            if not assignments and not messages:
                reason = "empty"
            elif any(a["state"] in ("blocked", "decision", "failed") for a in assignments):
                reason = "attention"
            elif assignments and not unfinished:
                reason = "completed"
            elif target == "any" and messages:
                reason = "messages"
            batch = {"reason": reason, "messages": messages, "assignments": assignments,
                     "unfinished": unfinished, "receipt": None, "more": more}
            if ready is not None:
                batch["reason"] = ready(batch)
            if messages and issue_receipt and (ready is None or batch["reason"] is not None):
                token = uid()
                db.execute("INSERT INTO receipts VALUES (?,?,?,?)",
                           (token, team, recipient, json.dumps([m["message_id"] for m in messages])))
                batch["receipt"] = token
            return batch

    TASK_STATUSES = ("pending", "in_progress", "complete", "blocked")

    def _tasks(self, db, team):
        rows = [dict(r) for r in db.execute("SELECT * FROM tasks WHERE team=? ORDER BY rowid", (team,))]
        done = {r["id"] for r in rows if r["status"] == "complete"}
        return [{"task_id": r["id"], "subject": r["subject"], "status": r["status"], "owner": r["owner"],
                 "assignment_id": r["assignment"], "depends_on": json.loads(r["deps"]),
                 "ready": r["status"] == "pending" and all(d in done for d in json.loads(r["deps"]))}
                for r in rows]

    def _deps(self, db, team, task, deps):
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise StoreError("depends_on must be a list of task IDs")
        known = {r[0]: json.loads(r[1]) for r in db.execute("SELECT id,deps FROM tasks WHERE team=?", (team,))}
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

    def task(self, actor, action, arguments, ack=None):
        """Create/list/claim/update tasks for the actor's own team in one transaction."""
        with self.transaction() as db:
            self.authorize(db, actor)
            if ack:
                self.acknowledge(db, *ack)
            team, task = actor["team_id"], arguments.get("task_id")
            row = db.execute("SELECT * FROM tasks WHERE id=? AND team=?", (task, team)).fetchone() if task else None
            if action == "create":
                subject = arguments.get("subject")
                if not isinstance(subject, str) or not subject.strip() or len(subject) > 1000:
                    raise StoreError("subject must be 1-1000 characters")
                task = uid()
                deps = self._deps(db, team, task, arguments.get("depends_on") or [])
                db.execute("INSERT INTO tasks VALUES (?,?,?,'pending',NULL,NULL,?)", (task, team, subject, json.dumps(deps)))
            elif action != "list":
                if action not in ("claim", "update"):
                    raise StoreError("action must be create, list, claim or update")
                if not row:
                    raise StoreError("unknown task for this team")
                if action == "claim":
                    done = {r[0] for r in db.execute("SELECT id FROM tasks WHERE team=? AND status='complete'", (team,))}
                    if actor["worker_id"] and db.execute("SELECT state FROM assignments WHERE id=?",
                                                         (actor["assignment_id"],)).fetchone()[0] in ("complete", "failed"):
                        raise StoreError("claim requires a current unfinished assignment")
                    if row["status"] != "pending":
                        raise StoreError("task is not pending (status %s)" % row["status"])
                    if any(d not in done for d in json.loads(row["deps"])):
                        raise StoreError("unsatisfied dependencies")
                    db.execute("UPDATE tasks SET status='in_progress',owner=?,assignment=? WHERE id=?",
                               (actor["worker_id"] or actor["team_id"], actor["assignment_id"], task))
                else:
                    if actor["worker_id"] and row["assignment"] != actor["assignment_id"]:
                        raise StoreError("only the lead or the linked assignment can update this task")
                    status, deps = arguments.get("status"), arguments.get("depends_on")
                    if status is not None:
                        if status not in self.TASK_STATUSES:
                            raise StoreError("status must be one of %s" % ", ".join(self.TASK_STATUSES))
                        if status == "pending":
                            db.execute("UPDATE tasks SET owner=NULL,assignment=NULL WHERE id=?", (task,))
                        db.execute("UPDATE tasks SET status=? WHERE id=?", (status, task))
                    if deps is not None:
                        db.execute("UPDATE tasks SET deps=? WHERE id=?", (json.dumps(self._deps(db, team, task, deps)), task))
            tasks = self._tasks(db, team)
        if action == "list":
            return {"tasks": tasks}
        return next(t for t in tasks if t["task_id"] == task)
