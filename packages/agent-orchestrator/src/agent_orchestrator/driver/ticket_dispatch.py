"""The dispatch: a ticket's packet, the branch and worktree it runs in, and the contract text its worker is held to.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines and no class twenty-five public methods.
"""

import re
import shlex
import subprocess
import sys
from typing import Any

from .. import config as repo_config
from .. import narrate
from .book import claim, worker_name
from .pointers import task_text
from .records import ROUTE, normalise_check, satisfied, unique_checks
from .review import review_brief
from .runtime import TREE_TIMEOUT


def _engine():
    """The package this module is split from, asked when the checkout is made and the record is bound.

    A test answers these seams by patching `driver.run` and `driver.bind`; a name
    bound here at import time would answer the real one and ignore it.
    """
    return sys.modules[__name__.rpartition(".")[0]]


MATRIX = {
    ("small", "simple"): ("small", "low"),
    ("small", "complex"): ("medium", "medium"),
    ("regular", "simple"): ("medium", "medium"),
    ("regular", "complex"): ("medium", "high"),
}


CONTRACT = """# Contract for every task in this run

- Write your result to the report path your task.md names, as JSON in this shape. The driver validates it; a missing or invalid file counts as no report:
  {"contract":1,"task_id","status":"COMPLETE|PARTIAL|BLOCKED","summary","acceptance_met":bool,"files":[],"decisions":[],"interfaces":[],"remaining":[],"checks":[{"command","outcome":"passed|failed|not_run","reason?"}],"review_rounds":0,"usage":"unknown","head":"<commit sha>","pr":"<url or null>","context":{"status":"unknown"},"test_cost":null}
  BLOCKED needs "blocker" and a next action in "remaining". In a fix round report "review_rounds":0: the driver counts failed rounds.
- A YAML plan's ticket reports `"contract":3` and adds `"covers":{"S<n>":["path::test_name"]}`: every scenario the ticket satisfies, mapped to the test nodes that prove it. The implementation provides its evidence: each scenario it satisfies needs a covering test this ticket adds or changes. The report also adds `"red":{"S<n>":"<excerpt>"}`: for each of those scenarios, the failing output of its covering test from before the fix, as the runner printed it, naming the node and its assertion failure. verify refuses a satisfied scenario with no node or no red excerpt, a node that does not exist or that this ticket did not add or change, and an excerpt that names none of the scenario's nodes.
- COMPLETE checks: every required check must be reported `passed`, its command spelled as the task gives it (whitespace apart). Each required command is required once, and the one `not_run` allowance (`affected` with a `reason`, when no test was selected) never waives a command another tier also requires. A non-required check may be `not_run` with a `reason`. A `failed` check, or a required check not `passed`, refuses COMPLETE.
- Prose, if any, goes in report.md beside it. Files are write-once: a later attempt writes report.2.json.
- Your agent-runtime `report` call carries ONE line: status + report path. Use kind=complete, blocked or decision.
- Red-first: one test per scenario, named with its S-ID, seen failing before the code exists; its failing output goes in the report's `red`.
- TDD: before your first edit read the `tdd` skill's `SKILL.md` and its `rules.md`; your task names the path. Codex has no skill loader, so these rules are the whole skill here: one behaviour per test at the cheapest level that shows it; build expensive setup once per class and let readers share it; wait on an event or an injected clock, never a sleep; admit a test only when it protects a requirement or a credible failure, is not already covered at the same boundary, and would fail on a regression someone could ship. Name the seams and the admission answers in report.md.
- Test cost: a YAML ticket whose diff adds or changes a test reports `"test_cost": {"command": "<the command that timed the touched tests>", "modules": {"<test file>": <seconds>}, "tests": {"<path::test>": <seconds>}}`. verify refuses the report without a row for every touched test file and every covered node.
- Fix the class, not the instance: when you fix a defect, search for every other place with the same pattern.
- Start from the Paths your task lists; edit any other file the change needs, except one another unfinished ticket lists. Never touch other worktrees or the main checkout.
- Commit with Conventional Commits and push the branch. Describe behaviour, not plan ticket numbers.
"""


class Dispatch:
    """The dispatch, mixed into the Driver: the packet a ticket's worker is handed.

    A member builds the task one ticket is dispatched with, and asks the driver it belongs to for the plan,
    the record and the worktrees.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def review_text(self, packet, review_path, delta=None, open_items=None):
        """The reviewer's brief: the paths it works from, the pass in one order and the schema of its verdict."""
        return review_brief(self, packet, review_path, delta, open_items)

    def dispatch(self, plan, ticket, task_id=None, note=""):
        """Hand one ticket to a worker: its route, its packet, its worktree, its prompt and its agent."""
        number = ticket["#"][1:]
        name = worker_name(plan["slug"], number)
        task_id = task_id or "t" + number
        self._dispatch_open(task_id)
        size, difficulty = ticket["Size"].strip(), ticket["Difficulty"].strip()
        klass, effort, model, backend = self._dispatch_route(
            ticket, number, task_id, size, difficulty
        )
        checkout = self.worktree(name, "feat/" + name)
        scenarios = [plan["scenarios"][sid] for sid in satisfied(ticket, plan["scenarios"])[0]]
        packet = {
            "task_id": task_id,
            "worker": name,
            "plan": str(self.plan_path),
            "checkout": str(checkout),
            "backend": backend,
            **self.contract_fields(ticket, scenarios),
            "depends_on": [],
            "unit": "t" + number,
            "size": size,
            "difficulty": difficulty,
            "class": klass,
            "effort": effort,
            "model": model,
        }
        with self.book() as data:
            claim(data, packet)
        self._log_route(packet)
        task_md = self._dispatch_prompt(plan, ticket, packet, scenarios, note)
        self._start_worker(name, task_id, packet, checkout, task_md)
        self.write_status(ticket["#"], "IN PROGRESS")
        self.spawned.append(name)
        narrate.say(
            "start",
            "build",
            "started worker %s · %s · %s · %s"
            % (name, packet["class"], packet["model"], packet["effort"]),
            task=task_id,
        )

    def _dispatch_open(self, task_id):
        """A closed task id is never dispatched again: it would be refused after a worker had started."""
        with self.book() as data:
            closed = (data["tasks"].get(task_id) or {}).get("status")
        if closed in ("COMPLETE", "RELEASED"):
            raise ValueError(
                "task %s is %s in the ledger, and a closed task id is never dispatched again: "
                "dispatch the unit under a new attempt id" % (task_id, closed)
            )

    def _dispatch_route(self, ticket, number, task_id, size, difficulty):
        """(class, effort, model, backend) the implementer runs on, never below the last attempt's class."""
        klass, matrix_effort = MATRIX[(size, difficulty)]
        last = self.last_attempt("t" + number, task_id)
        if last and ROUTE["class"].index(last["class"]) > ROUTE["class"].index(klass):
            # A retry the driver raised keeps its class: a re-dispatch never drops below the last attempt.
            klass, matrix_effort = last["class"], last.get("effort") or matrix_effort
        route = self.routes[klass]
        backend = self.step_backend("implementer", route.backend)
        model = self.step_model("implementer", route.model)
        effort = self.step_effort("implementer", route.effort or matrix_effort)
        return klass, effort, model, backend

    def _log_route(self, packet):
        """The route the dispatch took, on the events log, with where each part of it was decided."""
        route = self.routes[packet["class"]]
        step = self.step_route("implementer")
        self.log(
            "route",
            unit=packet["unit"],
            task=packet["task_id"],
            **{
                "class": packet["class"],
                "backend": packet["backend"],
                "model": packet["model"],
                "effort": packet["effort"],
                "effort_source": "step"
                if (step and step.effort)
                else ("config" if route.effort else "matrix"),
                "source": self.route_source,
            },
        )

    def _dispatch_prompt(self, plan, ticket, packet, scenarios, note):
        """The task file the worker reads, written once and left alone on a re-dispatch."""
        task_dir = self.run_dir / packet["task_id"]
        task_dir.mkdir(exist_ok=True, mode=0o700)
        task_md = task_dir / "task.md"
        if not task_md.exists():
            task_md.write_text(
                self.task_text(plan, ticket, packet, scenarios, task_dir) + note, encoding="utf-8"
            )
        return task_md

    def _start_worker(self, name, task_id, packet, checkout, task_md):
        """Spawn the worker, bind its assignment to the attempt and keep the address a hand-over reuses."""
        runtime_id, assignment_id = self.ensure_agent(
            name,
            "worker",
            packet["unit"],
            {
                "kind": "worker",
                "name": name,
                "task": "Read %s and follow it." % task_md,
                "cwd": str(checkout),
                "cli": packet["backend"],
                "model": packet["model"],
                "effort": packet["effort"],
            },
            attempt=task_id,
        )
        with self.book() as data:
            _engine().bind(data, task_id, str(runtime_id))
            data["tasks"][task_id]["assignment_id"] = assignment_id
            self.keep_address(data, task_id, name)

    # The task brief is rendered by the pointer guard's own module: every file an agent is told to read or
    # write is a pointer that must resolve, and one home keeps the guard on all of them.
    task_text = task_text

    def worktree(self, name, branch):
        path = self.repo.parent / (self.repo.name + ".worktrees") / name
        remote, _, ref = self.args.base.partition("/")
        _engine().run(["git", "-C", str(self.repo), "fetch", "-q", remote, ref], timeout=120)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            exists = (
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(self.repo),
                        "rev-parse",
                        "--verify",
                        "-q",
                        "refs/heads/" + branch,
                    ],
                    capture_output=True,
                ).returncode
                == 0
            )
            _engine().run(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "worktree",
                    "add",
                    *([] if exists else ["-b", branch]),
                    str(path),
                    *([branch] if exists else [self.args.base]),
                ],
                timeout=TREE_TIMEOUT,
            )
        return path

    def contract_fields(self, ticket, scenarios):
        """The packet fields a ticket's contract fixes: what it delivers, the paths it owns, the scenarios it
        satisfies and the checks its worker runs. A dispatch and an adopted re-approval write the same ones."""
        owns = [p.strip() for p in ticket["Paths"].split(",") if p.strip()]
        checks, affected, full = self.tier_checks(owns)
        return {
            "objective": ticket["What it delivers"],
            "owns": owns,
            "acceptance": [re.sub(r"^- \*\*(S\d+[a-z]?)\*\*", r"\1:", s) for s in scenarios],
            "checks": checks,
            **({"affected": affected} if affected else {}),
            **({"full": full} if full else {}),
        }

    def tier_checks(self, owns):
        """The checks a worker runs: the named `--gate` alone, else the repo's `fast` and rendered `affected` tiers.
        Each required command appears once. Returns (checks, the rendered affected command when it is a
        requirement of its own, the `full` command the worker leaves to CI when it is not one of them)."""
        if self.args.gate:
            return unique_checks(self.args.gate), None, None
        tiers = repo_config.load(self.repo).checks
        if tiers is None or not (tiers.fast or tiers.affected):
            return [], None, None
        affected = None
        if tiers.affected:
            affected = tiers.affected.replace("{base}", shlex.quote(self.args.base)).replace(
                "{paths}", " ".join(shlex.quote(p) for p in owns)
            )
        checks = unique_checks([c for c in (tiers.fast, affected) if c])
        # A rendered `affected` that repeats `fast` adds no requirement, so it keeps no `not_run` allowance of its
        # own; and a command the worker must pass is never the one it is told not to run.
        if (
            affected is not None
            and tiers.fast
            and normalise_check(affected) == normalise_check(tiers.fast)
        ):
            affected = None
        full = tiers.full_run
        if full and normalise_check(full) in {normalise_check(c) for c in checks}:
            full = None
        return checks, affected, full

    def last_attempt(self, unit, task_id):
        """The packet of the unit's most recent other attempt that carries a class, or None."""
        with self.book() as data:
            earlier = [
                task["packet"]
                for other_id, task in data["tasks"].items()
                if other_id != task_id
                and task["packet"].get("unit") == unit
                and "class" in task["packet"]
            ]
        return earlier[-1] if earlier else None
