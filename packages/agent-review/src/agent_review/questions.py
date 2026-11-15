"""The configured question bank: one model and the curated, stable questions it is asked (D04, D29, D49).

An invocation without a configuration evaluates the packaged baseline, the mandatory global bank. A
configuration replaces that bank in this phase: additive repository questions, which extend the baseline
and never override or disable it, are a later phase's (D05). Only the surface this phase uses is validated
(P0-D4): the model, and each question's id, priority, ask, type and criteria. The type names the instrument
the question is asked with - a bounded yes/no question is a `noul`, a graded one a `score`, a classified
one a `choice` - and `criteria` carries what that instrument needs: the ladder of levels a score is graded
on (required), the optional criteria mapping a noul may declare, or the options a choice is answered with
(required). A criteria field of the wrong shape for its type is a misplaced field, not a preference, so it
is refused rather than sent. A bank that asks nothing is refused, so no screening completes having
evaluated no question. A refusal names the question and the field by its path, so a long bank is fixable.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent_review.errors import ReviewError

BASELINE = Path(__file__).with_name("questions.yaml")

PRIORITIES = ("critical", "high", "advisory")
TYPES = ("noul", "score", "choice")
DEFAULT_TYPE = "noul"

# D51: identity is authoring-time. A stable, dotted id lets a frozen report name the question it evaluated.
QUESTION_ID = re.compile(r"[a-z0-9]+(\.[a-z0-9-]+)+")

TOP_LEVEL = ("model", "questions", "safeguards")
FIELDS = ("id", "priority", "type", "ask", "criteria")

# A score's ladder needs two ends to grade between; a choice needs two options to choose between.
MIN_LEVELS = 2
MIN_OPTIONS = 2


@dataclass(frozen=True)
class Question:
    """One curated question: its stable id, the priority configuration declares, and what it asks.

    `criteria` is the provider's own input for the instrument: the ladder a score is graded on, or what a
    noul or choice may declare. It travels to the provider as configured.
    """

    id: str
    priority: str
    ask: str
    type: str = DEFAULT_TYPE
    criteria: Any = None


@dataclass(frozen=True)
class QuestionBank:
    """The bank one run evaluates: the screening model and the questions, in configuration order.

    `safeguards` is the run's own declaration - which members may carry only the standard library, and
    whatever else a rule needs to read - carried whole beside the questions. It is configuration, not a
    question (SG-D6), and the shape of what it holds is refused where a rule reads it.
    """

    model: str
    questions: tuple[Question, ...]
    safeguards: Mapping[str, Any] = field(default_factory=dict)


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
    return QuestionBank(
        model=_model(data, path),
        questions=_questions(data, path),
        safeguards=_safeguards(data, path),
    )


def _model(data: dict[str, Any], path: Path) -> str:
    if "model" not in data:
        raise ReviewError(f"the question configuration {path} names no model: add `model: <name>`")
    model = data["model"]
    if not isinstance(model, str) or not model.strip():
        raise ReviewError(f"{path}: model must be a non-empty string, not {model!r}")
    return model


def _safeguards(data: dict[str, Any], path: Path) -> Mapping[str, Any]:
    """The run's safeguards declaration as configured, or nothing when it declares none."""
    if "safeguards" not in data:
        return {}
    block = data["safeguards"]
    if not isinstance(block, dict):
        raise ReviewError(
            f"{path}: safeguards must be a mapping of declarations, not {type(block).__name__}"
        )
    return block


def _questions(data: dict[str, Any], path: Path) -> tuple[Question, ...]:
    if "questions" not in data:
        raise ReviewError(
            f"the question configuration {path} has no questions: add `questions: [...]`"
        )
    entries = data["questions"]
    if not isinstance(entries, list):
        raise ReviewError(f"{path}: questions must be a list, not {type(entries).__name__}")
    if not entries:
        raise ReviewError(
            f"{path}: questions is empty; a screening that asks nothing is not a screening"
        )
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
    return Question(
        id=identifier,
        priority=priority,
        ask=ask,
        type=kind,
        criteria=_criteria(entry.get("criteria"), kind, identifier, field, path),
    )


def _criteria(criteria: Any, kind: str, identifier: str, field: str, path: Path) -> Any:
    """The criteria this instrument needs, or a refusal naming the question and the field by path.

    The provider's request schema is the authority (00b-D1): a score without its levels is refused by the
    provider with a 422, and a noul handed a level list is graded as a question it never asked. Both are
    refused here, before any request is made, so a screening never pays for a bank that cannot be asked.
    """
    if kind == "noul":
        if criteria is None:
            return None
        if not isinstance(criteria, dict):
            raise ReviewError(
                f"{path}: {field}.criteria is not a mapping for the noul question {identifier!r}; "
                "a noul takes a mapping of criteria, and a level list belongs to a score question"
            )
        return dict(criteria)
    if kind == "choice":
        return _options(criteria, identifier, field, path)
    return _levels(criteria, identifier, field, path)


def _levels(criteria: Any, identifier: str, field: str, path: Path) -> list[str]:
    """A score question's ladder, best level first: required, and never a mapping or a lone level."""
    if criteria is None:
        raise ReviewError(
            f"{path}: {field}.criteria is missing for the score question {identifier!r}; "
            "a score question is graded on a list of levels, so add `criteria: [<best>, ..., <worst>]`"
        )
    if not isinstance(criteria, list):
        raise ReviewError(
            f"{path}: {field}.criteria must be a list of levels for the score question "
            f"{identifier!r}, not a {type(criteria).__name__}"
        )
    if len(criteria) < MIN_LEVELS:
        raise ReviewError(
            f"{path}: {field}.criteria carries {len(criteria)} level for the score question "
            f"{identifier!r}; a score question is graded on at least {MIN_LEVELS}"
        )
    for index, level in enumerate(criteria):
        if not isinstance(level, str) or not level.strip():
            raise ReviewError(
                f"{path}: {field}.criteria[{index}] must be a non-empty level for the score "
                f"question {identifier!r}, not {level!r}"
            )
    return list(criteria)


def _options(criteria: Any, identifier: str, field: str, path: Path) -> dict[str, str]:
    """A choice question's options and what each means: required, and never a level list."""
    if criteria is None:
        raise ReviewError(
            f"{path}: {field}.criteria is missing for the choice question {identifier!r}; "
            "a choice question declares its options as `criteria: {<option>: <what it means>}`"
        )
    if not isinstance(criteria, dict):
        raise ReviewError(
            f"{path}: {field}.criteria must be a mapping of option to meaning for the choice "
            f"question {identifier!r}, not a {type(criteria).__name__}"
        )
    if len(criteria) < MIN_OPTIONS:
        raise ReviewError(
            f"{path}: {field}.criteria declares {len(criteria)} option for the choice question "
            f"{identifier!r}; a choice question offers at least {MIN_OPTIONS}"
        )
    for option, meaning in criteria.items():
        if not isinstance(option, str) or not option.strip():
            raise ReviewError(
                f"{path}: {field}.criteria has an option named {option!r} for the choice question "
                f"{identifier!r}; an option is a non-empty name"
            )
        if not isinstance(meaning, str) or not meaning.strip():
            raise ReviewError(
                f"{path}: {field}.criteria[{option!r}] must say what the option means for the choice "
                f"question {identifier!r}, not {meaning!r}"
            )
    return dict(criteria)
