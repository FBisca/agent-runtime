"""verify: the one gate. A ticket's report is checked against built-in invariants the plan never declares.

A plan (`plan/1`) is gated by three invariants, each a rule a worker cannot argue with:

scope      every file the ticket changed is inside the ticket's paths
integrity  no ticket touches the plan, an oracle file or an oracle place (where the driver puts an oracle)
covers     every scenario the ticket satisfies maps, in the report's `covers`, to test nodes that exist and
           changed in the ticket; that they pass is the CI green check on the same head

Findings are `{"check", "excerpt"}`; the driver turns them into the fix round's items.

Evidence is the other half. `gate_scenarios` says which scenarios' evidence runs at a ticket's gate, and
`acceptance` computes from the ledger and the receipts on disk whether a plan's outcome holds on the one tree the
driver pinned for integration: a receipt that is missing, was produced for another tree or approval digest, or no
longer matches the sha256 the ledger holds does not count (the item is UNKNOWN, naming the scenario and why).
"""

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

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


# ---- evidence: which items run at a ticket's gate, and whether a plan's receipts add up to acceptance ----------


def scenario_of(model, sid):
    return next((s for s in model.scenarios if s.id == sid), None)


def check_scenarios(model):
    """The scenarios whose evidence is a command the driver can run (a `human` item is confirmed by a person)."""
    return [s for s in model.scenarios if s.evidence is not None and s.evidence.check]


def gate_scenarios(model, ticket_id, done):
    """The scenario ids whose evidence runs at `ticket_id`'s gate, in plan order (D11, D29).

    A risk-tagged ticket runs the evidence of the scenarios it satisfies. Whatever its risk, the ticket that
    completes an outcome scenario's satisfies set runs that outcome's evidence: every other ticket that satisfies it
    is in `done`. Every other ticket runs none."""
    ticket = next((t for t in model.tickets if t.id == ticket_id), None)
    if ticket is None:
        raise VerifyError("the plan has no ticket %s" % ticket_id)
    wanted = set()
    if ticket.risk:
        wanted |= set(ticket.satisfies)
    for sid in model.outcome:
        others = {t.id for t in model.tickets if sid in t.satisfies} - {ticket_id}
        if sid in ticket.satisfies and others <= set(done):
            wanted.add(sid)
    return [s.id for s in check_scenarios(model) if s.id in wanted]


def snapshot_commit(checkout):
    """A commit holding exactly what the ticket's checkout holds now, or its HEAD when it holds nothing more.

    A worker reports before anything is committed for it, so a gate that ran evidence at HEAD alone would judge the
    base. The commit is built in a scratch index: the checkout's own index, HEAD and files are not touched. It is
    unreferenced; the runner needs it only while it runs."""
    head = git(checkout, "rev-parse", "HEAD").strip()
    if not git(checkout, "status", "--porcelain").strip():
        return head
    scratch = tempfile.mkdtemp(prefix="eec-index-")
    try:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(scratch, "index"))

        def stage(*args):
            done = subprocess.run(
                ["git", "-C", str(checkout), *args],
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            if done.returncode:
                raise VerifyError("git %s failed: %s" % (args[0], done.stderr.strip()))
            return done.stdout.strip()

        stage("read-tree", head)
        stage("add", "-A")
        tree = stage("write-tree")
        return stage(
            "-c",
            "user.name=orchestrator",
            "-c",
            "user.email=orchestrator@localhost",
            "commit-tree",
            tree,
            "-p",
            head,
            "-m",
            "ticket checkout as the gate saw it",
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def receipt_stems(data, sid, phase):
    """The ledger's receipt keys of one item and phase, oldest first: `S1.integration.1`, `S1.integration.2`..."""
    found = []
    for stem in data.get("receipts") or {}:
        match = re.fullmatch(r"%s\.%s\.(\d+)" % (re.escape(sid), re.escape(phase)), stem)
        if match:
            found.append((int(match.group(1)), stem))
    return [stem for _, stem in sorted(found)]


def read_receipt(data, stem, receipts_dir, digest, tree):
    """(receipt, None) when the receipt counts for this digest and tree, else (None, why it does not)."""
    path = Path(receipts_dir) / (stem + ".json")
    if not path.is_file():
        return None, "its receipt %s.json is missing" % stem
    recorded = (data.get("receipts") or {}).get(stem)
    if not recorded:
        return None, "the ledger holds no sha256 for %s.json" % stem
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != recorded:
        return None, "%s.json no longer matches the sha256 the ledger holds" % stem
    try:
        receipt = json.loads(raw)
    except ValueError:
        return None, "%s.json is not valid JSON" % stem
    if not isinstance(receipt, dict):
        return None, "%s.json is not a receipt" % stem
    if receipt.get("digest") != digest:
        return None, "%s.json was produced for another approval digest" % stem
    if receipt.get("tree") != tree:
        return None, "%s.json was produced for another tree (%s, not %s)" % (
            stem,
            str(receipt.get("tree"))[:12],
            str(tree)[:12],
        )
    return receipt, None


def holds_acceptance(record, digest, tree):
    """A human acceptance counts for the contract and the tree it was given on, and no other."""
    return (
        isinstance(record, dict)
        and record.get("state") == "HUMAN_ACCEPTED"
        and record.get("digest") == digest
        and record.get("tree") == tree
    )


def unverified_scenarios(data):
    """{scenario: ticket} of the evidence a person merged past: it must pass at integration, a person cannot waive it."""
    found = {}
    for ticket, entry in (data.get("unverified") or {}).items():
        for sid in (entry or {}).get("scenarios") or []:
            found[sid] = ticket
    return found


def mark_archive(model, data, receipts_dir, digest, items):
    """An ARCHIVED plan's outcome also holds on the archive head: each outcome item that passed at integration must
    have a PASS receipt for the tree the archive recorded, else it is UNKNOWN (the archive was not verified)."""
    pin = data.get("archive") or {}
    for item in items:
        sc = scenario_of(model, item["scenario"])
        if (
            item["state"] != "PASS"
            or item["scenario"] not in model.outcome
            or not sc.evidence.check
        ):
            continue
        stems = receipt_stems(data, item["scenario"], "archive")
        if not pin or pin.get("digest") != digest:
            why = "the archive head was not verified for this approval digest"
        elif not stems:
            why = "no archive receipt"
        else:
            receipt, why = read_receipt(data, stems[-1], receipts_dir, digest, pin.get("tree"))
            if receipt is not None and receipt.get("result") == "PASS":
                continue
            why = why or "the archive receipt is %s" % receipt.get("result")
        item.update(state="UNKNOWN", reason=why)


def acceptance(model, data, receipts_dir, *, digest, tree_of=None):
    """What the receipts say about a plan: {"state", "items"}. Each item is {"scenario", "state", "reason"} with the
    state PASS, FAIL, UNKNOWN, HUMAN_ACCEPTED or AWAITING_HUMAN.

    The plan is ACCEPTED when every item is PASS; ACCEPTED_WITH_HUMAN when each is PASS or HUMAN_ACCEPTED and one is the
    latter (never ACCEPTED); VERIFYING while only a person's word is missing; REJECTED otherwise. `tree_of` maps the
    pinned commit to its tree, so a ledger whose tree was not the commit's is caught too."""
    pin = data.get("integration") or {}
    tree = pin.get("tree")
    problem = None
    if not pin:
        problem = "the plan has not been verified on a pinned default-branch commit"
    elif pin.get("digest") != digest:
        problem = "the integration run was for another approval digest; verify the plan again"
    elif tree_of is not None:
        actual = tree_of(pin.get("sha"))
        if actual != tree:
            problem = "the pinned commit %s holds tree %s, not the recorded %s" % (
                str(pin.get("sha"))[:12],
                str(actual)[:12],
                str(tree)[:12],
            )
    unverified = unverified_scenarios(data)
    items = []
    for sc in model.scenarios:
        ev = sc.evidence
        if ev is None:
            continue
        record = (data.get("acceptance") or {}).get(sc.id) or {}
        human = holds_acceptance(record, digest, tree) and problem is None
        if ev.human:
            items.append(
                {
                    "scenario": sc.id,
                    "state": "HUMAN_ACCEPTED",
                    "reason": "accepted by %s" % record.get("by"),
                }
                if human
                else {
                    "scenario": sc.id,
                    "state": "AWAITING_HUMAN",
                    "reason": "a person confirms: %s (agent-orchestrator accept <plan> %s --note ...)"
                    % (ev.human, sc.id),
                }
            )
            continue
        state, reason = "UNKNOWN", problem
        if problem is None:
            stems = receipt_stems(data, sc.id, "integration")
            if not stems:
                reason = "no integration receipt"
            else:
                receipt, why = read_receipt(data, stems[-1], receipts_dir, digest, tree)
                if receipt is None:
                    reason = why
                else:
                    state = (
                        receipt.get("result")
                        if receipt.get("result") in ("PASS", "FAIL")
                        else "UNKNOWN"
                    )
                    reason = receipt.get("label") if state != "PASS" else None
        if state != "PASS" and sc.id in unverified:
            reason = (
                "%s; %s merged with this evidence unverified, so it must pass at integration"
                % (
                    reason,
                    unverified[sc.id],
                )
            )
        elif state != "PASS" and human:
            state, reason = "HUMAN_ACCEPTED", "accepted by %s over %s" % (record.get("by"), state)
        items.append({"scenario": sc.id, "state": state, "reason": reason})
    if (data.get("plan") or {}).get("state") == "ARCHIVED":
        mark_archive(model, data, receipts_dir, digest, items)
    states = {item["state"] for item in items}
    if states <= {"PASS"}:
        overall = "ACCEPTED"
    elif states <= {"PASS", "HUMAN_ACCEPTED"}:
        overall = "ACCEPTED_WITH_HUMAN"
    elif states <= {"PASS", "HUMAN_ACCEPTED", "AWAITING_HUMAN"}:
        overall = "VERIFYING"
    else:
        overall = "REJECTED"
    return {"state": overall, "items": items}


def acceptance_errors(result):
    """One line per item that does not count, naming the scenario and the reason: what `verify` exits non-zero on."""
    return [
        "%s %s: %s" % (item["scenario"], item["state"], item["reason"])
        for item in result["items"]
        if item["state"] not in ("PASS", "HUMAN_ACCEPTED")
    ]
