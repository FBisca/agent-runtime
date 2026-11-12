"""What `workbench plans` says about each plan beyond its state: the group it belongs to, when it last moved and what
waits on the person.

Everything here is read-only and prints only kinds, ids and times: an escalation's reason, a report or a message is
never read into an answer (R7). A plan's `events.jsonl` can be megabytes, so an older plan costs one read of its tail;
only an active plan's log is read whole, once, because `needs_me` needs the last start, the last error and every
unit's last request."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

TAIL_WINDOW = 64 * 1024


def is_final(state):
    """Whether a plan state ends the plan's story: archived, rejected or accepted (with or without a person)."""
    return isinstance(state, str) and (
        state in ("ARCHIVED", "REJECTED") or state.startswith("ACCEPTED")
    )


def group_of(state, markdown, ledger_state):
    """`active` or `older`. A plan that loads is by its state; one that does not is by its ledger's state, so an
    archived plan whose oracle was promoted away stops asking to be fixed. A Markdown plan is never run: older."""
    if markdown:
        return "older"
    if state is None:
        return "older" if is_final(ledger_state) else "active"
    return "older" if is_final(state) else "active"


def ledger_state_of(ledger_path):
    """The plan state a ledger holds, else None (no ledger, or one that cannot be read)."""
    try:
        data = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    moves = data.get("plan") if isinstance(data, dict) else None
    state = moves.get("state") if isinstance(moves, dict) else None
    return state if isinstance(state, str) else None


def parsed(text):
    """The moment an ISO 8601 string names, UTC when it names no zone; None when it is not one."""
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def at_of(line):
    """The `at` of one log line when it is a JSON object with an ISO 8601 `at`, else None (a torn line)."""
    try:
        row = json.loads(line)
    except ValueError:
        return None
    at = row.get("at") if isinstance(row, dict) else None
    return at if isinstance(at, str) and parsed(at) else None


def newest_at(path):
    """The `at` of the last readable line of an event log, read from its tail; None when the log has none."""
    try:
        with open(path, "rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            window = TAIL_WINDOW
            while True:
                start = max(0, size - window)
                stream.seek(start)
                lines = stream.read(size - start).split(b"\n")
                # With bytes before the window, the first line may be cut: it is read in the next, wider window.
                for line in reversed(lines[1:] if start else lines):
                    found = at_of(line)
                    if found:
                        return found
                if not start:
                    return None
                window *= 4
    except OSError:
        return None


def plan_mtime(path):
    try:
        stamp = os.stat(path).st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()


def last_activity(events_path, plan_path, scanned=None):
    """When the plan last moved: the newest event, else the plan file's mtime, else None."""
    found = scanned["newest"] if scanned else newest_at(events_path) if events_path else None
    return found or plan_mtime(plan_path)


def order_key(entry):
    """Active before older, newest activity first, no activity last; the sort is stable, so ties keep plan order."""
    moment = parsed(entry["last_activity"])
    return (
        entry["group"] != "active",
        moment is None,
        -moment.timestamp() if moment else 0,
    )


def scan(path):
    """What an active plan's event log says, in one pass: the newest `at`, whether the plan was ever driven, the last
    `start`'s pid, whether it finished or logged an error since, and the units whose last request is unanswered
    (a unit's last escalation, with no later decision or merged stop), oldest first. None for a log that is not
    there."""
    found = {"newest": None, "started": False, "pid": None, "finished": False, "error": False}
    asked = {}
    try:
        with open(path, "rb") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                kind, unit = row.get("kind"), row.get("unit")
                if isinstance(row.get("at"), str) and parsed(row["at"]):
                    found["newest"] = row["at"]
                if kind == "start":
                    found.update(started=True, pid=row.get("pid"), finished=False, error=False)
                elif kind == "finish":
                    found["finished"] = True
                elif kind == "error":
                    found["error"] = True
                elif isinstance(unit, str):
                    if kind == "escalation":
                        asked.pop(unit, None)
                        asked[unit] = True
                    elif kind == "decision" or (kind == "stop" and row.get("why") == "merged"):
                        asked.pop(unit, None)
    except OSError:
        return None
    found["asking"] = list(asked)
    return found


def alive(pid):
    """Whether a driver's pid is still a process on this machine. What cannot be told (no pid, no permission to
    signal it) counts as alive: `driver_gone` is only said when the system says the process does not exist."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def needs_of(plan, state, tickets, run):
    """The `{plan, ticket, kind}` items an active plan holds for the person. `state` is None for a plan that does not
    load; `tickets` are the ledger's rows (id, state, unverified); `run` is `scan`'s answer or None."""

    def item(ticket, kind):
        return {"plan": plan, "ticket": ticket, "kind": kind}

    if state is None:
        return [item(None, "fix_plan")]
    items = []
    if state == "DRAFT":
        items.append(item(None, "ready_to_approve"))
    for ticket in tickets:
        if ticket["state"] == "needs you":
            items.append(item(ticket["id"], "needs_you"))
        if ticket.get("unverified"):
            items.append(item(ticket["id"], "unverified"))
    if state == "APPROVED" and not (run and run["started"]):
        items.append(item(None, "ready_to_drive"))
    if run:
        if state == "RUNNING" and run["started"] and not run["finished"] and not alive(run["pid"]):
            items.append(item(None, "driver_gone"))
        if run["error"]:
            items.append(item(None, "error"))
        ids = {ticket["id"].lower(): ticket["id"] for ticket in tickets}
        for unit in run["asking"]:
            items.append(item(ids.get(unit.lower(), unit), "lead_pending"))
    return items
