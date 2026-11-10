"""`label/2`: a judge's label with the evidence behind every score.

`label/1` holds a verdict and four scores and cannot say why; `label/2` holds the whole output of the
score-agent rubric, and every finding points at the line of the case it rests on. The shape is strict
(any other field is refused); `check` then holds the label against the case it judged, so a quote that is
not in the case, a score that is not the weighted mean of its dimensions or a verdict that does not follow
the rubric never reaches `labels/`. All of it is validated before a byte is written.
"""

import gzip
import json
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

from agent_eval.errors import CaptureError
from agent_eval.shapes import Date, Unit, describe

PATCH = "changes.patch"
INSUFFICIENT = "insufficient_evidence"
# The rubric's weights: a dimension with no score leaves the mean, the others are renormalised.
WEIGHTS = {
    "quality": Decimal("0.35"),
    "tests": Decimal("0.25"),
    "efficiency": Decimal("0.25"),
    "communication": Decimal("0.15"),
}
PASS_LINE = Decimal("0.8")

ANCHORS = (1.0, 0.8, 0.4, 0.2)


def anchor(value: float) -> float:
    if value not in ANCHORS:
        raise ValueError(f"must be one of {', '.join(str(a) for a in ANCHORS)}, or null")
    return value


Text = Annotated[str, StringConstraints(min_length=1)]
Anchor = Annotated[float, AfterValidator(anchor)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Evidence(Strict):
    """A quote from a file of the case; `file` is the path the manifest lists, `line` is 1-based."""

    file: Text
    line: Annotated[int, Field(ge=1)] | None = None
    quote: Annotated[str, StringConstraints(min_length=1, max_length=300)]


class Finding(Strict):
    kind: Literal["proven", "risk", "strength"]
    claim: Text
    deduction: bool
    evidence: list[Evidence]


class Dimension(Strict):
    label: Text
    score: Anchor | None
    rationale: Text
    findings: list[Finding]


class Dimensions(Strict):
    quality: Dimension
    tests: Dimension
    efficiency: Dimension
    communication: Dimension

    def named(self) -> Iterator[tuple[str, Dimension]]:
        for name in WEIGHTS:
            yield name, getattr(self, name)


class Gate(Strict):
    gate: Text
    rationale: Text
    evidence: Annotated[list[Evidence], Field(min_length=1)]


class Subject(Strict):
    session: Text


class Judge(Strict):
    skill: Text
    rubric_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    model: Text


class ProbableCause(Strict):
    cause: Literal["model", "harness", "repo", "instructions", "mixed", "unknown"]
    confidence: Literal["low", "medium", "high"]


class Label2(Strict):
    """A `label/2` record. `schema` is its one spelling: the Python name is an unknown field."""

    schema_version: Literal["label/2"] = Field(alias="schema")
    kind: Literal["agent-judge"]
    subject: Subject
    judge: Judge
    judged_at: Date
    source: Literal["judged"]
    verdict: Literal["PASS", "FAIL", "BLOCKED", "INCOMPLETE"]
    score: Unit | None
    gates: list[Gate]
    dimensions: Dimensions
    top_issue: Text
    smallest_improvement: Text
    probable_cause: ProbableCause


SESSION = "8b1d6f52-0c3e-4a79-9d1f-5e2a7c4b9013"
TRANSCRIPT = f"transcripts/{SESSION}.jsonl.gz"

# Shown by `agent-eval add-label --help` and in the spec: complete, and valid against a case whose
# transcript holds these quotes on these lines (a test builds that case).
EXAMPLE: dict[str, Any] = {
    "schema": "label/2",
    "kind": "agent-judge",
    "subject": {"session": SESSION},
    "judge": {
        "skill": "score-agent",
        "rubric_sha256": "9f2c4d7a1b3e5f60718293a4b5c6d7e8f9a0b1c2d3e4f5061728394a5b6c7d8e",
        "model": "claude-opus-4-7",
    },
    "judged_at": "2026-11-10",
    "source": "judged",
    "verdict": "FAIL",
    "score": 0.69,
    "gates": [],
    "dimensions": {
        "quality": {
            "label": "weak",
            "score": 0.4,
            "rationale": "The worker reported the fix done while its patch still returns None.",
            "findings": [
                {
                    "kind": "proven",
                    "claim": "The worker reported done on a patch that returns None.",
                    "deduction": True,
                    "evidence": [
                        {
                            "file": TRANSCRIPT,
                            "line": 212,
                            "quote": "All done, the fix is in place.",
                        },
                        {"file": "changes.patch", "line": 14, "quote": "+    return None"},
                    ],
                }
            ],
        },
        "tests": {
            "label": "good",
            "score": 0.8,
            "rationale": "The suite ran and passed, but no test covers the idle path.",
            "findings": [
                {
                    "kind": "risk",
                    "claim": "Only the happy path is tested.",
                    "deduction": True,
                    "evidence": [{"file": TRANSCRIPT, "line": 140, "quote": "3 passed in 0.12s"}],
                }
            ],
        },
        "efficiency": {
            "label": "strong",
            "score": 1.0,
            "rationale": "It read the task, changed one file and stopped.",
            "findings": [
                {
                    "kind": "strength",
                    "claim": "It started from the task file.",
                    "deduction": False,
                    "evidence": [
                        {"file": TRANSCRIPT, "line": 31, "quote": "Read task.md and follow it."}
                    ],
                }
            ],
        },
        "communication": {
            "label": INSUFFICIENT,
            "score": None,
            "rationale": "The case holds no message the worker sent to its parent.",
            "findings": [],
        },
    },
    "top_issue": "Reported done on a change that does nothing.",
    "smallest_improvement": "Run the idle path before reporting done.",
    "probable_cause": {"cause": "instructions", "confidence": "medium"},
}


def scored(dimensions: Dimensions) -> dict[str, Decimal]:
    return {n: Decimal(str(d.score)) for n, d in dimensions.named() if d.score is not None}


def expected_score(dimensions: Dimensions) -> Decimal | None:
    marks = scored(dimensions)
    if not marks:
        return None
    weighted = sum((WEIGHTS[n] * s for n, s in marks.items()), Decimal(0))
    mean = weighted / sum((WEIGHTS[n] for n in marks), Decimal(0))
    return mean.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def expected_verdict(label: Label2, score: Decimal | None) -> str:
    if label.gates:
        return "BLOCKED"
    if label.dimensions.quality.score is None or label.dimensions.tests.score is None:
        return "INCOMPLETE"
    marks = scored(label.dimensions).values()
    if score is not None and score >= PASS_LINE and all(m >= PASS_LINE for m in marks):
        return "PASS"
    return "FAIL"


def number(value: Decimal | None) -> str:
    return "null" if value is None else str(float(value))


class CaseFiles:
    """The files of one case that a label may cite, and their text, read at most once."""

    def __init__(self, case: Path, manifest: dict) -> None:
        sessions = [s for s in manifest.get("sessions") or [] if isinstance(s, dict)]
        run = manifest.get("orchestrate")
        listed = [s.get("file") for s in sessions]
        listed += [f.get("file") for s in sessions for f in s.get("files") or []]
        listed += [f.get("file") for f in (run.get("files") or [] if isinstance(run, dict) else [])]
        self.case = case
        self.sessions = {s.get("id") for s in sessions}
        self.listed = {f for f in listed if isinstance(f, str)} | {PATCH}
        self.texts: dict[str, str | None] = {}

    def text(self, file: str) -> str | None:
        """The file's decompressed text, or None when the case does not hold it."""
        if file not in self.texts:
            path = self.case / file
            try:
                if path.is_symlink() or not path.is_file():
                    raise OSError(file)
                data = path.read_bytes()
                if file.endswith(".gz"):
                    data = gzip.decompress(data)
                self.texts[file] = data.decode("utf-8", errors="replace")
            except (OSError, EOFError, gzip.BadGzipFile):
                self.texts[file] = None
        return self.texts[file]

    def problem(self, ref: Evidence) -> str | None:
        """Why `ref` does not resolve against the case, or None when it does."""
        file = ref.file
        if PurePosixPath(file).is_absolute() or Path(file).is_absolute():
            return (
                f"{file!r} is an absolute path; cite a file of the case as the manifest lists it "
                f"(`transcripts/<id>.jsonl.gz`, `orchestrate/<path>.gz`, `{PATCH}`)"
            )
        if ".." in PurePosixPath(file).parts or ".." in Path(file).parts:
            return f"{file!r} leaves the case; cite a file inside it, as the manifest lists it"
        if file not in self.listed:
            return (
                f"{file!r} is not listed in the case manifest; cite a transcript or an orchestrate "
                f"file by its manifest `file`, or {PATCH}"
            )
        text = self.text(file)
        if text is None:
            return f"{file!r} is listed in the manifest but missing from the case"
        return locate(ref, text)


def locate(ref: Evidence, text: str) -> str | None:
    """Why `ref.quote` is not in `text` (on line `ref.line` when given: where it starts), or None."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    if ref.line is None:
        if ref.quote in text:
            return None
        return f"quote not found in {ref.file!r}: it must occur there verbatim"
    if ref.line > len(lines):
        return f"line {ref.line} is past the end of {ref.file!r} ({len(lines)} lines)"
    start = sum(len(line) + 1 for line in lines[: ref.line - 1])
    end = start + len(lines[ref.line - 1])
    found = text.find(ref.quote, start)
    if start <= found < end:
        return None
    if ref.quote in text:
        return (
            f"quote does not start on line {ref.line} of {ref.file!r}; "
            "correct the line, or leave it out"
        )
    return f"quote not found in {ref.file!r} (line {ref.line}): it must occur there verbatim"


def load_manifest(case: Path) -> dict:
    path = case / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CaptureError(
            f"{path}: a label/2 is checked against the case manifest, which cannot be read "
            f"({error}); capture the case again",
            2,
        ) from None
    if not isinstance(manifest, dict):
        raise CaptureError(f"{path}: not a case manifest", 2)
    return manifest


def refs(label: Label2) -> Iterator[tuple[str, Evidence]]:
    for i, gate in enumerate(label.gates):
        for j, ref in enumerate(gate.evidence):
            yield f"gates[{i}].evidence[{j}]", ref
    for name, dim in label.dimensions.named():
        for i, finding in enumerate(dim.findings):
            for j, ref in enumerate(finding.evidence):
                yield f"dimensions.{name}.findings[{i}].evidence[{j}]", ref


def dimension_problems(name: str, dim: Dimension) -> Iterator[str]:
    where = f"dimensions.{name}"
    deductions = [f for f in dim.findings if f.deduction]
    if dim.score is None:
        if dim.label != INSUFFICIENT:
            yield f"{where}: a null score has label {INSUFFICIENT!r}, not {dim.label!r}"
        if deductions:
            yield f"{where}: a null score takes no deduction; drop the findings with deduction true"
        return
    if dim.label == INSUFFICIENT:
        yield f"{where}: label {INSUFFICIENT!r} is for a null score, and this one is {dim.score}"
    if not any(f.evidence for f in dim.findings):
        yield f"{where}: a scored dimension needs at least one finding with evidence"
    if any(not f.evidence for f in deductions):
        yield f"{where}: a finding with deduction true needs evidence"
    if dim.score < 1.0 and not any(f.evidence for f in deductions):
        yield (
            f"{where}: scored {dim.score}, below 1.0, it needs a finding with deduction true "
            "and evidence"
        )


def problems(label: Label2, files: CaseFiles) -> list[str]:
    found = []
    if label.subject.session not in files.sessions:
        found.append(
            f"subject.session: {label.subject.session!r} is not a session of the case manifest; "
            "name one of its `sessions[].id`"
        )
    for name, dim in label.dimensions.named():
        found.extend(dimension_problems(name, dim))
    for path, ref in refs(label):
        why = files.problem(ref)
        if why:
            found.append(f"{path}: {why}")
    score = expected_score(label.dimensions)
    given = None if label.score is None else Decimal(str(label.score))
    if given != score:
        found.append(
            f"score: is {number(given)}, expected {number(score)} "
            "(the weighted mean of the scored dimensions)"
        )
    verdict = expected_verdict(label, score)
    if label.verdict != verdict:
        found.append(f"verdict: is {label.verdict}, expected {verdict}")
    return found


def check(source: Path, data: object, case: Path) -> Label2:
    """The `label/2` in `data`, held against `case`; CaptureError 2 names every failing field."""
    try:
        label = Label2.model_validate(data)
    except ValidationError as error:
        raise CaptureError(
            f"{source}: not a valid label/2; fix these fields: {describe(error)}", 2
        ) from None
    found = problems(label, CaseFiles(case, load_manifest(case)))
    if found:
        raise CaptureError(
            f"{source}: the label/2 does not hold against the case; fix these fields: "
            + "; ".join(found),
            2,
        )
    return label
