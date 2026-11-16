"""The evidence a ticket is accepted on: the gate run, the integration run and the mutation check.

A ticket's scenarios each name a check, and the evidence for one is the check run on the committed
tree, not the word of the worker who wrote it. The outcome is run again on the base a plan is promoted
from, and the mutation check asks whether the evidence can tell a broken tree from a working one. This
module keeps those runs, their receipts and the items a failing one turns into.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines and no class twenty-five public methods.
"""

import copy
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .. import approval_recovery, narrate, runner, verify
from .. import config as repo_config
from .. import plan as yaml_plan
from ..timeutil import iso as now
from .answers import IntentUnsettled, failure_line
from .attention import ACCEPTED_STATES
from .book import ARCHIVE
from .pull import log_tail
from .records import is_agent, is_done, is_flaky
from .routing import load_approved, repo_root, require_yaml
from .runtime import TREE_TIMEOUT
from .state import enter_plan_state, locate_state, read_ledger_file


def _engine():
    """The package this module is split from, asked when the pinned base is fetched and read.

    A test answers the git seam by patching `driver.run`; a name bound here at
    import time would answer the real process and ignore it.
    """
    return sys.modules[__name__.rpartition(".")[0]]


def acceptance_context(args):
    """(plan model, repo, ledger path, run directory, ledger data) of the YAML plan a Lead command names. The model is
    the approved one: a plan or oracle edited since approval is refused here, as everywhere."""
    plan_path = Path(args.plan).resolve(strict=True)
    require_yaml(plan_path)
    repo = repo_root(plan_path, args.repo)
    model = load_approved(
        plan_path, repo=repo, state_dir=args.state_dir, require_approval=True, recover=True
    )
    ledger_path, run_dir = locate_state(args.state_dir, plan_path, repo, model.slug)
    data = read_ledger_file(ledger_path) if ledger_path.is_file() else {}
    return model, repo, ledger_path, run_dir, data


def tree_in(repo):
    def tree_of(sha):
        try:
            return _engine().run(
                ["git", "-C", str(repo), "rev-parse", "--verify", str(sha) + "^{tree}"]
            )
        except ValueError:
            return None

    return tree_of


MUTATION_ATTEMPTS = 2


def _held(result, data, digest, pin):
    """Mark every item an unkilled mutation holds with what it waits for; True when one is held."""
    held = False
    for item in result["items"]:
        record = (data.get("mutation") or {}).get(item["scenario"])
        if not record or item["state"] != "PASS" or record.get("sha") != pin.get("sha"):
            continue
        if record["state"] == "killed":
            continue
        held = True
        accepted = (data.get("acceptance") or {}).get(item["scenario"])
        if accepted and verify.holds_acceptance(accepted, digest, pin.get("tree")):
            item.update(
                state="HUMAN_ACCEPTED",
                reason="accepted by %s over an unkilled mutation" % accepted.get("by"),
            )
        else:
            item.update(
                state="AWAITING_HUMAN",
                reason=(
                    "the mutation check is running"
                    if record["state"] == "reviewing"
                    else "no reviewer-written mutation made the uncalibrated oracle fail after %d attempts; a person "
                    "confirms it (agent-orchestrator accept <plan> %s --note ...)"
                    % (record["attempt"], item["scenario"])
                ),
            )
    return held


def acceptance(model, data, receipts_dir, *, digest, tree_of=None):
    """`verify.acceptance`, plus the mutation check (S21): an outcome item that passes at integration but whose
    uncalibrated oracle no reviewer-written mutation killed is a human item (AWAITING_HUMAN, or HUMAN_ACCEPTED once a
    person accepted it), and the plan state follows from the items again."""
    result = verify.acceptance(model, data, receipts_dir, digest=digest, tree_of=tree_of)
    pin = data.get("integration") or {}
    if _held(result, data, digest, pin):
        states = {item["state"] for item in result["items"]}
        if states <= {"PASS"}:
            result["state"] = "ACCEPTED"
        elif states <= {"PASS", "HUMAN_ACCEPTED"}:
            result["state"] = "ACCEPTED_WITH_HUMAN"
        elif states <= {"PASS", "HUMAN_ACCEPTED", "AWAITING_HUMAN"}:
            result["state"] = "VERIFYING"
        else:
            result["state"] = "REJECTED"
    return result


class Evidence:
    """The evidence runs and the mutation check, mixed into the Driver.

    A member drives one run - the gate's checks, the outcome on a pinned base, the mutation a scenario
    must catch - and asks the driver it belongs to for the repo, the run directory and the runtime.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def resume_plan_state(self, data):
        """A plan state a drive that ended (or died) mid-way left behind, made right for this drive. A rejected plan
        is verified again by a new `run`; a plan with a ticket still to do is running; an accepted plan stays so
        only while its integration run is for the contract this drive holds."""
        model, approval = self.contract()
        state = data["plan"]["state"]
        statuses = data.get("ticket_status") or {}
        pending = [
            t
            for t in model.tickets
            if t.owner.strip().lower() == "agent"
            and not str(statuses.get(t.id, "")).strip().upper().startswith("COMPLETE")
        ]
        if state not in ("DELIVERED", "VERIFYING", "REJECTED", *ACCEPTED_STATES):
            return
        if pending:
            enter_plan_state(data, "RUNNING")
        elif state == "REJECTED":
            enter_plan_state(data, "DELIVERED")
        elif state == "VERIFYING":
            if self.acceptance_of(data)["state"] != "VERIFYING":
                enter_plan_state(data, "DELIVERED")  # a verification that did not finish
        elif state in ACCEPTED_STATES:
            if (data.get("integration") or {}).get("digest") != approval.digest:
                enter_plan_state(data, "DELIVERED")

    def run_evidence(self, scenario_id, phase, commit, task=None):
        """One run of a scenario's evidence at `commit`, its receipt written and its sha256 in the ledger."""
        model, approval = self.contract()
        scenario = verify.scenario_of(model, scenario_id)
        assert scenario is not None and scenario.evidence is not None
        item = scenario.evidence
        prior = self.earlier_receipts(scenario_id, phase)
        # A run can take minutes in its scratch tree; said before and after, the pane never looks idle through it.
        step = "archive" if phase == "archive" else "verify"
        narrate.say(
            "start",
            step,
            "running %s evidence (%s) at %s" % (scenario_id, phase, commit[:12]),
            task=task,
        )
        started = time.monotonic()
        result = self._evidence_run(scenario_id, phase, commit, item, approval.digest)
        receipt = result.receipt
        if is_flaky(receipt, prior):
            receipt.update(
                result="UNKNOWN",
                reason="flaky",
                detail="it failed on this tree before and passed now",
                label="UNKNOWN (flaky)",
            )
        runner.persist(result, self.receipts_dir())
        with self.book() as data:
            runner.record_in_ledger(data, result)
        kind = {"PASS": "done", "FAIL": "fail"}.get(receipt["result"], "warn")
        narrate.say(
            kind,
            step,
            "%s evidence %s (%s, %ds)"
            % (
                scenario_id,
                receipt.get("label") or receipt["result"],
                phase,
                time.monotonic() - started,
            ),
            task=task,
        )
        return result

    def _evidence_run(self, scenario_id, phase, commit, item, digest):
        """One runner invocation for one evidence item at `commit`: the same check, oracles and timeout at every
        call site, with the placement and the negative case resolved from the item."""
        oracles, place, negative = self.inputs(item)
        return runner.run_evidence(
            self.repo,
            commit,
            scenario=scenario_id,
            phase=phase,
            digest=digest,
            check=item.check or "",
            oracles=oracles,
            place=place,
            env_names=item.env,
            timeout=item.timeout,
            negative=negative,
        )

    def earlier_receipts(self, scenario_id, phase):
        with self.book() as data:
            stems = verify.receipt_stems(data, scenario_id, phase)
        found = []
        for stem in stems:
            try:
                found.append(
                    json.loads((self.receipts_dir() / (stem + ".json")).read_text(encoding="utf-8"))
                )
            except (OSError, ValueError):
                continue
        return found

    def ticket_evidence(self, task_id, packet):
        """The evidence a ticket's gate runs at its head, after the invariants: ("PASS" | "FAIL" | "error", items).
        Only a risk-tagged ticket that satisfies a scenario with evidence, or the ticket that completes an outcome
        scenario's satisfies set, runs any; FAIL and UNKNOWN are both a fix round."""
        unit = packet["unit"].upper()
        plan = self.read_plan()
        done = {t["#"] for t in plan["tickets"] if is_done(t)}
        try:
            scenario_ids = verify.gate_scenarios(self.model, unit, done)
            if not scenario_ids:
                return "PASS", []
            commit = verify.snapshot_commit(packet["checkout"])
            items = []
            for scenario_id in scenario_ids:
                result = self.run_evidence(scenario_id, "ticket", commit, task=task_id)
                receipt = result.receipt
                assert (
                    result.path is not None
                )  # run_evidence persisted it: the ledger keeps its stem
                with self.book() as data:
                    data.setdefault("ticket_evidence", {}).setdefault(unit, {})[scenario_id] = {
                        "result": receipt["result"],
                        "receipt": result.path.stem,
                        "commit": commit,
                    }
                if receipt["result"] != "PASS":
                    items.append(self.evidence_item(len(items) + 1, scenario_id, result))
        except (runner.RunnerError, verify.VerifyError) as error:
            return "error", [str(error)[:500]]
        return ("FAIL", items) if items else ("PASS", [])

    def evidence_item(self, number, scenario_id, result):
        """A fix-round item for evidence that did not pass: the command and the tail of its log."""
        receipt = result.receipt
        text = log_tail(result.log).strip()
        detail = ": " + receipt["detail"] if receipt.get("detail") else ""
        # A request's reason shows only the excerpt's first line, so the failing line goes there.
        if not detail and failure_line(text):
            detail = ": " + failure_line(text)
        return {
            "id": "E%d" % number,
            "kind": "blocking",
            "source": "verify",
            "check": "evidence %s" % scenario_id,
            "command": receipt["check"],
            "exit": receipt["exit"] if isinstance(receipt["exit"], int) else 1,
            "excerpt": "%s %s%s%s"
            % (
                scenario_id,
                receipt["label"],
                detail,
                (" — log tail:\n" + text) if text else "",
            ),
            "log_path": str(result.path.with_suffix(".log")),
        }

    def flag_unverified_evidence(self, task_id):
        """A person merged the ticket. If its gate evidence failed or never ran, the ticket is COMPLETE and flagged
        unverified: the plan cannot be accepted until that evidence passes at integration (D12)."""
        if self.task(task_id)["packet"]["unit"] == ARCHIVE:
            return
        unit = self.ticket_name(task_id)
        plan = self.read_plan()
        done = {t["#"] for t in plan["tickets"] if is_done(t)} | {unit}
        scenario_ids = verify.gate_scenarios(self.model, unit, done)
        with self.book() as data:
            ran = (data.get("ticket_evidence") or {}).get(unit) or {}
            bad = [s for s in scenario_ids if (ran.get(s) or {}).get("result") != "PASS"]
            if not bad:
                return
            data.setdefault("unverified", {})[unit] = {
                "scenarios": bad,
                "reason": "merged with its evidence failed or never run: %s" % ", ".join(bad),
                "at": now(),
            }
        self.log("evidence-unverified", unit=unit.lower(), task=task_id, scenarios=bad)
        narrate.say(
            "warn", "merge", "merged with its evidence unverified: %s" % ", ".join(bad), task=unit
        )

    def integrate(self, plan):
        """A YAML plan whose every agent ticket is COMPLETE is DELIVERED, verified on one pinned default-branch
        commit and accepted, or rejected. Steps taken (state changes), 0 for a plan that is not there yet."""
        agents = [t for t in plan["tickets"] if is_agent(t)]
        if not agents or not all(is_done(t) for t in agents):
            return 0
        if self.plan_state() not in ("RUNNING", "DELIVERED"):
            return 0
        steps = 0
        try:
            return self.verify_integration()
        except (
            ValueError,
            OSError,
            subprocess.TimeoutExpired,
            runner.RunnerError,
            verify.VerifyError,
        ) as error:
            message = "integration: %s" % " ".join(str(error).split())[:500]
            if message not in self.errors:
                self.errors.append(message)
                self.log("error", gate="integration", error=message)
                narrate.say("fail", "verify", message)
            return steps

    def pin_default_branch(self):
        """Fetch the default branch and pin its SHA: (sha, the units whose merge the commit does not contain). A merge
        commit this repository does not have even after the fetch is not contained either, nor is one nobody knows. The
        pin is one commit for every evidence item of the run."""
        sha = self._pinned_sha()
        with self.book() as data:
            merges = {
                task["packet"].get("unit"): task["merge_commit"]
                for task in data["tasks"].values()
                if task.get("merge_commit") and task["packet"].get("unit") != ARCHIVE
            }
        # A COMPLETE agent ticket whose merge commit is unknown (gh named none, or no task of the driver merged it) is
        # contained by no commit this can pin, so it makes integration wait like a missing one.
        unmerged = [
            ticket["#"].lower()
            for ticket in self.read_plan()["tickets"]
            if is_agent(ticket) and is_done(ticket) and ticket["#"].lower() not in merges
        ]
        lacking = list(unmerged)
        for unit, oid in sorted(merges.items()):
            known = subprocess.run(
                ["git", "-C", str(self.repo), "cat-file", "-e", oid + "^{commit}"],
                capture_output=True,
                timeout=60,
            )
            if (
                known.returncode
                or subprocess.run(
                    ["git", "-C", str(self.repo), "merge-base", "--is-ancestor", oid, sha],
                    capture_output=True,
                    timeout=60,
                ).returncode
            ):
                lacking.append(unit)
        return sha, sorted(lacking)

    def _pinned_sha(self):
        """`--base` fetched from its remote and resolved as the commit this repository holds now; a base naming no
        branch is read as it stands."""
        remote, _, branch = self.args.base.partition("/")
        if branch:
            _engine().run(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "fetch",
                    "--quiet",
                    remote,
                    "+refs/heads/%s:refs/remotes/%s/%s" % (branch, remote, branch),
                ],
                timeout=TREE_TIMEOUT,
            )
        return _engine().run(
            ["git", "-C", str(self.repo), "rev-parse", "--verify", self.args.base + "^{commit}"]
        )

    def verify_integration(self):
        sha, lacking = self.pin_default_branch()
        if lacking:
            self.wake(
                "integration-waits",
                "plan",
                "integration waits: %s does not contain the merge commit of %s; it verifies once the default branch "
                "does (run again after it has been fetched)"
                % (self.args.base, ", ".join(unit.upper() for unit in lacking)),
                key="%s:%s" % (sha, ",".join(lacking)),
            )
            return 0
        tree = self.tree_of(sha)
        with self.book() as data:
            attempt = (data.get("integration") or {}).get("attempt", 0) + 1
            data["integration"] = {
                "sha": sha,
                "tree": tree,
                "digest": self.contract()[1].digest,
                "at": now(),
                "attempt": attempt,
            }
            data["mutation"] = {}  # a mutation check belongs to one integration run
            # Delivered means a default branch that holds every merge was found, so the two states change together.
            enter_plan_state(data, "DELIVERED")
            enter_plan_state(data, "VERIFYING")
        self.log("integration", sha=sha, tree=tree, attempt=attempt)
        narrate.say(
            "start", "verify", "verifying %s on %s (tree %s)" % (self.slug, sha[:12], tree[:12])
        )
        full_failed = self.verify_full(sha, tree, attempt)
        passed = [
            scenario.id
            for scenario in verify.check_scenarios(self.model)
            if self.run_evidence(scenario.id, "integration", sha).receipt["result"] == "PASS"
        ]
        # The plan settles once every mutation check has: an unkilled oracle is not an accepted outcome. A plan whose
        # whole suite failed is rejected whatever its mutations show, so none is started for it.
        if full_failed or not self.start_mutations(sha, passed):
            self.settle_acceptance()
        return 2

    def verify_full(self, sha, tree, attempt):
        """The repo's whole suite (`checks.full.run`), once for this verify pass, on the pinned commit; True when it
        failed. `--skip-full` or `checks.full.verify: false` leave it to CI: a `skipped` event says so and why. A
        repo that declares no `full` has nothing to run or skip."""
        tiers = repo_config.load(self.repo).checks
        if tiers is None or not tiers.full_run:
            return False
        command = tiers.full_run
        reason = (
            "--skip-full"
            if getattr(self.args, "skip_full", False)
            else None
            if tiers.full_verify
            else "checks.full.verify: false"
        )
        record = {
            "state": "skipped" if reason else None,
            "sha": sha,
            "tree": tree,
            "digest": self.contract()[1].digest,
            "attempt": attempt,
            "command": command,
            "log": str(self.run_dir / "full.log"),
            "at": now(),
        }
        if reason:
            record["reason"] = reason
        else:
            narrate.say(
                "start", "verify", "running the whole suite on %s: %s" % (sha[:12], command)
            )
            result = verify.run_full(self.repo, sha, command, self.run_dir / "full.log")
            record.update(state=result["state"], exit=result["exit"], detail=result["detail"])
        with self.book() as data:
            data["full"] = record
        self.log(
            "full",
            **{
                k: v
                for k, v in record.items()
                if k in ("state", "sha", "command", "log", "reason", "exit", "attempt")
            },
        )
        return record["state"] == "FAIL"

    def uncalibrated_outcomes(self, passed):
        """Outcome scenarios among `passed` whose new oracle was uncalibrated at the approval baseline."""
        found = []
        model = self.model
        assert model is not None
        with self.book() as data:
            for scenario in model.scenarios:
                item = scenario.evidence
                if scenario.id not in passed or scenario.id not in model.outcome:
                    continue
                if item is None or not (item.check and item.has_tests) or item.baseline != "fail":
                    continue
                stems = verify.receipt_stems(data, scenario.id, "baseline")
                try:
                    receipt = json.loads(
                        (self.receipts_dir() / (stems[-1] + ".json")).read_text(encoding="utf-8")
                    )
                except (IndexError, OSError, ValueError):
                    continue  # without its baseline receipt nobody can say it was uncalibrated
                if receipt.get("calibrated") is False:
                    found.append(scenario.id)
        return found

    def start_mutations(self, sha, passed):
        """Start the first attempt of each uncalibrated outcome item that passed; True when any is waiting."""
        targets = self.uncalibrated_outcomes(passed)
        for scenario_id in targets:
            with self.book() as data:
                data["mutation"][scenario_id] = {
                    "state": "reviewing",
                    "attempt": 0,
                    "sha": sha,
                    "history": [],
                }
            self.spawn_mutation_reviewer(scenario_id, 1)
        return bool(targets)

    def spawn_mutation_reviewer(self, scenario_id, number):
        """One fresh reviewer for one attempt. A name no implementer or ticket reviewer holds, so no agent that saw
        the plan's work is resumed."""
        with self.book() as data:
            record = data["mutation"][scenario_id]
            previous = list(record["history"])
            sha = record["sha"]
        directory = self.run_dir / "mutation"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        patch = self.mutation_patch(scenario_id, number)
        task_md = directory / ("%s.%d-task.md" % (scenario_id, number))
        suffix = "-mut-%s-%d" % (scenario_id.lower(), number)
        name = self.slug[: 32 - len(suffix)].rstrip("-_") + suffix
        self._mutation_brief(scenario_id, sha, previous, patch, task_md)
        record_update = {"attempt": number, "name": name, "ended": False}
        assignment_id, error = self._mutation_agent(name, task_md)
        if error is not None:
            record_update.update(ended=True, error=str(error)[:300])
        else:
            record_update["assignment_id"] = assignment_id
        with self.book() as data:
            data["mutation"][scenario_id].update(record_update)
        self.log("mutation-reviewer", scenario=scenario_id, attempt=number, name=name)

    def _mutation_brief(self, scenario_id, sha, previous, patch, task_md):
        """The file one mutation reviewer reads: the plan, the oracle and the check, the delivered commit, the rules
        and why the earlier attempts did not do."""
        model, approval = self.contract()
        scenario = verify.scenario_of(model, scenario_id)
        assert scenario is not None and scenario.evidence is not None
        item = scenario.evidence
        lines = [
            "# Mutation check of %s: one behavioural mutation of the delivered code" % scenario_id,
            "",
            "You did not build this plan. Read these yourself; nothing is pasted here.",
            "- Plan: %s (scenario %s: WHEN/THEN, and its Decisions)"
            % (self.plan_path, scenario_id),
            "- Oracle: %s (the files named %s, approved bytes)"
            % (
                approval_recovery.snapshot_path(approval, self.args.state_dir).parent
                / ("acceptance" if item.acceptance else "oracle"),
                ", ".join(item.acceptance or item.oracle),
            ),
            "- Check: `%s`" % item.check,
            "- Delivered code: commit %s of %s" % (sha, self.repo),
            "",
            "The oracle failed at the base for a missing interface, so nobody has seen its assertions fail. Show that "
            "they do: change the delivered code so the behaviour %s promises is wrong, while the code still runs "
            "(a branch dropped, a condition inverted, a value changed). The oracle must then fail on an assertion."
            % scenario_id,
            "",
            "Rules:",
            "- Mutate delivered code only. Never edit the oracle or the file it is placed as (%s); such a patch "
            "counts as ineffective."
            % ", ".join(yaml_plan.placed_files(self.plan_path, self.model, item, self.repo)),
            "- Do not touch the repository checkout. Make the change in a scratch worktree "
            "(`git worktree add --detach <dir> %s`), write `git diff` there to %s, then remove the worktree."
            % (sha, patch),
            "- The patch must apply with `git apply` at %s and break behaviour, not syntax or formatting."
            % sha[:12],
        ]
        if previous:
            lines += [
                "",
                "Earlier attempts did not work; choose a different, more telling mutation:",
            ]
            lines += ["- attempt %d: %s" % (h["attempt"], h["detail"]) for h in previous]
        lines += [
            "",
            "Then call the agent-runtime `report` tool with kind=complete and one line: `mutation - %s`."
            % patch,
            "",
        ]
        task_md.write_text("\n".join(lines), encoding="utf-8")

    def _mutation_agent(self, name, task_md):
        """Start the reviewer on the brief: (its assignment id, the error that kept it from starting)."""
        try:
            backend, model_name = self.gate_step("reviewer", "medium")
            effort = self.step_effort("reviewer", "medium")
            _, assignment_id = self.ensure_agent(
                name,
                "mutation",
                "mutation",
                {
                    "kind": "worker",
                    "name": name,
                    "task": "Read %s and follow it." % task_md,
                    "cwd": str(self.repo),
                    "cli": backend,
                    "model": model_name,
                    "effort": effort,
                },
            )
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            if isinstance(error, IntentUnsettled):
                self.release_intents(error.operations, "no attempt is waiting on it")
            narrate.say("fail", "mutation", "mutation reviewer %s not started: %s" % (name, error))
            return None, error
        narrate.say(
            "start",
            "mutation",
            "started mutation reviewer %s · %s · %s · %s"
            % (name, self.gate_label("reviewer", "medium"), model_name, effort),
        )
        return assignment_id, None

    def mutation_patch(self, scenario_id, number):
        return self.run_dir / "mutation" / ("%s.%d.patch" % (scenario_id, number))

    def support_silent(self, assignment_id):
        """A mutation or gaps reviewer whose turn ended without `complete`. Its output for the current attempt on disk
        is its answer: it counts as done and is validated as a reported completion is. With no output it is a live
        agent that may be waiting on a background job, and only the idle watchdog asks the Lead about it (R77)."""
        landed = None
        with self.book() as data:
            for scenario_id, record in (data.get("mutation") or {}).items():
                if (
                    record.get("state") == "reviewing"
                    and record.get("assignment_id") == assignment_id
                ):
                    if (
                        not record.get("ended")
                        and self.mutation_patch(scenario_id, record["attempt"]).exists()
                    ):
                        record["ended"] = True
                        landed = (
                            record["name"],
                            self.mutation_patch(scenario_id, record["attempt"]),
                        )
                    break
            else:
                gaps = data.get("gaps") or {}
                if (
                    gaps.get("assignment_id") == assignment_id
                    and not gaps.get("ended")
                    and Path(gaps["path"]).exists()
                ):
                    gaps["ended"] = True
                    landed = (gaps["name"], Path(gaps["path"]))
        if landed:
            self.log(
                "output-landed", agent=landed[0], file=str(landed[1]), assignment_id=assignment_id
            )

    def mutation_ended(self, assignment_id):
        """True when `assignment_id` is a mutation reviewer's: its turn is over, so its patch is read next round."""
        with self.book() as data:
            for record in (data.get("mutation") or {}).values():
                if (
                    record.get("state") == "reviewing"
                    and record.get("assignment_id") == assignment_id
                ):
                    record["ended"] = True
                    return True
        return False

    def mutation_outcome(self, scenario_id, record):
        """(outcome, detail, run) of one reviewer's attempt: the patch is read, refused when it is no patch or edits the
        oracle, else applied and run. Outcomes: killed, survived, invalid, oracle, error."""
        scenario = verify.scenario_of(self.model, scenario_id)
        assert scenario is not None and scenario.evidence is not None
        item = scenario.evidence
        number = record["attempt"]
        path = self.mutation_patch(scenario_id, number)
        try:
            patch = path.read_bytes()
        except OSError:
            return "invalid", "no patch was written to %s" % path, None
        if record.get("error"):
            return "error", "the reviewer did not start: %s" % record["error"], None
        touched = {
            line[6:].strip().split("\t")[0]
            for line in patch.decode("utf-8", "replace").splitlines()
            if line.startswith(("--- a/", "+++ b/"))
        }
        placed = set(yaml_plan.placed_files(self.plan_path, self.model, item, self.repo))
        if not touched:
            return (
                "invalid",
                "the patch changes no file (it must be a `git diff` against the commit)",
                None,
            )
        if touched & placed:
            return (
                "oracle",
                "the patch edits the oracle's file %s" % sorted(touched & placed)[0],
                None,
            )
        oracles, place, _ = self.inputs(item)
        run_ = runner.run_mutation(
            self.repo,
            record["sha"],
            scenario=scenario_id,
            digest=self.contract()[1].digest,
            check=item.check,
            patch=patch,
            oracles=oracles,
            place=place,
            env_names=item.env,
            timeout=item.timeout,
            attempt_number=number,
        )
        return self._mutation_result(run_)

    def _mutation_result(self, run_):
        """What one applied mutation shows: killed, or survived and why it did not make the oracle fail."""
        receipt = run_.receipt
        if receipt["mutation"]["killed"]:
            return "killed", "the oracle failed on an assertion with the mutation applied", run_
        if receipt["result"] == "PASS":
            return "survived", "the oracle still passed with the mutation applied", run_
        return (
            "survived",
            "the mutation did not make the oracle fail on an assertion (%s%s)"
            % (receipt["label"], ": " + receipt["detail"] if receipt.get("detail") else ""),
            run_,
        )

    def advance_mutations(self):
        """Read each finished reviewer's patch and apply it; a killed mutation settles the item, an ineffective one
        asks for one more attempt, then the item is human. The plan settles when no check is left running."""
        with self.book() as data:
            ready = [
                (sid, copy.deepcopy(record))
                for sid, record in (data.get("mutation") or {}).items()
                if record.get("state") == "reviewing" and record.get("ended")
            ]
        steps = 0
        for scenario_id, record in ready:
            self._mutation_step(scenario_id, record)
            steps += 1
        with self.book() as data:
            records = (data.get("mutation") or {}).values()
            done = bool(records) and all(r["state"] != "reviewing" for r in records)
            waiting = (data.get("plan") or {}).get("state") == "VERIFYING"
        if steps and done and waiting:
            self.settle_acceptance()
        return steps

    def _mutation_step(self, scenario_id, record):
        """One finished reviewer's patch applied and its outcome booked; a mutation that did not kill the oracle gets
        one more attempt, then a person does."""
        try:
            outcome, detail, run_ = self.mutation_outcome(scenario_id, record)
        except (OSError, ValueError, subprocess.TimeoutExpired, runner.RunnerError) as error:
            outcome, detail, run_ = "error", "the mutation could not run: %s" % error, None
        receipt = None
        if run_ is not None:
            runner.persist(run_, self.receipts_dir())
            assert run_.path is not None  # persist wrote the receipt this line names
            receipt = run_.path.stem
        with self.book() as data:
            entry = data["mutation"][scenario_id]
            entry["history"].append(
                {
                    "attempt": record["attempt"],
                    "outcome": outcome,
                    "detail": detail,
                    "receipt": receipt,
                }
            )
            if run_ is not None:
                runner.record_in_ledger(data, run_)
            if outcome == "killed":
                entry["state"] = "killed"
            elif record["attempt"] >= MUTATION_ATTEMPTS:
                entry["state"] = "human"
            else:
                entry["ended"] = False
        self.log("mutation", scenario=scenario_id, attempt=record["attempt"], outcome=outcome)
        narrate.say(
            "done" if outcome == "killed" else "warn",
            "mutation",
            "%s attempt %d: %s — %s" % (scenario_id, record["attempt"], outcome, detail),
        )
        if outcome != "killed" and record["attempt"] < MUTATION_ATTEMPTS:
            self.spawn_mutation_reviewer(scenario_id, record["attempt"] + 1)

    def settle_acceptance(self):
        """The plan's state from its receipts: accepted, accepted with a person's word, rejected (and the Lead is
        asked), or still waiting for a person to confirm a human item."""
        with self.book() as data:
            result = self.acceptance_of(data)
            if result["state"] != "VERIFYING":
                enter_plan_state(data, result["state"])
            pin = dict(data["integration"])
        lines = verify.acceptance_errors(result)
        if result["state"] == "REJECTED":
            self.wake(
                "plan-rejected",
                "plan",
                "plan-rejected %s: %s -> %s. Ways out: add a ticket for the failing scenario (edit the plan, `plan "
                "approve`, `run` again), `run` again to verify a newer default branch, or `accept %s <S-id> --note "
                "...` for an item a person has checked (the plan is then ACCEPTED_WITH_HUMAN, never ACCEPTED)"
                % (self.slug, "; ".join(lines), self.receipts_dir(), self.plan_path),
                key="%s:%s" % (pin["sha"], pin["attempt"]),
            )
        elif result["state"] == "VERIFYING":
            self.wake(
                "plan-needs-accept",
                "plan",
                "plan-needs-accept %s: %s. A person confirms with `accept %s <S-id> --note ...`; the archive waits for it"
                % (self.slug, "; ".join(lines), self.plan_path),
                key="%s:%s" % (pin["sha"], pin["attempt"]),
            )
        self.log("acceptance", state=result["state"], items=result["items"])
