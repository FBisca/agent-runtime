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
            if version not in (0, 1):
                raise StoreError("incompatible dispatch schema version")
            for statement in (
                "CREATE TABLE IF NOT EXISTS teams (id TEXT PRIMARY KEY, scope TEXT, lead TEXT, generation TEXT, UNIQUE(scope,lead,generation))",
                "CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, team TEXT, name TEXT, address TEXT, active INTEGER, UNIQUE(team,name))",
                "CREATE TABLE IF NOT EXISTS assignments (id TEXT PRIMARY KEY, team TEXT, worker TEXT, state TEXT, current INTEGER)",
                "CREATE TABLE IF NOT EXISTS messages (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, team TEXT, recipient TEXT, assignment TEXT, body TEXT, acked INTEGER DEFAULT 0)",
                "CREATE INDEX IF NOT EXISTS inbox ON messages(team,recipient,acked,seq)",
                "CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, team TEXT, recipient TEXT, ids TEXT)",
            ):
                db.execute(statement)
            db.execute("PRAGMA user_version=1")

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

    def bind(self, assignment, address, scope):
        with self.transaction() as db:
            self._bind(db, assignment, address, scope)

    def fail(self, assignment):
        with self.transaction() as db:
            db.execute("UPDATE assignments SET state='failed' WHERE id=? AND state IN ('pending','working')", (assignment,))

    def publish(self, scope, address, parent, assignment, text, kind, message_id=None, caller_name=None):
        if kind not in REPORT_KINDS:
            raise StoreError("kind must be progress, complete, blocked or decision")
        if max(len(text.encode("utf-8")), len(json.dumps(text).encode("utf-8"))) > MAX_TEXT_BYTES:
            raise StoreError("report limit is 65536 bytes; split into progress reports")
        if message_id is not None and (not isinstance(message_id, str) or not 1 <= len(message_id) <= 128):
            raise StoreError("message_id must be 1-128 characters")
        message_id = message_id or uid()
        with self.transaction() as db:
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
            return envelope

    def acknowledge(self, db, team, recipient, receipt):
        if receipt is None:
            return
        row = db.execute("SELECT * FROM receipts WHERE id=?", (receipt,)).fetchone()
        if not row or (row["team"], row["recipient"]) != (team, recipient):
            raise StoreError("invalid or foreign receipt")
        for message_id in json.loads(row["ids"]):
            db.execute("UPDATE messages SET acked=1 WHERE id=?", (message_id,))

    def read(self, team, recipient, target, receipt=None):
        with self.transaction() as db:
            self.acknowledge(db, team, recipient, receipt)
            assignments = [dict(row) for row in db.execute(
                "SELECT a.id AS assignment_id,a.worker AS worker_id,a.state,w.name FROM assignments a "
                "JOIN workers w ON w.id=a.worker WHERE a.team=? AND a.current=1 ORDER BY a.rowid", (team,))]
            if target not in ("any", "all"):
                assignments = [a for a in assignments if target in (a["name"], a["assignment_id"])]
            # Historical reports remain readable for all/any; named reads select that worker's history.
            query = "SELECT body FROM messages WHERE team=? AND recipient=? AND acked=0"
            params = [team, recipient]
            if target not in ("any", "all"):
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
            token = None
            if messages:
                token = uid()
                db.execute("INSERT INTO receipts VALUES (?,?,?,?)",
                           (token, team, recipient, json.dumps([m["message_id"] for m in messages])))
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
            return {"reason": reason, "messages": messages, "assignments": assignments,
                    "unfinished": unfinished, "receipt": token, "more": more}
