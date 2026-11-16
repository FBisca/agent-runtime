"""One rule owns a terminal: the newest team row that is neither dead nor empty.

The rule decides which session a terminal belongs to, so it is the same rule for the store's
registration, the CLI's verbs and the hooks: it lives here once, beside the process evidence it asks
about. A session that left nothing behind never owns a terminal, and a session that runs inside the
owner's own process tree may not take it whatever the owner holds.

Nothing here writes. Every reader takes the connection it is given, and the evidence is injected, so
no `ps` runs while the store holds its write lock.
"""

import re

from . import agent_runtime as lib
from .agent_runtime import process_start_stamp

# The undelivered rule, written where it runs: a lead-addressed message no read has delivered. A receipt
# names what a page handed over, so a message a receipt names is delivered even before the next call
# acknowledges it. One reader decides it: the Stop guard counts it to block a turn, and the server counts
# it to refuse a mutating verb, so the two cannot drift apart.
UNDELIVERED_SQL = (
    "SELECT COUNT(*) FROM messages m WHERE m.team=? AND m.recipient=? AND m.acked=0 AND NOT EXISTS "
    "(SELECT 1 FROM receipts r, json_each(r.ids) j WHERE r.team=m.team AND r.recipient=m.recipient "
    "AND j.value=m.id)"
)


# One message names a terminal's owner: the terminal, the generation that owns it and the way out.
WAY_OUT = (
    "run this session in its own hc terminal, "
    "or start a new session in this terminal once the owner has exited"
)


# Why a session is refused: the rule that decided, in the words the rule uses.
REFUSED_IN_TREE = "this session runs inside the owning session's own process tree"
REFUSED_LIVE_OWNER = "the owning session is still running and its team still holds work"


def terminal_owned(terminal, generation, reason=""):
    """The one message a session gets when the terminal belongs to another generation.

    It names the terminal, the generation that owns it, the rule that refused the call and the
    way out, so the caller can act on it without reading the store."""
    detail = "; %s" % reason if reason else ""
    return "terminal %s is owned by generation %s%s; %s" % (terminal, generation, detail, WAY_OUT)


# One rule owns a terminal: the newest team row for that scope and terminal that is neither dead nor
# empty. A row is dead when its generation is a `pid:lstart` stamp whose process no longer carries that
# stamp; a generation with no pid is never provably dead, so its row is judged by what it holds. A row
# is empty when it holds no worker, assignment, message, receipt, caller, task, last_wait row or
# operation. The caller's own row is never skipped for it, so a caller always has a team; when every
# other row would be skipped, the newest one owns the terminal.
GENERATION_PID = re.compile(r"^(\d+):(.+)$")


def generation_alive(generation, stamp=None):
    """True when the generation's process is provably alive: that pid still carries that start stamp.

    A generation with no pid is never provably alive (nor provably dead): only what its team holds
    can judge it. The process evidence is injected, so the store keeps no subprocess code and a test
    can stand for the process table."""
    match = GENERATION_PID.match(generation) if isinstance(generation, str) else None
    if not match:
        return False
    exists, start = (stamp or process_start_stamp)(int(match.group(1)))
    return exists and start == match.group(2)


def alive_verdicts(generations, stamp=None):
    """What the process table says about each generation: {generation: alive}.

    The evidence is gathered once, outside the write transaction, and handed to the readers that
    need it, so no `ps` runs while the store holds its write lock."""
    return {generation: generation_alive(generation, stamp) for generation in set(generations)}


def alive_verdict(liveness, generation):
    """Whether one generation's process is alive, from the evidence gathered outside the transaction.

    The map holds the verdicts for the rows the reader was given. A generation the map does not know is
    a row that landed after the evidence was taken (another session registered in that window), so the
    reader is asked then: the process table is read in that rare window only, never on the common path."""
    if liveness is None:
        return generation_alive(generation)
    verdict = liveness.get(generation)
    return generation_alive(generation) if verdict is None else verdict


def team_empty(db, team):
    """True when the team holds no worker, assignment, message, receipt, caller, task, last_wait row or
    operation: the session that owned it left nothing behind."""
    if db.execute("SELECT 1 FROM workers WHERE team=? LIMIT 1", (team,)).fetchone():
        return False
    if db.execute("SELECT 1 FROM assignments WHERE team=? LIMIT 1", (team,)).fetchone():
        return False
    if db.execute("SELECT 1 FROM messages WHERE team=? LIMIT 1", (team,)).fetchone():
        return False
    if db.execute("SELECT 1 FROM receipts WHERE team=? LIMIT 1", (team,)).fetchone():
        return False
    if db.execute("SELECT 1 FROM callers WHERE team=? LIMIT 1", (team,)).fetchone():
        return False
    if db.execute("SELECT 1 FROM last_wait WHERE team=? LIMIT 1", (team,)).fetchone():
        return False
    # The task and operation tables arrived with later store versions: an older one has neither.
    if (
        db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone()
        and db.execute("SELECT 1 FROM tasks WHERE team=? LIMIT 1", (team,)).fetchone()
    ):
        return False
    if (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operations'"
        ).fetchone()
        and db.execute("SELECT 1 FROM operations WHERE team=? LIMIT 1", (team,)).fetchone()
    ):
        return False
    return True


def owner_session_tree(owner_generation, ancestors=None):
    """True when the owner's process is in this process's ancestor chain.

    A session that descends from the owner (the owner's pid in the caller's ancestor chain) is the
    owner's own session tree, not a new session: it may not take the owner's terminal, whatever the
    owner's team holds. The ancestry is injected, so the store keeps no subprocess code and a test
    can stand for the process table."""
    owner = GENERATION_PID.match(owner_generation) if isinstance(owner_generation, str) else None
    if not owner:
        return False
    return int(owner.group(1)) in ((ancestors or lib.ancestor_pids)())


def undelivered_count(db, team, lead):
    """Lead-addressed messages no read has delivered yet: unacknowledged and named by no receipt."""
    return db.execute(UNDELIVERED_SQL, (team, lead)).fetchone()[0]


def team_holds_work(db, team, lead=None):
    """True when the team holds work its lead is still serving.

    This is the liveness rule, not the emptiness rule: unfinished assignments of active workers or
    results not yet acknowledged, plus a message the lead has not acknowledged. The rule is the
    query above, so one reader decides what a live owner holds: an owner that holds only a task, an
    operation or a last-wait row is idle, and an idle terminal is free for the next session. The CLI's
    live-lead check asks a looser question (the current turn of every worker), so it may let a call
    through that this rule refuses: the store's answer is the one that keeps the terminal with its
    owner."""
    return bool(
        db.execute(
            "SELECT 1 FROM assignments a JOIN workers w ON w.id=a.worker WHERE a.team=? "
            "AND ((w.active=1 AND a.state NOT IN ('complete','failed')) "
            "OR EXISTS (SELECT 1 FROM messages m WHERE m.assignment=a.id AND m.acked=0)) LIMIT 1",
            (team,),
        ).fetchone()
        or db.execute(
            "SELECT 1 FROM messages WHERE team=? AND recipient=? AND acked=0 LIMIT 1", (team, lead)
        ).fetchone()
    )


def owning_team(db, scope, lead, own_team=None, own_generation=None, liveness=None):
    """The team row that owns (scope, lead), or None when the terminal has no team.

    The owner is the newest row that is neither dead nor empty. The caller's own row (own_team, or the
    row of own_generation) is never skipped for it, so a caller always has a team; when every other row
    would be skipped, the newest one owns the terminal.

    `liveness` carries the process evidence gathered outside the transaction, so this reader asks no
    process table while the store holds its write lock; without it the reader asks the runtime. A
    generation the evidence does not know is a row that landed after it was taken, and the reader is
    asked for that one row alone."""
    rows = db.execute(
        "SELECT id,generation FROM teams WHERE scope=? AND lead=? ORDER BY rowid DESC",
        (scope, lead),
    ).fetchall()
    for row in rows:
        if row["id"] == own_team or (
            own_generation is not None and row["generation"] == own_generation
        ):
            return row
        if team_empty(db, row["id"]) and not alive_verdict(liveness, row["generation"]):
            continue  # a session that left nothing behind never owns the terminal
        return row
    return rows[0] if rows else None
