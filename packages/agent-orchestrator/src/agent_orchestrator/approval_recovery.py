"""The snapshot that proves a plan is still the approved one, and the approval a lost span is recovered with.

A hand edit can drop the `approval` span and leave the contract alone. The snapshot the approval's digest names
proves whether it did — the plan's bytes and its files, exactly — and the ledger keeps the approval the drive held,
which is the record a restart survives. So a span-less plan whose approval is still provable is read with it
restored, and one nobody holds a record of is still refused as unapproved.

It lives here rather than beside the plan or the driver because both are over their line ceilings at the base
revision and may not grow, while the recovery needs the plan's own proof and the ledger's record together.
"""

from datetime import datetime, timezone
from pathlib import Path

from . import ledger as ledgers
from . import narrate
from . import plan as yaml_plan


def snapshot_path(approval, state_dir):
    # Filenames are not digest inputs: renaming a contract must reuse its snapshot.
    return Path(state_dir).expanduser() / "approvals" / approval.digest / "plan.yaml"


def snapshot_dir(approval, state_dir):
    """Where the approval snapshot lives: the run's state dir first, else the default one `plan approve` wrote to.

    A snapshot is addressed by its digest and verified byte for byte, so its location carries no trust."""
    here = snapshot_path(approval, state_dir).parent
    if (here / "plan.yaml").is_file():
        return here
    return snapshot_path(approval, yaml_plan.DEFAULT_STATE).parent


def verify_snapshot(path, model, repo, state_dir):
    """The plan `model` proved against the snapshot its approval's digest names, byte for byte: the snapshot's own
    model, or a refusal naming `plan approve`. An approval with no snapshot, or an edit since it, is not a plan."""
    way_out = f"run agent-orchestrator plan approve {path}"
    snap = snapshot_path(model.approval, state_dir)
    if not snap.is_file():
        raise yaml_plan.PlanError(f"approval: snapshot missing at {snap} — {way_out}")
    snap_data, errors = yaml_plan.read_yaml(snap)
    if errors or not isinstance(snap_data, dict):
        raise yaml_plan.PlanError(f"approval: invalid snapshot {snap} — {way_out}")
    old_plan = snap.read_bytes()
    old_files = yaml_plan.snapshot_files(snap.parent, path)
    live_data, _ = yaml_plan.read_yaml(path)
    live_files = yaml_plan.approved_files(path, live_data)
    changed = []
    if yaml_plan.canonical(live_data) != old_plan:
        changed.append(str(path))
    changed += [
        str(yaml_plan.file_path(path, n))
        for n in sorted(old_files.keys() | live_files.keys())
        if old_files.get(n) != live_files.get(n)
    ]
    held = yaml_plan.digest(yaml_plan.identity(repo), model.approval.base, old_plan, old_files)
    if held != model.approval.digest:
        changed.append(f"repo identity/base or snapshot {snap.parent}")
    if changed:
        raise yaml_plan.PlanError("approval: changed " + ", ".join(changed) + " — " + way_out)
    # The driver consumes the snapshot, after checking the authored file against it.
    return yaml_plan.Plan.model_validate(snap_data).model_copy(update={"approval": model.approval})


def approval_block(record):
    """The `Approval` a record names, or None: the drive's own block, or the ledger's record of it (its `base` and
    its `digest`). The ledger keeps neither who approved the plan nor when, so a record that does not name them is
    recovered as `unknown`, at the moment it is recovered."""
    if isinstance(record, yaml_plan.Approval):
        return record
    if not isinstance(record, dict) or not record.get("base") or not record.get("digest"):
        return None
    return yaml_plan.Approval(
        by=record.get("by") or "unknown",
        at=record.get("at") or datetime.now(timezone.utc).isoformat(),
        base=record["base"],
        digest=record["digest"],
    )


def recorded_approval(repo, slug, state_dir):
    """The approval a plan's ledger keeps (its `base` and `digest`), or None: the record that outlives the plan
    file, which is what loses the span. A read: a command that writes nothing creates nothing, so no ledger is made
    here."""
    # The state key is the driver's, and the driver imports this module.
    from . import driver

    state = Path(state_dir).expanduser()
    for path in (state / driver.repo_key(repo) / (slug + ".json"), state / (slug + ".json")):
        approval = (ledgers.snapshot(path) or {}).get("approval")
        if isinstance(approval, dict) and approval:
            return approval
    return None


def ledger_approval(drive):
    """The approval the drive's ledger keeps for the plan: its base and its digest, the record that outlives a
    restart. The plan file is what loses the span, so a drive that starts again reads the block from here."""
    path = getattr(drive, "ledger_path", None)
    if path is None:  # the first read, before the run has located its ledger
        # The state layout is the driver's rule, and the driver imports this module.
        from . import driver

        path = driver.state_layout(
            drive.args.state_dir, drive.plan_path, drive.repo, yaml_plan.slug_of(drive.plan_path)
        )[0]
    return (ledgers.snapshot(path) or {}).get("approval") or {}


def recover_approval(path, record, *, repo=None, state_dir=None):
    """The plan at `path` read with the approval `record` names restored onto it, or None when the file is not that
    approval's plan.

    A hand edit can drop the `approval` span and leave the contract alone. The snapshot the digest names proves
    whether it did — the plan's bytes and its files, exactly — so a caller repairs the span instead of treating the
    plan as edited. A plan whose contract moved, or that does not load, is the edit it looks like and stays one."""
    restored = approval_block(record)
    if restored is None:
        return None
    state_dir = state_dir or yaml_plan.DEFAULT_STATE
    path = Path(path).resolve()
    repo = yaml_plan.repository(path, repo)
    try:
        model = yaml_plan.load(path, repo=repo, state_dir=state_dir, verify=False, lint=False)
        if model.approval is not None:
            return None  # the file carries the block it was last given: nothing was lost
        return verify_snapshot(
            path, model.model_copy(update={"approval": restored}), repo, state_dir
        )
    except yaml_plan.PlanError:
        return None


def ledger_approved(model, repo, state_dir):
    """`model` with the approval its ledger keeps, or None when nothing holds a record of that approval.

    A hand edit can drop the `approval` span while the plan stays the one a drive approved: `plan amend` amends it
    at that approval's own base and publishes the spans back, so the amendment that puts the span back is not
    refused as an unapproved plan."""
    approval = approval_block(recorded_approval(repo, model.slug, state_dir))
    if approval is None:
        return None
    return model.model_copy(update={"approval": approval})


class Recovered:
    """The approval a drive restored onto a span-less plan, and whether the run has said so.

    The drive's first read happens before its run directory exists, so the line waits for the log: one line per
    run, whichever read recovers the span."""

    def __init__(self):
        self.approval = None
        self.said = False


def recover_plan(drive):
    """The plan the drive holds, read with the approval its file lost: None when the file is anything else (an edit,
    or a plan that does not load), which is the drain it already is.

    The approval is the drive's own block, or the ledger's record when the drive starts again, and the snapshot of
    its digest proves the file is still the approved plan: the drive repairs the span and keeps running on a plan it
    can read, instead of draining on one it cannot."""
    model = recover_approval(
        drive.plan_path,
        drive.approval or ledger_approval(drive),
        repo=drive.repo,
        state_dir=drive.args.state_dir,
    )
    if model is None or model.approval is None:
        return None
    # The run says so once: its log opens after the first read.
    drive.recovered.approval = model.approval
    say_restored(drive)
    return model


def say_restored(drive):
    """Tell the Lead the plan's lost approval span is back: one line per run, in the log and in the pane."""
    recovered = drive.recovered
    if recovered.approval is None or recovered.said or getattr(drive, "run_dir", None) is None:
        return
    recovered.said = True
    drive.log("approval-recovered", base=recovered.approval.base, digest=recovered.approval.digest)
    narrate.say(
        "info", None, "the plan lost its approval span: the drive restored the approval it holds"
    )


def load_approved(path, *, repo, state_dir, recover=False, **kwargs):
    """yaml_plan.load that finds a snapshot approved under the default state dir when the run uses another. With
    `recover`, a plan whose `approval` span was lost is read with the approval the ledger keeps, when its bytes and
    its files are the ones that approval's snapshot holds."""
    try:
        return yaml_plan.load(path, repo=repo, state_dir=state_dir, **kwargs)
    except yaml_plan.PlanError as error:
        default = Path(yaml_plan.DEFAULT_STATE).expanduser()
        if "snapshot missing" in str(error) and Path(state_dir).expanduser() != default:
            return load_approved(path, repo=repo, state_dir=default, recover=recover, **kwargs)
        found = (
            recover_approval(
                path,
                recorded_approval(repo, yaml_plan.slug_of(path), state_dir),
                repo=repo,
                state_dir=state_dir,
            )
            if recover
            else None
        )
        if found is None:
            raise
        return found
