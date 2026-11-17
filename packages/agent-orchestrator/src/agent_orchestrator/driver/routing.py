"""The rules a run is routed by and the packet its workers are briefed with.

A run's route config says which backend, model and effort each class and each gate step runs on, and
the driver reads it rather than any model table. A dispatch is what that routing produces: the ticket's
packet, the branch and worktree it runs in, and the contract text its worker is held to. Both are here
- the config the run was routed by, and the task the worker is handed - so a change to what a tier runs
on and a change to what a worker is told are read side by side.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines and no class twenty-five public methods.
"""

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from .. import approval_recovery, contracts, narrate, routes
from .. import config as repo_config
from .. import ledger as ledgers
from .. import plan as yaml_plan
from ..plan import PlanError
from .book import claim
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
- Edit only the Paths your task owns. Never touch other worktrees or the main checkout.
- Commit with Conventional Commits and push the branch. Describe behaviour, not plan ticket numbers.
"""


TDD_SKILL_ENV = "AGENT_RUNTIME_TDD_SKILL"


TDD_SKILL_PLACES = ("~/.agents/skills/tdd/SKILL.md", "~/.claude/skills/tdd/SKILL.md")


def tdd_skill_path(env=None):
    """The `tdd` skill's SKILL.md: the environment's path, else the places a host installs it.

    A backend with no skill loader still gets the admission and cost rules in the contract; this
    path is what a Claude or Pi worker reads before its first edit."""
    env = os.environ if env is None else env
    named = env.get(TDD_SKILL_ENV)
    if named:
        return named
    for place in TDD_SKILL_PLACES:
        path = Path(os.path.expanduser(place))
        if path.is_file():
            return str(path)
    return None


def worker_name(slug, number, tail=""):
    """`<slug>-t<n>[tail]` within agent-runtime's 32-character name limit; the `-t<n>` suffix always survives."""
    suffix = "-t" + str(number) + tail
    return slug[: 32 - len(suffix)].rstrip("-_") + suffix


def live_terminals():
    """Names of the live Herdr terminals. `hc ls --json` is {"terminals": [{"name"?, "state", …}], …}."""
    result = subprocess.run(["hc", "ls", "--json"], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ValueError(
            "hc ls failed (exit %d): %s"
            % (result.returncode, (result.stderr or result.stdout).strip())
        )
    try:
        rows = json.loads(result.stdout or "{}").get("terminals", [])
    except ValueError as error:
        raise ValueError("hc ls printed no JSON: %s" % error) from None
    return {
        row["name"]
        for row in rows
        if isinstance(row, dict) and row.get("name") and row.get("state") == "live"
    }


def pane_name(slug):
    """hc names allow a-z 0-9 - _ (1-32), so the pane is `orchestrate-<slug>`, not `orchestrate:<slug>`."""
    return ("orchestrate-" + slug)[:32].rstrip("-")


def forwarded(argv, option):
    """The value of `option V` or `option=V` among arguments `launch` forwards to `run`, else None."""
    for index, item in enumerate(argv):
        if item == option and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(option + "="):
            return item[len(option) + 1 :]
    return None


snapshot_dir = approval_recovery.snapshot_dir


load_approved = approval_recovery.load_approved


def is_yaml(plan_path):
    return yaml_plan.PlanLayout.is_plan_file(plan_path)


def require_yaml(plan_path):
    """A plan is a plan/1 YAML file; a Markdown plan is refused with the way out."""
    if not is_yaml(plan_path):
        raise PlanError(
            "%s is not a plan/1 YAML plan (<name>.plan.yaml, or plan.yaml in its own folder): the Markdown plan format is no longer read. "
            "Rewrite it as plan/1 YAML and run `plan check <plan.yaml>`, then `plan approve`"
            % plan_path
        )


def repo_root(plan_path, repo=None):
    """The repository a plan belongs to: the one named, else the one holding the plan file."""
    if repo:
        return Path(repo).resolve()
    return Path(
        _engine().run(
            ["git", "-C", str(Path(plan_path).resolve().parent), "rev-parse", "--show-toplevel"]
        )
    ).resolve()


def read_repo_config(args):
    """Every command that names a plan reads the repo's config, so a bad key refuses before any work.

    A plan outside any repository, with no --repo, has no config to read."""
    plan = getattr(args, "plan", None)
    if args.command == "plan" and getattr(args, "plan_command", None) == "schema":
        return
    if not plan or args.command not in (
        "plan",
        "status",
        "run",
        "complete",
        "launch",
    ):
        return None
    where = Path(args.repo).resolve() if args.repo else Path(plan).resolve().parent
    found = subprocess.run(
        ["git", "-C", str(where), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if found.returncode == 0:
        return yaml_plan.repo_config(Path(found.stdout.strip()).resolve())
    return None


CHECKS_WARNING = (
    "plan check: %s declares no checks node — add checks.fast, checks.affected and checks.full "
    "to brief workers with the cheap check tiers; until then workers run the repository's full CI "
    "gate (or the --gate commands)"
)


def repo_key(repo):
    """The directory a repository's YAML plan state lives in under the state dir: its name for the eye, a hash of its
    git common dir for uniqueness. A worktree of the repository has the repository's key, and two clones that share
    a name do not share one."""
    common = Path(yaml_plan.identity(Path(repo).resolve()))
    home = common.parent if common.name == ".git" else common
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", home.name.removesuffix(".git")).strip("-.") or "repo"
    return "%s-%s" % (name[:40], hashlib.sha256(str(common).encode("utf-8")).hexdigest()[:12])


class Routes:
    """The routing and the dispatch, mixed into the Driver: what runs where, and the packet it is handed.

    A member reads the route config the run holds or builds the task one ticket is dispatched with, and
    asks the driver it belongs to for the plan, the record and the worktrees.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    step_routes: Any
    added_steps: Any
    route_source: Any

    def gate_cli(self):
        """The backend the review gates spawn on: the brain backend when one was named, the worker backend
        otherwise."""
        return getattr(self, "brain_cli", None) or self.args.cli

    def recorded_routes(self):
        """The routes an existing run's ledger recorded, or None for a run that has not started.

        A run that started and has no complete record cannot be recovered from whatever policy is there now:
        it stops here, before any worker, unless --routes-file names the config that migrates it."""
        data = ledgers.snapshot(self.ledger_path)
        if data is None or not ("lead" in data or "routes" in data):
            return None
        pinned, why = routes.read_record(data.get("routes"))
        if pinned is None:
            if getattr(self.args, "routes_file", None):
                return None  # the explicit config migrates the run and records the complete routes
            raise PlanError(
                "cannot resume %s: %s, and the policy now in force may not be what it ran on. Pass "
                "--routes-file with the typed route config to migrate it; the complete resolved routes "
                "are then recorded in the ledger before the run resumes" % (self.slug, why)
            )
        return pinned

    def pin_routes(self, pinned):
        """The recorded routes answer the run; an explicit argument that says otherwise stops it.

        An argument that matches the record is accepted and one the command line omits is not a conflict. The
        ledger is not written until the run is clear to resume."""
        args = self.args
        chosen, chosen_steps = _overrides(args)
        via = {route.klass: "--route" for route in chosen}
        via.update({route.step: "--step" for route in chosen_steps})
        classes = {route.klass: route for route in chosen}
        steps = {route.step: route for route in chosen_steps}
        classes, steps, via, dropped = _explicit_routes(
            args, pinned, chosen, chosen_steps, classes, steps, via
        )
        brain = self.brain_cli or pinned.brain_cli
        added = _gate_steps(pinned, steps, brain)
        found = routes.conflicts(
            pinned, classes, steps, cli=args.cli, brain_cli=self.brain_cli, via=via
        )
        found += [
            "--routes-file: step %s is recorded as %s, the file names no such step"
            % (name, routes.describe(pinned.steps[name]))
            for name in dropped
        ]
        if found:
            raise PlanError(
                "cannot resume %s: the arguments conflict with the routes its ledger recorded (%s). Drop "
                "the conflicting argument to keep the recorded routes; the ledger is unchanged"
                % (self.slug, "; ".join(found))
            )
        args.cli = args.cli or pinned.cli
        self.brain_cli = self.brain_cli or pinned.brain_cli
        self.routes, self.step_routes = dict(pinned.classes), dict(pinned.steps) | added
        self.added_steps = added
        self.routes_file, self.route_source = pinned.path, pinned.source
        self._brain_gates_refused()

    def _brain_gates_refused(self):
        """A record whose medium route is not the brain backend cannot spawn its gates, and the refusal names why."""
        if not self.brain_cli:
            return
        wanted = [s for s in ("reviewer",) if s not in self.step_routes]
        medium = self.routes["medium"]
        if wanted and medium.backend != self.brain_cli:
            raise PlanError(
                "cannot resume %s: the recorded medium route runs on %s, not on the brain backend %s the "
                "%s gate(s) would spawn on. Pass --step %s=%s/MODEL[@EFFORT] for them, or --routes-file "
                "with a medium route on %s to migrate"
                % (
                    self.slug,
                    medium.backend,
                    self.brain_cli,
                    "/".join(wanted),
                    wanted[0],
                    self.brain_cli,
                    self.brain_cli,
                )
            )

    def load_routes(self):
        """The class -> route map this run uses, from the typed route config the run selected. An invalid route
        config stops the run here, before any worker; it never falls back to another file.

        The step routes come with it: the config's own `steps` list, then the command line's overrides,
        which replace the class or the step they name."""
        try:
            config = routes.load(self.routes_file)
            resolved = routes.resolve(config)
            steps = {step.step: step for step in config.steps}
        except routes.RouteError as error:
            raise PlanError("invalid route config %s: %s" % (self.routes_file, error)) from None
        try:
            resolved = routes.apply_overrides(
                resolved, routes.overrides(getattr(self.args, "route", None))
            )
        except routes.RouteError as error:
            raise PlanError("invalid route override: %s" % error) from None
        try:
            steps = routes.apply_step_overrides(
                steps, routes.step_overrides(getattr(self.args, "step", None))
            )
        except routes.RouteError as error:
            raise PlanError("invalid step override: %s" % error) from None
        return resolved, steps

    def require_gate_routes(self):
        """A brain backend needs a route for each gate it spawns: a step route, or a medium route on that backend.

        A run that cannot route its gates stops before any worker, with the arguments that would route them."""
        brain = self.brain_cli
        if not brain:
            return
        wanted = [step for step in ("reviewer",) if step not in self.step_routes]
        medium = self.routes["medium"]
        if wanted and medium.backend != brain:
            raise PlanError(
                "the medium route runs on %s, not on the brain backend %s the %s gate(s) would spawn on. "
                "Pass --step %s=%s/MODEL[@EFFORT] for them, or give the route config a medium route on %s"
                % (
                    medium.backend,
                    brain,
                    "/".join(wanted),
                    wanted[0],
                    brain,
                    brain,
                )
            )

    def step_route(self, step):
        """The step's own route, when the run names one."""
        return (getattr(self, "step_routes", None) or {}).get(step)

    def step_backend(self, step, default):
        """The backend one step spawns on: its own route, or the run's answer."""
        route = self.step_route(step)
        return route.backend if route else default

    def step_model(self, step, default):
        """The model one step spawns on: its own route, or the run's answer."""
        route = self.step_route(step)
        return route.model if route else default

    def step_effort(self, step, default):
        """The effort one step spawns on: its own route, or the run's answer."""
        route = self.step_route(step)
        return (route.effort or default) if route else default

    def gate_model(self, klass):
        """The model a review gate spawns on: the brain's medium model when a brain backend was named.

        A typed route config answers the brain model: its medium class must run on the brain backend, and a run
        whose medium route does not stopped at start unless its gates have step routes."""
        brain = getattr(self, "brain_cli", None)
        if not brain:
            return self.routes[klass].model
        route = self.routes.get("medium")
        if route is None or route.backend != brain:
            raise PlanError(
                "the route config has no medium route on the brain backend '%s'" % brain
            )
        return route.model

    def gate_step(self, step, klass):
        """The backend and model one gate spawns on: the step's own route, or the brain's answer.

        A step route wins over the brain: the reviewer may sit on another backend, and on another
        model, than the implementer."""
        route = self.step_route(step)
        if route:
            return route.backend, route.model
        return self.gate_cli(), self.gate_model(klass)

    def gate_label(self, step, klass):
        """The class a review gate runs on, for the event line: the step's own name when a step route names it."""
        if self.step_route(step):
            return step
        return "medium" if getattr(self, "brain_cli", None) else klass

    def decision_unapplied(self, task_id, gate, error):
        """A step that was applying the Lead's decision failed (the hand-over was refused, e.g. the pane is blocked on
        a login): the unit gets a new request naming the failure and the way out, instead of sitting stuck for the rest
        of the run. A worker message that lands while it is open is held, and the next decision acts on it. False when
        the step was not applying a decision."""
        task = self.task(task_id)
        applying = _applying(task, gate, self)
        if applying is None:
            return False
        worker, from_gate, menu, way_out = _handover_target(task, gate, applying)
        # the wake cuts a reason at 200 characters: the way out comes first, the runtime's words (all in the file) after
        reason = "the Lead's decision (%s) could not reach %s; %s: %s" % (
            applying or "decision",
            worker,
            way_out,
            " ".join(str(error).split())[:600],
        )
        with self.book() as data:
            if "decision" in data["tasks"][task_id]:
                data["tasks"][task_id]["decision"].pop("applying", None)
        self.log(
            "error",
            unit=task["packet"]["unit"],
            task=task_id,
            gate=gate,
            error=str(error)[:500],
        )
        self.request(task_id, "handover", reason, menu=menu, from_gate=from_gate)
        return True

    def decided_action(self, task):
        try:
            return contracts.load(task["decision"]["path"], contracts.validate_decision)["action"]
        except (contracts.ContractError, KeyError, OSError, ValueError):
            return None

    def class_up(self, packet):
        """The packet fields that move a retry one model class up; at the top class the effort goes up a notch."""
        classes, efforts = ROUTE["class"], ROUTE["effort"]
        at = classes.index(packet["class"])
        if at + 1 < len(classes):
            return {"class": classes[at + 1], "model": self.routes[classes[at + 1]].model}
        return {"effort": efforts[min(efforts.index(packet["effort"]) + 1, len(efforts) - 1)]}

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
        satisfies and the checks its worker runs. A dispatch and an adopted amendment write the same ones."""
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


def _overrides(args):
    """(class routes, step routes) named on the command line, refused when one is not a route."""
    try:
        chosen = routes.overrides(getattr(args, "route", None))
        chosen_steps = routes.step_overrides(getattr(args, "step", None))
    except routes.RouteError as error:
        raise PlanError("invalid route override: %s" % error) from None
    return chosen, chosen_steps


def _explicit_routes(args, pinned, chosen, chosen_steps, classes, steps, via):
    """The routes an explicit `--routes-file` names, with the ledger's own steps checked against it."""
    explicit = getattr(args, "routes_file", None)
    if not explicit:
        return classes, steps, via, []
    try:
        config = routes.load(explicit)
        resolved = routes.resolve(config)
    except routes.RouteError as error:
        raise PlanError("invalid route config %s: %s" % (explicit, error)) from None
    # An explicit file is the whole routing of the run, so a step the ledger has and it lacks differs too.
    via = {name: "--routes-file" for name in (*routes.CLASSES, *routes.STEPS)} | via
    classes = routes.apply_overrides(resolved, chosen)
    steps = routes.apply_step_overrides({s.step: s for s in config.steps}, chosen_steps)
    return classes, steps, via, sorted(set(pinned.steps) - set(steps))


def _gate_steps(pinned, steps, brain):
    """The gate steps the command line adds, for a record whose own routes cannot reach its gates."""
    gates = (
        [s for s in ("reviewer",) if s not in pinned.steps]
        if brain and pinned.classes["medium"].backend != brain
        else []
    )
    return {name: steps.pop(name) for name in gates if name in steps}


def _applying(task, gate, drive):
    """What a step was applying, or None when it was not applying a decision and nothing is owed."""
    if task.get("gate") != gate:
        return None
    applying = (task.get("decision") or {}).get("applying")
    if gate == "decided":
        return applying or drive.decided_action(task)
    if gate in ("redo-pending", "fix-pending") and applying:
        return applying
    return None


def _handover_target(task, gate, applying):
    """(worker, from_gate, menu, the way out) the new request names, after a hand-over the pane refused."""
    worker, from_gate, menu = task["packet"]["worker"], None, None
    way_out = "retry it once the pane works, or fresh worker, mark blocked or skip"
    if (
        gate == "decided"
        and applying == "answer"
        and (task.get("request") or {}).get("from_gate") == "reviewing"
        and task.get("review")
    ):
        # The answer was for the reviewer: the new request names it and keeps the review routing, so the Lead's next
        # `answer` reaches the reviewer again, not the implementer.
        worker, from_gate = task["review"]["name"], "reviewing"
        # `retry` and `fresh worker` on this request resume the implementer, not the reviewer: name and offer only
        # the actions that reach the reviewer.
        way_out = "answer again once the pane works, or mark blocked or skip"
        menu = ("answer", "mark blocked", "skip")
    return worker, from_gate, menu, way_out
