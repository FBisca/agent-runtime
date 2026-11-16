"""The plan/1 contract and its local approval snapshots.

Digest inputs use eight-byte big-endian length prefixes. The plan input is
safe_dump(sort_keys=True) after dropping approval: comments, key order and YAML
presentation are preserved on disk but do not affect the contract digest.

This module holds the rules the contract itself makes - the semantic validator, the legacy and folder
rules, the selector lint, the approval's baseline and its amendment - and every other collaborator
lives in the module beside it: the YAML document in `plan_document.py`, where a plan's files live in
`plan_layout.py`, the refusals over a plan's paths in `plan_checks.py`, the pytest selector in
`plan_selectors.py`, what an approval covers in `plan_approval.py`, the state and its locks in
`plan_locks.py`, and plan discovery in `plan_discovery.py`. The names those modules publish are
re-exported here, so a caller, a test and the CLI keep asking `plan.<name>`.
"""

import ast as ast
import copy
import fcntl as fcntl
import fnmatch as fnmatch
import functools as functools
import hashlib as hashlib
import os as os
import posixpath as posixpath
import re
import shlex as shlex
import shutil as shutil
import subprocess as subprocess
import tempfile as tempfile
from contextlib import ExitStack
from contextlib import contextmanager as contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated as Annotated
from typing import Literal as Literal

import yaml as yaml
from pydantic import AfterValidator as AfterValidator
from pydantic import BaseModel as BaseModel
from pydantic import ConfigDict as ConfigDict
from pydantic import Field as Field
from pydantic import ValidationError
from pydantic import field_validator as field_validator
from pydantic import model_validator as model_validator

# Published, not used here: `dir(plan)` is what callers and tests read, so a module and a helper the
# groups above are built on keeps its name in this module even where only they use it.
from . import approval_recovery as recovery
from . import config as config
from . import ledger as ledgers
from . import runner, runtime_client
from .plan_approval import DEFAULT_STATE as DEFAULT_STATE
from .plan_approval import FOLDER_PAYLOADS as FOLDER_PAYLOADS
from .plan_approval import UNDIGESTED as UNDIGESTED
from .plan_approval import approved_files as approved_files
from .plan_approval import approved_text as approved_text
from .plan_approval import canonical as canonical
from .plan_approval import checked_scenarios as checked_scenarios
from .plan_approval import completed as completed
from .plan_approval import destination as destination
from .plan_approval import digest as digest
from .plan_approval import evidence_key as evidence_key
from .plan_approval import file_path as file_path
from .plan_approval import folder_files as folder_files
from .plan_approval import frozen_problems as frozen_problems
from .plan_approval import git_email as git_email
from .plan_approval import payloads as payloads
from .plan_approval import placed_files as placed_files
from .plan_approval import publish as publish
from .plan_approval import replaced_text as replaced_text
from .plan_approval import runner_inputs as runner_inputs
from .plan_approval import snapshot_files as snapshot_files
from .plan_checks import discard_problems as discard_problems
from .plan_checks import holds_no_test as holds_no_test
from .plan_checks import is_test_path as is_test_path
from .plan_checks import person_only_problems as person_only_problems
from .plan_discovery import DiscoveryError as DiscoveryError
from .plan_discovery import discover as discover
from .plan_discovery import folder_plans as folder_plans
from .plan_discovery import plan_entries as plan_entries
from .plan_discovery import repo_config as repo_config
from .plan_discovery import repository_root as repository_root
from .plan_discovery import slug_of as slug_of
from .plan_document import Amendment as Amendment
from .plan_document import Approval as Approval
from .plan_document import Evidence as Evidence
from .plan_document import Mapping as Mapping
from .plan_document import Plan as Plan
from .plan_document import PlanError as PlanError
from .plan_document import RepoPath as RepoPath
from .plan_document import Scenario as Scenario
from .plan_document import ScenarioId as ScenarioId
from .plan_document import Text as Text
from .plan_document import Ticket as Ticket
from .plan_document import TicketId as TicketId
from .plan_document import UniqueLoader as UniqueLoader
from .plan_document import literal_path as literal_path
from .plan_document import read_yaml as read_yaml
from .plan_document import yaml_path as yaml_path
from .plan_layout import FOLDER_PLAN as FOLDER_PLAN
from .plan_layout import LISTED as LISTED
from .plan_layout import PlanLayout as PlanLayout
from .plan_layout import git as git
from .plan_layout import identity as identity
from .plan_layout import not_under_plans_dir as not_under_plans_dir
from .plan_layout import package_files as package_files
from .plan_layout import repository as repository
from .plan_locks import amend_lock as amend_lock
from .plan_locks import plan_lock as plan_lock
from .plan_locks import run_lock as run_lock
from .plan_locks import state_paths as state_paths
from .plan_selectors import collected_tests as collected_tests
from .plan_selectors import control_test as control_test
from .plan_selectors import named_scenarios as named_scenarios
from .plan_selectors import pytest_selector as pytest_selector
from .plan_selectors import selector_terms as selector_terms

ARCHIVE_MODES = ("promote", "discard")


# A word of a test name that names a scenario, as the plans write them: `test_S1_x` carries S1, `test_S4a_x` S4a.
SCENARIO_WORD = re.compile(r"^S\d+[a-z]?$")


def selector_problems(model, path):
    """The folder plan's checks whose pytest `-k` selector does not select its own scenario's tests, and only them,
    from the acceptance files the evidence names.

    A selector must name the scenario's id; it must select a test other than a control in those files (none means
    the tests are not written yet, and `baseline: fail` passes vacuously); and every test it selects that carries a
    scenario id must carry one the selector names (`S1` also selects `test_S10_x` by substring). Control tests —
    `control` is one of the name's words, as in `test_control_x` and `test_S1_control_x` — are always allowed. A
    legacy plan, a check with no `-k` and a check that runs no pytest are not linted."""
    layout = PlanLayout(path)
    if not layout.is_folder:
        return []
    problems = []
    for i, scenario in enumerate(model.scenarios):
        ev = scenario.evidence
        if ev is None or not ev.check or not ev.acceptance:
            continue
        expression = pytest_selector(ev.check)
        terms = selector_terms(expression) if expression is not None else None
        if terms is None:
            continue
        sid = scenario.id
        where = f"scenarios[{i}].evidence.check"
        anchored = f'-k "test_{sid}_ or test_control_"'
        named = set().union(*(named_scenarios(term) for term in terms))
        if sid not in named:
            why = (
                f"{sid}: the -k selector {expression!r} names no scenario id"
                if not named
                else f"{sid}: the -k selector {expression!r} names {', '.join(sorted(named))} but not {sid}"
            )
            problems.append(
                f"{where}: {why}, so the check does not run {sid}'s tests — select them with {anchored}"
            )
            continue
        tests, unreadable = [], []
        for name in ev.acceptance:
            if not name.endswith(".py"):
                continue
            source = layout.folder / "acceptance" / name
            try:
                tests += collected_tests(source.read_text(encoding="utf-8"), Path(name).name)
            except (OSError, SyntaxError, UnicodeDecodeError):
                unreadable.append(name)
        if unreadable:
            problems.append(
                f"{where}: {sid}: the acceptance file {', '.join(unreadable)} does not parse, so the -k selector "
                f"{expression!r} can select no test of {sid} — fix the file; then select test_{sid}_ tests with {anchored}"
            )
            continue
        if not any(n.endswith(".py") for n in ev.acceptance):
            continue
        lowered = [term.lower() for term in terms]
        selected = [
            function
            for names, function in tests
            if any(term in n.lower() for term in lowered for n in names)
        ]
        scenario_tests = [f for f in selected if not control_test(f)]
        if not scenario_tests:
            problems.append(
                f"{where}: {sid}: the -k selector {expression!r} selects no test in {', '.join(ev.acceptance)} but "
                f"controls, so the check never runs {sid}'s tests and baseline: fail passes vacuously — write the "
                f"test_{sid}_ tests in the acceptance file, and select them with {anchored}"
            )
            continue
        for function in scenario_tests:
            others = sorted({w for w in function.split("_") if SCENARIO_WORD.match(w)} - named)
            if others:
                problems.append(
                    f"{where}: {sid}: the -k selector {expression!r} also selects {function}, which carries "
                    f"{', '.join(others)}, by substring — anchor the selector as {anchored}, or name "
                    f"{', '.join(others)} in it on purpose"
                )
    return problems


def lint_problems(model, repo, path):
    """Problems `plan check`, `plan approve` and `plan amend` refuse; the driver's load never runs this, so a plan
    approved before a rule existed keeps running."""
    patterns = repo_config(repo).test_patterns
    return (
        discard_problems(model, path, repo)
        + person_only_problems(model, repo)
        + selector_problems(model, path)
        + [
            f"tickets[{i}] {ticket.id}: satisfies {', '.join(ticket.satisfies)} but its paths hold no test file "
            "or directory, so the covers gate could never pass — add a test file, or a directory that holds its "
            "tests, to its paths"
            for i, ticket in enumerate(model.tickets)
            if ticket.owner.strip().lower() == "agent" and holds_no_test(ticket.paths, patterns)
        ]
    )


def semantic_problems(data, path, repo):
    """Inspect independent fields even when other fields fail Pydantic validation."""
    problems = []
    if not isinstance(data, dict):
        return problems

    def defect(loc, why, fix):
        problems.append(f"{loc}: {why} — {fix}")

    folder = PlanLayout(path).is_folder
    scenarios = data.get("scenarios")
    tickets = data.get("tickets")
    scenarios = scenarios if isinstance(scenarios, list) else []
    tickets = tickets if isinstance(tickets, list) else []
    ids = {}
    for collection, rows in (("scenarios", scenarios), ("tickets", tickets)):
        seen = set()
        for i, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            sid = row["id"]
            if sid in seen:
                defect(f"{collection}[{i}].id", f"duplicate id {sid}", "use a unique id")
            seen.add(sid)
        ids[collection] = seen
    evidence_ids = {
        s.get("id")
        for s in scenarios
        if isinstance(s, dict)
        and isinstance(s.get("id"), str)
        and isinstance(s.get("evidence"), dict)
        and (s["evidence"].get("check") or s["evidence"].get("human"))
    }
    for i, sid in enumerate(
        data.get("outcome", []) if isinstance(data.get("outcome"), list) else []
    ):
        if not isinstance(sid, str) or sid not in ids["scenarios"]:
            defect(f"outcome[{i}]", f"{sid} is not a scenario", "name a declared scenario id")
        elif sid not in evidence_ids:
            defect(
                f"outcome[{i}]",
                f"{sid} has no evidence",
                "attach check + baseline or human evidence",
            )
    edges = {}
    for i, ticket in enumerate(tickets):
        if not isinstance(ticket, dict):
            continue
        prefix = f"tickets[{i}]"
        for field, target in (("satisfies", "scenarios"), ("blocked_by", "tickets")):
            values = ticket.get(field, [])
            if not isinstance(values, list):
                continue
            for j, value in enumerate(values):
                if not isinstance(value, str) or value not in ids[target]:
                    noun = "ticket" if target == "tickets" else "scenario"
                    defect(
                        f"{prefix}.{field}[{j}]",
                        f"{value} is not a {noun}",
                        f"name an existing {noun} id or remove it",
                    )
        if isinstance(ticket.get("id"), str):
            edges[ticket["id"]] = (
                (i, [b for b in ticket.get("blocked_by", []) if isinstance(b, str)])
                if isinstance(ticket.get("blocked_by", []), list)
                else (i, [])
            )
        if (
            ticket.get("repo") is not None
            and str(ticket.get("owner", "agent")).strip().lower() == "agent"
        ):
            defect(
                prefix + ".repo",
                "repo is only allowed for a non-agent owner",
                "remove repo or assign the ticket to the Lead",
            )
        satisfies = ticket.get("satisfies", [])
        satisfies = satisfies if isinstance(satisfies, list) else []
        if str(ticket.get("owner", "agent")).strip().lower() == "agent":
            for field in ("size", "difficulty"):
                if ticket.get(field) is None:
                    defect(
                        prefix + "." + field,
                        "missing agent route field",
                        "supply size and difficulty",
                    )
        if (
            ticket.get("risk")
            and not ticket.get("evidence_waiver")
            and not any(isinstance(s, str) and s in evidence_ids for s in satisfies)
        ):
            defect(
                prefix + ".risk",
                "risk-tagged ticket has no evidence",
                "satisfy an evidence-bearing scenario or add evidence_waiver",
            )
        for j, value in enumerate(
            ticket.get("paths", []) if isinstance(ticket.get("paths"), list) else []
        ):
            if isinstance(value, str):
                try:
                    literal_path(value)
                except ValueError as error:
                    problems.append(f"{prefix}.paths[{j}]: {error}")
    state = {}

    def visit(sid, stack):
        state[sid] = "open"
        i, blockers = edges[sid]
        for b in blockers:
            if state.get(b) == "open":
                defect(
                    f"tickets[{i}].blocked_by",
                    "dependency cycle: " + " -> ".join(stack + [b]),
                    "remove a dependency to break the cycle",
                )
            elif b in edges and b not in state:
                visit(b, stack + [b])
        state[sid] = "done"

    for sid in edges:
        if sid not in state:
            visit(sid, [sid])
    # D14: a risk-tagged ticket that is not waived must prove its scenarios with a negative control, since a
    # baseline pass alone cannot show that the oracle would catch the behaviour breaking.
    risk_gated = {
        s
        for ticket in tickets
        if isinstance(ticket, dict) and ticket.get("risk") and not ticket.get("evidence_waiver")
        for s in (ticket.get("satisfies") if isinstance(ticket.get("satisfies"), list) else [])
        if isinstance(s, str)
    }
    for i, scenario in enumerate(scenarios):
        ev = scenario.get("evidence") if isinstance(scenario, dict) else None
        if not isinstance(ev, dict):
            continue
        prefix = f"scenarios[{i}].evidence"
        if (
            ev.get("check")
            and ev.get("baseline") == "pass"
            and ev.get("negative") is None
            and scenario.get("id") in risk_gated
        ):
            defect(
                prefix,
                "risk-tagged ticket relies on baseline-pass evidence with no negative control",
                "add a negative patch that breaks the behaviour (set negative to its oracle file), "
                "set baseline: fail, or add evidence_waiver to the ticket",
            )
        if bool(ev.get("human")) == bool(ev.get("check")):
            defect(
                prefix,
                "evidence needs exactly one of check or human",
                "choose human or check + baseline",
            )
        if ev.get("check") and ev.get("baseline") is None:
            defect(prefix + ".baseline", "check needs baseline", "set pass or fail")
        if ev.get("human") and any(
            k in ev
            for k in (
                "check",
                "baseline",
                "oracle",
                "place",
                "acceptance",
                "package",
                "negative",
                "timeout",
                "env",
                "promote",
            )
        ):
            defect(
                prefix,
                "human evidence cannot carry check options",
                "keep only human or use check + baseline",
            )
        declared = ev.get("env", [])
        for name in declared if isinstance(declared, list) else []:
            if isinstance(name, str) and (name in ("HOME", "PATH") or name.startswith("XDG_")):
                defect(
                    prefix + ".env",
                    f"{name} is reserved: the runner owns the check's HOME, PATH and XDG_* variables",
                    "drop the name; the runner supplies a scratch value",
                )
        if folder:
            continue  # a folder plan's evidence is read by folder_problems
        names = ev.get("oracle", [])
        names = names if isinstance(names, list) else []
        if names and not ev.get("place"):
            defect(
                prefix + ".place",
                "oracle needs a placement directory",
                "set a directory inside the repo",
            )
        place = ev.get("place")
        destination = None
        if isinstance(place, str):
            try:
                literal_path(place)
                destination = (repo / place).resolve()
                destination.relative_to(repo)
            except ValueError:
                defect(
                    prefix + ".place",
                    "oracle place escapes the repo",
                    "use a directory inside the repo without ..",
                )
                destination = None
        for label, name in [(f"oracle[{j}]", n) for j, n in enumerate(names)] + (
            [("negative", ev["negative"])] if ev.get("negative") is not None else []
        ):
            if not isinstance(name, str):
                continue
            source = PlanLayout(path).oracle_dir / name
            try:
                literal_path(name)
                source.resolve().relative_to(PlanLayout(path).oracle_dir.resolve())
                exists = source.is_file()
            except ValueError:
                exists = False
            if not exists:
                defect(
                    prefix + "." + label,
                    f"oracle file {name} missing or outside oracle dir",
                    "add the file to the oracle directory or remove the reference",
                )
            if label.startswith("oracle") and destination is not None:
                placed = destination / name.removesuffix(".oracle")
                # archive promotes the oracle onto its place byte for byte: that copy is the plan's own, not a clash
                promoted = (
                    exists
                    and placed.is_file()
                    and not placed.is_symlink()
                    and placed.read_bytes() == source.read_bytes()
                )
                if not placed.resolve().is_relative_to(repo) or (
                    (placed.exists() or placed.is_symlink()) and not promoted
                ):
                    defect(
                        prefix + ".place",
                        f"placed oracle {placed} already exists or escapes repo",
                        "choose an unused location inside the repo",
                    )
    if folder:
        problems += folder_problems(data, path, repo, scenarios)
    else:
        problems += legacy_problems(data, scenarios)
    return problems


# What a folder plan says in place of each legacy evidence key.
LEGACY_KEYS = {
    "oracle": "acceptance (files in the plan folder's acceptance/)",
    "place": "package (the destination follows from package, acceptance.dir and domain)",
    "promote": "the top-level archive: promote | discard",
}


def legacy_problems(data, scenarios):
    """A legacy plan keeps its keys: the folder plan's are refused, each naming the legacy key to use."""
    problems = []
    for key in ("domain", "archive"):
        if key in data:
            problems.append(
                f"{key}: only a plan in its own folder (<folder>/plan.yaml) has this key — remove it, "
                "or move the plan into a folder"
            )
    if data.get("spec") is None:
        problems.append("spec: Field required — supply the required field")
    for i, scenario in enumerate(scenarios):
        ev = scenario.get("evidence") if isinstance(scenario, dict) else None
        for key, legacy in (("acceptance", "oracle"), ("package", "place")):
            if isinstance(ev, dict) and key in ev:
                problems.append(
                    f"scenarios[{i}].evidence.{key}: only a plan in its own folder has this key — "
                    f"use {legacy} (the legacy plan's {legacy} key), or move the plan into a folder"
                )
    return problems


def folder_problems(data, path, repo, scenarios):
    """The rules of a plan in its own folder: the folder keys, their files, and what a promote may not clobber."""
    problems = []

    def defect(loc, why, fix):
        problems.append(f"{loc}: {why} — {fix}")

    cfg = repo_config(repo)
    folder = Path(path).resolve().parent
    mode = data.get("archive") if data.get("archive") in ARCHIVE_MODES else cfg.archive_mode
    promote = mode == "promote"
    domain = data.get("domain")
    if promote and domain is None:
        defect(
            "domain",
            "a plan that promotes needs a domain",
            "set domain: <slug> (the folder under <package>/%s/ the tests join), or set archive: discard"
            % cfg.acceptance_dir,
        )
    if promote and data.get("spec") is None:
        defect(
            "spec",
            "a plan that promotes needs the living spec the archive writes",
            "set spec: <repo-relative path>, or set archive: discard",
        )
    skip = {folder, cfg.plans_dir.resolve()}
    indexes = {}
    for i, scenario in enumerate(scenarios):
        ev = scenario.get("evidence") if isinstance(scenario, dict) else None
        if not isinstance(ev, dict):
            continue
        prefix = f"scenarios[{i}].evidence"
        for key, new in LEGACY_KEYS.items():
            if key in ev:
                defect(f"{prefix}.{key}", "a plan in its own folder has no such key", f"use {new}")
        names = ev.get("acceptance", [])
        names = names if isinstance(names, list) else []
        package = ev.get("package")
        package_dir = None
        if isinstance(package, str):
            try:
                literal_path(package)
                package_dir = (repo / package).resolve()
                package_dir.relative_to(repo)
            except ValueError:
                defect(
                    f"{prefix}.package",
                    f"package {package} is outside the repository",
                    "name a directory inside the repository, or . for its root",
                )
                package_dir = None
        elif names:
            defect(
                f"{prefix}.package",
                "acceptance files need a package to be placed in",
                "set package: <directory inside the repository> or .",
            )
        for j, name in enumerate(names):
            if not isinstance(name, str):
                continue
            where = f"{prefix}.acceptance[{j}]"
            try:
                literal_path(name)
                source = (folder / "acceptance" / name).resolve()
                source.relative_to((folder / "acceptance").resolve())
                exists = source.is_file()
            except ValueError:
                exists = False
            if not exists:
                defect(
                    where,
                    f"acceptance file {folder / 'acceptance' / name} is missing or outside the plan's acceptance/",
                    "add the file to the plan's acceptance/ or remove the reference",
                )
                continue
            if not promote or package_dir is None or not isinstance(domain, str):
                continue
            if package_dir not in indexes:
                indexes[package_dir] = package_files(package_dir, skip)
            clash = indexes[package_dir].get(Path(name).name)
            if clash is not None:
                defect(
                    where,
                    f"the basename {Path(name).name} is held by the copy {clash.relative_to(repo)} in the "
                    "package, and pytest imports modules by basename",
                    "rename the acceptance file",
                )
            placed = package_dir / cfg.acceptance_dir / domain / name
            if placed.exists() or placed.is_symlink() or not placed.resolve().is_relative_to(repo):
                defect(
                    where,
                    f"the destination {placed} already exists or is outside the repository",
                    "choose another domain or rename the file",
                )
        negative = ev.get("negative")
        if isinstance(negative, str):
            try:
                literal_path(negative)
                source = (folder / negative).resolve()
                source.relative_to(folder)
                exists = source.is_file()
            except ValueError:
                exists = False
            if not exists:
                defect(
                    f"{prefix}.negative",
                    f"negative file {folder / negative} is missing or outside the plan folder",
                    "add the patch to the plan folder (negatives/) or remove the reference",
                )
    return problems


def load(
    path, *, repo=None, state_dir=DEFAULT_STATE, require_approval=False, verify=True, lint=None
):
    """`lint` runs the refusals that only authoring commands make. It defaults to on for `plan check` (which
    verifies without requiring an approval) and off for the driver's load, which requires one."""
    lint = verify and not require_approval if lint is None else lint
    path = Path(path).resolve()
    data, problems = read_yaml(path)
    model = None
    try:
        model = Plan.model_validate(data)
    except ValidationError as error:
        for item in error.errors(include_input=False, include_url=False):
            fix = (
                "remove the unknown key"
                if item["type"] == "extra_forbidden"
                else "supply the required field"
                if item["type"] == "missing"
                else "use the declared plan/1 type or value"
            )
            problems.append(f"{yaml_path(item['loc'])}: {item['msg']} — {fix}")
    repo = repository(path, repo)
    problems += semantic_problems(data, path, repo)
    if problems:
        if isinstance(data, dict) and data.get("approval") and verify:
            problems.append(
                f"approval: after fixing {path}, run agent-orchestrator plan approve {path}"
            )
        raise PlanError("; ".join(problems))
    assert model is not None
    if lint and (found := lint_problems(model, repo, path)):
        raise PlanError("; ".join(found))
    if model.approval and verify:
        return recovery.verify_snapshot(path, model, repo, state_dir)
    if require_approval and model.approval is None:
        raise PlanError(
            f"approval: {path} is unapproved — run agent-orchestrator plan approve {path}"
        )
    return model


def baseline(path, model, repo, base_sha, approval_digest, files, carried=None):
    """Run every check item of the plan once at `base` and judge it against its `baseline:` (D24).

    Scenarios whose items would run the same thing share one run, and every run shares one worktree, so a plan pays
    for each distinct check and one venv, not for each scenario. `carried` maps a scenario ID to the Run of its
    unchanged item, re-issued from an earlier approval: it is judged like a fresh run and not run again. Returns
    the runs to persist, each with the calibration to record; raises PlanError naming every item that is not what
    it claims, before anything is written."""
    runs, refusals, seen, carried = [], [], {}, carried or {}
    checked = checked_scenarios(model)
    if not checked:
        return runs
    with ExitStack() as stack:
        shared = None
        for scenario in checked:
            item = scenario.evidence
            oracles, place, negative = runner_inputs(path, model, item, files, repo)
            key = evidence_key(path, model, item, files, repo)
            if scenario.id in carried:
                run = seen[key] = carried[scenario.id]
            elif key in seen:
                first = seen[key]
                run = runner.Run(copy.deepcopy(first.receipt), first.log)
                run.receipt["scenario"] = scenario.id
            else:
                if shared is None:
                    shared = stack.enter_context(runner.Workspace(repo, base_sha))
                run = seen[key] = runner.run_evidence(
                    repo,
                    base_sha,
                    scenario=scenario.id,
                    phase="baseline",
                    digest=approval_digest,
                    check=item.check,
                    oracles=oracles,
                    place=place,
                    env_names=item.env,
                    timeout=item.timeout,
                    negative=negative,
                    workspace=shared,
                )
            ok, observed, calibrated = runner.judge_baseline(
                run.receipt, item.baseline, item.has_tests
            )
            if not ok:
                refusals.append(
                    "%s: baseline expected %s at %s, observed %s — %s"
                    % (
                        scenario.id,
                        item.baseline,
                        base_sha[:12],
                        observed,
                        "make the oracle fail on an assertion at base and pass its controls, or set baseline: pass"
                        if item.baseline == "fail"
                        else "fix the check or set baseline: fail",
                    )
                )
            runs.append((run, {"calibrated": calibrated, "expected": item.baseline}))
    if refusals:
        raise PlanError("approval: " + "; ".join(refusals) + " — nothing was approved")
    return runs


def carried_baselines(path, repo, old, old_files, model, files, previous, sha, state_dir):
    """The baseline Runs `plan amend` re-issues instead of running: each check item whose evidence is the same in the
    approved snapshot `old` and in the edit, and whose receipt for the previous digest still passes the receipt
    ledger's tamper check, under the new digest `sha`. A changed or new item, or one whose receipt cannot be vouched
    for, is not here and runs."""
    from . import (
        verify,
    )  # verify reads plans, so it imports this module: the import waits until it is called

    ledger, receipts = state_paths(repo, model.slug, state_dir)
    found = ledgers.snapshot(ledger)  # a read: it never waits for the driver
    if not found:
        return {}
    try:
        was = Plan.model_validate(old)
    except ValidationError:
        return {}
    before = {s.id: s.evidence for s in checked_scenarios(was)}
    tree = git(repo, "rev-parse", previous.base + "^{tree}")
    carried = {}
    for scenario in checked_scenarios(model):
        item = scenario.evidence
        if scenario.id not in before:
            continue
        if evidence_key(path, was, before[scenario.id], old_files, repo) != evidence_key(
            path, model, item, files, repo
        ):
            continue
        kept = verify.carryable_baseline(found, scenario.id, receipts, previous.digest, tree)
        if kept is not None:
            carried[scenario.id] = runner.reissue(
                *kept, scenario=scenario.id, digest=sha, carried_from=previous.digest
            )
    return carried


def approve(path, *, repo=None, state_dir=DEFAULT_STATE, base="origin/main"):
    """Approve a plan: run its baseline, then write the receipts, their ledger hashes, the snapshot and the block.

    Everything runs under the plan's run lock, so a live driver is never moved or raced; a held lock refuses before
    anything is written."""
    refusal = runtime_client.approval_refusal()
    if refusal:
        raise PlanError("approval: %s — the plan was not approved" % refusal)
    path = Path(path).resolve()
    repo = repository(path, repo)
    model = load(path, repo=repo, verify=False, lint=True)
    with run_lock(repo, model.slug):
        try:
            return approve_locked(path, repo, model, state_dir, base)
        except runner.RunnerError as error:
            raise PlanError(
                "approval: %s — the plan was not approved; fix that and run plan approve again"
                % error
            ) from None


def approve_locked(path, repo, model, state_dir, base):
    data, _ = read_yaml(path)
    plan_bytes = canonical(data)
    files = approved_files(path, data)
    base_sha = git(repo, "rev-parse", "--verify", base + "^{commit}")
    sha = digest(identity(repo), base_sha, plan_bytes, files)
    # A refusal here leaves no file behind: the runner writes nothing until the runs are accepted.
    runs = baseline(path, model, repo, base_sha, sha, files)
    if runs:
        ledger, receipts = state_paths(repo, model.slug, state_dir)
        for run, calibration in runs:
            runner.persist(run, receipts, calibration)
        runner.record_receipts(ledger, [run for run, _ in runs])
    approval = {
        "by": git_email(repo),
        "at": datetime.now(timezone.utc).isoformat(),
        "base": base_sha,
        "digest": sha,
    }
    # Only the top-level approval span is replaced; the author's comments survive.
    publish(
        path,
        approved_text(path.read_bytes().decode("utf-8"), approval),
        plan_bytes,
        files,
        sha,
        state_dir,
    )
    return approval


def amend(path, *, reason, repo=None, state_dir=DEFAULT_STATE, runtime_id=None):
    """Re-approve an approved plan after an edit, at the base its approval already has.

    The baselines run at that base, not at `origin/main`: a ticket that merged since may have turned a
    `baseline: pass` check red, and the plan's evidence is still judged against the tree it was approved on. The
    amendment holds its own lock, never the run lock, so a live driver is not stopped; a ticket the ledger marks
    COMPLETE is frozen, and nothing is written when the amendment is refused."""
    refusal = runtime_client.approval_refusal(verb="amend")
    if refusal:
        raise PlanError("amend: %s — the plan was not amended" % refusal)
    path = Path(path).resolve()
    repo = repository(path, repo)
    model = load(path, repo=repo, verify=False, lint=False)
    if model.approval is None:
        # A hand edit that dropped the `approval` span leaves a plan the ledger still holds the approval of.
        model = recovery.ledger_approved(model, repo, state_dir)
    if model is None:
        raise PlanError(
            f"approval: {path} is unapproved, so there is nothing to amend — run agent-orchestrator plan approve {path}"
        )
    if not reason.strip():
        raise PlanError("amend: --reason is empty — say why the plan changes")
    if found := lint_problems(model, repo, path):
        raise PlanError("; ".join(found))
    with amend_lock(repo, model.slug):
        try:
            return amend_locked(path, repo, model, state_dir, reason, runtime_id)
        except runner.RunnerError as error:
            raise PlanError(
                "approval: %s — the plan was not amended; fix that and run plan amend again" % error
            ) from None


def amend_locked(path, repo, model, state_dir, reason, runtime_id):
    previous = model.approval
    way_out = f"run agent-orchestrator plan approve {path}"
    snap = recovery.snapshot_path(previous, state_dir)
    if not snap.is_file():
        raise PlanError(f"approval: snapshot of the approved plan missing at {snap} — {way_out}")
    old, errors = read_yaml(snap)
    old_bytes, old_files = snap.read_bytes(), snapshot_files(snap.parent, path)
    if (
        errors
        or not isinstance(old, dict)
        or digest(identity(repo), previous.base, old_bytes, old_files) != previous.digest
    ):
        raise PlanError(
            f"approval: the approval block of {path} does not match its snapshot {snap} — {way_out}"
        )
    data, _ = read_yaml(path)
    problems = frozen_problems(old, data, completed(repo, model.slug, state_dir))
    if problems:
        raise PlanError(
            "amend: "
            + "; ".join(problems)
            + " — a finished ticket is frozen: restore it and add a new ticket for the change"
        )
    plan_bytes = canonical(data)
    files = approved_files(path, data)
    base_sha = git(repo, "rev-parse", "--verify", previous.base + "^{commit}")
    sha = digest(identity(repo), base_sha, plan_bytes, files)
    if sha == previous.digest and "approval" in (data or {}):
        raise PlanError(
            f"amend: {path} holds the approved contract, nothing to amend — edit the plan, then run plan amend again"
        )
    carried = carried_baselines(path, repo, old, old_files, model, files, previous, sha, state_dir)
    runs = baseline(path, model, repo, base_sha, sha, files, carried)
    if runs:
        ledger, receipts = state_paths(repo, model.slug, state_dir)
        for run, calibration in runs:
            runner.persist(run, receipts, calibration)
        runner.record_receipts(ledger, [run for run, _ in runs])
    email, at = git_email(repo), datetime.now(timezone.utc).isoformat()
    approval = {"by": email, "at": at, "base": base_sha, "digest": sha}
    was = {t["id"]: t for t in old.get("tickets", [])}
    entry = {
        "prev_digest": previous.digest,
        "digest": sha,
        "at": at,
        "by": f"{email} ({runtime_id})" if runtime_id else email,
        "reason": reason.strip(),
        "tickets": [t["id"] for t in data.get("tickets", []) if was.get(t["id"]) != t],
    }
    text = replaced_text(
        path.read_bytes().decode("utf-8"),
        "amendments",
        list(data.get("amendments") or []) + [entry],
    )
    publish(path, replaced_text(text, "approval", approval), plan_bytes, files, sha, state_dir)
    return {"approval": approval, "amendment": entry}
