"""The plan's state paths and the locks that keep an approval, a re-approval and a driver apart.

Each lock is one non-blocking flock under `<git common dir>/agent-orchestrator/`, and the file names
its holder, so a refusal can name the process the caller is waiting for. The ledger lives under the
state directory beside it.
"""

import fcntl
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .plan_document import PlanError
from .plan_layout import git


class LockHeld(PlanError):
    """The lock is held by another process; the message names it."""


def ledger_path(repo, slug, state_dir):
    """The ledger path of a plan: `<state>/<repo-key>/<slug>.json` (D17).

    A ledger an older driver wrote under `<state>/<slug>.json` is imported first when this repository owns it, so
    creating the ledger here never hides it."""
    # The driver owns the state key and imports this module, so it is imported late.
    from . import driver

    state = Path(state_dir).expanduser()
    keyed = state / driver.repo_key(repo)
    ledger, run_dir = keyed / (slug + ".json"), keyed / slug
    driver.import_old_ledger(state, repo, slug, ledger, run_dir)
    return ledger


@contextmanager
def plan_lock(repo, filename, who, refusal):
    """A non-blocking flock on `<git common dir>/agent-orchestrator/<filename>`, the protocol of `driver.run_lock`
    (it is written here because the driver's helper runs git through a function its callers replace). The file names
    its holder; `refusal` is the PlanError text for a held lock, given that holder."""
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    directory = common / "agent-orchestrator"
    directory.mkdir(exist_ok=True, mode=0o700)
    fd = os.open(directory / filename, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockHeld(refusal(lock.read().strip() or "another process")) from None
        lock.seek(0)
        lock.truncate()
        lock.write(
            "%s, pid %d since %s\n"
            % (who, os.getpid(), datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        )
        lock.flush()
        yield


def run_lock(repo, slug):
    """The plan's run lock, the file a driver holds for a whole run, so a live driver refuses approval and a driver
    refuses to start under one."""
    return plan_lock(
        repo,
        slug + ".lock",
        "plan approve",
        lambda holder: (
            "plan %s is being driven by %s; stop that driver (close its orchestrate-%s pane), then "
            "run plan approve again" % (slug, holder, slug)
        ),
    )


def reapprove_lock(repo, slug):
    """The lock of one re-approval under a live driver at a time. It is not the run lock: the driver holds that for
    its whole run and a re-approval must go through it, so two re-approvals exclude each other here."""
    return plan_lock(
        repo,
        slug + ".approve.lock",
        "plan approve",
        lambda holder: (
            "plan %s is being re-approved by %s; wait for that plan approve to finish, then run it again"
            % (slug, holder)
        ),
    )
