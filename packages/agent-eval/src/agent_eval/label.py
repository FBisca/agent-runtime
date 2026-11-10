"""Attach a judge's label to a case: `<home>/cases/<slug>/labels/<kind>.json`, shape `label/1`.

A label is a judgement about a case, so it is validated here before a byte is written and never
overwritten: a second label of the same kind is refused, and the first one stays as it was.
"""

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

from agent_eval.errors import CaptureError
from agent_eval.store import check_slug, home, make_private_dirs, write_file

KINDS = ("agent-judge",)
DIMENSIONS = ("quality", "tests", "efficiency", "communication")
Unit = Annotated[float, Field(ge=0, le=1)]


def real_date(value: str) -> str:
    """The pattern fixes the shape; the calendar decides whether the day exists."""
    date.fromisoformat(value)
    return value


Date = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"), AfterValidator(real_date)]


class Dimensions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    quality: Unit
    tests: Unit
    efficiency: Unit
    communication: Unit


class Label(BaseModel):
    """A `label/1` record: the verdict and score a judge gave a case, and where they came from."""

    # Validation by alias only: `schema` is the one spelling, the Python name is an unknown field.
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["label/1"] = Field(alias="schema")
    kind: Literal["agent-judge"]
    verdict: Literal["PASS", "FAIL"]
    score: Unit
    dimensions: Dimensions
    judged_at: Date
    source: Literal["transcribed", "judged"]


def case_dir(slug: str) -> Path:
    return home() / "cases" / slug


def describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'label'}: {item['msg']}"
        for item in error.errors()
    )


def add_label(slug: str, kind: str, source: Path) -> Path:
    """Validate the JSON file at `source` as a `label/1` of `kind` and store it in the case.

    Raises CaptureError: 2 when the case or the file is not usable, 1 when the case does not exist
    or already has a label of this kind. Nothing is written on any refusal.
    """
    check_slug(slug)
    case = case_dir(slug)
    if not case.is_dir():
        raise CaptureError(
            f"case {slug!r} does not exist under {home() / 'cases'}: capture the run first with "
            "`agent-eval capture --session <id>`",
            1,
        )
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as error:
        raise CaptureError(f"{source}: cannot read ({error.strerror or error})", 2) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise CaptureError(f"{source}: not valid JSON ({error.msg})", 2) from None
    try:
        label = Label.model_validate(data)
    except ValidationError as error:
        raise CaptureError(
            f"{source}: not a valid label/1 {kind} label; fix these fields: {describe(error)}", 2
        ) from None
    if label.kind != kind:
        raise CaptureError(
            f"{source}: kind is {label.kind!r} but --kind is {kind!r}; pass the matching --kind", 2
        )

    target = case / "labels" / f"{kind}.json"
    if target.exists():
        raise CaptureError(
            f"{target} already exists: a label is never overwritten; keep it, or write the new "
            "judgement under another kind",
            1,
        )
    make_private_dirs(target.parent)
    write_file(target, (label.model_dump_json(by_alias=True, indent=2) + "\n").encode("utf-8"))
    return target
