"""The plan's integration: once every ticket merged, one pinned default-branch commit and the whole suite on it.

Each ticket proved its scenarios at its own gate, with the covering tests its report maps and CI green
on its head. What is left once every ticket merged is whether they hold together: the driver pins the
default branch, runs the repo's whole suite there when it declares one, and the plan is ACCEPTED or
REJECTED on that.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines and no class twenty-five public methods.
"""

import subprocess
import sys
from pathlib import Path
from typing import Any

from .. import config as repo_config
from .. import narrate, verify
from ..timeutil import iso as now
from .book import ARCHIVE
from .records import is_agent, is_done
from .routing import load_approved, repo_root, require_yaml
from .runtime import TREE_TIMEOUT
from .state import enter_plan_state, locate_state, read_ledger_file
from .watch import ACCEPTED_STATES


def _engine():
    """The package this module is split from, asked when the pinned base is fetched and read.

    A test answers the git seam by patching `driver.run`; a name bound here at
    import time would answer the real process and ignore it.
    """
    return sys.modules[__name__.rpartition(".")[0]]


def acceptance_context(args):
    """(plan model, repo, ledger path, run directory, ledger data) of the YAML plan a Lead command names. The model is
    the approved one: a plan edited since approval is refused here, as everywhere."""
    plan_path = Path(args.plan).resolve(strict=True)
    require_yaml(plan_path)
    repo = repo_root(plan_path, args.repo)
    model = load_approved(
        plan_path, repo=repo, state_dir=args.state_dir, require_approval=True, recover=True
    )
    ledger_path, run_dir = locate_state(args.state_dir, plan_path, repo, model.slug)
    data = read_ledger_file(ledger_path) if ledger_path.is_file() else {}
    return model, repo, ledger_path, run_dir, data


def tree_in(repo):
    def tree_of(sha):
        try:
            return _engine().run(
                ["git", "-C", str(repo), "rev-parse", "--verify", str(sha) + "^{tree}"]
            )
        except ValueError:
            return None

    return tree_of


class Integration:
    """The integration run, mixed into the Driver.

    A member pins the default branch, runs the whole suite on it and settles the plan's state; it asks the driver
    it belongs to for the repo, the run directory and the runtime.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def resume_plan_state(self, data):
        """A plan state a drive that ended (or died) mid-way left behind, made right for this drive. A rejected plan
        is verified again by a new `run`; a plan with a ticket still to do is running; an accepted plan stays so
        only while its integration run is for the contract this drive holds."""
        model, approval = self.contract()
        state = data["plan"]["state"]
        statuses = data.get("ticket_status") or {}
        pending = [
            t
            for t in model.tickets
            if t.owner.strip().lower() == "agent"
            and not str(statuses.get(t.id, "")).strip().upper().startswith("COMPLETE")
        ]
        if state not in ("DELIVERED", "VERIFYING", "REJECTED", *ACCEPTED_STATES):
            return
        if pending:
            enter_plan_state(data, "RUNNING")
        elif state in ("REJECTED", "VERIFYING"):
            enter_plan_state(
                data, "DELIVERED"
            )  # a rejection is verified again; a verification that did not finish
        elif state in ACCEPTED_STATES:
            if (data.get("integration") or {}).get("digest") != approval.digest:
                enter_plan_state(data, "DELIVERED")

    def integrate(self, plan):
        """A YAML plan whose every agent ticket is COMPLETE is DELIVERED, verified on one pinned default-branch
        commit and accepted, or rejected. Steps taken (state changes), 0 for a plan that is not there yet."""
        agents = [t for t in plan["tickets"] if is_agent(t)]
        if not agents or not all(is_done(t) for t in agents):
            return 0
        if self.plan_state() not in ("RUNNING", "DELIVERED"):
            return 0
        try:
            return self.verify_integration()
        except (ValueError, OSError, subprocess.TimeoutExpired, verify.VerifyError) as error:
            message = "integration: %s" % " ".join(str(error).split())[:500]
            if message not in self.errors:
                self.errors.append(message)
                self.log("error", gate="integration", error=message)
                narrate.say("fail", "verify", message)
            return 0

    def pin_default_branch(self):
        """Fetch the default branch and pin its SHA: (sha, the units whose merge the commit does not contain). A merge
        commit this repository does not have even after the fetch is not contained either, nor is one nobody knows."""
        sha = self._pinned_sha()
        with self.book() as data:
            merges = {
                task["packet"].get("unit"): task["merge_commit"]
                for task in data["tasks"].values()
                if task.get("merge_commit") and task["packet"].get("unit") != ARCHIVE
            }
        # A COMPLETE agent ticket whose merge commit is unknown (gh named none, or no task of the driver merged it) is
        # contained by no commit this can pin, so it makes integration wait like a missing one.
        unmerged = [
            ticket["#"].lower()
            for ticket in self.read_plan()["tickets"]
            if is_agent(ticket) and is_done(ticket) and ticket["#"].lower() not in merges
        ]
        lacking = list(unmerged)
        for unit, oid in sorted(merges.items()):
            known = subprocess.run(
                ["git", "-C", str(self.repo), "cat-file", "-e", oid + "^{commit}"],
                capture_output=True,
                timeout=60,
            )
            if (
                known.returncode
                or subprocess.run(
                    ["git", "-C", str(self.repo), "merge-base", "--is-ancestor", oid, sha],
                    capture_output=True,
                    timeout=60,
                ).returncode
            ):
                lacking.append(unit)
        return sha, sorted(lacking)

    def _pinned_sha(self):
        """`--base` fetched from its remote and resolved as the commit this repository holds now; a base naming no
        branch is read as it stands."""
        remote, _, branch = self.args.base.partition("/")
        if branch:
            _engine().run(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "fetch",
                    "--quiet",
                    remote,
                    "+refs/heads/%s:refs/remotes/%s/%s" % (branch, remote, branch),
                ],
                timeout=TREE_TIMEOUT,
            )
        return _engine().run(
            ["git", "-C", str(self.repo), "rev-parse", "--verify", self.args.base + "^{commit}"]
        )

    def verify_integration(self):
        sha, lacking = self.pin_default_branch()
        if lacking:
            self.wake(
                "integration-waits",
                "plan",
                "integration waits: %s does not contain the merge commit of %s; it verifies once the default branch "
                "does (run again after it has been fetched)"
                % (self.args.base, ", ".join(unit.upper() for unit in lacking)),
                key="%s:%s" % (sha, ",".join(lacking)),
            )
            return 0
        tree = self.tree_of(sha)
        with self.book() as data:
            attempt = (data.get("integration") or {}).get("attempt", 0) + 1
            data["integration"] = {
                "sha": sha,
                "tree": tree,
                "digest": self.contract()[1].digest,
                "at": now(),
                "attempt": attempt,
            }
            # Delivered means a default branch that holds every merge was found, so the two states change together.
            enter_plan_state(data, "DELIVERED")
            enter_plan_state(data, "VERIFYING")
        self.log("integration", sha=sha, tree=tree, attempt=attempt)
        narrate.say(
            "start", "verify", "verifying %s on %s (tree %s)" % (self.slug, sha[:12], tree[:12])
        )
        self.verify_full(sha, tree, attempt)
        self.settle_acceptance()
        return 2

    def verify_full(self, sha, tree, attempt):
        """The repo's whole suite (`checks.full.run`), once for this verify pass, on the pinned commit; True when it
        failed. `--skip-full` or `checks.full.verify: false` leave it to CI: a `skipped` event says so and why. A
        repo that declares no `full` has nothing to run or skip."""
        tiers = repo_config.load(self.repo).checks
        if tiers is None or not tiers.full_run:
            return False
        command = tiers.full_run
        reason = (
            "--skip-full"
            if getattr(self.args, "skip_full", False)
            else None
            if tiers.full_verify
            else "checks.full.verify: false"
        )
        record = {
            "state": "skipped" if reason else None,
            "sha": sha,
            "tree": tree,
            "digest": self.contract()[1].digest,
            "attempt": attempt,
            "command": command,
            "log": str(self.run_dir / "full.log"),
            "at": now(),
        }
        if reason:
            record["reason"] = reason
        else:
            narrate.say(
                "start", "verify", "running the whole suite on %s: %s" % (sha[:12], command)
            )
            result = verify.run_full(self.repo, sha, command, self.run_dir / "full.log")
            record.update(state=result["state"], exit=result["exit"], detail=result["detail"])
        with self.book() as data:
            data["full"] = record
        self.log(
            "full",
            **{
                k: v
                for k, v in record.items()
                if k in ("state", "sha", "command", "log", "reason", "exit", "attempt")
            },
        )
        return record["state"] == "FAIL"

    def support_silent(self, assignment_id):
        """A gaps reviewer whose turn ended without `complete`. Its output on disk is its answer: it counts as done
        and is validated as a reported completion is. With no output it is a live agent that may be waiting on a
        background job, and only the idle watchdog asks the Lead about it (R77)."""
        landed = None
        with self.book() as data:
            gaps = data.get("gaps") or {}
            if (
                gaps.get("assignment_id") == assignment_id
                and not gaps.get("ended")
                and Path(gaps["path"]).exists()
            ):
                gaps["ended"] = True
                landed = (gaps["name"], Path(gaps["path"]))
        if landed:
            self.log(
                "output-landed", agent=landed[0], file=str(landed[1]), assignment_id=assignment_id
            )

    def settle_acceptance(self):
        """The plan's state from its integration run: accepted, or rejected and the Lead is asked."""
        with self.book() as data:
            result = self.acceptance_of(data)
            enter_plan_state(data, result["state"])
            pin = dict(data["integration"])
        if result["state"] == "REJECTED":
            self.wake(
                "plan-rejected",
                "plan",
                "plan-rejected %s: %s. Ways out: fix the failure (add a ticket: edit the plan, `plan amend`, `run` "
                "again), or `run` again to verify a newer default branch"
                % (self.slug, "; ".join(verify.acceptance_errors(result))),
                key="%s:%s" % (pin["sha"], pin["attempt"]),
            )
        self.log("acceptance", state=result["state"], items=result["items"])
