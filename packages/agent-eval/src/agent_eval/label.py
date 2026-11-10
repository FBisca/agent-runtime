"""Attach a judge's label to a case: `<home>/cases/<slug>/labels/<kind>.json`, shape `label/1` or `label/2`.

A label is a judgement about a case, so it is validated here before a byte is written and never
overwritten: a second label of the same kind is refused, and the first one stays as it was.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_eval import label2
from agent_eval.errors import CaptureError
from agent_eval.shapes import Date, Unit, describe
from agent_eval.store import check_slug, home, make_private_dirs, write_file

KINDS = ("agent-judge",)


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
    source: Literal["transcribed"]


def case_dir(slug: str) -> Path:
    return home() / "cases" / slug


def check_label(source: Path, kind: str, data: object, case: Path) -> Label | label2.Label2:
    """The label `data` is, validated; raises CaptureError 2 naming every failing field."""
    schema = data.get("schema") if isinstance(data, dict) else None
    if schema == "label/2":
        return label2.check(source, data, case)
    if schema == "label/1" and isinstance(data, dict) and data.get("source") == "judged":
        raise CaptureError(
            f"{source}: a judged label is label/2: it carries the evidence behind its score, "
            "which label/1 cannot; write a label/2 (see `agent-eval add-label --help`)",
            2,
        )
    try:
        label = Label.model_validate(data)
    except ValidationError as error:
        raise CaptureError(
            f"{source}: not a valid label/1 {kind} label (or a label/2: set schema to "
            f'"label/2"); fix these fields: {describe(error)}',
            2,
        ) from None
    return label


def add_label(slug: str, kind: str, source: Path) -> Path:
    """Validate the JSON file at `source` as a `label/1` or `label/2` of `kind`; store it in the case.

    A `label/2` is also checked against the case: its evidence, its subject, its score and its verdict.

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
    label = check_label(source, kind, data, case)
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
    # Unset fields stay unset, so a label is stored as it was given.
    dump = label.model_dump_json(by_alias=True, exclude_unset=True, indent=2)
    write_file(target, (dump + "\n").encode("utf-8"))
    return target
