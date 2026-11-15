"""The configured question bank: one model and the curated, stable questions it is asked (D04, D29, D49).

An invocation without a configuration evaluates the packaged baseline, the mandatory global bank. A
configuration is additive (D05): its questions extend the baseline, which is asked whatever the repository
declares, and a repository question that repeats a baseline id is refused by name, because extending the
baseline is not replacing it - no repository can override or disable a mandatory question. An empty
additions list is legal and asks the baseline alone, so a configuration that only protects paths or
recalibrates a threshold needs no question of its own. Only the surface this phase uses is validated
(P0-D4): the model, and each question's id, priority, ask, type and criteria. The type names the instrument
the question is asked with - a bounded yes/no question is a `noul`, a graded one a `score`, a classified
one a `choice` - and `criteria` carries what that instrument needs: the ladder of levels a score is graded
on (required), the optional criteria mapping a noul may declare, or the options a choice is answered with
(required). A criteria field of the wrong shape for its type is a misplaced field, not a preference, so it
is refused rather than sent. A refusal names the question and the field by its path, so a long bank is
fixable.

The numbers a screening is decided with are configuration too, never the model's choice (D07, D48): the
bank carries the per-question `weights` and the calibrated `thresholds` - the value a question's answer
must clear and the confidence band it must keep - beside its questions, and a repository's declaration
adds to the packaged baseline's per question rather than dropping it, so no configuration silently
un-calibrates a question the baseline calibrated. A threshold may name a question this run does not ask:
a repository carries the calibration of its whole catalogue and asks part of it per run.

The bank also carries the paths this run protects, the safeguards it declares and the budgets a change is
split under (`chunk`, `cross_file`): the first two are read by rules, the last two by the chunking stage,
and all four are refused here by path when their shape is not one a stage can read. The packaged baseline
ships the mandatory sensitive paths (D13); a repository's configuration extends that list and never
replaces it, so the two lists are carried apart and joined where the rule reads them (SG-D4). A declaration
a rule will read is refused here, by its field path, when its shape is not the one that rule can use: a
screening never starts from a configuration the engine cannot honour.
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

TOP_LEVEL = (
    "model",
    "questions",
    "sensitive_paths",
    "safeguards",
    "weights",
    "thresholds",
    "chunk",
    "cross_file",
)
FIELDS = ("id", "priority", "type", "ask", "criteria")
# The declarations whose shape a rule reads; a declaration no rule reads is carried as configured.
DECLARATIONS = ("stdlib_only",)
# The run's own declarations the question bank carries whole, so the stage that reads one finds it here. Each is
# a block of budgets and limits: a positive whole number per setting, refused here rather than honoured nowhere.
CARRIED = ("chunk", "cross_file")
# What a question weighs in a screening when no configuration declares a weight for it.
DEFAULT_WEIGHT = 1.0
# A threshold's own fields: the value an answer must clear, and the confidence band it must keep.
THRESHOLD_FIELDS = ("value", "confidence")

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
class Threshold:
    """One question's calibrated numbers: the value its answer must clear, and the confidence band.

    Both are the tool's own decision inputs and never travel to the provider (D07, D48). A noul is
    escalated when its probability falls below `value`; a score, whose ladder runs from best to worst, when
    its position rises above it. `confidence` is the band below which an answer abstains whatever its value.
    """

    value: float | None = None
    confidence: float | None = None

    def as_dict(self) -> dict[str, float]:
        """The threshold as configured, without the half it does not declare."""
        declared: dict[str, float] = {}
        if self.value is not None:
            declared["value"] = self.value
        if self.confidence is not None:
            declared["confidence"] = self.confidence
        return declared


@dataclass(frozen=True)
class QuestionBank:
    """The bank one run evaluates: the screening model and the questions, in configuration order.

    `safeguards` is the run's own declaration - which members may carry only the standard library, and
    whatever else a rule needs to read - carried whole beside the questions. It is configuration, not a
    question (SG-D6), and the shape of what it holds is refused where a rule reads it.

    `sensitive_paths` are the paths this configuration protects: the packaged baseline's mandatory ones,
    or a repository's own, which the rule adds to the baseline rather than substituting for it (D13).

    `weights` and `thresholds` are what the screening is decided with, declared beside the questions and
    never chosen by the model: a question's weight, and the calibrated value and confidence band its
    answer is read against. `chunk` and `cross_file` are the budgets the chunking stage reads: accepted and
    shape-checked here, honoured there.
    """

    model: str
    questions: tuple[Question, ...]
    safeguards: Mapping[str, Any] = field(default_factory=dict)
    sensitive_paths: tuple[str, ...] = ()
    weights: Mapping[str, float] = field(default_factory=dict)
    thresholds: Mapping[str, Threshold] = field(default_factory=dict)
    chunk: Mapping[str, Any] = field(default_factory=dict)
    cross_file: Mapping[str, Any] = field(default_factory=dict)

    def weight(self, question: Question | str) -> float:
        """What this question weighs, and `DEFAULT_WEIGHT` when no configuration declares one."""
        identifier = question if isinstance(question, str) else question.id
        return self.weights.get(identifier, DEFAULT_WEIGHT)

    def threshold(self, question: Question | str) -> Threshold | None:
        """This question's calibrated numbers, or nothing when no configuration declares them."""
        identifier = question if isinstance(question, str) else question.id
        return self.thresholds.get(identifier)


def baseline() -> QuestionBank:
    """The packaged global bank: an invocation without a configuration evaluates exactly these (D30)."""
    return _bank(_data(BASELINE), BASELINE)


def load(path: Path | str) -> QuestionBank:
    """One repository configuration, read as the additions that extend the packaged baseline (D05).

    The baseline is asked whatever this configuration declares, so the merged bank is its questions first
    and the repository's own after them; a repository question that repeats a baseline id is refused by
    name. The configuration's weights and thresholds are laid over the baseline's per question, so a
    repository recalibrates what it names and leaves the rest calibrated.
    """
    path = Path(path)
    data = _data(path)
    packaged = baseline()
    additions = _questions(data, path)
    _extending(additions, packaged, path)
    return QuestionBank(
        model=_model(data, path),
        questions=packaged.questions + additions,
        safeguards=_safeguards(data, path),
        sensitive_paths=_sensitive_paths(data, path),
        weights=_weights(data, path, packaged.weights),
        thresholds=_thresholds(data, path, packaged.thresholds),
        chunk=_carried(data, "chunk", path),
        cross_file=_carried(data, "cross_file", path),
    )


def _bank(data: dict[str, Any], path: Path) -> QuestionBank:
    """The packaged baseline as a bank of its own: its questions, the paths it protects and its numbers."""
    return QuestionBank(
        model=_model(data, path),
        questions=_questions(data, path),
        safeguards=_safeguards(data, path),
        sensitive_paths=_sensitive_paths(data, path),
        weights=_weights(data, path, {}),
        thresholds=_thresholds(data, path, {}),
        chunk=_carried(data, "chunk", path),
        cross_file=_carried(data, "cross_file", path),
    )


def _data(path: Path) -> dict[str, Any]:
    """One configuration file as the mapping it holds, refusing what the schema does not name."""
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
    return data


def _extending(additions: tuple[Question, ...], packaged: QuestionBank, path: Path) -> None:
    """Refuse an addition that repeats a baseline id, naming the id and the way out (D05).

    Extending the baseline is not replacing it: a repository that wants a question of its own declares a
    new id, and a repository that wants a calibrated question keeps the baseline's and calibrates it
    through `thresholds`.
    """
    baseline_ids = {question.id for question in packaged.questions}
    for index, question in enumerate(additions):
        if question.id not in baseline_ids:
            continue
        raise ReviewError(
            f"{path}: questions[{index}].id is {question.id!r}, which the mandatory baseline already "
            "asks; a repository question extends the baseline and never replaces or disables one - "
            "declare a new id, or calibrate the baseline's question through `thresholds`"
        )


def _weights(
    data: dict[str, Any], path: Path, inherited: Mapping[str, float]
) -> Mapping[str, float]:
    """The weight each question carries, the configuration's laid over the bank it extends (D07, D48)."""
    if "weights" not in data:
        return dict(inherited)
    declared = data["weights"]
    if not isinstance(declared, dict):
        raise ReviewError(
            f"{path}: weights must be a mapping of question id to weight, not "
            f"{type(declared).__name__}"
        )
    weights = dict(inherited)
    for identifier, weight in declared.items():
        if not isinstance(identifier, str) or not QUESTION_ID.fullmatch(identifier):
            raise ReviewError(
                f"{path}: weights names {identifier!r}, which is not a dotted lowercase question id"
            )
        weights[identifier] = _number(weight, f"weights[{identifier!r}]", path)
    return weights


def _thresholds(
    data: dict[str, Any], path: Path, inherited: Mapping[str, Threshold]
) -> Mapping[str, Threshold]:
    """The calibrated numbers per question, the configuration's laid over the bank it extends."""
    if "thresholds" not in data:
        return dict(inherited)
    declared = data["thresholds"]
    if not isinstance(declared, dict):
        raise ReviewError(
            f"{path}: thresholds must be a mapping of question id to its calibrated numbers, not "
            f"{type(declared).__name__}"
        )
    thresholds = dict(inherited)
    for identifier, entry in declared.items():
        if not isinstance(identifier, str) or not QUESTION_ID.fullmatch(identifier):
            raise ReviewError(
                f"{path}: thresholds names {identifier!r}, which is not a dotted lowercase question id"
            )
        thresholds[identifier] = _threshold(entry, f"thresholds[{identifier!r}]", path)
    return thresholds


def _threshold(entry: Any, field_name: str, path: Path) -> Threshold:
    """One question's calibrated numbers, or a refusal naming the field by its path (D48)."""
    if not isinstance(entry, dict):
        raise ReviewError(
            f"{path}: {field_name} must be a mapping with {', '.join(THRESHOLD_FIELDS)}, not "
            f"{type(entry).__name__}"
        )
    unknown = sorted(set(entry) - set(THRESHOLD_FIELDS))
    if unknown:
        raise ReviewError(
            f"{path}: {field_name} has an unknown field {unknown[0]!r}; it takes "
            f"{', '.join(THRESHOLD_FIELDS)}"
        )
    if not entry:
        raise ReviewError(
            f"{path}: {field_name} declares nothing; give the question the value its answer must clear "
            "or the confidence band it must keep"
        )
    value = entry.get("value")
    confidence = entry.get("confidence")
    return Threshold(
        value=None if value is None else _number(value, f"{field_name}.value", path),
        confidence=None
        if confidence is None
        else _band(confidence, f"{field_name}.confidence", path),
    )


def _number(value: Any, field_name: str, path: Path) -> float:
    """A declared number, refused when it is not one a rule can read: a boolean, a text or a negative."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ReviewError(
            f"{path}: {field_name} must be a number of zero or more, not {value!r}; a number a rule "
            "cannot read would decide nothing"
        )
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ReviewError(f"{path}: {field_name} must be a finite number, not {value!r}")
    return number


def _band(value: Any, field_name: str, path: Path) -> float:
    """A confidence band: a probability, so a number between zero and one (D48)."""
    band = _number(value, field_name, path)
    if band > 1:
        raise ReviewError(
            f"{path}: {field_name} must be a confidence between 0 and 1, not {value!r}; a band above "
            "one would abstain on every answer"
        )
    return band


def _carried(data: dict[str, Any], name: str, path: Path) -> Mapping[str, Any]:
    """A block of budgets the chunking stage reads, carried beside the questions and checked for its shape.

    `chunk` and `cross_file` are honoured where a change is split and a cross-file question is bounded, not
    here, but their shape is refused here: a budget no stage can read (`chunk: {max_diff_tokens: banana}`)
    would otherwise be accepted silently and leave the request unbounded.
    """
    if name not in data:
        return {}
    block = data[name]
    if not isinstance(block, dict):
        raise ReviewError(
            f"{path}: {name} must be a mapping of settings, not {type(block).__name__}"
        )
    for setting, value in block.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ReviewError(
                f"{path}: {name}[{setting!r}] must be a positive whole number, not {value!r}; it is a "
                "budget the chunking stage reads"
            )
    return dict(block)


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
    for name in DECLARATIONS:
        if name in block:
            _paths(block[name], f"safeguards.{name}", path, noun="paths")
    return block


def _sensitive_paths(data: dict[str, Any], path: Path) -> tuple[str, ...]:
    """The paths this configuration protects, or nothing when it declares none (D13)."""
    if "sensitive_paths" not in data:
        return ()
    return _paths(data["sensitive_paths"], "sensitive_paths", path, noun="patterns")


def _paths(value: Any, field: str, path: Path, *, noun: str) -> tuple[str, ...]:
    """`value` as a list of non-empty paths, or a refusal naming the field by its path.

    A rule reads this list directly, so a scalar where a list belongs, a number where a path belongs or an
    empty spelling is refused here: the run never starts from a configuration no rule can honour (S9).
    """
    if not isinstance(value, list):
        raise ReviewError(f"{path}: {field} must be a list of {noun}, not {type(value).__name__}")
    for index, entry in enumerate(value):
        if not isinstance(entry, str) or not entry.strip():
            raise ReviewError(
                f"{path}: {field}[{index}] must be a non-empty {noun[:-1]}, not {entry!r}"
            )
    return tuple(entry.strip() for entry in value)


def _questions(data: dict[str, Any], path: Path) -> tuple[Question, ...]:
    """The questions this configuration declares, in its own order: its additions to the bank it extends.

    An empty (or absent) list is legal: the baseline is asked alone, which is what a configuration that
    only protects a path or recalibrates a threshold declares (D05).
    """
    entries = data.get("questions", [])
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
