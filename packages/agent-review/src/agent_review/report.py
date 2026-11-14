"""`report.json`: the review's verdict kept apart from the execution's own health.

One invocation writes exactly one report under the caller's directory (D53, D54), whether the screening
completed or not: `status` says whether the execution finished, `verdict` says what the review concluded
and is null when nothing was concluded, and `failure.reason` names why an incomplete run stopped (D34,
D35, D39, D40). The report also freezes what the run was made of - both revisions, the question
configuration, the model and the usage - so the same frozen input can be recognised again (D26, D44).

`usage.requests` is how many times the provider was asked. One screening is one request (D40): a retry
or a model fallback would show as a second one.
"""

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_review.questions import Question

REPORT = "report.json"
COMPLETED = "completed"
INCOMPLETE = "incomplete"
SCREENED = "screened"


@dataclass(frozen=True)
class Report:
    """One invocation's report: the execution's status, the review's verdict, and what both rest on."""

    model: str
    base: str
    head: str
    questions: Sequence[Question]
    judgments: tuple[Mapping[str, Any], ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    # One screening is one request: the run never retries, so anything else would be a defect (D40).
    requests: int = 1
    failure: str | None = field(default=None)

    @property
    def status(self) -> str:
        """Whether the execution finished; never what the review concluded (D34)."""
        return COMPLETED if self.failure is None else INCOMPLETE

    @property
    def verdict(self) -> str | None:
        """What the review concluded: `screened` once screened, nothing when it did not complete."""
        return SCREENED if self.failure is None else None

    def as_dict(self) -> dict[str, Any]:
        """The report as `report.json` holds it, in the contract's field order (P0-D3)."""
        return {
            "status": self.status,
            "verdict": self.verdict,
            "input": {"base": self.base, "head": self.head},
            "model": self.model,
            "usage": {
                "requests": self.requests,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            },
            "questions": [
                {"id": question.id, "priority": question.priority, "type": question.type}
                for question in self.questions
            ],
            "judgments": [dict(judgment) for judgment in self.judgments],
            "failure": None if self.failure is None else {"reason": self.failure},
        }

    def write(self, directory: Path) -> Path:
        """Write the report atomically under `directory`, so no reader sees a half-written one (D54)."""
        path = directory / REPORT
        temporary = path.with_name(f"{REPORT}.tmp")
        temporary.write_text(json.dumps(self.as_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return path

    def summary(self, path: Path) -> str:
        """What a person reads on the terminal: the verdict, the judgments behind it and where the report is."""
        change = f"{self.base}..{self.head}"
        if self.failure is not None:
            lines = [f"screened nothing: {change} was not screened with {self.model}"]
            lines.append(f"execution: {self.status}")
            lines.append(f"reason: {self.failure}")
        else:
            lines = [f"screened {change} with {self.model}"]
            lines.extend(
                f"  {judgment['question']} [{judgment['priority']}] {json.dumps(judgment['value'])}"
                for judgment in self.judgments
            )
            lines.append(f"verdict: {self.verdict}")
        lines.append(
            f"usage: {self.requests} request, {self.input_tokens} input tokens, "
            f"{self.output_tokens} output tokens"
        )
        lines.append(f"report: {path}")
        return "\n".join(lines)
