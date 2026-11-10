"""verify: the one gate. A ticket's report is checked against built-in invariants the plan never declares.

A YAML plan (`plan/1`) is gated by three invariants, each a rule a worker cannot argue with:

scope      every file the ticket changed is inside the ticket's paths
integrity  no ticket touches the plan, an oracle file or an oracle place (where the driver puts an oracle)
covers     every scenario the ticket satisfies maps, in the report's `covers`, to test nodes that exist and
           changed in the ticket; that they pass is the CI green check on the same head

A Markdown plan in flight keeps the legacy path: `converge.py`, whose name-based coverage verdicts are the ones it
gave before. A Markdown plan can finish as `COMPLETE (outcome unverified)` and never as accepted: nothing ran its
outcome. The Markdown reader and this path go together.

Findings are `{"check", "excerpt"}`; the driver turns them into the fix round's items.
"""

import ast
import re
import shlex
import subprocess
import sys
from pathlib import Path

UNVERIFIED = "COMPLETE (outcome unverified)"
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
    untracked files. Renames count as a delete and an add, so leaving an oracle place touches it too."""
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


def places(model):
    """The files the driver puts oracles in: each evidence item's `place` directory plus the oracle's name."""
    found = set()
    for scenario in model.scenarios:
        evidence = scenario.evidence
        if evidence and evidence.place:
            for name in evidence.oracle:
                found.add((Path(evidence.place) / name.removesuffix(".oracle")).as_posix())
    return found


def integrity(model, plan_rel, name):
    """Why a ticket may never touch `name`, or None."""
    oracle_dir = plan_rel.removesuffix(".plan.yaml") + ".oracle"
    if name == plan_rel:
        return "is the plan; a ticket never edits its contract"
    if name.startswith(oracle_dir + "/"):
        return "is an oracle file; a ticket never edits one"
    if name in places(model):
        return "is an oracle place; the driver places the oracle there, a ticket never does"
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


def covers(ticket, report, repo, base, files, added):
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


def invariants(model, ticket_id, report, *, repo, base, plan_rel):
    """The findings of a YAML plan ticket's gate: scope, integrity and covers. Empty means the gate passes."""
    ticket = next((t for t in model.tickets if t.id == ticket_id), None)
    if ticket is None:
        raise VerifyError("the plan has no ticket %s" % ticket_id)
    files, added = changed(repo, base)
    findings = []
    for name in sorted(files):
        why = integrity(model, plan_rel, name)
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
    return findings + covers(ticket, report, repo, base, files, added)


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


def legacy_gate(script, plan, checkout, base, log_file, *, ticket=None):
    """A Markdown plan's gate: converge, run as before. ("PASS" | "FAIL" | "error", items or the reason).
    `ticket` T<n> gates one ticket; None is `--final`, the archive."""
    argv = [sys.executable, str(script), str(plan)]
    argv += ["--ticket", ticket.upper()] if ticket else ["--final"]
    argv += ["--repo", str(checkout), "--base", base]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    lines = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
    if result.returncode == 0:
        return "PASS", []
    if result.returncode == 1 and lines:
        log = log_file()
        log.write_text(result.stdout, encoding="utf-8")
        command = " ".join(shlex.quote(part) for part in argv)
        return "FAIL", [
            {
                "id": "C%d" % number,
                "kind": "blocking",
                "source": "converge",
                "check": (
                    (re.match(r"FAIL ([^:]+):", line) or [None, "converge"])[1] or "converge"
                ).strip(),
                "command": command,
                "exit": result.returncode,
                "excerpt": line,
                "log_path": str(log),
            }
            for number, line in enumerate(lines, 1)
        ]
    return "error", [
        (
            result.stderr.strip()
            or result.stdout.strip()
            or "converge exited %d" % result.returncode
        )[:500]
    ]


def legacy_outcome(complete):
    """What a Markdown plan can be once its tickets are done: complete with an unverified outcome, never accepted."""
    return UNVERIFIED if complete else None
