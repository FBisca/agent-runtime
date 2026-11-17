"""The prompt templates ship as package data and are filled strictly."""

from importlib.resources import files
from pathlib import Path
from string import Template

import pytest
from agent_orchestrator.driver import prompts

pytestmark = pytest.mark.unit

SOURCE = Path(prompts.__file__).parent


def templates():
    return sorted(files("agent_orchestrator.driver").joinpath("prompts").iterdir(), key=str)


def test_every_template_loads_through_importlib_resources():
    found = templates()
    assert len(found) >= 10
    for template in found:
        assert template.name.endswith(".md")
        assert Template(template.read_text(encoding="utf-8")).is_valid(), template.name


def test_every_template_is_named_by_the_code_that_renders_it():
    code = "".join(path.read_text(encoding="utf-8") for path in SOURCE.glob("*.py"))
    for template in templates():
        assert '"%s"' % template.name.removesuffix(".md") in code, template.name


def test_a_missing_variable_fails_loudly():
    with pytest.raises(KeyError):
        prompts.render("redo-lead-answer")


def test_an_empty_block_takes_its_line_and_a_full_one_does_not():
    full = prompts.render("review", **_review(rulebook="- Rulebook: r"))
    bare = prompts.render("review", **_review(rulebook=""))
    assert "- Checkout contents: x\n- Rulebook: r\n- Repo rules" in full
    assert "- Checkout contents: x\n- Repo rules" in bare


def _review(**more):
    names = Template(
        files("agent_orchestrator.driver").joinpath("prompts", "review.md").read_text("utf-8")
    ).get_identifiers()
    return dict({name: "p" if name == "plan" else "x" for name in names}, **more)
