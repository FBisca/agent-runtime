"""A plan's requirement changes: `plan check` holds them to the plan's spec, and the archive applies them."""

import io
import subprocess
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
import yaml
from agent_orchestrator import spec_blocks
from agent_orchestrator.plan_document import Plan
from fixtures import load_script

pytestmark = pytest.mark.integration

SPEC = textwrap.dedent(
    """\
    # Demo

    ## Requirements

    ### Requirement: Kept
    The demo SHALL keep this.

    #### Scenario: kept
    - WHEN a
    - THEN b

    ### Requirement: Changed
    The demo SHALL change this.

    #### Scenario: old
    - WHEN c
    - THEN d

    ### Requirement: Gone
    The demo SHALL drop this.

    #### Scenario: gone
    - WHEN e
    - THEN f
    """
)


def requirement(title, sid="S1"):
    return {
        "title": title,
        "shall": "The demo SHALL do %s." % title.lower(),
        "scenarios": [{"id": sid, "name": "%s works" % title.lower(), "when": "x", "then": "y"}],
    }


def plan(added=(), modified=(), removed=()):
    changes = {
        "added": [requirement(t, "S%d" % i) for i, t in enumerate(added, 1)],
        "modified": [requirement(t, "S%d" % i) for i, t in enumerate(modified, len(added) + 1)],
        "removed": [{"title": t, "why": "%s is folded elsewhere" % t} for t in removed],
    }
    ids = [s["id"] for kind in ("added", "modified") for r in changes[kind] for s in r["scenarios"]]
    return {
        "schema": "plan/2",
        "slug": "demo",
        "goal": "deliver",
        "spec": "docs/specs/demo.md",
        "requirements": changes,
        "tickets": [
            {
                "id": "T1",
                "delivers": "it",
                "satisfies": ids,
                "paths": ["tests/"],
                "size": "small",
                "difficulty": "simple",
            }
        ],
    }


class PlanCheck(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.repo)])
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "docs" / "specs").mkdir(parents=True)
        (self.repo / "docs" / "specs" / "demo.md").write_text(SPEC, encoding="utf-8")
        self.plan = self.repo / "demo.plan.yaml"

    def check(self, data):
        self.plan.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = load_script().run_cli(
                ["plan", "check", str(self.plan), "--repo", str(self.repo)]
            )
        return code, out.getvalue() + err.getvalue()

    @pytest.mark.spec("Requirement changes fit the spec")
    def test_a_valid_plan_v2_passes(self):
        code, message = self.check(plan(added=["New"], modified=["Changed"], removed=["Gone"]))
        self.assertEqual(code, 0, message)

    @pytest.mark.spec("Plan format")
    def test_a_plan_v1_file_is_refused_naming_plan_v2_and_the_way_to_convert(self):
        data = plan(added=["New"])
        data["schema"] = "plan/1"
        data["scenarios"] = data.pop("requirements")["added"][0]["scenarios"]
        code, message = self.check(data)
        self.assertEqual(code, 2, message)
        self.assertIn("plan/1 is not plan/2", message)
        self.assertIn("requirements:", message)

    @pytest.mark.spec("Requirement changes fit the spec")
    def test_a_modified_title_the_spec_lacks_is_refused_naming_it_the_spec_and_the_fix(self):
        code, message = self.check(plan(modified=["Missing"]))
        self.assertEqual(code, 2, message)
        self.assertIn("'Missing' does not exist in docs/specs/demo.md", message)
        self.assertIn("add it instead", message)

    @pytest.mark.spec("Requirement changes fit the spec")
    def test_an_added_title_the_spec_holds_is_refused_naming_it_the_spec_and_the_fix(self):
        code, message = self.check(plan(added=["Kept"]))
        self.assertEqual(code, 2, message)
        self.assertIn("'Kept' already exists in docs/specs/demo.md", message)
        self.assertIn("move it to modified", message)


class ArchiveApply(unittest.TestCase):
    @pytest.mark.spec("Archive applies the change set")
    def test_the_change_set_adds_replaces_and_deletes_the_right_blocks(self):
        model = Plan.model_validate(plan(added=["New"], modified=["Changed"], removed=["Gone"]))
        text = spec_blocks.apply(SPEC, model.requirements, "docs/specs/demo.md")
        self.assertEqual(
            text,
            SPEC.split("### Requirement: Changed")[0]
            + textwrap.dedent(
                """\
                ### Requirement: Changed
                The demo SHALL do changed.

                #### Scenario: changed works
                - WHEN x
                - THEN y

                ### Requirement: New
                The demo SHALL do new.

                #### Scenario: new works
                - WHEN x
                - THEN y
                """
            ),
        )

    @pytest.mark.spec("Archive applies the change set")
    def test_a_change_the_spec_cannot_take_is_refused_and_nothing_is_written(self):
        model = Plan.model_validate(plan(modified=["Missing"]))
        with self.assertRaises(ValueError) as refused:
            spec_blocks.apply(SPEC, model.requirements, "docs/specs/demo.md")
        self.assertIn("modified 'Missing'", str(refused.exception))
