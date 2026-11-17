"""The briefs a round hands a worker back: an answer it was asked for, a redo, a fix round and a rebase.

A worker that has to be told something is told it in the same shape every time: the task it was given, the
checkout and branch it keeps, the paths it may edit, and what this round wants from it. Those texts are
rendered here so the files they name are pointers that resolve, and so one round's brief cannot drift from
the next.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand lines
and no class twenty-five public methods.
"""

from typing import Any

from ..invariants import next_path
from .pointers import collection, owned_collections, owned_scope
from .records import CHECK_INSTRUCTIONS
from .report import describe


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
        packet, task_id, unit = task["packet"], task["packet"]["task_id"], task["packet"]["unit"]
        report = next_path(self.run_dir / task_id, "report", ".json")[0]
        items = (task.get("fix") or {}).get("items") or []
        return "\n".join(
            [
                "Contract: %s" % (self.run_dir / "contract.md"),
                "Report: %s" % report,
                "",
                "# Answer to %s (task %s)" % (unit.upper(), task_id),
                "",
                "What happened: %s" % task["event"]["reason"],
                "The Lead's instruction: %s" % decision["reason"],
                "The Lead's answer: %s" % decision["answer"],
                *(
                    ["", "Findings still open:", *["- " + describe(item) for item in items], ""]
                    if items
                    else []
                ),
                "This is the same attempt, not a new one: keep using task_id `%s` in your report, and write it to "
                "the path above (files are write-once; an earlier report stays where it is)."
                % task_id,
                "Original task: %s. Same checkout and branch: %s on %s. Work only here; start from: %s (edit any other file the change needs, except one another unfinished ticket lists in its paths)."
                % (
                    self.run_dir / unit / "task.md",
                    packet["checkout"],
                    task["checkout_at_claim"]["branch"],
                    owned_scope(packet["checkout"], packet["owns"]),
                ),
                "Checkout contents: %s" % collection(packet["checkout"]),
                "Scope contents: %s" % owned_collections(packet["checkout"], packet["owns"]),
                "Commit and push to the same branch. Run before you report COMPLETE: %s."
                % ("; ".join(packet["checks"]) or "the repository's full CI gate"),
                CHECK_INSTRUCTIONS,
                "Report review_rounds 0: the driver counts failed rounds.",
                "",
            ]
        )

    def redo_text(self, task, new_id, new_dir, redo):
        packet, unit = task["packet"], task["packet"]["unit"]
        return "\n".join(
            [
                "Contract: %s" % (self.run_dir / "contract.md"),
                "Report: %s" % (new_dir / "report.json"),
                "",
                "# Retry %s (task %s) after a decision" % (unit.upper(), new_id),
                "",
                "What happened: %s" % task["event"]["reason"],
                "The Lead's instruction: %s" % redo["reason"],
                *(["The Lead's answer: %s" % redo["answer"]] if redo.get("answer") else []),
                "Original task: %s. Use task_id `%s` in your report."
                % (self.run_dir / unit / "task.md", new_id),
                "Same checkout and branch: %s on %s. Work only here; start from: %s (edit any other file the change needs, except one another unfinished ticket lists in its paths)."
                % (
                    packet["checkout"],
                    task["checkout_at_claim"]["branch"],
                    owned_scope(packet["checkout"], packet["owns"]),
                ),
                "Checkout contents: %s" % collection(packet["checkout"]),
                "Scope contents: %s" % owned_collections(packet["checkout"], packet["owns"]),
                "Commit and push to the same branch. Run before you report COMPLETE: %s."
                % ("; ".join(packet["checks"]) or "the repository's full CI gate"),
                CHECK_INSTRUCTIONS,
                "Report review_rounds 0: the driver counts failed rounds.",
                "",
            ]
        )

    def fix_text(self, task, new_id, new_dir, findings):
        packet = task["packet"]
        if findings["source"] == "conflict":
            return self.rebase_text(task, new_id, new_dir, findings)
        return "\n".join(
            [
                "Contract: %s" % (self.run_dir / "contract.md"),
                "Report: %s" % (new_dir / "report.json"),
                "",
                "# Fix round %d for %s (task %s): %s findings"
                % (findings["attempt"], packet["unit"].upper(), new_id, findings["source"]),
                "",
                "Original task: %s. Use task_id `%s` in your report."
                % (self.run_dir / packet["unit"] / "task.md", new_id),
                "Same checkout and branch: %s on %s. Work only here; start from: %s (edit any other file the change needs, except one another unfinished ticket lists in its paths)."
                % (
                    packet["checkout"],
                    task["checkout_at_claim"]["branch"],
                    owned_scope(packet["checkout"], packet["owns"]),
                ),
                "Checkout contents: %s" % collection(packet["checkout"]),
                "Scope contents: %s" % owned_collections(packet["checkout"], packet["owns"]),
                "Findings, verbatim, also in %s. Fix only these:" % (new_dir / "findings.json"),
                "",
                *["- " + describe(item) for item in findings["items"]],
                "",
                *self._fix_checks(findings),
                *self._fix_answers(findings),
                *(
                    ["The Lead's answer: %s" % task["fix"]["answer"], ""]
                    if task["fix"].get("answer")
                    else []
                ),
                "Commit and push to the same branch; open no new pull request. Run before you report COMPLETE: %s."
                % ("; ".join(packet["checks"]) or "the repository's full CI gate"),
                CHECK_INSTRUCTIONS,
                "Report review_rounds 0: the driver counts failed rounds.",
                "",
            ]
        )

    def _answers_brief(self, task, answer_id, asks, findings_path):
        """The answerer's task file: which findings it answers, what a response is, and where it works."""
        packet, unit = task["packet"], task["packet"]["unit"]
        return "\n".join(
            [
                "Contract: %s" % (self.run_dir / "contract.md"),
                "Report: %s" % (self.run_dir / answer_id / "report.json"),
                "",
                "# Answers only for %s (task %s)" % (unit.upper(), answer_id),
                "",
                "The review passed. These findings need your answer, nothing else (also in %s):"
                % findings_path,
                "",
                *["- " + describe(item) for item in asks],
                "",
                "Change no code unless an answer needs it. Report with task_id `%s`; `responses` holds one "
                '`{"id", "status": "fixed|disputed", "commit", "note", "changes_code"?}` per finding above. '
                "A `disputed` answer that needs a code change sets `changes_code` true and names the commit: the reviewer sees it."
                % answer_id,
                "Same checkout and branch: %s on %s. Start from: %s (edit any other file the change needs, except one another unfinished ticket lists in its paths)."
                % (
                    packet["checkout"],
                    task["checkout_at_claim"]["branch"],
                    owned_scope(packet["checkout"], packet["owns"]),
                ),
                "Checkout contents: %s" % collection(packet["checkout"]),
                "Scope contents: %s" % owned_collections(packet["checkout"], packet["owns"]),
                "Re-run only what a change touches, then the final gate if you changed code. Open no new pull request.",
                "",
            ]
        )

    def _fix_checks(self, findings):
        """The reviewer's checks, for a fix round to re-run only what it touches, then the final gate."""
        if not findings.get("checks_run"):
            return []
        return [
            "The reviewer already ran these; re-run only what your fix touches, then the final gate:",
            *[
                "- `%s` exit %s at %s" % (c["command"], c["exit"], c["at_commit"])
                for c in findings["checks_run"]
            ],
            "",
        ]

    def _fix_answers(self, findings):
        """What a fix round is told when the reviewer wants an answer per item."""
        wants = any(
            item.get("answer_required") is True
            for item in findings["items"]
            if isinstance(item, dict)
        )
        if not wants:
            return []
        return [
            "Items marked answer_required need an answer: add `responses` to your report, one "
            '`{"id", "status": "fixed|disputed", "commit", "note", "changes_code"?}` each. '
            "Without them the driver asks you once more.",
            "",
        ]

    def rebase_text(self, task, new_id, new_dir, findings):
        """The brief of a rebase round: the branch conflicts with its base, so the worker rebases it."""
        packet = task["packet"]
        return "\n".join(
            [
                "Contract: %s" % (self.run_dir / "contract.md"),
                "Report: %s" % (new_dir / "report.json"),
                "",
                "# Rebase %s (task %s): the branch conflicts with its base"
                % (packet["unit"].upper(), new_id),
                "",
                "This is a rebase round: the pull request of %s conflicts with its base. Rebase the branch onto the "
                "base, resolve the conflicts, and push the result to the same branch. What `gh` said (also in %s):"
                % (packet["unit"].upper(), new_dir / "findings.json"),
                "",
                *["- " + describe(item) for item in findings["items"]],
                "",
                "Original task: %s. Use task_id `%s` in your report."
                % (self.run_dir / packet["unit"] / "task.md", new_id),
                "Same checkout and branch: %s on %s. Work only here; start from: %s (edit any other file the change needs, except one another unfinished ticket lists in its paths)."
                % (
                    packet["checkout"],
                    task["checkout_at_claim"]["branch"],
                    owned_scope(packet["checkout"], packet["owns"]),
                ),
                "Checkout contents: %s" % collection(packet["checkout"]),
                "Scope contents: %s" % owned_collections(packet["checkout"], packet["owns"]),
                "Commit and push to the same branch; open no new pull request. Run before you report COMPLETE: %s."
                % ("; ".join(packet["checks"]) or "the repository's full CI gate"),
                CHECK_INSTRUCTIONS,
                "Report review_rounds 0: the driver counts failed rounds.",
                "",
            ]
        )
