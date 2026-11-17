"""The briefs a round hands a worker back: an answer it was asked for, a redo, a fix round and a rebase.

A worker that has to be told something is told it in the same shape every time: the task it was given, the
checkout and branch it keeps, the paths it may edit, and what this round wants from it. Those texts are
rendered here so the files they name are pointers that resolve, and so one round's brief cannot drift from
the next.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand lines
and no class twenty-five public methods.
"""

from typing import Any

from .pointers import collection, owned_collections, owned_scope
from .prompts import block, bullets, render
from .records import CHECK_INSTRUCTIONS
from .report import describe


def _worker_values(run_dir, task):
    """What every brief to a worker says about where it works and what it runs: the checkout and branch, the
    scope and the checks it owes."""
    packet = task["packet"]
    return {
        "contract": run_dir / "contract.md",
        "unit": packet["unit"].upper(),
        "task_md": run_dir / packet["unit"] / "task.md",
        "checkout": packet["checkout"],
        "branch": task["checkout_at_claim"]["branch"],
        "scope": owned_scope(packet["checkout"], packet["owns"]),
        "checkout_contents": collection(packet["checkout"]),
        "scope_contents": owned_collections(packet["checkout"], packet["owns"]),
        "checks": "; ".join(packet["checks"]) or "the repository's full CI gate",
        "check_instructions": CHECK_INSTRUCTIONS,
    }


class Briefs:
    """The texts one round hands a worker, mixed into the Driver.

    A brief names the files its reader must open; nothing here reads the plan or the runtime, and the
    driver it belongs to supplies the run directory, the plan path and the contract.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the runtime are the Driver's
        own, and only what this module owns lives here."""
        raise AttributeError(name)

    def answer_text(self, task, decision):
        task_id = task["packet"]["task_id"]
        items = (task.get("fix") or {}).get("items") or []
        return render(
            "answer",
            **_worker_values(self.run_dir, task),
            task_id=task_id,
            reason=task["event"]["reason"],
            instruction=decision["reason"],
            answer=decision["answer"],
            open_findings=block("answer-open-findings", items=bullets(map(describe, items)))
            if items
            else "",
        )

    def redo_text(self, task, new_id, new_dir, redo):
        return render(
            "redo",
            **_worker_values(self.run_dir, task),
            task_id=new_id,
            reason=task["event"]["reason"],
            instruction=redo["reason"],
            lead_answer=block("redo-lead-answer", answer=redo["answer"])
            if redo.get("answer")
            else "",
        )

    def fix_text(self, task, new_id, new_dir, findings):
        if findings["source"] == "conflict":
            return self.rebase_text(task, new_id, new_dir, findings)
        answer = task["fix"].get("answer")
        return render(
            "fix",
            **_worker_values(self.run_dir, task),
            task_id=new_id,
            attempt=findings["attempt"],
            source=findings["source"],
            findings_path=new_dir / "findings.json",
            items=bullets(map(describe, findings["items"])),
            reviewer_checks=self._fix_checks(findings),
            ci_instructions=block("fix-ci") if findings["source"] == "ci" else "",
            answer_required=self._fix_answers(findings),
            lead_answer=block("fix-lead-answer", answer=answer) if answer else "",
        )

    def _answers_brief(self, task, answer_id, asks, findings_path):
        """The answerer's task file: which findings it answers, what a response is, and where it works."""
        return render(
            "answers-only",
            **_worker_values(self.run_dir, task),
            task_id=answer_id,
            findings_path=findings_path,
            items=bullets(map(describe, asks)),
        )

    def _fix_checks(self, findings):
        """The reviewer's checks, for a fix round to re-run only what it touches, then the final gate."""
        if not findings.get("checks_run"):
            return ""
        return block(
            "fix-reviewer-checks",
            checks=bullets(
                "`%s` exit %s at %s" % (c["command"], c["exit"], c["at_commit"])
                for c in findings["checks_run"]
            ),
        )

    def _fix_answers(self, findings):
        """What a fix round is told when the reviewer wants an answer per item."""
        wants = any(
            item.get("answer_required") is True
            for item in findings["items"]
            if isinstance(item, dict)
        )
        return block("fix-answer-required") if wants else ""

    def rebase_text(self, task, new_id, new_dir, findings):
        """The brief of a rebase round: the branch conflicts with its base, so the worker rebases it."""
        return render(
            "rebase",
            **_worker_values(self.run_dir, task),
            task_id=new_id,
            findings_path=new_dir / "findings.json",
            items=bullets(map(describe, findings["items"])),
        )
