"""The defect bank's scoring: a defect is named when a finding is in its file and near its line."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BANK = Path(__file__).resolve().parents[3] / "evals" / "review-bank"
spec = importlib.util.spec_from_file_location("review_bank_run", BANK / "run.py")
assert spec and spec.loader
bank = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bank)

LOCATIONS = {"src/a.py": [[100, 102]], "src/b.py": [[5, 5]]}


@pytest.mark.parametrize(
    ("finding", "expected"),
    [
        ({"file": "src/a.py", "line": 101}, True),
        ({"file": "src/a.py", "line": 102 + bank.LINE_SLACK}, True),
        ({"file": "src/a.py", "line": 100 - bank.LINE_SLACK}, True),
        ({"file": "src/a.py", "line": 102 + bank.LINE_SLACK + 1}, False),
        ({"file": "src/c.py", "line": 101}, False),
        ({"file": "./src/b.py", "line": 6}, True),
        ({"file": "src/a.py", "line": "101"}, False),
    ],
)
def test_a_finding_names_a_defect_by_file_and_nearby_line(finding, expected):
    assert bank.named([finding], LOCATIONS) is expected


def test_the_bank_holds_twelve_defects_with_locations_and_every_case_has_a_commit_range():
    import json

    defects = json.loads((BANK / "defects.json").read_text())
    cases = json.loads((BANK / "cases.json").read_text())
    assert sum("locations" in entry for entry in defects.values()) == 12
    assert all(case["base"] and case["head"] for case in cases)
