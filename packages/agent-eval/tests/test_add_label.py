"""Add a judge's label to a case: the `label/1` shape, refusal of an invalid or existing label
(agent-eval-p0/R219)."""

import json
import stat

import pytest
from agent_eval.cli import main

pytestmark = pytest.mark.integration

CASE = "org-chart-custodian-leaves"


def valid_label() -> dict:
    return {
        "schema": "label/1",
        "kind": "agent-judge",
        "verdict": "FAIL",
        "score": 0.64,
        "dimensions": {"quality": 0.8, "tests": 0.8, "efficiency": 0.4, "communication": 0.4},
        "judged_at": "2026-11-09",
        "source": "transcribed",
    }


@pytest.fixture
def home(tmp_path, monkeypatch):
    evals = tmp_path / "evals"
    (evals / "cases" / CASE).mkdir(parents=True)
    monkeypatch.setenv("AGENT_EVALS_HOME", str(evals))
    return evals


def write_json(path, data) -> str:
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_R219_an_invalid_label_exits_2_naming_each_failing_field(home, tmp_path, capsys):
    bad = valid_label()
    bad["verdict"] = "MAYBE"
    bad["score"] = 1.5
    del bad["dimensions"]["tests"]
    bad["source"] = "guessed"
    bad["extra"] = True
    file = write_json(tmp_path / "label.json", bad)

    code = main(["add-label", CASE, "--kind", "agent-judge", file])

    err = capsys.readouterr().err
    assert code == 2
    for field in ("verdict", "score", "dimensions.tests", "source", "extra"):
        assert field in err, f"{field!r} not named in: {err}"
    assert not (home / "cases" / CASE / "labels").exists()


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"judged_at": "2026-13-45"}, "judged_at"),
        ({"judged_at": "2026-02-30"}, "judged_at"),
        ({"schema": None, "schema_version": "label/1"}, "schema"),
    ],
    ids=["impossible-month", "impossible-day", "python-name-for-schema"],
)
def test_R219_a_label_valid_only_in_form_exits_2(home, tmp_path, capsys, change, field):
    """agent-eval-p0/R219

    label/1 has one spelling per field and real dates: a pattern-shaped date or the model's own
    field name is invalid, not silently accepted."""
    bad = valid_label()
    for key, value in change.items():
        if value is None:
            del bad[key]
        else:
            bad[key] = value
    file = write_json(tmp_path / "label.json", bad)

    code = main(["add-label", CASE, "--kind", "agent-judge", file])

    err = capsys.readouterr().err
    assert code == 2, err
    assert field in err, f"{field!r} not named in: {err}"
    assert not (home / "cases" / CASE / "labels").exists()


def test_R219_an_unreadable_label_file_exits_2(home, tmp_path, capsys):
    file = tmp_path / "label.json"
    file.write_text("{not json", encoding="utf-8")

    code = main(["add-label", CASE, "--kind", "agent-judge", str(file)])

    assert code == 2
    assert "label.json" in capsys.readouterr().err


def test_R219_a_valid_label_is_written_to_labels_agent_judge_json(home, tmp_path, capsys):
    file = write_json(tmp_path / "label.json", valid_label())

    code = main(["add-label", CASE, "--kind", "agent-judge", file])

    target = home / "cases" / CASE / "labels" / "agent-judge.json"
    assert code == 0
    assert capsys.readouterr().out.strip() == str(target)
    assert json.loads(target.read_text(encoding="utf-8")) == valid_label()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_R219_an_existing_label_is_refused_and_left_unchanged(home, tmp_path, capsys):
    file = write_json(tmp_path / "label.json", valid_label())
    assert main(["add-label", CASE, "--kind", "agent-judge", file]) == 0
    target = home / "cases" / CASE / "labels" / "agent-judge.json"
    before = target.read_bytes()

    changed = valid_label()
    changed["score"] = 0.9
    other = write_json(tmp_path / "other.json", changed)
    code = main(["add-label", CASE, "--kind", "agent-judge", other])

    err = capsys.readouterr().err
    assert code == 1
    assert str(target) in err
    assert target.read_bytes() == before
