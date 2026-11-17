"""The plan's acceptance: a plan is ACCEPTED once every agent ticket is COMPLETE.

A ticket reaches COMPLETE only through its gate (scope, integrity, covers, red) and its pull request merged
with CI green on its head, and CI runs again on every merge. So nothing is re-run once the tickets are in:
the plan is accepted on its tickets alone, and the archive starts from there.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines and no class twenty-five public methods.
"""

from pathlib import Path
from typing import Any

from .. import narrate
from .records import is_agent, is_done
from .routing import load_approved, repo_root, require_yaml
from .state import enter_plan_state, locate_state, read_ledger_file
from .watch import ACCEPTED_STATES


def acceptance_context(args):
    """(plan model, repo, ledger path, run directory, ledger data) of the YAML plan a Lead command names. The model is
    the approved one: a plan edited since approval is refused here, as everywhere."""
    plan_path = Path(args.plan).resolve(strict=True)
    require_yaml(plan_path)
    repo = repo_root(plan_path, args.repo)
    model = load_approved(
        plan_path, repo=repo, state_dir=args.state_dir, require_approval=True, recover=True
    )
    ledger_path, run_dir = locate_state(args.state_dir, plan_path, repo, model.slug)
    data = read_ledger_file(ledger_path) if ledger_path.is_file() else {}
    return model, repo, ledger_path, run_dir, data


class Acceptance:
    """The plan's acceptance, mixed into the Driver."""

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def resume_plan_state(self, data):
        """A plan state a drive that ended (or died) mid-way left behind, made right for this drive: a plan with an
        agent ticket still to do is RUNNING, whatever an earlier drive or engine wrote."""
        model, _ = self.contract()
        statuses = data.get("ticket_status") or {}
        pending = [
            t
            for t in model.tickets
            if t.owner.strip().lower() == "agent"
            and not str(statuses.get(t.id, "")).strip().upper().startswith("COMPLETE")
        ]
        if data["plan"]["state"] not in ("APPROVED", "RUNNING", "ARCHIVED") and (
            pending or data["plan"]["state"] not in ACCEPTED_STATES
        ):
            enter_plan_state(data, "RUNNING")

    def accept(self, plan):
        """A running plan whose every agent ticket is COMPLETE is ACCEPTED. Steps taken: 1 when it was, else 0."""
        agents = [t for t in plan["tickets"] if is_agent(t)]
        if not agents or not all(is_done(t) for t in agents):
            return 0
        with self.book() as data:
            if (data.get("plan") or {}).get("state") != "RUNNING":
                return 0
            enter_plan_state(data, "ACCEPTED")
        self.log("acceptance", state="ACCEPTED")
        narrate.say("done", "verify", "%s accepted: every agent ticket is complete" % self.slug)
        return 1
