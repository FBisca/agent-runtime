"""`agent-eval list`: the cases on this machine and the label beside each (agent-eval-list/R234-R236).

The index is a read of `manifest.json` and `labels/`, never of a transcript: a case is listed from its
own record, so listing is as cheap for a 5 MB case as for an empty one.
"""

import copy
import json
import os

import pytest
from agent_eval.cli import main
from agent_eval.label2 import EXAMPLE

pytestmark = pytest.mark.integration


def manifest(slug: str, captured: str, sessions: int, file: str = "transcripts/x.jsonl.gz") -> dict:
    return {
        "schema": "case/1",
        "case": slug,
        "captured": captured,
        "excluded": [],
        "missing": [],
        "git": None,
        "orchestrate": None,
        "sessions": [
            {
                "id": f"{index:08d}-0000-4000-8000-000000000000",
                "harness": "claude",
                "role": "root" if index == 0 else "worker",
                "parent": None,
                "file": file,
                "sha256": "0" * 64,
                "bytes": 1024,
                "source": "/nowhere/transcript.jsonl",
                "modified": captured,
                "cwd": "/work/app",
                "assignment_id": None,
                "files": [],
            }
            for index in range(sessions)
        ],
    }


def label(verdict: str, score: float, model: str) -> dict:
    data = copy.deepcopy(EXAMPLE)
    data["verdict"] = verdict
    data["score"] = score
    data["judge"]["model"] = model
    return data


def case(home, slug: str, captured: str, sessions: int, judged=None) -> None:
    directory = home / "cases" / slug
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps(manifest(slug, captured, sessions)), encoding="utf-8"
    )
    if judged is not None:
        (directory / "labels").mkdir()
        (directory / "labels" / "agent-judge.json").write_text(json.dumps(judged), encoding="utf-8")


@pytest.fixture
def home(tmp_path, monkeypatch):
    evals = tmp_path / "evals"
    (evals / "cases").mkdir(parents=True)
    monkeypatch.setenv("AGENT_EVALS_HOME", str(evals))
    return evals


def rows(out: str) -> list[list[str]]:
    """The printed rows, split on two or more spaces: the columns are aligned, not delimiter-separated."""
    return [
        [cell.strip() for cell in line.split("  ") if cell.strip()]
        for line in out.splitlines()
        if line.strip()
    ]


def test_R234_two_cases_are_listed_newest_first_with_their_verdict_and_score(home, capsys):
    case(home, "a-older", "2026-11-10T09:00:00+00:00", 1)
    case(home, "b-newer", "2026-11-11T10:00:00+00:00", 3, label("PASS", 0.85, "glm-5.3"))
    case(home, "c-newest", "2026-11-11T12:00:00+00:00", 2, label("FAIL", 0.4, "gpt-6.1-terra"))

    assert main(["list"]) == 0
    out = capsys.readouterr().out

    assert [row[0] for row in rows(out)] == ["c-newest", "b-newer", "a-older"]
    c_newest, b_newer, a_older = rows(out)
    assert ["2026-11-11", "2", "FAIL", "0.40", "gpt-6.1-terra"] == c_newest[1:]
    assert ["2026-11-11", "3", "PASS", "0.85", "glm-5.3"] == b_newer[1:]
    assert ["2026-11-10", "1", "-", "-", "-"] == a_older[1:]


def test_R234_an_unlabelled_case_is_listed_with_a_dash_not_hidden(home, capsys):
    case(home, "unjudged", "2026-11-11T10:00:00+00:00", 2)

    assert main(["list"]) == 0
    (row,) = rows(capsys.readouterr().out)

    assert row[0] == "unjudged"
    assert row[-2:] == ["-", "-"], f"verdict and score should read '-': {row}"


def test_R234_a_directory_that_is_not_a_case_is_not_listed(home, capsys):
    case(home, "a-case", "2026-11-11T10:00:00+00:00", 1)
    (home / "cases" / "stray").mkdir()
    (home / "cases" / "stray" / "notes.txt").write_text("not a case", encoding="utf-8")
    broken = home / "cases" / "broken"
    broken.mkdir()
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")

    assert main(["list"]) == 0

    assert [row[0] for row in rows(capsys.readouterr().out)] == ["a-case"]


def test_R235_json_prints_one_array_with_nulls_for_an_unlabelled_case(home, capsys):
    case(home, "judged", "2026-11-11T10:00:00+00:00", 2, label("BLOCKED", 0.33, "gpt-6.1-terra"))
    case(home, "open", "2026-11-11T11:00:00+00:00", 1)

    assert main(["list", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)

    assert [row["case"] for row in data] == ["open", "judged"]
    assert data[0] == {
        "case": "open",
        "captured": "2026-11-11T11:00:00+00:00",
        "sessions": 1,
        "verdict": None,
        "score": None,
        "judge": None,
    }
    assert (data[1]["verdict"], data[1]["score"], data[1]["judge"]) == (
        "BLOCKED",
        0.33,
        "gpt-6.1-terra",
    )


def test_R235_listing_reads_no_transcript(home, capsys):
    """The manifest's transcript is absent from the case: a listing that opened it would fail."""
    case(home, "big", "2026-11-11T10:00:00+00:00", 2, label("PASS", 0.9, "glm-5.3"))
    assert not (home / "cases" / "big" / "transcripts").exists()

    assert main(["list"]) == 0

    assert rows(capsys.readouterr().out)[0][0] == "big"


@pytest.mark.parametrize("json_flag", [[], ["--json"]], ids=["text", "json"])
def test_R236_an_empty_or_missing_home_prints_nothing_and_creates_nothing(
    tmp_path, monkeypatch, capsys, json_flag
):
    for evals in (tmp_path / "evals", tmp_path / "absent"):
        if evals.name == "evals":
            (evals / "cases").mkdir(parents=True)
        expected = "[]\n" if json_flag else ""
        monkeypatch.setenv("AGENT_EVALS_HOME", str(evals))

        assert main(["list", *json_flag]) == 0
        assert capsys.readouterr().out == expected
        if evals.name == "absent":
            assert not evals.exists(), "list created the home it was told to read"
        else:
            assert os.listdir(evals) == ["cases"]
