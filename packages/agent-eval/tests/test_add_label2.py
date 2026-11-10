"""`label/2`: a judge's label with the evidence behind every score (agent-eval-label-evidence/S1-S6).

Each test builds a small case (manifest, gzipped transcript, an orchestrate file, a patch) in a throwaway
`AGENT_EVALS_HOME` and runs `agent-eval add-label` against it.
"""

import copy
import gzip
import json
import re
import stat
from pathlib import Path

import pytest
from agent_eval.cli import build_parser, main
from agent_eval.label2 import EXAMPLE

pytestmark = pytest.mark.integration

CASE = "orchestrator-silent-idle"
SESSION = "3f2a9c1e-5b7d-4e08-9a64-1c2d3e4f5a6b"
TRANSCRIPT = f"transcripts/{SESSION}.jsonl.gz"
TOOL_RESULT = f"transcripts/{SESSION}/tool-results/out.txt.gz"
EVENTS = "orchestrate/events.jsonl.gz"
PATCH = "changes.patch"
SPEC = Path(__file__).resolve().parents[3] / "docs" / "specs" / "agent-eval.md"

TRANSCRIPT_TEXT = (
    '{"type":"user","text":"fix the idle worker"}\n'
    '{"type":"assistant","text":"I did not run the test suite before reporting done"}\n'
    '{"type":"assistant","text":"the final report names the files it changed"}\n'
)
EVENTS_TEXT = '{"event":"worker_idle","at":"2026-11-10T14:00:00+00:00"}\n'
PATCH_TEXT = "diff --git a/x b/x\n+def wait():\n+    return None\n"
TOOL_TEXT = "3 passed in 0.12s\n"
UNLISTED_TEXT = "this file sits in the case but the manifest does not list it\n"


def ref(file: str, quote: str, line: int | None = None) -> dict:
    found = {"file": file, "quote": quote}
    if line is not None:
        found["line"] = line
    return found


def finding(kind="proven", deduction=True, evidence=None, claim="the claim") -> dict:
    return {
        "kind": kind,
        "claim": claim,
        "deduction": deduction,
        "evidence": [ref(TRANSCRIPT, "did not run the test suite", 2)]
        if evidence is None
        else evidence,
    }


def dimension(score, label="adequate", findings=None, rationale="because the transcript shows it"):
    if findings is None:
        findings = [finding(deduction=score is not None and score < 1.0)]
    return {"label": label, "score": score, "rationale": rationale, "findings": findings}


def valid_label() -> dict:
    """quality 0.4, tests 0.8, efficiency 1.0, communication unscored: 0.69, and FAIL."""
    return {
        "schema": "label/2",
        "kind": "agent-judge",
        "subject": {"session": SESSION},
        "judge": {"skill": "score-agent", "rubric_sha256": "ab12" * 16, "model": "claude-opus-4-7"},
        "judged_at": "2026-11-10",
        "source": "judged",
        "verdict": "FAIL",
        "score": 0.69,
        "gates": [],
        "dimensions": {
            "quality": dimension(0.4, "weak"),
            "tests": dimension(
                0.8, "good", [finding(evidence=[ref(TOOL_RESULT, "3 passed")], kind="risk")]
            ),
            "efficiency": dimension(
                1.0, "strong", [finding("strength", False, [ref(EVENTS, "worker_idle")])]
            ),
            "communication": dimension(None, "insufficient_evidence", []),
        },
        "top_issue": "the suite was never run",
        "smallest_improvement": "run the tests before reporting done",
        "probable_cause": {"cause": "instructions", "confidence": "medium"},
    }


def gz(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"))


def build_case(home: Path, sessions=(SESSION,)) -> Path:
    case = home / "cases" / CASE
    for rel, data in {
        TRANSCRIPT: gz(TRANSCRIPT_TEXT),
        TOOL_RESULT: gz(TOOL_TEXT),
        EVENTS: gz(EVENTS_TEXT),
        PATCH: PATCH_TEXT.encode(),
        "transcripts/unlisted.jsonl.gz": gz(UNLISTED_TEXT),
    }.items():
        (case / rel).parent.mkdir(parents=True, exist_ok=True)
        (case / rel).write_bytes(data)
    manifest = {
        "schema": "case/1",
        "case": CASE,
        "sessions": [
            {
                "id": sid,
                "harness": "claude",
                "role": "worker",
                "file": f"transcripts/{sid}.jsonl.gz" if sid != SESSION else TRANSCRIPT,
                "files": [{"kind": "tool-result", "file": TOOL_RESULT}] if sid == SESSION else [],
            }
            for sid in sessions
        ],
        "orchestrate": {"slug": "run", "files": [{"file": EVENTS}]},
        "git": {"repo": "/r", "base": "a", "head": "b"},
    }
    (case / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return case


@pytest.fixture
def home(tmp_path, monkeypatch):
    evals = tmp_path / "evals"
    build_case(evals)
    monkeypatch.setenv("AGENT_EVALS_HOME", str(evals))
    return evals


def run(tmp_path, label, capsys) -> tuple[int, str]:
    path = tmp_path / "label.json"
    path.write_text(json.dumps(label), encoding="utf-8")
    code = main(["add-label", CASE, "--kind", "agent-judge", str(path)])
    return code, capsys.readouterr().err


def stored(home) -> Path:
    return home / "cases" / CASE / "labels" / "agent-judge.json"


def refused(home, tmp_path, capsys, label, *needles) -> str:
    code, err = run(tmp_path, label, capsys)
    assert code == 2, err
    for needle in needles:
        assert needle in err, f"{needle!r} not in: {err}"
    assert not (home / "cases" / CASE / "labels").exists()
    return err


# --- S1: the shape ------------------------------------------------------------------------------


def test_S1_a_valid_label2_is_written_as_given(home, tmp_path, capsys):
    label = valid_label()
    code, err = run(tmp_path, label, capsys)

    assert code == 0, err
    assert json.loads(stored(home).read_text(encoding="utf-8")) == label
    assert stat.S_IMODE(stored(home).stat().st_mode) == 0o600


def test_S1_an_invalid_label2_names_each_failing_field_by_its_path(home, tmp_path, capsys):
    bad = valid_label()
    bad["dimensions"]["quality"]["findings"][0]["evidence"].append(ref(TRANSCRIPT, "x" * 301))
    bad["judge"]["rubric_sha256"] = "not-hex"
    bad["judged_at"] = "2026-02-30"
    bad["probable_cause"]["confidence"] = "certain"
    bad["dimensions"]["tests"]["score"] = 0.5
    bad["surprise"] = True
    del bad["top_issue"]
    bad["dimensions"]["efficiency"]["findings"][0]["kind"] = "nit"

    refused(
        home,
        tmp_path,
        capsys,
        bad,
        "dimensions.quality.findings[0].evidence[1].quote",
        "judge.rubric_sha256",
        "judged_at",
        "probable_cause.confidence",
        "dimensions.tests.score",
        "surprise",
        "top_issue",
        "dimensions.efficiency.findings[0].kind",
    )


@pytest.mark.parametrize(
    ("edit", "field"),
    [
        (lambda b: b.update(source="transcribed"), "source"),
        (lambda b: b.update(verdict="MAYBE"), "verdict"),
        (lambda b: b.update(score=1.2), "score"),
        (lambda b: b["subject"].update(extra=1), "subject.extra"),
        (lambda b: b["dimensions"].pop("tests"), "dimensions.tests"),
        (lambda b: b["dimensions"]["quality"].update(extra=1), "dimensions.quality.extra"),
        (lambda b: b["probable_cause"].update(cause="weather"), "probable_cause.cause"),
        (lambda b: b.update(gates=[{"gate": "g", "rationale": "r"}]), "gates[0].evidence"),
        (lambda b: b.update(gates="none"), "gates"),
        (
            lambda b: b["dimensions"]["quality"]["findings"][0]["evidence"][0].update(line=0),
            "dimensions.quality.findings[0].evidence[0].line",
        ),
    ],
    ids=[
        "source-judged-only",
        "verdict",
        "score-range",
        "subject-extra",
        "missing-dimension",
        "dimension-extra",
        "cause",
        "gate-without-evidence",
        "gates-not-a-list",
        "line-zero",
    ],
)
def test_S1_each_shape_fault_is_refused_and_nothing_is_written(home, tmp_path, capsys, edit, field):
    bad = valid_label()
    edit(bad)
    refused(home, tmp_path, capsys, bad, field)


# --- S2: the evidence refs ----------------------------------------------------------------------


def with_ref(evidence: dict) -> dict:
    label = valid_label()
    label["dimensions"]["quality"]["findings"][0]["evidence"] = [evidence]
    return label


@pytest.mark.parametrize(
    "evidence",
    [
        ref(TRANSCRIPT, "did not run the test suite"),
        ref(TRANSCRIPT, "did not run the test suite", 2),
        ref(TRANSCRIPT, "fix the idle worker", 1),
        ref(TOOL_RESULT, "3 passed in 0.12s"),
        ref(EVENTS, "worker_idle", 1),
        ref(PATCH, "def wait():", 2),
        ref(PATCH, "def wait():\n+    return None", 2),
        ref(TRANSCRIPT, TRANSCRIPT_TEXT.splitlines()[1], 2),
    ],
    ids=[
        "transcript",
        "transcript-line",
        "first-line",
        "tool-result",
        "orchestrate-file",
        "patch",
        "quote-spanning-lines-starts-on-its-line",
        "the-whole-line",
    ],
)
def test_S2_a_ref_into_a_listed_file_with_a_verbatim_quote_is_accepted(
    home, tmp_path, capsys, evidence
):
    code, err = run(tmp_path, with_ref(evidence), capsys)

    assert code == 0, err


@pytest.mark.parametrize(
    ("evidence", "words"),
    [
        (ref("/etc/passwd", "root"), "absolute"),
        (ref(str(Path.home() / "x"), "root"), "absolute"),
        (ref("../outside.txt", "root"), "leaves the case"),
        (ref("transcripts/../../outside.txt", "root"), "leaves the case"),
        (ref("transcripts/unlisted.jsonl.gz", "case but"), "not listed"),
        (ref("manifest.json", "case/1"), "not listed"),
        (ref("transcripts/missing.jsonl.gz", "x"), "not listed"),
        (ref(TRANSCRIPT, "did not run the test suite", 4), "past the end"),
        (ref(TRANSCRIPT, "did not run the test suite", 99), "past the end"),
        (ref(TRANSCRIPT, "did not run the test suite", 1), "line 1"),
        (ref(TRANSCRIPT, "never said this"), "not found"),
        (ref(PATCH, "def wait():", 1), "line 1"),
    ],
    ids=[
        "absolute",
        "absolute-home",
        "leaves-the-case",
        "leaves-through-a-listed-dir",
        "unlisted-file",
        "the-manifest-itself",
        "missing-file",
        "line-past-the-end",
        "line-far-past",
        "quote-on-another-line",
        "quote-not-there",
        "patch-wrong-line",
    ],
)
def test_S2_a_ref_that_does_not_resolve_exits_2_naming_it(home, tmp_path, capsys, evidence, words):
    err = refused(
        home,
        tmp_path,
        capsys,
        with_ref(evidence),
        "dimensions.quality.findings[0].evidence[0]",
        evidence["file"],
        words,
    )
    assert err.count("evidence[0]") >= 1


def test_S2_a_gate_ref_is_resolved_like_any_other(home, tmp_path, capsys):
    label = valid_label()
    label["verdict"] = "BLOCKED"
    label["gates"] = [
        {
            "gate": "secret leaked",
            "rationale": "r",
            "evidence": [ref(TRANSCRIPT, "nothing of the kind")],
        }
    ]

    refused(home, tmp_path, capsys, label, "gates[0].evidence[0]", "not found")


def test_S2_a_listed_file_that_is_gone_from_the_case_is_refused(home, tmp_path, capsys):
    (home / "cases" / CASE / TOOL_RESULT).unlink()
    label = valid_label()

    refused(home, tmp_path, capsys, label, "dimensions.tests.findings[0].evidence[0]", TOOL_RESULT)


# --- S3: the dimensions -------------------------------------------------------------------------


def edited(edit) -> dict:
    label = valid_label()
    edit(label["dimensions"])
    return label


@pytest.mark.parametrize(
    ("edit", "dimension_name"),
    [
        (lambda d: d["quality"].update(findings=[]), "quality"),
        (lambda d: d["efficiency"].update(findings=[finding("strength", False, [])]), "efficiency"),
        (lambda d: d["quality"]["findings"][0].update(deduction=False), "quality"),
        (lambda d: d["quality"]["findings"][0].update(evidence=[]), "quality"),
        (lambda d: d["tests"]["findings"][0].update(deduction=False), "tests"),
        (lambda d: d["communication"].update(label="weak"), "communication"),
        (lambda d: d["communication"].update(rationale=""), "communication"),
        (lambda d: d["communication"].update(findings=[finding()]), "communication"),
        (lambda d: d["quality"].update(label="insufficient_evidence"), "quality"),
    ],
    ids=[
        "scored-without-findings",
        "scored-without-evidence",
        "below-1-without-a-deduction",
        "deduction-without-evidence",
        "tests-below-1-without-a-deduction",
        "null-with-a-scored-label",
        "null-without-a-rationale",
        "null-with-a-deduction",
        "scored-labelled-insufficient",
    ],
)
def test_S3_a_dimension_that_breaks_a_rule_exits_2_naming_it(
    home, tmp_path, capsys, edit, dimension_name
):
    refused(home, tmp_path, capsys, edited(edit), f"dimensions.{dimension_name}")


def test_S3_a_perfect_dimension_needs_evidence_but_no_deduction(home, tmp_path, capsys):
    code, err = run(tmp_path, valid_label(), capsys)

    assert code == 0, err
    assert valid_label()["dimensions"]["efficiency"]["findings"][0]["deduction"] is False


def test_S3_every_dimension_is_named_when_several_break(home, tmp_path, capsys):
    label = edited(
        lambda d: (
            d["quality"].update(findings=[]),
            d["tests"]["findings"][0].update(deduction=False),
        )
    )

    refused(home, tmp_path, capsys, label, "dimensions.quality", "dimensions.tests")


# --- S4: the score and the verdict --------------------------------------------------------------


def scored(**scores) -> dict:
    """A label whose dimensions carry `scores`, with the score and verdict left for the test."""
    label = valid_label()
    for name, score in scores.items():
        label["dimensions"][name] = (
            dimension(None, "insufficient_evidence", [])
            if score is None
            else dimension(score, "label")
        )
    return label


@pytest.mark.parametrize(
    ("scores", "score", "verdict"),
    [
        (dict(quality=1.0, tests=1.0, efficiency=1.0, communication=1.0), 1.0, "PASS"),
        (dict(quality=0.8, tests=0.8, efficiency=0.8, communication=0.8), 0.8, "PASS"),
        (dict(quality=0.4, tests=0.8, efficiency=1.0, communication=None), 0.69, "FAIL"),
        (dict(quality=0.8, tests=0.8, efficiency=0.4, communication=1.0), 0.73, "FAIL"),
        (dict(quality=1.0, tests=0.2, efficiency=1.0, communication=1.0), 0.8, "FAIL"),
        (dict(quality=0.8, tests=None, efficiency=1.0, communication=1.0), 0.91, "INCOMPLETE"),
        (dict(quality=None, tests=0.8, efficiency=1.0, communication=1.0), 0.92, "INCOMPLETE"),
        (dict(quality=None, tests=None, efficiency=None, communication=None), None, "INCOMPLETE"),
        (dict(quality=None, tests=None, efficiency=None, communication=0.2), 0.2, "INCOMPLETE"),
        (dict(quality=0.2, tests=0.2, efficiency=0.2, communication=0.2), 0.2, "FAIL"),
    ],
    ids=[
        "all-perfect",
        "all-at-the-pass-line",
        "renormalised-over-three",
        "one-weak-dimension",
        "mean-passes-but-a-dimension-does-not",
        "tests-null",
        "quality-null",
        "none-scored",
        "only-communication",
        "all-weak",
    ],
)
def test_S4_the_recomputed_score_and_verdict_are_accepted(
    home, tmp_path, capsys, scores, score, verdict
):
    label = scored(**scores)
    label["score"], label["verdict"] = score, verdict

    code, err = run(tmp_path, label, capsys)

    assert code == 0, err


@pytest.mark.parametrize(
    ("score", "expected"),
    [(0.7, "0.69"), (None, "0.69"), (0.690000001, "0.69")],
    ids=["off-by-one-hundredth", "null-when-scored", "unrounded"],
)
def test_S4_a_wrong_score_exits_2_naming_the_expected_value(
    home, tmp_path, capsys, score, expected
):
    label = valid_label()
    label["score"] = score

    refused(home, tmp_path, capsys, label, "score", f"expected {expected}")


def test_S4_a_score_where_nothing_is_scored_must_be_null(home, tmp_path, capsys):
    label = scored(quality=None, tests=None, efficiency=None, communication=None)
    label["score"], label["verdict"] = 0.5, "INCOMPLETE"

    refused(home, tmp_path, capsys, label, "score", "expected null")


@pytest.mark.parametrize(
    ("verdict", "expected"), [("PASS", "FAIL"), ("INCOMPLETE", "FAIL"), ("BLOCKED", "FAIL")]
)
def test_S4_a_wrong_verdict_exits_2_naming_the_expected_value(
    home, tmp_path, capsys, verdict, expected
):
    label = valid_label()
    label["verdict"] = verdict

    refused(home, tmp_path, capsys, label, "verdict", f"expected {expected}")


def test_S4_a_gate_makes_the_verdict_blocked_and_only_a_gate_does(home, tmp_path, capsys):
    blocked = valid_label()
    blocked["gates"] = [
        {
            "gate": "secret leaked",
            "rationale": "a token is printed",
            "evidence": [ref(EVENTS, "worker_idle")],
        }
    ]
    refused(home, tmp_path, capsys, blocked, "verdict", "expected BLOCKED")

    blocked["verdict"] = "BLOCKED"
    code, err = run(tmp_path, blocked, capsys)
    assert code == 0, err


def test_S4_a_blocked_verdict_without_a_gate_is_refused(home, tmp_path, capsys):
    label = valid_label()
    label["verdict"] = "BLOCKED"

    refused(home, tmp_path, capsys, label, "verdict", "expected FAIL")


# --- S5: the subject and the source -------------------------------------------------------------


def test_S5_a_subject_that_is_not_a_session_of_the_manifest_exits_2_naming_it(
    home, tmp_path, capsys
):
    label = valid_label()
    label["subject"]["session"] = "00000000-dead-beef-0000-000000000000"

    refused(
        home, tmp_path, capsys, label, "subject.session", "00000000-dead-beef-0000-000000000000"
    )


def test_S5_a_label2_needs_a_readable_manifest(home, tmp_path, capsys):
    (home / "cases" / CASE / "manifest.json").unlink()

    refused(home, tmp_path, capsys, valid_label(), "manifest.json")


def test_S5_a_judged_label1_exits_2_saying_a_judged_label_is_label2(home, tmp_path, capsys):
    old = {
        "schema": "label/1",
        "kind": "agent-judge",
        "verdict": "FAIL",
        "score": 0.66,
        "dimensions": {"quality": 0.4, "tests": 0.8, "efficiency": 0.8, "communication": 0.8},
        "judged_at": "2026-11-10",
        "source": "judged",
    }

    refused(home, tmp_path, capsys, old, "label/2")


def test_S5_a_transcribed_label1_is_still_accepted(home, tmp_path, capsys):
    old = {
        "schema": "label/1",
        "kind": "agent-judge",
        "verdict": "FAIL",
        "score": 0.66,
        "dimensions": {"quality": 0.4, "tests": 0.8, "efficiency": 0.8, "communication": 0.8},
        "judged_at": "2026-11-10",
        "source": "transcribed",
    }

    code, err = run(tmp_path, old, capsys)

    assert code == 0, err
    assert json.loads(stored(home).read_text(encoding="utf-8")) == old


def test_S5_a_label2_of_another_kind_is_refused(home, tmp_path, capsys):
    label = valid_label()
    label["kind"] = "someone-else"

    refused(home, tmp_path, capsys, label, "kind")


def test_S5_an_existing_label_still_stays(home, tmp_path, capsys):
    assert run(tmp_path, valid_label(), capsys)[0] == 0
    before = stored(home).read_bytes()

    code, err = run(tmp_path, valid_label(), capsys)

    assert code == 1
    assert str(stored(home)) in err
    assert stored(home).read_bytes() == before


# --- S6: the help and the spec ------------------------------------------------------------------


def help_text(capsys) -> str:
    with pytest.raises(SystemExit) as stop:
        build_parser().parse_args(["add-label", "--help"])
    assert stop.value.code == 0
    return capsys.readouterr().out


def spec_example(text: str) -> dict:
    blocks = re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)
    found = [json.loads(b) for b in blocks if '"label/2"' in b]
    assert len(found) == 1, f"the spec carries {len(found)} label/2 examples"
    return found[0]


def test_S6_the_help_shows_where_the_label_goes_and_a_complete_label2(capsys):
    out = help_text(capsys)

    assert "labels/<kind>.json" in out
    assert json.dumps(EXAMPLE, indent=2) in out


def test_S6_the_spec_case_layout_and_example_match_the_help(capsys):
    text = SPEC.read_text(encoding="utf-8")
    layout = text.split("## Case layout")[1].split("\n## ")[0]

    assert "labels/<kind>.json" in layout
    assert spec_example(layout) == EXAMPLE


def test_S6_the_spec_names_pydantic_as_the_one_dependency():
    text = SPEC.read_text(encoding="utf-8")

    assert "Pydantic" in text
    assert "standard library only" not in text.split("## Claude chain")[0]


def test_S6_the_example_is_accepted_by_add_label_against_the_case_it_cites(
    tmp_path, monkeypatch, capsys
):
    evals = tmp_path / "evals"
    case = evals / "cases" / CASE
    example = copy.deepcopy(EXAMPLE)
    session = example["subject"]["session"]
    refs = [
        r
        for node in [*example["dimensions"].values(), *example["gates"]]
        for f in node.get("findings", [node])
        for r in f["evidence"]
    ]
    manifest_files = {}
    for r in refs:
        manifest_files.setdefault(r["file"], []).append(r)
    for file, wanted in manifest_files.items():
        # One line per quote, placed on the line the ref names: the example must be self-consistent.
        lines: dict[int, str] = {}
        for r in wanted:
            lines[r.get("line", len(lines) + 1)] = r["quote"]
        text = "\n".join(lines.get(n, "filler") for n in range(1, max(lines) + 1)) + "\n"
        target = case / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode() if file == PATCH else gz(text))
    sessions = [
        {"id": session, "file": next(f for f in manifest_files if f.startswith("transcripts/"))}
    ]
    others = [{"file": f} for f in manifest_files if f != sessions[0]["file"] and f != PATCH]
    sessions[0]["files"] = others
    (case / "manifest.json").write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
    monkeypatch.setenv("AGENT_EVALS_HOME", str(evals))

    code, err = run(tmp_path, example, capsys)

    assert code == 0, err
