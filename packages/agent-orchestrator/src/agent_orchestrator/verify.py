"""verify: the one gate. A ticket's report is checked against built-in invariants the plan never declares.

A plan (`plan/2`) declares requirement changes with their scenarios, and tickets; the implementation provides its evidence. Three invariants gate a
ticket, each a rule a worker cannot argue with:

integrity  no ticket touches its plan: a folder plan's folder, or a legacy plan's file and narrative
covers     every scenario the ticket satisfies maps, in the report's `covers`, to a test node `path::name` that
           counts: its file exists, the ticket changed that file and the scenario's `red` excerpt contains `name`;
           that the tests pass is the CI green check on the same head
red        every scenario the ticket satisfies carries, in the report's `red`, the failing output of its
           covering test from before the fix: the test was seen red first

Findings are `{"check", "excerpt"}`; the driver turns them into the fix round's items. A plan is accepted once every
agent ticket is COMPLETE: merged with its gate passed and CI green on its head.

A file the ticket changed that another unfinished ticket lists in its paths is an overlap, never a finding: the worker
keeps the change and the driver tells the Lead, who may order the tickets or expect a merge conflict. A block would
send the worker to revert a change it needs, and when the other ticket waits on this one it can never land first.
"""

import re
import subprocess
from pathlib import Path

from . import plan as yaml_plan

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
    """The files of the ticket: everything since the merge base, committed or not, and untracked files. Renames
    count as a delete and an add, so moving a file out of the plan touches the plan too."""
    merge_base = git(repo, "merge-base", base, "HEAD").strip()
    files = set(git(repo, "diff", "--name-only", "--no-renames", merge_base).splitlines())
    return files | set(filter(None, git(repo, "ls-files", "-o", "--exclude-standard").splitlines()))


def inside(name, paths):
    """`name` is one of `paths` or under one that is a directory."""
    return any(name == p.rstrip("/") or name.startswith(p.rstrip("/") + "/") for p in paths)


def integrity(layout, name):
    """Why a ticket may never touch `name`, or None. `layout` is the plan's resolver, which says what belongs to the
    plan whichever layout it has."""
    if layout.owns(name):
        return "is in the plan; a ticket never edits the plan that gates it"
    return None


def node_problem(repo, node, base, files, red):
    """Why a covers node does not count, or None. A node is `path::name` (a parameter suffix is ignored); it
    counts when its file exists, the ticket changed that file and the scenario's `red` excerpt contains `name`,
    whatever the language: the worker copies the name from the runner's failing output."""
    path, *names = [NODE_PARAMS.sub("", part) for part in node.split("::")]
    if not names:
        return "test node %s names no test (use path::test_name)" % node
    location = (Path(repo) / path).resolve()
    if not location.is_relative_to(Path(repo).resolve()):
        return "test node %s is outside the checkout" % node
    if not location.is_file():
        return "test node %s does not exist (no file %s)" % (node, path)
    if path not in files:
        return "test node %s did not change in this ticket (base is %s)" % (node, base)
    if names[-1] not in red:
        return (
            "test node %s: the red excerpt does not contain %s; copy the name from the runner's failing output"
            % (node, names[-1])
        )
    return None


def holds_no_test(ticket, repo):
    """The lint's predicate, shared with the covers gate: the ticket's Paths hold no test file or directory, so no
    node the ticket maps could ever count. The plan's repo config decides the test patterns."""
    return yaml_plan.holds_no_test(ticket.paths, yaml_plan.repo_config(repo).test_patterns)


def covers(ticket, report, repo, base, files):
    """An implementation must provide its evidence: each scenario the ticket satisfies needs a covering test node
    that counts; one is enough, the others may be helpers. Nothing in the plan exempts a scenario."""
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
    mapped, red = report.get("covers") or {}, report.get("red") or {}
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
            continue
        excerpt = red.get(scenario)
        problems = [
            node_problem(repo, node, base, files, excerpt if isinstance(excerpt, str) else "")
            for node in nodes
        ]
        if all(problems):
            findings.extend(
                {"check": "covers", "excerpt": "%s: %s" % (scenario, problem)}
                for problem in problems
            )
    return findings


def red_first(ticket, report):
    """Red first: each scenario the ticket satisfies carries the failing output of a covering test from before the
    fix. Whether the failure is a genuine assertion of the behaviour is the reviewer's to judge."""
    if report.get("contract") != 3:
        return []  # `covers` already names the contract a YAML plan ticket reports
    mapped, red = report.get("covers") or {}, report.get("red") or {}
    return [
        {
            "check": "red",
            "excerpt": "%s: no red excerpt; add red {%s: <the covering test's failing output from "
            "before the fix, naming its test>}" % (scenario, scenario),
        }
        for scenario in ticket.satisfies
        if mapped.get(scenario)
        and not (isinstance(red.get(scenario), str) and red[scenario].strip())
    ]


def overlaps(model, ticket_id, files, done=()):
    """The files the ticket changed outside its paths that another unfinished ticket lists: `{"file", "ticket",
    "excerpt"}`, one per file, for the Lead. `done` holds the ids of the tickets the ledger marks COMPLETE."""
    ticket = next((t for t in model.tickets if t.id == ticket_id), None)
    if ticket is None:
        raise VerifyError("the plan has no ticket %s" % ticket_id)
    found = []
    for name in sorted(files):
        if inside(name, ticket.paths):
            continue
        owner = next(
            (
                t
                for t in model.tickets
                if t.id != ticket_id and t.id not in done and inside(name, t.paths)
            ),
            None,
        )
        if owner:
            found.append(
                {
                    "file": name,
                    "ticket": owner.id,
                    "excerpt": "%s changed %s, which %s also lists in its paths and has not finished: order"
                    " the tickets if %s must build on this change, or expect a merge conflict when both land"
                    % (ticket_id, name, owner.id, owner.id),
                }
            )
    return found


def invariants(model, ticket_id, report, *, repo, base, layout, files=None):
    """The findings of a YAML plan ticket's gate: integrity, covers and red. Empty means the gate passes.

    A ticket's `paths` are its starting map, never a fence: a file outside them is the ticket's to edit, and one
    another unfinished ticket lists is an overlap for the Lead (`overlaps`), not a finding. `files` are the ticket's
    changed files when the caller already read them."""
    ticket = next((t for t in model.tickets if t.id == ticket_id), None)
    if ticket is None:
        raise VerifyError("the plan has no ticket %s" % ticket_id)
    files = changed(repo, base) if files is None else files
    findings = [
        {"check": "integrity", "excerpt": "%s %s" % (name, why)}
        for name in sorted(files)
        if (why := integrity(layout, name))
    ]
    return findings + covers(ticket, report, repo, base, files) + red_first(ticket, report)


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
