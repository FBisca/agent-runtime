"""The configured question bank: one model and the curated, stable questions it is asked (D04, D29, D49).

The packaged baseline is the mandatory global bank; a repository's configuration extends it and never
overrides or disables it (D05). Only the surface this phase uses is validated (P0-D4): the model, and each
question's id, priority, ask and type. A refusal names the field by its path, so a long bank is fixable.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent_review.errors import ReviewError

BASELINE = Path(__file__).with_name("questions.yaml")

PRIORITIES = ("critical", "high", "advisory")
TYPES = ("boolean", "choice", "score")
DEFAULT_TYPE = "boolean"

# D51: identity is authoring-time. A stable, dotted id lets a frozen report name the question it evaluated.
QUESTION_ID = re.compile(r"[a-z0-9]+(\.[a-z0-9-]+)+")

TOP_LEVEL = ("model", "questions")
FIELDS = ("id", "priority", "type", "ask")


@dataclass(frozen=True)
class Question:
    """One curated question: its stable id, the priority configuration declares, and what it asks."""

    id: str
    priority: str
    ask: str
    type: str = DEFAULT_TYPE


@dataclass(frozen=True)
class QuestionBank:
    """The bank one run evaluates: the screening model and the questions, in configuration order."""

    model: str
    questions: tuple[Question, ...]


def baseline() -> QuestionBank:
    """The packaged global bank: an invocation without a configuration evaluates exactly these (D30)."""
    return load(BASELINE)


def load(path: Path | str) -> QuestionBank:
    """Read one YAML configuration, refusing what the schema does not name."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReviewError(
            f"cannot read the question configuration {path}: {error.strerror}; "
            "point --config at a YAML file"
        ) from None
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ReviewError(f"the question configuration {path} is not YAML: {error}") from None
    if not isinstance(data, dict):
        raise ReviewError(
            f"the question configuration {path} must be a mapping of model and questions"
        )
    unknown = sorted(set(data) - set(TOP_LEVEL))
    if unknown:
        raise ReviewError(
            f"the question configuration {path} has an unknown field {unknown[0]!r}; "
            f"it takes {', '.join(TOP_LEVEL)}"
        )
    return QuestionBank(model=_model(data, path), questions=_questions(data, path))


def _model(data: dict[str, Any], path: Path) -> str:
    if "model" not in data:
        raise ReviewError(f"the question configuration {path} names no model: add `model: <name>`")
    model = data["model"]
    if not isinstance(model, str) or not model.strip():
        raise ReviewError(f"{path}: model must be a non-empty string, not {model!r}")
    return model


def _questions(data: dict[str, Any], path: Path) -> tuple[Question, ...]:
    if "questions" not in data:
        raise ReviewError(
            f"the question configuration {path} has no questions: add `questions: [...]`"
        )
    entries = data["questions"]
    if not isinstance(entries, list):
        raise ReviewError(f"{path}: questions must be a list, not {type(entries).__name__}")
    questions: list[Question] = []
    seen: dict[str, int] = {}
    for index, entry in enumerate(entries):
        field = f"questions[{index}]"
        if not isinstance(entry, dict):
            raise ReviewError(f"{path}: {field} must be a mapping with {', '.join(FIELDS)}")
        unknown = sorted(set(entry) - set(FIELDS))
        if unknown:
            raise ReviewError(f"{path}: {field} has an unknown field {unknown[0]!r}")
        for required in ("id", "priority", "ask"):
            if required not in entry:
                raise ReviewError(f"{path}: {field}.{required} is missing")
        question = _question(entry, field, path)
        if question.id in seen:
            raise ReviewError(
                f"{path}: {field}.id repeats {question.id!r}, already declared by "
                f"questions[{seen[question.id]}]; ids are unique and stable (D51)"
            )
        seen[question.id] = index
        questions.append(question)
    return tuple(questions)


def _question(entry: dict[str, Any], field: str, path: Path) -> Question:
    identifier = entry["id"]
    if not isinstance(identifier, str) or not QUESTION_ID.fullmatch(identifier):
        raise ReviewError(
            f"{path}: {field}.id must be a dotted lowercase id such as contract.error-handling, "
            f"not {identifier!r}"
        )
    priority = entry["priority"]
    if priority not in PRIORITIES:
        raise ReviewError(
            f"{path}: {field}.priority must be one of {', '.join(PRIORITIES)}, not {priority!r}"
        )
    kind = entry.get("type", DEFAULT_TYPE)
    if kind not in TYPES:
        raise ReviewError(f"{path}: {field}.type must be one of {', '.join(TYPES)}, not {kind!r}")
    ask = entry["ask"]
    if not isinstance(ask, str) or not ask.strip():
        raise ReviewError(f"{path}: {field}.ask must be a non-empty string, not {ask!r}")
    return Question(id=identifier, priority=priority, ask=ask, type=kind)
