"""A worker's hand-in: the report JSON its agent-runtime `report` call carries.

The text is validated the moment it arrives, with the contract and the rules the merge holds a COMPLETE to, so a
hand-in whose shape is wrong goes straight back to its sender with the exact errors and never reaches the Lead.
"""

import json
import re

from .. import contracts
from ..report_rules import require_report
from .records import check_commit, require_completable
from .runtime import head_of

FENCE = re.compile(r"\A```[a-z]*\s*(.*?)\s*```\Z", re.DOTALL)


def read_hand_in(task, task_id, text):
    """(the report, None) when the text is an acceptable hand-in for `task_id`, else (None, the errors a worker
    reads). A COMPLETE report must name the commit the branch is at."""
    raw = FENCE.sub(r"\1", (text or "").strip())
    try:
        report = json.loads(raw)
    except ValueError as error:
        return None, "the report call's text is not the report JSON: %s" % error
    try:
        contracts.validate_report(report)
        require_report(report)
        contracts.need(
            report["task_id"] == task_id,
            "report task_id %r is not %s" % (report["task_id"], task_id),
        )
        if report["status"] == "COMPLETE":
            require_completable(task, report)
            check_commit(report["head"], head_of(task["packet"]["checkout"]), "report")
    except ValueError as error:
        return None, str(error)
    return report, None
