"""verify: the one gate. A ticket's report is checked against built-in invariants the plan never declares.

A plan (`plan/1`) declares scenarios and tickets; the implementation provides its evidence. Three invariants gate a
ticket, each a rule a worker cannot argue with:

scope      every file the ticket changed is inside the ticket's paths
integrity  no ticket touches its plan: a folder plan's folder, or a legacy plan's file and narrative
covers     every scenario the ticket satisfies maps, in the report's `covers`, to test nodes that exist and
           changed in the ticket; that they pass is the CI green check on the same head
red        every scenario the ticket satisfies carries, in the report's `red`, the failing output of its
           covering test from before the fix, naming one of its `covers` nodes: the test was seen red first

Findings are `{"check", "excerpt"}`; the driver turns them into the fix round's items. A plan is accepted once every
agent ticket is COMPLETE: merged with its gate passed and CI green on its head.
"""

import ast
import re
import subprocess
from pathlib import Path

from . import plan as yaml_plan

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


def names_node(excerpt, node):
    """Whether a failing-output excerpt names `node`: its full id, or the test's own name (a runner may print
    either)."""
    bare = "::".join(NODE_PARAMS.sub("", part) for part in node.split("::"))
    return bare in excerpt or bare.rsplit("::", 1)[-1] in excerpt


def red_first(ticket, report):
    """Red first: each scenario the ticket satisfies carries the failing output of a covering test from before the
    fix. The gate checks the excerpt is there and names a node of that scenario's `covers`; whether the failure is
    a genuine assertion of the behaviour is the reviewer's to judge."""
    if report.get("contract") != 3:
        return []  # `covers` already names the contract a YAML plan ticket reports
    mapped, red = report.get("covers") or {}, report.get("red") or {}
    findings = []
    for scenario in ticket.satisfies:
        excerpt = red.get(scenario)
        nodes = mapped.get(scenario) or []
        if not nodes:
            continue  # the covers check names a scenario with no node; its red follows once it has one
        if not isinstance(excerpt, str) or not excerpt.strip():
            findings.append(
                {
                    "check": "red",
                    "excerpt": "%s: no red excerpt; add red {%s: <the covering test's failing output from "
                    "before the fix, naming its node>}" % (scenario, scenario),
                }
            )
        elif not any(names_node(excerpt, node) for node in nodes):
            findings.append(
                {
                    "check": "red",
                    "excerpt": "%s: the red excerpt names none of its covers nodes (%s); paste the failing "
                    "output of one of them from before the fix" % (scenario, ", ".join(nodes)),
                }
            )
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
    """The findings of a YAML plan ticket's gate: scope, integrity, covers and red. Empty means the gate passes."""
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
        + red_first(ticket, report)
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
