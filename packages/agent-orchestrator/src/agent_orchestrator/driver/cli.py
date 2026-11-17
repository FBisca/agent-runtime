"""The `agent-orchestrator` command line: one builder per command group, one parser, one entry.

`main` was one four-hundred-line function that built the whole parser in one body, so a command's name,
its flags and its help could only be read by scrolling past nineteen others. Each builder below
registers one command (or one group of them) and nothing else, `build_parser` collects them in the order
a caller reads them, and `main` parses the line once and hands it to the engine's dispatch. `run_cli` is
the entry point the console script, `python -m agent_orchestrator` and `agent_orchestrator.main` share.
"""

import argparse
import json
import subprocess
import sys

from .. import contracts, invariants, workbench
from .. import ledger as ledgers
from .. import plan as yaml_plan
from ..plan import PlanError
from . import __doc__ as ENGINE_DOC


def add_plan_commands(commands):
    """`plan`: the plan/2 contract's own verbs under one group."""
    plan_commands = commands.add_parser(
        "plan", help="Validate and approve plan/2 YAML contracts"
    ).add_subparsers(dest="plan_command", required=True)
    plan_commands.add_parser("schema", help="Print the plan/2 JSON Schema")

    for verb in ("check", "approve"):
        sub = plan_commands.add_parser(verb)
        sub.add_argument("plan")
        sub.add_argument("--repo", help="Repository root; default the plan file's repository")
        sub.add_argument("--state-dir", default=yaml_plan.DEFAULT_STATE)
        if verb == "approve":
            sub.add_argument("--base", default="origin/main")
            sub.add_argument(
                "--reason",
                help="Why the plan changes; a re-approval under a live driver keeps its approval's base",
            )


def add_plans_commands(commands):
    """`plans list`: the plans a repository holds, as a listing or as JSON."""
    plans = commands.add_parser(
        "plans", help="List the plans of a repository (or of an explicit plan's directory)"
    ).add_subparsers(dest="plans_command", required=True)
    listing = plans.add_parser("list")
    listing.add_argument("path", nargs="?", help="A plan file, or the directory holding plans")
    listing.add_argument("--repo", help="Repository root; default the current repository")
    listing.add_argument("--json", action="store_true", help="Print the list as a JSON array")


def add_workbench_commands(commands):
    """`workbench`: the read-only JSON views the Herdr workbench reads."""
    bench = commands.add_parser(
        "workbench",
        help="Read-only JSON views of plans for the Herdr workbench: nothing is written",
    ).add_subparsers(dest="workbench_command", required=True)
    for verb, what in (
        ("plans", "Every plan of a repository with its state and ticket counts"),
        ("plan", "One YAML plan's ticket graph: blocked_by edges, layers, states and revision"),
        ("totals", "The last seven days of every plan, summed for the cards and their dashboards"),
    ):
        sub = bench.add_parser(verb, help=what)
        sub.add_argument(
            "plan" if verb == "plan" else "path",
            nargs=None if verb == "plan" else "?",
            help="The plan file"
            if verb == "plan"
            else "A plan file, or the directory holding plans",
        )
        sub.add_argument("--repo", help="Repository root; default the current repository")
        sub.add_argument("--state-dir", default=yaml_plan.DEFAULT_STATE)
        sub.add_argument("--json", action="store_true", help="Accepted; the answer is always JSON")


def add_inspect_command(commands):
    """`inspect`: what this checkout is, and where the commands it drives live."""
    inspect = commands.add_parser(
        "inspect", help="Read checkout identity and installed command paths"
    )
    inspect.add_argument("--checkout", default=".")


def add_task_commands(commands):
    """The task ledger's own verbs: one parser per verb, its state path and the options it alone needs."""
    for name in ("claim", "bind", "checkpoint", "release", "show"):
        sub = commands.add_parser(name)
        sub.add_argument(
            "--state", required=True, help="Shared ledger path outside tracked product files"
        )
        if name in ("bind", "checkpoint", "release"):
            sub.add_argument("--task", required=True)
        if name == "show":
            sub.add_argument(
                "--task", help="Show the full record for one task; otherwise show a compact index"
            )
            sub.add_argument("--unit", help="Sum failed reviews and cutoffs over a unit's attempts")
        if name == "claim":
            sub.add_argument("--packet", required=True)
        if name == "bind":
            sub.add_argument("--runtime-id", required=True)
        if name == "checkpoint":
            sub.add_argument("--report", required=True)
        if name == "release":
            sub.add_argument("--reason", required=True)
            sub.add_argument(
                "--stopped", action="store_true", help="Assert runtime confirms the worker stopped"
            )
            sub.add_argument(
                "--cutoff", action="store_true", help="The worker was stopped at its cutoff"
            )


def add_ledger_command(commands):
    """`ledger check`: the read side of the ledger's invariants, for a captured ledger."""
    invariants.ledger_commands(commands)


def add_context_command(commands):
    """`context`: how much room a child has left, read from its runtime telemetry."""
    sub = commands.add_parser("context")
    sub.add_argument("--sample", required=True)
    sub.add_argument("--reserve-tokens", type=int, required=True)
    sub.add_argument("--max-age-seconds", type=int, default=300)


def add_metrics_command(commands):
    """`metrics`: a run's metrics.json rebuilt from its events, or a usage file normalized."""
    sub = commands.add_parser(
        "metrics", help="Rebuild a run's metrics.json from its events.jsonl, or normalize --samples"
    )
    sub.add_argument("plan", nargs="?", help="Plan file of the run to rebuild")
    sub.add_argument("--samples", help="A file of per-request usage records to normalize")
    sub.add_argument(
        "--state-dir",
        default="~/.local/state/orchestrate",
        help="Holds <slug>.json and the <slug>/ run directory; a YAML plan's are under <repo-key>/",
    )
    sub.add_argument(
        "--repo", help="Repository root of a YAML plan; default the plan file's repository"
    )


def add_evidence_command(commands):
    """`evidence`: a run's per-attempt evidence rebuilt from its ledger."""
    sub = commands.add_parser(
        "evidence", help="Rebuild a run's attempts.jsonl (the per-attempt evidence) from its ledger"
    )
    sub.add_argument("plan", help="Plan file of the run to rebuild")
    sub.add_argument(
        "--state-dir",
        default="~/.local/state/orchestrate",
        help="Holds <slug>.json and the <slug>/ run directory; a YAML plan's are under <repo-key>/",
    )
    sub.add_argument(
        "--repo", help="Repository root of a YAML plan; default the plan file's repository"
    )


def add_run_command(commands):
    """`run`: the whole drive — its plan, its workers, its routes and its timing."""
    sub = commands.add_parser(
        "run", help="Dispatch an approved plan's ready agent tickets and wait for them"
    )
    sub.add_argument("plan")
    _add_run_workers(sub)
    _add_run_gates(sub)
    _add_run_routes(sub)
    _add_run_timing(sub)


def _add_run_workers(sub):
    """Who a run spawns: how many, on which backend and profile, under which role and base."""
    sub.add_argument("--max-workers", type=int, default=3)
    sub.add_argument(
        "--max-fix-rounds",
        type=int,
        default=2,
        help="Fix rounds a unit may use before the next failed gate raises a request; a plan line "
        "`Max fix rounds: T3=3` sets it for one ticket",
    )
    sub.add_argument(
        "--context-reserve",
        type=int,
        default=40000,
        help="Tokens an implementer or reviewer must have left before a resume; below it the driver swaps in a fresh agent",
    )
    sub.add_argument(
        "--cli",
        choices=("claude", "codex", "pi"),
        help="The worker backend (default claude). A run that resumes keeps the backend its ledger recorded; "
        "an explicit --cli that differs from it stops the run",
    )
    sub.add_argument(
        "--brain-cli",
        choices=("claude", "codex", "pi"),
        help="The backend the review gate (the reviewer) spawns on, with its "
        "medium model; the implementer stays on --cli",
    )
    sub.add_argument(
        "--agent",
        help="Role every worker runs under; 'none' runs them without one. "
        "Default: implementer for implementation workers, no role for review gates",
    )
    sub.add_argument(
        "--profile",
        help="Profile every worker runs under, as the runtime knows it "
        "(a configured profile such as 'minimax'); it replaces the CLI family "
        "in the spawn. Default: the CLI family alone",
    )
    sub.add_argument(
        "--base", default="origin/main", help="Remote branch each worktree starts from"
    )
    sub.add_argument("--repo", help="Repository root; default the plan file's repository")


def _add_run_gates(sub):
    """What every worker must pass, and where the run keeps its state."""
    sub.add_argument(
        "--gate", action="append", default=[], help="CI command a worker must pass (repeatable)"
    )
    sub.add_argument(
        "--state-dir",
        default="~/.local/state/orchestrate",
        help="Holds <slug>.json and the <slug>/ run directory; a YAML plan's are under <repo-key>/",
    )


def _add_run_routes(sub):
    """Typed routes, one per model class or one per step of the run, and the table it replaced."""
    sub.add_argument(
        "--models-file",
        help="Removed: the driver reads no model table. Naming it stops the run; pass --routes-file instead",
    )
    sub.add_argument(
        "--routes-file",
        help="Typed route config (JSON, or YAML with PyYAML): what each model class runs on. It wins over "
        "the repository's .orchestrate/routes.json, which wins over the user's profile for --cli "
        "(~/.config/orchestrate/routes.json for claude, routes-codex.json, routes-pi.json). "
        "A new run with none of them stops before any worker",
    )
    sub.add_argument(
        "--route",
        action="append",
        default=[],
        metavar="CLASS=BACKEND/MODEL[@EFFORT]",
        help="An explicit route for one model class: 'small=codex/gpt-s@high'. It wins over the route "
        "config",
    )
    sub.add_argument(
        "--step",
        action="append",
        default=[],
        metavar="STEP=BACKEND/MODEL[@EFFORT]",
        help="An explicit route for one step of the run: 'reviewer=codex/gpt-l@xhigh'. The steps are "
        "implementer and reviewer. It wins over the route config and over --brain-cli",
    )


def _add_run_timing(sub):
    """A run's waits: how long one blocks, and how its idle and working minutes are counted."""
    sub.add_argument(
        "--notify-terminal",
        default=None,
        help="hc terminal told about each new wake (`launch` passes its own); unset: wakes.jsonl only",
    )
    sub.add_argument(
        "--wait-seconds",
        type=int,
        default=60,
        help="Longest one wait blocks before the plan is re-read",
    )
    sub.add_argument(
        "--idle-interval",
        type=float,
        default=30,
        help="Seconds to sleep after an iteration that made no progress",
    )
    sub.add_argument(
        "--idle-minutes",
        type=float,
        default=10,
        help="Minutes between the idle ticks: a unit whose agent and CI show nothing new is logged idle, "
        "and at two ticks in a row the Lead is asked",
    )
    sub.add_argument(
        "--working-minutes",
        type=float,
        default=120,
        help="Minutes a unit's agent may be reported `working` by the runtime before the idle tick counts it",
    )


def add_await_command(commands):
    """`await`: the next wake the Lead has not seen, waited for."""
    sub = commands.add_parser(
        "await",
        help="Print the next wake the Lead has not seen, or `finished -> <retro>`; waits for one",
    )
    sub.add_argument("plan")
    sub.add_argument(
        "--state-dir",
        default="~/.local/state/orchestrate",
        help="Holds <slug>.json and the <slug>/ run directory; a YAML plan's are under <repo-key>/",
    )
    sub.add_argument(
        "--repo", help="Repository root of a YAML plan; default the plan file's repository"
    )
    sub.add_argument(
        "--poll-seconds", type=float, default=2, help="How often wakes.jsonl is read while waiting"
    )


def add_decide_command(commands):
    """`decide`: one action from a unit's open request's menu."""
    sub = commands.add_parser(
        "decide", help="Answer a unit's open request with one action from its menu"
    )
    sub.add_argument("plan")
    sub.add_argument("unit")
    sub.add_argument(
        "action", nargs="?", help="One of: " + ", ".join(contracts.ACTIONS) + " (or use --json)"
    )
    sub.add_argument("--reason", help="Why; required with an action")
    sub.add_argument(
        "--answer", help="The text the worker is resumed with; required for the answer action"
    )
    sub.add_argument(
        "--json",
        metavar="FILE",
        help='The whole decision as a JSON object {"action", "reason", "answer"?}, from FILE or - for stdin; '
        "a refusal is one JSON object {error, reason, way_out} on stderr",
    )
    sub.add_argument(
        "--state-dir",
        default="~/.local/state/orchestrate",
        help="Holds <slug>.json and the <slug>/ run directory; a YAML plan's are under <repo-key>/",
    )
    sub.add_argument(
        "--repo", help="Repository root of a YAML plan; default the plan file's repository"
    )


def add_status_command(commands):
    """`status`: a plan's state and history, and each ticket's state, from its ledger."""
    sub = commands.add_parser(
        "status",
        help="A plan's state and history and each ticket's state, reason and PR, from its ledger",
    )
    sub.add_argument("plan")
    sub.add_argument("--json", action="store_true", help="Print the answer as JSON")
    sub.add_argument("--repo", help="Repository root; default the plan file's repository")
    sub.add_argument("--state-dir", default=yaml_plan.DEFAULT_STATE)


def add_complete_command(commands):
    """`complete`: the Lead records a person-owned ticket complete, with its proving nodes."""
    sub = commands.add_parser(
        "complete",
        help="The Lead records a person-owned ticket COMPLETE, with the test nodes that prove its scenarios",
    )
    sub.add_argument("plan")
    sub.add_argument("ticket", help="The person-owned ticket, such as T2")
    sub.add_argument("--note", required=True, help="What was done: kept in the ticket's status")
    sub.add_argument(
        "--cover",
        action="append",
        metavar="S<n>=<node>",
        help="A test node that proves a scenario the ticket satisfies; a node in another repository is "
        "<repo-name>/<path>::<test>. Repeat for more",
    )
    sub.add_argument("--repo", help="Repository root; default the plan file's repository")
    sub.add_argument("--state-dir", default=yaml_plan.DEFAULT_STATE)


def add_launch_command(commands):
    """`launch`: check the plan, then start `run` in a terminal of its own."""
    sub = commands.add_parser(
        "launch", help="Check the plan and start `run` in a Herdr terminal named orchestrate-<slug>"
    )
    sub.add_argument("plan")
    sub.add_argument("--repo", help="Repository root; default the plan file's repository")


# The parser's command order, one builder each: the help a caller reads lists them in this order.
BUILDERS = (
    add_plan_commands,
    add_plans_commands,
    add_workbench_commands,
    add_inspect_command,
    add_task_commands,
    add_ledger_command,
    add_context_command,
    add_metrics_command,
    add_evidence_command,
    add_run_command,
    add_await_command,
    add_decide_command,
    add_status_command,
    add_complete_command,
    add_launch_command,
)


def build_parser():
    """The one parser every entry point drives: its twenty commands, each registered by its builder."""
    parser = argparse.ArgumentParser(description=ENGINE_DOC)
    commands = parser.add_subparsers(dest="command", required=True)
    for builder in BUILDERS:
        builder(commands)
    return parser


def main(argv=None):
    """The command line parsed once, then answered: the engine dispatches the command it names."""
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra and args.command != "launch":
        parser.error("unrecognized arguments: " + " ".join(extra))
    # The package imports this module for its parser, so its dispatch — built from the engine's own
    # command implementations — is fetched once the package has finished defining them.
    from . import dispatch

    return dispatch(args, extra)


def run_cli(argv=None):
    """The script's entry point: dispatch, print the answer, return the exit code.

    The tests call this in process, so a driver run starts no process of its own."""
    # The engine's JSON refusal is fetched here for the same reason the dispatch is: it is defined by
    # the package this module belongs to, after this module loads.
    from . import JsonRefusal

    try:
        output = main(argv)
        print(output if isinstance(output, str) else json.dumps(output, separators=(",", ":")))
        return 1 if isinstance(output, dict) and output.get("errors") else 0
    except workbench.WorkbenchError as error:
        # stdout holds a view or nothing: a refusal is one JSON object on stderr, so a reader never mistakes it for
        # a partial view.
        print(json.dumps(error.output, separators=(",", ":")), file=sys.stderr)
        return 2
    except JsonRefusal as error:
        print(json.dumps(error.output), file=sys.stderr)
        return 2
    except invariants.LedgerInvariant as error:
        # A state the ledger's invariants refuse is a refusal, not a crash: exit 2 says nothing was written, and the
        # object names the invariant, the task and the way out.
        print(json.dumps(error.output), file=sys.stderr)
        return 2
    except PlanError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired, ledgers.LedgerBusy) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
