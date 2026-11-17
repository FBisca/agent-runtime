"""The plan/1 contract and its local approval snapshots.

Digest inputs use eight-byte big-endian length prefixes. The plan input is
safe_dump(sort_keys=True) after dropping approval: comments, key order and YAML
presentation are preserved on disk but do not affect the contract digest.

This module holds the rules the contract itself makes - the semantic validator, the legacy and folder
rules, the approval and its amendment - and every other collaborator lives in the module beside it:
the YAML document in `plan_document.py`, where a plan's files live in `plan_layout.py`, the refusals
over a plan's paths in `plan_checks.py`, what an approval covers in `plan_approval.py`, the state and
its locks in `plan_locks.py`, and plan discovery in `plan_discovery.py`. A plan declares scenarios and
tickets; the implementation provides its evidence, which `verify` holds it to. The names those modules publish are
re-exported here, so a caller, a test and the CLI keep asking `plan.<name>`.
"""

import ast as ast
import fcntl as fcntl
import fnmatch as fnmatch
import functools as functools
import hashlib as hashlib
import os as os
import posixpath as posixpath
import shlex as shlex
import shutil as shutil
import subprocess as subprocess
import tempfile as tempfile
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
from . import runtime_client
from .plan_approval import DEFAULT_STATE as DEFAULT_STATE
from .plan_approval import UNDIGESTED as UNDIGESTED
from .plan_approval import approved_text as approved_text
from .plan_approval import canonical as canonical
from .plan_approval import completed as completed
from .plan_approval import digest as digest
from .plan_approval import frozen_problems as frozen_problems
from .plan_approval import git_email as git_email
from .plan_approval import publish as publish
from .plan_approval import replaced_text as replaced_text
from .plan_checks import discard_problems as discard_problems
from .plan_checks import holds_no_test as holds_no_test
from .plan_checks import is_test_path as is_test_path
from .plan_discovery import DiscoveryError as DiscoveryError
from .plan_discovery import discover as discover
from .plan_discovery import folder_plans as folder_plans
from .plan_discovery import plan_entries as plan_entries
from .plan_discovery import repo_config as repo_config
from .plan_discovery import repository_root as repository_root
from .plan_discovery import slug_of as slug_of
from .plan_document import Amendment as Amendment
from .plan_document import Approval as Approval
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
from .plan_layout import PlanLayout as PlanLayout
from .plan_layout import git as git
from .plan_layout import identity as identity
from .plan_layout import not_under_plans_dir as not_under_plans_dir
from .plan_layout import repository as repository
from .plan_locks import amend_lock as amend_lock
from .plan_locks import ledger_path as ledger_path
from .plan_locks import plan_lock as plan_lock
from .plan_locks import run_lock as run_lock

ARCHIVE_MODES = ("promote", "discard")


def lint_problems(model, repo, path):
    """Problems `plan check`, `plan approve` and `plan amend` refuse; the driver's load never runs this, so a plan
    approved before a rule existed keeps running."""
    patterns = repo_config(repo).test_patterns
    return discard_problems(model, path, repo) + [
        f"tickets[{i}] {ticket.id}: satisfies {', '.join(ticket.satisfies)} but its paths hold no test file "
        "or directory, so the covers gate could never pass — add a test file, or a directory that holds its "
        "tests, to its paths"
        for i, ticket in enumerate(model.tickets)
        if ticket.owner.strip().lower() == "agent" and holds_no_test(ticket.paths, patterns)
    ]


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
        if str(ticket.get("owner", "agent")).strip().lower() == "agent":
            for field in ("size", "difficulty"):
                if ticket.get(field) is None:
                    defect(
                        prefix + "." + field,
                        "missing agent route field",
                        "supply size and difficulty",
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
    for i, scenario in enumerate(scenarios):
        if isinstance(scenario, dict) and "evidence" in scenario:
            defect(
                f"scenarios[{i}].evidence",
                "scenario evidence is gone; the ticket's tests are its evidence",
                "delete the block",
            )
    if folder:
        problems += folder_problems(data, path, repo)
    else:
        problems += legacy_problems(data)
    return problems


def legacy_problems(data):
    """A legacy plan keeps its keys: the folder plan's are refused, naming the way out."""
    problems = []
    if "archive" in data:
        problems.append(
            "archive: only a plan in its own folder (<folder>/plan.yaml) has this key — remove it, "
            "or move the plan into a folder"
        )
    if data.get("spec") is None:
        problems.append("spec: Field required — supply the required field")
    return problems


def folder_problems(data, path, repo):
    """The rules of a plan in its own folder: a plan that promotes writes a living spec."""
    cfg = repo_config(repo)
    mode = data.get("archive") if data.get("archive") in ARCHIVE_MODES else cfg.archive_mode
    if mode == "promote" and data.get("spec") is None:
        return [
            "spec: a plan that promotes needs the living spec the archive writes — set spec: "
            "<repo-relative path>, or set archive: discard"
        ]
    return []


def is_evidence_key(loc):
    """Whether a validation location is a scenario's `evidence` key."""
    return len(loc) == 3 and loc[0] == "scenarios" and loc[2] == "evidence"


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
            if item["type"] == "extra_forbidden" and is_evidence_key(item["loc"]):
                continue  # semantic_problems refuses it with its own reason and way out
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


def approve(path, *, repo=None, state_dir=DEFAULT_STATE, base="origin/main"):
    """Approve a plan: write its snapshot and its approval block. Nothing runs: the tickets' tests are its evidence.

    Everything runs under the plan's run lock, so a live driver is never moved or raced; a held lock refuses before
    anything is written."""
    refusal = runtime_client.approval_refusal()
    if refusal:
        raise PlanError("approval: %s — the plan was not approved" % refusal)
    path = Path(path).resolve()
    repo = repository(path, repo)
    model = load(path, repo=repo, verify=False, lint=True)
    with run_lock(repo, model.slug):
        return approve_locked(path, repo, model, state_dir, base)


def approve_locked(path, repo, model, state_dir, base):
    data, _ = read_yaml(path)
    plan_bytes = canonical(data)
    base_sha = git(repo, "rev-parse", "--verify", base + "^{commit}")
    sha = digest(identity(repo), base_sha, plan_bytes)
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
        sha,
        state_dir,
    )
    return approval


def amend(path, *, reason, repo=None, state_dir=DEFAULT_STATE, runtime_id=None):
    """Re-approve an approved plan after an edit, at the base its approval already has.

    The amendment holds its own lock, never the run lock, so a live driver is not stopped; a ticket the ledger marks
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
        return amend_locked(path, repo, model, state_dir, reason, runtime_id)


def amend_locked(path, repo, model, state_dir, reason, runtime_id):
    previous = model.approval
    way_out = f"run agent-orchestrator plan approve {path}"
    snap = recovery.snapshot_path(previous, state_dir)
    if not snap.is_file():
        raise PlanError(f"approval: snapshot of the approved plan missing at {snap} — {way_out}")
    old, errors = read_yaml(snap)
    old_bytes = snap.read_bytes()
    if (
        errors
        or not isinstance(old, dict)
        or digest(identity(repo), previous.base, old_bytes) != previous.digest
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
    base_sha = git(repo, "rev-parse", "--verify", previous.base + "^{commit}")
    sha = digest(identity(repo), base_sha, plan_bytes)
    if sha == previous.digest and "approval" in (data or {}):
        raise PlanError(
            f"amend: {path} holds the approved contract, nothing to amend — edit the plan, then run plan amend again"
        )
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
    publish(path, replaced_text(text, "approval", approval), plan_bytes, sha, state_dir)
    return {"approval": approval, "amendment": entry}
