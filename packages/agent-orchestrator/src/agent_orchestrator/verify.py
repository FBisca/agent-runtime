"""verify: the one gate. A ticket's report is checked against built-in invariants the plan never declares.

A plan (`plan/1`) declares scenarios and tickets; the implementation provides its evidence. Three invariants gate a
ticket, each a rule a worker cannot argue with:

scope      every file the ticket changed is inside the ticket's paths
integrity  no ticket touches its plan: a folder plan's folder, or a legacy plan's file and narrative
covers     every scenario the ticket satisfies maps, in the report's `covers`, to test nodes that exist and
           changed in the ticket; that they pass is the CI green check on the same head

Findings are `{"check", "excerpt"}`; the driver turns them into the fix round's items.

Once every ticket merged, `acceptance` says whether the plan holds on the one default-branch commit the driver
pinned for integration: the integration ran for this approval digest on that commit's tree, and the repo's whole
suite, when it declares one, did not fail there.
"""

import ast
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import plan as yaml_plan
from . import runner

HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")
NODE_PARAMS = re.compile(r"\[.*\]$")


class VerifyError(RuntimeError):
    """The gate could not run (not a verdict): a git failure or a ticket the plan does not have."""


def git(repo, *args):
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=off", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if done.returncode:
        raise VerifyError("git %s failed: %s" % (" ".join(args), done.stderr.strip()))
    return done.stdout


def changed(repo, base):
    """(files, {file: added line numbers}) of the ticket: everything since the merge base, committed or not, and
    untracked files. Renames count as a delete and an add, so moving a file out of the plan touches the plan too."""
    merge_base = git(repo, "merge-base", base, "HEAD").strip()
    files = set(git(repo, "diff", "--name-only", "--no-renames", merge_base).splitlines())
    added, current = {}, None
    for line in git(repo, "diff", "-U0", "--no-renames", "--no-color", merge_base).splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif current and (hunk := HUNK.match(line)):
            start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
            added.setdefault(current, set()).update(range(start, start + count))
    for name in filter(None, git(repo, "ls-files", "-o", "--exclude-standard").splitlines()):
        files.add(name)
        path = Path(repo) / name
        if path.is_file():
            added[name] = set(range(1, len(path.read_text(errors="ignore").splitlines()) + 1))
    return files, added


def inside(name, paths):
    """`name` is one of `paths` or under one that is a directory."""
    return any(name == p.rstrip("/") or name.startswith(p.rstrip("/") + "/") for p in paths)


def integrity(layout, name):
    """Why a ticket may never touch `name`, or None. `layout` is the plan's resolver, which says what belongs to the
    plan whichever layout it has."""
    if layout.owns(name):
        return "is in the plan; a ticket never edits the plan that gates it"
    return None


def python_node(source, names):
    """(first, last) line of the test the `names` path points at, or None. A class is its whole body."""
    try:
        scope = ast.parse(source).body
    except SyntaxError:
        scope = None
    node = None
    if scope is not None:
        for name in names:
            node = next(
                (
                    n
                    for n in scope
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and n.name == name
                ),
                None,
            )
            if node is None:
                break
            scope = node.body
        if node is not None:
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            return first, node.end_lineno
    # Unparseable or defined under a condition: the last name's `def` line is the node.
    for number, line in enumerate(source.splitlines(), 1):
        if re.match(r"\s*(?:async\s+)?(?:def|class)\s+%s\b" % re.escape(names[-1]), line):
            return number, number
    return None


def node_problem(repo, node, base, files, added):
    """Why a covers node does not count, or None. A node is `path::name` (pytest ids; a parameter suffix is ignored)."""
    path, *names = [NODE_PARAMS.sub("", part) for part in node.split("::")]
    if not names:
        return "test node %s names no test (use path::test_name)" % node
    location = (Path(repo) / path).resolve()
    if not location.is_relative_to(Path(repo).resolve()):
        return "test node %s is outside the checkout" % node
    if not location.is_file():
        return "test node %s does not exist (no file %s)" % (node, path)
    source = location.read_text(errors="ignore")
    if location.suffix == ".py":
        span = python_node(source, names)
        if span is None:
            return "test node %s does not exist (no %s in %s)" % (node, names[-1], path)
        moved = bool(added.get(path, set()) & set(range(span[0], span[1] + 1)))
    else:
        if names[-1] not in source:
            return "test node %s does not exist (no %s in %s)" % (node, names[-1], path)
        moved = path in files
    if not moved:
        return "test node %s did not change in this ticket (base is %s)" % (node, base)
    return None


def holds_no_test(ticket, repo):
    """The lint's predicate, shared with the covers gate: the ticket's Paths hold no test file or directory, so no
    node the ticket maps could ever count. The plan's repo config decides the test patterns."""
    return yaml_plan.holds_no_test(ticket.paths, yaml_plan.repo_config(repo).test_patterns)


def covers(ticket, report, repo, base, files, added):
    """An implementation must provide its evidence: each scenario the ticket satisfies needs a covering test node
    that exists and changed in the ticket. Nothing in the plan exempts a scenario."""
    contract = report.get("contract")
    if contract != 3:
        return [
            {
                "check": "covers",
                "excerpt": "report is contract %s, not 3: a YAML plan ticket reports contract 3 with covers"
                " {S<n>: [path::test_name]} for each scenario it satisfies (%s)"
                % (contract, ", ".join(ticket.satisfies)),
            }
        ]
    mapped = report.get("covers") or {}
    findings = []
    for scenario in ticket.satisfies:
        nodes = mapped.get(scenario) or []
        if not nodes:
            findings.append(
                {
                    "check": "covers",
                    "excerpt": "%s: no test node in covers; map the scenario to the tests that prove it"
                    % scenario,
                }
            )
        for node in nodes:
            problem = node_problem(repo, node, base, files, added)
            if problem:
                findings.append({"check": "covers", "excerpt": "%s: %s" % (scenario, problem)})
    return findings


def seconds(value):
    """A measured cost: a non-negative number, not a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def test_cost(report, repo, base, files, added):
    """Why the report's `test_cost` does not account for the tests this ticket added or changed.

    A diff that adds or changes a test pays the suite; the report names the command that timed it, the
    seconds of each touched test file and the seconds of each covered node. A ticket that changes no
    test needs no `test_cost`, and a report from before the field existed still reads."""
    mapped = {node for nodes in (report.get("covers") or {}).values() for node in nodes}
    changed = sorted(
        node for node in mapped if node_problem(repo, node, base, files, added) is None
    )
    touched = sorted({node.split("::", 1)[0] for node in changed})
    if not touched:
        return []
    cost = report.get("test_cost")
    if not isinstance(cost, dict):
        return [
            {
                "check": "test_cost",
                "excerpt": "a test changed but the report carries no test_cost; add "
                '{"command": <the command that timed the touched tests>, "modules": {<file>: <seconds>}, '
                '"tests": {<path::test>: <seconds>}}',
            }
        ]
    findings = []
    command = cost.get("command")
    if not isinstance(command, str) or not command.strip():
        findings.append(
            {
                "check": "test_cost",
                "excerpt": "test_cost.command is empty: time the touched tests and name the command that did",
            }
        )
    modules = cost.get("modules")
    if not isinstance(modules, dict):
        findings.append(
            {
                "check": "test_cost",
                "excerpt": "test_cost.modules must map each touched test file to seconds",
            }
        )
    else:
        for name in touched:
            if not seconds(modules.get(name)):
                findings.append(
                    {
                        "check": "test_cost",
                        "excerpt": "test_cost.modules has no seconds for %s; time it and add it"
                        % name,
                    }
                )
    tests = cost.get("tests")
    if not isinstance(tests, dict):
        findings.append(
            {
                "check": "test_cost",
                "excerpt": "test_cost.tests must map each covered test node to seconds",
            }
        )
    else:
        for node in changed:
            if not seconds(tests.get(node)):
                findings.append(
                    {
                        "check": "test_cost",
                        "excerpt": "test_cost.tests has no seconds for %s; time it and add it"
                        % node,
                    }
                )
    return findings


def invariants(model, ticket_id, report, *, repo, base, layout):
    """The findings of a YAML plan ticket's gate: scope, integrity and covers. Empty means the gate passes."""
    ticket = next((t for t in model.tickets if t.id == ticket_id), None)
    if ticket is None:
        raise VerifyError("the plan has no ticket %s" % ticket_id)
    files, added = changed(repo, base)
    findings = []
    for name in sorted(files):
        why = integrity(layout, name)
        if why:
            findings.append({"check": "integrity", "excerpt": "%s %s" % (name, why)})
        elif not inside(name, ticket.paths):
            findings.append(
                {
                    "check": "scope",
                    "excerpt": "%s is outside the ticket paths (%s)"
                    % (name, ", ".join(ticket.paths)),
                }
            )
    return (
        findings
        + covers(ticket, report, repo, base, files, added)
        + test_cost(report, repo, base, files, added)
    )


def items(findings, command, log_path):
    """Findings as fix-round items: the evidence (command, exit, excerpt, log) the driver fills in."""
    return [
        {
            "id": "V%d" % number,
            "kind": "blocking",
            "source": "verify",
            "check": finding["check"],
            "command": command,
            "exit": 1,
            "excerpt": finding["excerpt"],
            "log_path": str(log_path),
        }
        for number, finding in enumerate(findings, 1)
    ]


# ---- the whole suite: `checks.full.run`, once per verify pass on the pinned default-branch commit -------------------

FULL_TIMEOUT = 3600


def run_full(repo, sha, command, log_path, *, timeout=FULL_TIMEOUT, base_env=None):
    """Run the repo's whole suite once: under bash in a scratch worktree of `sha`, with no virtualenv on PATH and a
    temporary HOME. Its whole output goes to `log_path`. Returns {"state": "PASS" | "FAIL", "exit", "detail"}; a
    command that outlives `timeout` is a FAIL."""
    base_env = dict(os.environ if base_env is None else base_env)
    scratch = Path(tempfile.mkdtemp(prefix="eec-full-"))
    tree = None
    try:
        env = runner.suite_env(base_env, scratch)
        tree = runner.worktree(repo, sha, scratch, "tree")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        code, timed_out = runner.execute(command, tree, env, timeout, log_path)
    finally:
        if tree is not None:
            runner.drop_worktree(repo, tree)
        shutil.rmtree(scratch, ignore_errors=True)
    if timed_out:
        return {"state": "FAIL", "exit": None, "detail": "killed after %ss" % timeout}
    return {
        "state": "PASS" if code == 0 else "FAIL",
        "exit": code,
        "detail": None if code == 0 else "exited %s" % code,
    }


def full_failure(data, pin):
    """The item of a whole suite that failed on this integration run, or None. A record of an earlier run (another
    attempt) does not count: each verify pass runs the suite again."""
    full = data.get("full") or {}
    if full.get("state") != "FAIL" or full.get("attempt") != pin.get("attempt"):
        return None
    return {
        "scenario": "full",
        "state": "FAIL",
        "reason": "`%s` %s on %s; its output is %s. Fix the failure, or run again with --skip-full to leave the "
        "whole suite to CI"
        % (
            full.get("command"),
            full.get("detail") or "failed",
            str(full.get("sha"))[:12],
            full.get("log"),
        ),
    }


# ---- acceptance: whether the delivered plan holds on the pinned default-branch commit ------------------------------


def acceptance(data, *, digest, tree_of=None):
    """{"state", "items"} of a delivered plan, from the ledger alone. Each item is {"scenario", "state", "reason"}.

    The plan is ACCEPTED when integration ran for this approval digest on the pinned commit, whose tree is the one
    recorded, and the whole suite did not fail there; REJECTED otherwise, with an item naming why. Each scenario's
    proof is its ticket's covering tests, which the gate and CI already held. `tree_of` maps the pinned commit to its
    tree, so a ledger whose tree was not the commit's is caught too."""
    pin = data.get("integration") or {}
    problem = None
    if not pin:
        problem = "the plan has not been verified on a pinned default-branch commit"
    elif pin.get("digest") != digest:
        problem = "the integration run was for another approval digest; verify the plan again"
    elif tree_of is not None:
        actual = tree_of(pin.get("sha"))
        if actual != pin.get("tree"):
            problem = "the pinned commit %s holds tree %s, not the recorded %s" % (
                str(pin.get("sha"))[:12],
                str(actual)[:12],
                str(pin.get("tree"))[:12],
            )
    items = []
    if problem:
        items.append({"scenario": "integration", "state": "UNKNOWN", "reason": problem})
    elif failed := full_failure(data, pin):
        items.append(failed)
    return {"state": "REJECTED" if items else "ACCEPTED", "items": items}


def acceptance_errors(result):
    """One line per item that does not hold, naming it and the reason: what `verify` exits non-zero on."""
    return [
        "%s %s: %s" % (item["scenario"], item["state"], item["reason"]) for item in result["items"]
    ]
