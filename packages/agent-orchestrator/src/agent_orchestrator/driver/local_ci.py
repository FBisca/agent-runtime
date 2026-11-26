"""Local CI: the repository's full check, run on this machine for a pull request's head, beside the driver's loop.

`run --local-ci` is for a repository whose hosted CI cannot run (a spent budget, a blocked account). The driver
then never asks GitHub for a ticket's checks: for each (task, head) a pull request poll reads, it starts one local
run, at most once, and maps what that run does to the states and rows `Pull.pr_checks` produces (`pending`,
`green`, `failed`), so the gates, the fix round and the readiness logic stay as they are.

A run is one shell script in its own process group, started without waiting: it adds a detached worktree at the
head, runs the repo's `worktree.setup` there, runs the check, removes the worktree and writes its exit code to a
file. The driver only reads that file, so a restarted driver reads a finished run back instead of running it again.

It lives beside `pull.py` rather than in it because no module of the package may pass a thousand lines.
"""

import os
import re
import signal
import subprocess
import sys
from pathlib import Path

from .. import config as repo_config
from .. import narrate
from ..plan import PlanError

ROW = "local-ci"  # the one check name a local run reports, on its row and as its GitHub commit status context
STATUS_STATE = {"pending": "pending", "green": "success", "failed": "failure"}
STATUS_TEXT = {
    "pending": "local CI is running on this machine",
    "green": "local CI passed",
    "failed": "local CI failed",
}

# One run. The values arrive in the environment, so no command text is spliced into the script. `set -e` stops the
# subshell at the first failing step and its status is the run's; the worktree is removed and the code written after
# it either way, through a temporary file so a reader never sees half a number.
SCRIPT = """\
code=0
(
  set -e
  git -C "$LOCAL_CI_REPO" cat-file -e "$LOCAL_CI_SHA^{commit}" 2>/dev/null \\
    || git -C "$LOCAL_CI_REPO" fetch -q origin "$LOCAL_CI_SHA"
  git -C "$LOCAL_CI_REPO" worktree prune
  rm -rf "$LOCAL_CI_TREE"
  git -C "$LOCAL_CI_REPO" worktree add -q --detach "$LOCAL_CI_TREE" "$LOCAL_CI_SHA"
  cd "$LOCAL_CI_TREE"
  export ORCHESTRATE_MAIN_ROOT="$LOCAL_CI_REPO"
  if [ -n "$LOCAL_CI_SETUP" ]; then sh -c "$LOCAL_CI_SETUP"; fi
  sh -c "$LOCAL_CI_COMMAND"
) || code=$?
cd "$LOCAL_CI_DIR"
git -C "$LOCAL_CI_REPO" worktree remove --force "$LOCAL_CI_TREE" >/dev/null 2>&1
git -C "$LOCAL_CI_REPO" worktree prune
echo "$code" > exit.tmp
mv exit.tmp exit
"""


def _engine():
    """The package this module belongs to, asked when a process is started: a test patches `ci_process` there."""
    return sys.modules[__name__.rpartition(".")[0]]


def command_of(repo, gates):
    """The command a local run executes: the repo's `checks.full.run`, else the `--gate` commands joined with
    `&&`; None when the run has neither."""
    tiers = repo_config.load(repo).checks
    if tiers is not None and tiers.full_run:
        return tiers.full_run
    return " && ".join(gates) if gates else None


def require_command(args, repo):
    """Refuse a `--local-ci` run that has nothing to run, naming the way out."""
    if getattr(args, "local_ci", False) and not command_of(repo, getattr(args, "gate", None)):
        raise PlanError(
            "--local-ci needs a command to run: declare checks.full.run in .orchestrate/config.yaml, "
            "or pass --gate COMMAND"
        )


def tail_lines(path, count):
    """The last `count` lines of a log file, or "" when it cannot be read."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-count:])


class LocalCi:
    """The run's local CI lane, held by the Pull lane: one method per way a local run is started, read or ended.

    It keeps only what a process holds (`live`, the runs this driver knows are going) and the last commit status
    posted per head. Everything else is on disk, in each run's directory, so a restart loses nothing.
    """

    def __init__(self, driver, lines):
        self.driver = driver
        self.lines = lines  # how much of a log a row and a fix round show
        self.live = {}  # run dir -> Popen, or the pid of a run an earlier driver started
        self.heads = {}  # task id -> the head its newest run is for
        self.posted = {}  # head -> the last state posted as its commit status
        self.base_sha = (
            None  # the base commit a base run was started on, until the verdict is final
        )

    @property
    def jobs(self):
        return max(1, int(getattr(self.driver.args, "local_ci_jobs", 1) or 1))

    def run_dir(self, task_id, sha):
        return self.driver.run_dir / task_id / ("local-ci-" + sha[:12])

    # ---- one run ---------------------------------------------------------------------------------------------

    def _alive(self, directory):
        """True while the run in `directory` is going. A run that ended with no exit file (its process was killed
        from outside) is written down as failed, so it is never started a second time behind the Lead's back."""
        held = self.live.get(directory)
        if held is None:
            try:
                held = int((directory / "pid").read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                return False
        if isinstance(held, int):
            try:
                os.kill(held, 0)
            except OSError:
                return False
            self.live[directory] = held
            return True
        if held.poll() is None:
            return True
        self.live.pop(directory, None)
        if not (directory / "exit").exists():
            (directory / "exit").write_text("%d\n" % (held.returncode or 1), encoding="utf-8")
        return False

    def _exit(self, directory):
        """The exit code the run wrote, or None while there is none."""
        try:
            return int((directory / "exit").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _running(self):
        return sum(1 for directory in list(self.live) if self._alive(directory))

    def _start(self, directory, sha):
        """Start the run in `directory` for `sha`, without waiting for it."""
        directory.mkdir(parents=True, exist_ok=True)
        for stale in ("exit", "exit.tmp", "pid"):
            (directory / stale).unlink(missing_ok=True)
        (directory / "run.sh").write_text(SCRIPT, encoding="utf-8")
        env = {
            **os.environ,
            "LOCAL_CI_REPO": str(self.driver.repo),
            "LOCAL_CI_SHA": sha,
            "LOCAL_CI_DIR": str(directory),
            "LOCAL_CI_TREE": str(directory / "tree"),
            "LOCAL_CI_SETUP": repo_config.load(self.driver.repo).worktree_setup or "",
            "LOCAL_CI_COMMAND": self.command(),
        }
        with (directory / "ci.log").open("wb") as log:
            process = _engine().ci_process(
                ["sh", str(directory / "run.sh")],
                cwd=str(directory),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        (directory / "pid").write_text("%d\n" % process.pid, encoding="utf-8")
        self.live[directory] = process
        self.driver.log("local-ci-start", sha=sha, dir=str(directory))

    def _stop(self, directory):
        """Kill a run that is no longer wanted, process group and all, and remove the worktree it left."""
        held = self.live.pop(directory, None)
        pid = held if isinstance(held, int) else getattr(held, "pid", None)
        if pid is not None:
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                pass
        tree = directory / "tree"
        if tree.exists():
            git = ["git", "-C", str(self.driver.repo), "worktree"]
            for step in (["remove", "--force", str(tree)], ["prune"]):
                try:
                    _engine().run([*git, *step], timeout=60)
                except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                    self.driver.log("local-ci-cleanup", dir=str(directory), error=str(error)[:200])

    def _state(self, directory, sha):
        """pending (queued or running), green or failed for the run in `directory`; the run is started when it
        has not been and a slot is free."""
        code = self._exit(directory)
        if code is not None:
            return "green" if code == 0 else "failed"
        if self._alive(directory):
            return "pending"
        code = self._exit(
            directory
        )  # a run that died without a code was just written down as failed
        if code is not None:
            return "green" if code == 0 else "failed"
        if self._running() < self.jobs:
            self._start(directory, sha)
        return "pending"

    def command(self):
        return command_of(self.driver.repo, self.driver.args.gate)

    # ---- a ticket's head -----------------------------------------------------------------------------------

    def checks(self, task_id, url, head):
        """(state, rows) for the head of a ticket's pull request, in the shape `Pull.pr_checks` answers."""
        if not head:
            return "pending", []
        previous = self.heads.get(task_id)
        if previous and previous != head:  # a newer head replaces the older one's run
            self._stop(self.run_dir(task_id, previous))
        self.heads[task_id] = head
        directory = self.run_dir(task_id, head)
        state = self._state(directory, head)
        self._post(url, head, state)
        return state, self.rows(directory, state)

    def rows(self, directory, state):
        """The one row a local run has, the equivalent of the rows `gh pr checks` prints."""
        log = directory / "ci.log"
        row = {
            "name": ROW,
            "bucket": {"pending": "pending", "green": "pass", "failed": "fail"}[state],
            "description": tail_lines(log, self.lines) if state == "failed" else STATUS_TEXT[state],
            "log_path": str(log),
            "command": self.command(),
        }
        return [row]

    def is_local(self, row):
        """True for a row this lane wrote: it names its own log."""
        return row.get("name") == ROW and bool(row.get("log_path"))

    def item(self, number, row):
        """A failed local row as a fix-round item: the log's last lines, the command and the log itself."""
        excerpt = tail_lines(row["log_path"], self.lines) or str(row.get("description") or "")
        return {
            "id": "CI%d" % number,
            "kind": "blocking",
            "source": "ci",
            "check": ROW,
            "command": row["command"],
            "exit": 1,
            "excerpt": excerpt or "the local check failed",
            "log_path": row["log_path"],
        }

    # ---- the base branch -----------------------------------------------------------------------------------

    def base_verdict(self):
        """pending, green or failed for the same check on the base branch's current commit, started when a ticket's
        run fails and settled once: the verdict is kept in `driver.main_failed`, which the Lead's decision resets."""
        settled = self.driver.main_failed
        if settled is not None:
            return "failed" if ROW in settled else "green"
        if self.base_sha is None:
            remote, _, ref = self.driver.args.base.partition("/")
            git = ["git", "-C", str(self.driver.repo)]
            try:
                _engine().run([*git, "fetch", "-q", remote, ref], timeout=120)
                self.base_sha = _engine().run(
                    [*git, "rev-parse", self.driver.args.base], timeout=60
                )
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                # no commit to run on is no verdict: the ticket waits and the next round asks again
                narrate.say(
                    "warn",
                    "ci",
                    "could not read %s to run local CI on it: %s"
                    % (self.driver.args.base, str(error)[:200]),
                )
                return "pending"
        directory = self.driver.run_dir / ("local-ci-base-" + self.base_sha[:12])
        state = self._state(directory, self.base_sha)
        if state != "pending":
            self.driver.main_failed = {ROW} if state == "failed" else set()
            self.base_sha = None
        return state

    # ---- visibility ---------------------------------------------------------------------------------------

    def _post(self, url, head, state):
        """Best effort: the head's commit status (not Actions, not billed). A failure is logged, never a verdict."""
        if self.posted.get(head) == state:
            return
        self.posted[head] = state
        repo = re.fullmatch(r"https?://[^/]+/([^/]+/[^/]+)/pull/\d+/?", url or "")
        if repo is None:
            return
        argv = [
            "api",
            "repos/%s/statuses/%s" % (repo.group(1), head),
            "-f",
            "state=" + STATUS_STATE[state],
            "-f",
            "context=" + ROW,
            "-f",
            "description=" + STATUS_TEXT[state],
        ]
        code, out, err = self.driver.gh(argv)
        if code != 0:
            self.driver.log(
                "local-ci-status", head=head, state=state, error=(err or out).strip()[:200]
            )
            narrate.say("warn", "ci", "could not post the local-ci status on %s" % head[:7])
