"""Versioned same-user dispatch store. No terminal text is a delivery mechanism.

Limits: 32 active workers per team, 32 messages / 128 KiB per compact JSON
message array (UTF-8, ensure_ascii=True, separators=(',', ':'), including brackets
and commas), 65536 bytes per report (UTF-8 and JSON-encoded text). Oversize publication
fails explicitly; reads never truncate or acknowledge implicitly. A receipt names
exactly the returned IDs; passing it on the next read acknowledges that page.
Connections/transactions are per operation, allowing separate MCP processes."""

import hashlib as hashlib
import json
import os
import re
import sqlite3
import time
import uuid as uuid
from contextlib import contextmanager as contextmanager
from pathlib import Path

from . import agent_runtime as lib
from . import dispatch_events
from .agent_runtime import ERROR_NEXT, UNREPORTED_NEXT, process_start_stamp
from .dispatch_assignments import (
    BIND_GONE_SQL,
    BIND_NEWER_ADDRESS_SQL,
    BIND_NEWER_ASSIGNMENT_SQL,
    Assignments,
)
from .dispatch_callers import ACTOR_SQL, Callers
from .dispatch_connections import Connections
from .dispatch_errors import StorageError, StoreError, TerminalRefused
from .dispatch_ids import uid
from .dispatch_limits import (
    HC_RESTARTED,
    MAX_BASE_BYTES,
    MAX_BATCH_BYTES,
    MAX_MESSAGES,
    MAX_RESPONSE_BYTES,
    MAX_TEXT_BYTES,
    MAX_WORKERS,
    REPORT_KINDS,
    TEAM_ATTEMPTS,
    TEAM_LIMIT,
    WORST_USAGE,
)
from .dispatch_limits import MAX_PAGE_BYTES as MAX_PAGE_BYTES
from .dispatch_operations import Operations
from .dispatch_ownership import GENERATION_PID as GENERATION_PID
from .dispatch_ownership import (
    REFUSED_IN_TREE,
    REFUSED_LIVE_OWNER,
    alive_verdict,
    alive_verdicts,
    owner_session_tree,
    owning_team,
    team_holds_work,
    terminal_owned,
)
from .dispatch_ownership import UNDELIVERED_SQL as UNDELIVERED_SQL
from .dispatch_ownership import WAY_OUT as WAY_OUT
from .dispatch_ownership import generation_alive as generation_alive
from .dispatch_ownership import team_empty as team_empty
from .dispatch_ownership import undelivered_count as undelivered_count
from .dispatch_read import (
    READ_MEMBERS_SQL,
    READ_MESSAGES_SQL,
    READ_NAMED_CLAUSE,
    READ_PINNED_CLAUSE,
    Reads,
    wait_fingerprint,
)
from .dispatch_read import SNAPSHOT_SQL as SNAPSHOT_SQL
from .dispatch_read import last_wait_state as last_wait_state
from .dispatch_receipts import Receipts
from .dispatch_stall import NOT_STARTED_NEXT, RESUME_GRACE_MS, STALL_MS, STALL_NEXT, Stall
from .dispatch_tasks import TASK_REPORT_SQL, TASKS_DONE_SQL, Tasks
from .dispatch_tasks import TASKS_DEPS_SQL as TASKS_DEPS_SQL
from .dispatch_tasks import TASKS_LIST_SQL as TASKS_LIST_SQL

# The store's namespace is its surface, and three stdlib modules are published here only because the
# code that used them moved: hashlib with the wait digest, uuid with the id source, contextmanager with
# the connections. They are re-exported by the redundant-alias idiom so `dir(dispatch_store)` answers
# what it answered before the split, and a caller that reached for them keeps finding them.


class Store(
    Connections,
    Stall,
    Assignments,
    Receipts,
    Callers,
    Reads,
    Operations,
    Tasks,
):
    def __init__(self, env, clock=time.time, alive=None, ancestors=None):
        """The store for this user's dispatch state.

        `alive` and `ancestors` are the process evidence (a pid's start stamp, this process's
        ancestor chain). They default to the runtime's own readers and are injected so the store keeps
        no subprocess code and a test can stand for the process table."""
        self.clock = clock
        self.alive = alive or process_start_stamp
        self.ancestors = ancestors or lib.ancestor_pids
        root = Path(
            env.get("XDG_STATE_HOME") or Path(env.get("HOME") or Path.home()) / ".local/state"
        )
        self.directory = root / "agent-runtime"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or self.directory.stat().st_uid != os.getuid():
            raise StorageError("dispatch directory must be owned by the current user")
        self.directory.chmod(0o700)
        self.path = self.directory / "dispatch.sqlite3"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise StorageError("incompatible dispatch schema version")
            for statement in (
                "CREATE TABLE IF NOT EXISTS teams (id TEXT PRIMARY KEY, scope TEXT, lead TEXT, generation TEXT, UNIQUE(scope,lead,generation))",
                "CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, team TEXT, name TEXT, address TEXT, active INTEGER, UNIQUE(team,name))",
                "CREATE TABLE IF NOT EXISTS assignments (id TEXT PRIMARY KEY, team TEXT, worker TEXT, state TEXT, current INTEGER)",
                "CREATE TABLE IF NOT EXISTS messages (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, team TEXT, recipient TEXT, assignment TEXT, body TEXT, acked INTEGER DEFAULT 0)",
                "CREATE INDEX IF NOT EXISTS inbox ON messages(team,recipient,acked,seq)",
                "CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, team TEXT, recipient TEXT, ids TEXT)",
                "CREATE TABLE IF NOT EXISTS callers (scope TEXT,address TEXT,generation TEXT,team TEXT,PRIMARY KEY(scope,address,generation))",
                # Side table (not columns) keeps 0.2.0 servers' INSERT INTO assignments working on a shared store.
                "CREATE TABLE IF NOT EXISTS dispatch_turns (assignment TEXT PRIMARY KEY, turn INTEGER, mode TEXT)",
                "CREATE TABLE IF NOT EXISTS last_wait (team TEXT PRIMARY KEY, reason TEXT, attention INTEGER, fingerprint TEXT)",
                # Stall tracking is additive and keyed by assignment, so `assignments` INSERTs stay untouched.
                "CREATE TABLE IF NOT EXISTS stall_progress (assignment TEXT PRIMARY KEY, last_progress_ms INTEGER, last_turn INTEGER, stall_reported_for INTEGER)",
                # Side table preserves older servers' positional assignment inserts.
                "CREATE TABLE IF NOT EXISTS resume_marks (assignment TEXT PRIMARY KEY, dispatched_ms INTEGER, turn INTEGER, mode TEXT)",
                # v2: shared tasks; rowid order is the durable ID sequence.
                "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, team TEXT, subject TEXT, status TEXT, owner TEXT, assignment TEXT, deps TEXT)",
                # D7: hot wait/read lookups; additive, so 0.2.x writers and user_version are untouched.
                "CREATE INDEX IF NOT EXISTS idx_assignments_team_current ON assignments(team,current)",
                "CREATE INDEX IF NOT EXISTS idx_assignments_worker ON assignments(worker)",
                "CREATE INDEX IF NOT EXISTS idx_workers_address ON workers(address)",
                "CREATE INDEX IF NOT EXISTS idx_messages_assignment_acked ON messages(assignment,acked)",
                "CREATE INDEX IF NOT EXISTS idx_tasks_team_status ON tasks(team,status)",
                "CREATE INDEX IF NOT EXISTS idx_tasks_assignment ON tasks(assignment)",
            ):
                db.execute(statement)
            db.execute("PRAGMA user_version=2")

    def team(self, scope, lead, generation):
        """Register this session's team for (scope, lead), or refuse: one terminal, one owning session.

        This is the one seam both registration sites share (Store.actor and lead_store), so a session
        that does not own the terminal registers nothing and is refused with the reason and the way
        out. A session that descends from the owner (the owner's process is in this session's ancestor
        chain) is refused whatever the owner's team holds: it is the owner's own session tree, not a
        new session. An unrelated session is refused only while the owner is alive and its team holds
        work; an idle terminal is free for the next session.

        The owner row is read, the process evidence is gathered outside any transaction, and the
        insert re-checks the owner: the residual window is the one the CLI's own live-lead check
        has. The process evidence is injected, so the store keeps no subprocess code and a test can
        stand for the process table."""
        owner = None
        for attempt in range(TEAM_ATTEMPTS):
            with self.reading() as db:
                rows = db.execute(
                    "SELECT id,generation FROM teams WHERE scope=? AND lead=? ORDER BY rowid DESC",
                    (scope, lead),
                ).fetchall()
            mine = next((row for row in rows if row["generation"] == generation), None)
            if mine is not None:
                return mine["id"]  # this session's team: the common case, one read
            # No `ps` runs while the store holds a lock: the evidence is gathered here, between the
            # read that found the owner and the write that registers this session.
            liveness = alive_verdicts((row["generation"] for row in rows), self.alive)
            with self.reading() as db:
                owner = owning_team(db, scope, lead, liveness=liveness)
                work = owner is not None and team_holds_work(db, owner["id"], lead)
            if owner is not None and owner["generation"] != generation:
                if owner_session_tree(owner["generation"], self.ancestors):
                    raise TerminalRefused(
                        terminal_owned(lead, owner["generation"], REFUSED_IN_TREE)
                    )
                if alive_verdict(liveness, owner["generation"]) and work:
                    raise TerminalRefused(
                        terminal_owned(lead, owner["generation"], REFUSED_LIVE_OWNER)
                    )
            with self.transaction() as db:
                current = db.execute(
                    "SELECT id,generation FROM teams WHERE scope=? AND lead=? ORDER BY rowid DESC",
                    (scope, lead),
                ).fetchall()
                if current != rows and attempt < TEAM_ATTEMPTS - 1:
                    continue  # the terminal's teams changed under the read: read them again
                db.execute(
                    "INSERT OR IGNORE INTO teams VALUES (?,?,?,?)", (uid(), scope, lead, generation)
                )
                return db.execute(
                    "SELECT id FROM teams WHERE scope=? AND lead=? AND generation=?",
                    (scope, lead, generation),
                ).fetchone()[0]
        raise TerminalRefused(
            terminal_owned(
                lead,
                owner["generation"] if owner else generation,
                REFUSED_LIVE_OWNER,
            )
        )

    def register(self, team, name, task_id=None, transfer=False, operation=None):
        """Register a pending assignment. operation=(owner, operation_id, fingerprint) records the key in the same
        transaction, so a key and its assignment exist together or not at all. A key this owner already used with
        the same fingerprint registers nothing and returns that assignment's identity with `_replay` set; with
        another fingerprint it is refused."""
        with self.transaction() as db:
            owner = operation_id = fingerprint = None
            if operation:
                owner, operation_id, fingerprint = operation
                db.execute(self.OPERATIONS)
                if self.previous_operation(db, owner, operation_id):
                    raise StoreError(HC_RESTARTED)
                used = db.execute(
                    "SELECT o.team,o.fingerprint,o.assignment_id,a.worker FROM operations o "
                    "LEFT JOIN assignments a ON a.id=o.assignment_id WHERE o.owner=? AND o.operation_id=?",
                    (owner, operation_id),
                ).fetchone()
                if used and used["fingerprint"] != fingerprint:
                    raise StoreError(
                        "operation_id %s conflicts with an earlier call that carried other arguments: "
                        "use a new operation_id (or repeat the original call exactly); nothing was started"
                        % operation_id
                    )
                if used:
                    return {
                        "team_id": used["team"],
                        "worker_id": used["worker"],
                        "assignment_id": used["assignment_id"],
                        "_replay": True,
                    }
            row = db.execute(
                "SELECT id,active FROM workers WHERE team=? AND name=?", (team, name)
            ).fetchone()
            worker = row[0] if row else uid()
            if not row or not row[1]:
                # The slot is reserved here, in the transaction that registers the assignment, so
                # concurrent registrations cannot pass the check together. An active worker already
                # holds its slot. A stopped worker holds one only while an assignment is pending
                # (the reservation); _bind activates it, and a failed launch leaves it stopped.
                count = db.execute(
                    "SELECT COUNT(*) FROM workers w WHERE w.team=? AND w.id<>? AND (w.active=1 OR EXISTS "
                    "(SELECT 1 FROM assignments a WHERE a.worker=w.id AND a.state='pending'))",
                    (team, worker),
                ).fetchone()[0]
                if count >= MAX_WORKERS:
                    raise StoreError(TEAM_LIMIT)
            if not row:
                db.execute("INSERT INTO workers VALUES (?,?,?,NULL,1)", (worker, team, name))
            assignment = uid()
            db.execute(
                "INSERT INTO assignments VALUES (?,?,?,'pending',0)", (assignment, team, worker)
            )
            identity = {"team_id": team, "worker_id": worker, "assignment_id": assignment}
            if operation:
                db.execute(
                    "INSERT INTO operations VALUES (?,?,?,?,?)",
                    (team, owner, operation_id, fingerprint, assignment),
                )
            if task_id is not None:
                identity["_prior"] = self._link_task(
                    db, team, worker, assignment, task_id, transfer
                )
                identity["task_id"] = task_id
            return identity

    def _bind(self, db, assignment, address, scope, turn=None, mode="fresh", liveness=None):
        row = db.execute(
            "SELECT a.worker,a.state,a.rowid AS sequence,t.id AS team,t.scope,t.lead "
            "FROM assignments a JOIN teams t ON t.id=a.team WHERE a.id=?",
            (assignment,),
        ).fetchone()
        if not row or row["scope"] != scope:
            raise StoreError("assignment does not belong to this scope")
        if row["state"] != "pending":
            return  # A fast report may already have bound/completed this assignment.
        resume = db.execute(
            "SELECT turn,mode FROM resume_marks WHERE assignment=?", (assignment,)
        ).fetchone()
        if resume:
            # A report can bind before send returns; retain the pre-resume turn in that path too.
            turn, mode = resume
        # Tables are append-only: registration order and lead-generation order are
        # durable authority, even when a pending launch never finished binding.
        newer_assignment = db.execute(
            BIND_NEWER_ASSIGNMENT_SQL, (row["worker"], row["sequence"])
        ).fetchone()
        # The binding follows the same ownership rule as the authorization: the team that owns the
        # terminal binds its workers, and a session that left nothing behind never takes it.
        owner = owning_team(db, scope, row["lead"], own_team=row["team"], liveness=liveness)
        newer_generation = owner is not None and owner["id"] != row["team"]
        newer_address = db.execute(
            BIND_NEWER_ADDRESS_SQL, (address, scope, row["sequence"])
        ).fetchone()
        if newer_assignment or newer_generation or newer_address:
            raise StoreError("assignment is stale; newer registration or generation owns binding")
        db.execute("UPDATE assignments SET current=0 WHERE worker=?", (row["worker"],))
        # A reused address means the previous terminal is gone: surface its unfinished work once, to its lead.
        for gone in db.execute(BIND_GONE_SQL, (address, row["worker"], scope)).fetchall():
            envelope = {
                "message_id": uid(),
                "team_id": gone["team"],
                "worker_id": gone["worker"],
                "assignment_id": gone["id"],
                "sender": gone["worker"],
                "recipient": gone["lead"],
                "kind": "failed",
                "text": "worker terminal was replaced before completion",
            }
            db.execute("UPDATE assignments SET state='failed' WHERE id=?", (gone["id"],))
            self._release(db, gone["id"])
            db.execute(
                "INSERT INTO messages(id,team,recipient,assignment,body) VALUES (?,?,?,?,?)",
                (
                    envelope["message_id"],
                    gone["team"],
                    gone["lead"],
                    gone["id"],
                    json.dumps(envelope, separators=(",", ":")),
                ),
            )
        db.execute(
            "UPDATE workers SET active=0 WHERE address=? AND team IN (SELECT id FROM teams WHERE scope=?)",
            (address, scope),
        )
        db.execute("UPDATE workers SET address=?,active=1 WHERE id=?", (address, row["worker"]))
        db.execute("UPDATE assignments SET state='working',current=1 WHERE id=?", (assignment,))
        db.execute("INSERT OR REPLACE INTO dispatch_turns VALUES (?,?,?)", (assignment, turn, mode))

    def fail(self, assignment, inactive=False, restore: dict | None = None):
        """restore: prior link of a task a failed resume moved; it goes back to a still-live earlier assignment.

        inactive means the terminal is gone: the assignment then fails in any state but complete (a blocked or
        decision report that landed first no longer keeps its task) and the lead hears one failed message.
        Without it (resume) a report that landed first stands: the worker is still alive.
        """
        wrote = False
        with self.transaction() as db:
            states = (
                "('pending','working','blocked','decision')"
                if inactive
                else "('pending','working')"
            )
            prior = db.execute(
                "SELECT a.state,a.team,a.worker,a.current,t.lead FROM assignments a JOIN teams t ON t.id=a.team "
                "WHERE a.id=?",
                (assignment,),
            ).fetchone()
            changed = db.execute(
                "UPDATE assignments SET state='failed' WHERE id=? AND state IN " + states,
                (assignment,),
            ).rowcount
            if changed:
                live = False
                restored = None
                if restore and restore.get("status") != "pending" and restore.get("assignment"):
                    restored = restore
                    live = db.execute(
                        "SELECT 1 FROM assignments WHERE id=? AND current=1 AND state NOT IN ('complete','failed')",
                        (restore["assignment"],),
                    ).fetchone()
                if live and restored:
                    db.execute(
                        "UPDATE tasks SET status=?,owner=?,assignment=? WHERE id=? AND assignment=?",
                        (
                            restored["status"],
                            restored["owner"],
                            restored["assignment"],
                            restored["task_id"],
                            assignment,
                        ),
                    )
                else:
                    self._release(db, assignment)
                if (
                    inactive and prior["state"] != "pending"
                ):  # the lead already heard from this worker
                    wrote = True
                    envelope = {
                        "message_id": uid(),
                        "team_id": prior["team"],
                        "worker_id": prior["worker"],
                        "assignment_id": assignment,
                        "sender": prior["worker"],
                        "recipient": prior["lead"],
                        "kind": "failed",
                        "text": "worker terminal removed before completion",
                    }
                    db.execute(
                        "INSERT INTO messages(id,team,recipient,assignment,body) VALUES (?,?,?,?,?)",
                        (
                            envelope["message_id"],
                            prior["team"],
                            prior["lead"],
                            assignment,
                            json.dumps(envelope, separators=(",", ":")),
                        ),
                    )
            # A worker that never bound an address never started: it is not a teammate (D25).
            db.execute(
                "UPDATE workers SET active=0 WHERE (address IS NULL OR ?) AND id=(SELECT worker FROM assignments WHERE id=?)",
                (int(inactive), assignment),
            )
        if wrote:
            dispatch_events.notify(self.directory)

    def publish(  # noqa: PLR0913
        self,
        scope,
        address,
        parent,
        assignment,
        text,
        kind,
        message_id=None,
        caller_name=None,
        ack=None,
        generation=None,
    ):
        if kind not in REPORT_KINDS:
            raise StoreError("kind must be progress, complete, blocked or decision")
        if max(len(text.encode("utf-8")), len(json.dumps(text).encode("utf-8"))) > MAX_TEXT_BYTES:
            raise StoreError("report limit is 65536 bytes; split into progress reports")
        if message_id is not None and (
            not isinstance(message_id, str) or not 1 <= len(message_id) <= 128
        ):
            raise StoreError("message_id must be 1-128 characters")
        message_id = message_id or uid()
        with self.transaction() as db:
            if ack:
                self.acknowledge(db, *ack)
            row = db.execute(
                "SELECT a.*,w.address,w.name,w.active,t.scope,t.lead FROM assignments a "
                "JOIN workers w ON w.id=a.worker JOIN teams t ON t.id=a.team WHERE a.id=?",
                (assignment,),
            ).fetchone()
            if (
                row
                and row["state"] == "pending"
                and (row["scope"], row["lead"], row["name"]) == (scope, parent, caller_name)
            ):
                self._bind(db, assignment, address, scope)
                row = db.execute(
                    "SELECT a.*,w.address,w.active,t.scope,t.lead FROM assignments a "
                    "JOIN workers w ON w.id=a.worker JOIN teams t ON t.id=a.team WHERE a.id=?",
                    (assignment,),
                ).fetchone()
            if (
                not row
                or not row["current"]
                or not row["active"]
                or row["state"] in ("pending", "failed")
                or (row["scope"], row["address"], row["lead"]) != (scope, address, parent)
            ):
                raise StoreError("assignment is stale or does not belong to this caller")
            envelope = {
                "message_id": message_id,
                "team_id": row["team"],
                "worker_id": row["worker"],
                "assignment_id": assignment,
                "sender": row["worker"],
                "recipient": row["lead"],
                "kind": kind,
                "text": text,
            }
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
            db.execute(
                "INSERT INTO messages(id,team,recipient,assignment,body) VALUES (?,?,?,?,?)",
                (message_id, row["team"], row["lead"], assignment, body),
            )
            db.execute(
                "UPDATE assignments SET state=? WHERE id=?",
                (
                    {"complete": "complete", "blocked": "blocked", "decision": "decision"}.get(
                        kind, "working"
                    ),
                    assignment,
                ),
            )
            db.execute(
                "INSERT INTO stall_progress(assignment,last_progress_ms) VALUES (?,?) ON CONFLICT(assignment) "
                "DO UPDATE SET last_progress_ms=excluded.last_progress_ms",
                (assignment, self.now_ms()),
            )
            if kind in (
                "complete",
                "blocked",
            ):  # same transaction as the report; replays returned above
                db.execute(
                    TASK_REPORT_SQL, ("complete" if kind == "complete" else "blocked", assignment)
                )
        dispatch_events.notify(self.directory)
        return envelope

    def track_progress(self, observations):
        """Start each observed assignment's idle clock at first observation; a turn change is progress.

        A plain read decides first, so polls that change nothing never take the write lock.
        """
        turns = {
            k[0]: v for k, v in observations.items() if isinstance(k, tuple) and k[1] == "turn"
        }
        if not turns:
            return

        def pending(db):
            work = {}
            for assignment, turn in turns.items():
                if not db.execute("SELECT 1 FROM assignments WHERE id=?", (assignment,)).fetchone():
                    continue
                row = db.execute(
                    "SELECT last_turn FROM stall_progress WHERE assignment=?", (assignment,)
                ).fetchone()
                if row is None:
                    work[assignment] = ("insert", turn)
                elif turn is not None and row[0] is None:
                    work[assignment] = ("adopt", turn)
                elif turn is not None and turn != row[0]:
                    work[assignment] = ("progress", turn)
            return work

        reader = sqlite3.connect(self.path, timeout=5)
        try:
            if not pending(reader):
                return
        finally:
            reader.close()
        with self.transaction() as db:
            now = self.now_ms()
            for assignment, (action, turn) in pending(db).items():
                if action == "insert":
                    db.execute(
                        "INSERT INTO stall_progress(assignment,last_progress_ms,last_turn) VALUES (?,?,?)",
                        (assignment, now, turn),
                    )
                elif action == "adopt":
                    db.execute(
                        "UPDATE stall_progress SET last_turn=? WHERE assignment=?",
                        (turn, assignment),
                    )
                else:
                    db.execute(
                        "UPDATE stall_progress SET last_progress_ms=?,last_turn=? WHERE assignment=?",
                        (now, turn, assignment),
                    )

    def actor(
        self, scope, address, parent, generation=None, assignment=None, name=None, defer_pin=False
    ):
        team = self.team(scope, address, generation) if parent is None else None
        liveness = self.terminal_liveness(scope, parent or address)
        with self.transaction() as db:
            pending = False
            if parent is None:
                actor = {
                    "team_id": team,
                    "recipient": address,
                    "worker_id": None,
                    "assignment_id": None,
                    "scope": scope,
                    "address": address,
                    "parent": None,
                }
            else:
                row = db.execute(ACTOR_SQL, (scope, parent, address, assignment, name)).fetchone()
                if not row or (assignment and row["id"] != assignment):
                    raise StoreError("caller has no matching active assignment")
                actor = {
                    "team_id": row["team"],
                    "recipient": row["worker"],
                    "worker_id": row["worker"],
                    "assignment_id": row["id"],
                    "scope": scope,
                    "address": address,
                    "parent": parent,
                }
                pinned = db.execute(
                    "SELECT team FROM callers WHERE scope=? AND address=? AND generation=?",
                    (scope, address, generation),
                ).fetchone()
                if not generation or (not pinned and not assignment):
                    raise StoreError("assignment_id required on first worker call to prove session")
                if pinned and pinned[0] != row["team"]:
                    raise StoreError("stale caller session cannot adopt another team generation")
                pending = row["state"] == "pending"
            self.authorize(db, actor, pending=pending, liveness=liveness)
            if actor["worker_id"] and not pending:
                if (
                    defer_pin
                ):  # finalized by read(pin=...) in the same transaction as the delivery read
                    actor["pin"] = (scope, address, generation, actor["team_id"])
                else:
                    self._pin(db, scope, address, generation, actor["team_id"])
            # A pending fast report proves the session only when publication commits.
            return actor

    def peer(self, actor, target, text, message_id=None, ack=None):
        if max(len(text.encode()), len(json.dumps(text).encode())) > MAX_TEXT_BYTES:
            raise StoreError("message limit is 65536 bytes")
        if message_id is not None and (
            not isinstance(message_id, str) or not 1 <= len(message_id) <= 128
        ):
            raise StoreError("message_id must be 1-128 characters")
        with self.transaction() as db:
            self.authorize(db, actor)
            if ack:
                self.acknowledge(db, *ack)
            lead = db.execute("SELECT lead FROM teams WHERE id=?", (actor["team_id"],)).fetchone()[
                0
            ]
            recipient = lead if target in ("lead", lead, actor["team_id"]) else None
            if recipient is None:
                row = db.execute(
                    "SELECT id FROM workers WHERE team=? AND active=1 AND (name=? OR id=?)",
                    (actor["team_id"], target, target),
                ).fetchone()
                if not row:
                    raise StoreError("recipient is not an active teammate")
                recipient = row[0]
            envelope = {
                "message_id": message_id or uid(),
                "team_id": actor["team_id"],
                "worker_id": actor["worker_id"],
                "assignment_id": actor["assignment_id"],
                "sender": actor["worker_id"] or actor["team_id"],
                "recipient": recipient,
                "kind": "message",
                "text": text,
            }
            body = json.dumps(envelope, separators=(",", ":"))
            previous = db.execute(
                "SELECT body FROM messages WHERE id=?", (envelope["message_id"],)
            ).fetchone()
            if previous and previous[0] != body:
                raise StoreError("message_id conflict")
            if not previous:
                # NULL distinguishes peer data from assignment reports during selection.
                db.execute(
                    "INSERT INTO messages(id,team,recipient,assignment,body) VALUES (?,?,?,NULL,?)",
                    (envelope["message_id"], actor["team_id"], recipient, body),
                )
        dispatch_events.notify(self.directory)
        return envelope

    def read(  # noqa: PLR0912, PLR0913, PLR0915
        self,
        team,
        recipient,
        target,
        receipt=None,
        assignment_ids=None,
        issue_receipt=True,
        ready=None,
        inbox=False,
        ack=None,
        pin=None,
        reserve=0,
    ):
        """Optional pure ready(batch) returns a final reason or None under this transaction.

        A pending snapshot creates no receipt; a final snapshot and its receipt
        commit together, so concurrent publications/acks cannot invalidate readiness.
        Final wait(all) reads also record the team state in that same transaction, except for a
        partial "messages" result: it leaves the row untouched.
        """
        with self.transaction() as db:
            self.acknowledge(db, *(ack or (team, recipient, receipt)))
            if pin:
                self._pin(db, *pin)
            selection = "a.current=1"
            selected_params = []
            if assignment_ids is not None:
                selection = "a.id IN (%s)" % (",".join("?" for _ in assignment_ids) or "NULL")
                selected_params = list(assignment_ids)
            assignments = [
                dict(row)
                for row in db.execute(
                    READ_MEMBERS_SQL.format(selection=selection), [team] + selected_params
                )
            ]
            if assignment_ids is None and target not in ("any", "all"):
                assignments = [a for a in assignments if target in (a["name"], a["assignment_id"])]
            # Historical reports remain readable for all/any; named reads select that worker's history.
            query = READ_MESSAGES_SQL
            params = [team, recipient]
            if assignment_ids is not None and not inbox:
                query += READ_PINNED_CLAUSE.format(
                    ids=",".join("?" for _ in assignment_ids) or "NULL"
                )
                params.extend(assignment_ids)
            elif assignment_ids is None and not inbox and target not in ("any", "all"):
                query += READ_NAMED_CLAUSE
                params.extend((team, target, target))
            unfinished = [a["assignment_id"] for a in assignments if a["state"] != "complete"]
            # Worst-case fixed fields (receipt, longest reason, a runtime_state added by wait) are
            # budgeted before selection so the receipt below names exactly what fits; usage is attached by wait.
            fixed = len(
                json.dumps(
                    {
                        "reason": "x" * 9,
                        "messages": [],
                        "assignments": [
                            dict(
                                a,
                                usage=WORST_USAGE,
                                runtime_state="interrupted",
                                attention="not_started",
                                next=max(
                                    STALL_NEXT,
                                    NOT_STARTED_NEXT,
                                    UNREPORTED_NEXT,
                                    ERROR_NEXT,
                                    key=len,
                                ),
                            )
                            for a in assignments
                        ],
                        "unfinished": unfinished,
                        "receipt": "0" * 32,
                        "more": False,
                    },
                    separators=(",", ":"),
                ).encode()
            )
            if reserve > MAX_BASE_BYTES + len(',"inbox":'):
                # A base above its cap could leave no room for even one legal message (infinite more=true retries).
                raise StoreError("response base exceeds its budget; cannot piggyback inbox")
            limit = min(MAX_BATCH_BYTES, MAX_RESPONSE_BYTES - reserve - (fixed - 2))
            messages, size, more = [], 2, False  # Opening and closing array brackets.
            for row in db.execute(query + " ORDER BY seq", params):
                body = row[0]
                entry_size = len(body.encode("utf-8")) + (1 if messages else 0)
                if len(messages) >= MAX_MESSAGES or size + entry_size > limit:
                    more = True
                    break
                messages.append(json.loads(body))
                size += entry_size
            reason = "timeout"
            if not assignments and not messages:
                reason = "empty"
            elif any(a["state"] in ("blocked", "decision", "failed") for a in assignments):
                reason = "attention"
            elif assignments and not unfinished:
                reason = "completed"
            elif target == "any" and messages:
                reason = "messages"
            # Undelivered: unacked and not named by any receipt for (team, recipient) - the same
            # definition the lead hooks use. Only this page is checked, never the whole queue.
            undelivered = []
            if ready is not None and not inbox and messages:
                ids = [m["message_id"] for m in messages]
                named = {
                    row[0]
                    for row in db.execute(
                        "SELECT DISTINCT j.value FROM receipts r, json_each(r.ids) j WHERE r.team=? AND r.recipient=? "
                        "AND j.value IN (%s)" % ",".join("?" for _ in ids),
                        [team, recipient] + ids,
                    )
                }
                undelivered = [m["message_id"] for m in messages if m["message_id"] not in named]
                if target == "all" and more and not undelivered:
                    # A replay-only first page must not hide new reports behind it. Keep
                    # replay rows unacked and select only new rows for this wait page.
                    unseen = query + (
                        " AND NOT EXISTS (SELECT 1 FROM receipts r, json_each(r.ids) j "
                        "WHERE r.team=messages.team AND r.recipient=messages.recipient "
                        "AND j.value=messages.id) ORDER BY seq"
                    )
                    fresh, size = [], 2
                    for row in db.execute(unseen, params):
                        body = row[0]
                        entry_size = len(body.encode("utf-8")) + (1 if fresh else 0)
                        if len(fresh) >= MAX_MESSAGES or size + entry_size > limit:
                            break
                        fresh.append(json.loads(body))
                        size += entry_size
                    if fresh:
                        messages = fresh
                        undelivered = [m["message_id"] for m in fresh]
            batch = {
                "reason": reason,
                "messages": messages,
                "assignments": assignments,
                "unfinished": unfinished,
                "receipt": None,
                "more": more,
                "_undelivered": undelivered,
            }
            if ready is not None and not inbox:
                idle_since = self.now_ms() - STALL_MS
                for a in assignments:
                    row = (
                        a["state"] == "working"
                        and db.execute(
                            "SELECT last_progress_ms,stall_reported_for FROM stall_progress WHERE assignment=?",
                            (a["assignment_id"],),
                        ).fetchone()
                    )
                    a["_stalled"] = bool(
                        row and row[0] is not None and row[0] <= idle_since and row[1] != row[0]
                    )
                    resume = (
                        a["state"] == "working"
                        and db.execute(
                            "SELECT r.turn FROM resume_marks r WHERE r.assignment=? AND r.dispatched_ms<=? "
                            "AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.assignment=r.assignment "
                            "AND json_extract(m.body,'$.kind') IN ('progress','complete','blocked','decision'))",
                            (a["assignment_id"], self.now_ms() - RESUME_GRACE_MS),
                        ).fetchone()
                    )
                    a["_resume_turn"] = resume[0] if resume else None
            if ready is not None:
                batch["reason"] = ready(batch)
            for a in assignments:
                if a.pop("_stalled", False) and batch["reason"] is not None:
                    db.execute(
                        "UPDATE stall_progress SET stall_reported_for=last_progress_ms WHERE assignment=?",
                        (a["assignment_id"],),
                    )
            attention = batch.pop("_attention", 0)
            if messages and issue_receipt and (ready is None or batch["reason"] is not None):
                token = uid()
                db.execute(
                    "INSERT INTO receipts VALUES (?,?,?,?)",
                    (token, team, recipient, json.dumps([m["message_id"] for m in messages])),
                )
                batch["receipt"] = token
            if (
                ready is not None
                and batch["reason"] is not None
                and batch["reason"] != "messages"
                and target == "all"
                and not inbox
            ):
                db.execute(
                    "INSERT INTO last_wait(team,reason,attention,fingerprint) VALUES (?,?,?,?) ON CONFLICT(team) DO UPDATE "
                    "SET reason=excluded.reason,attention=excluded.attention,fingerprint=excluded.fingerprint",
                    (team, batch["reason"], attention, wait_fingerprint(db, team)),
                )
            batch.pop("_undelivered", None)
            return batch

    def task(self, actor, action, arguments, ack=None):  # noqa: PLR0912
        """Create/list/claim/update tasks for the actor's own team in one transaction."""
        with self.transaction() as db:
            self.authorize(db, actor)
            if ack:
                self.acknowledge(db, *ack)
            team, task = actor["team_id"], arguments.get("task_id")
            row = (
                db.execute("SELECT * FROM tasks WHERE id=? AND team=?", (task, team)).fetchone()
                if task
                else None
            )
            if action == "create":
                subject = arguments.get("subject")
                if not isinstance(subject, str) or not subject.strip() or len(subject) > 1000:
                    raise StoreError("subject must be 1-1000 characters")
                task = uid()
                deps = self._deps(db, team, task, arguments.get("depends_on") or [])
                db.execute(
                    "INSERT INTO tasks VALUES (?,?,?,'pending',NULL,NULL,?)",
                    (task, team, subject, json.dumps(deps)),
                )
            elif action != "list":
                if action not in ("claim", "update"):
                    raise StoreError("action must be create, list, claim or update")
                if not row:
                    raise StoreError("unknown task for this team")
                if action == "claim":
                    done = {r[0] for r in db.execute(TASKS_DONE_SQL, (team,))}
                    if actor["worker_id"] and db.execute(
                        "SELECT state FROM assignments WHERE id=?", (actor["assignment_id"],)
                    ).fetchone()[0] in ("complete", "failed"):
                        raise StoreError("claim requires a current unfinished assignment")
                    if row["status"] != "pending":
                        raise StoreError("task is not pending (status %s)" % row["status"])
                    if any(d not in done for d in json.loads(row["deps"])):
                        raise StoreError("unsatisfied dependencies")
                    db.execute(
                        "UPDATE tasks SET status='in_progress',owner=?,assignment=? WHERE id=?",
                        (actor["worker_id"] or actor["team_id"], actor["assignment_id"], task),
                    )
                else:
                    if actor["worker_id"] and row["assignment"] != actor["assignment_id"]:
                        raise StoreError(
                            "only the lead or the linked assignment can update this task"
                        )
                    status, deps = arguments.get("status"), arguments.get("depends_on")
                    if status is not None:
                        if status not in self.TASK_STATUSES:
                            raise StoreError(
                                "status must be one of %s" % ", ".join(self.TASK_STATUSES)
                            )
                        if status == "pending":
                            db.execute(
                                "UPDATE tasks SET owner=NULL,assignment=NULL WHERE id=?", (task,)
                            )
                        db.execute("UPDATE tasks SET status=? WHERE id=?", (status, task))
                    if deps is not None:
                        db.execute(
                            "UPDATE tasks SET deps=? WHERE id=?",
                            (json.dumps(self._deps(db, team, task, deps)), task),
                        )
            if action == "list":
                cursor = arguments.get("cursor") or "0"
                if not (isinstance(cursor, str) and re.fullmatch("[0-9]{1,18}", cursor)):
                    raise StoreError("cursor must come from a previous task list")
                tasks, following = self._tasks(db, team, int(cursor))
                return dict({"tasks": tasks}, **({"next_cursor": following} if following else {}))
            return self._tasks(db, team, only=task)[0][0]
