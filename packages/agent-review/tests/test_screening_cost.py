"""The report's cost: the declared table's rate for the model the provider served, never a guess.

01-agent-review-smart-screening/S6 (D24, D26, P1-D6): a screening's cost is the declared table's rates for
the model the provider answered with, multiplied by the tokens the screening used, and the report carries the
basis - the table, its version, the model and both rates - so a frozen report says what priced it. A served
model the table does not name is unknown, never zero: a zero reads as free and would hide a paid screening
from a benchmark, and no declared table is unknown for the same reason. A table a rule cannot read is refused
by its field path before anything is charged for.
"""

import pytest
from agent_review import pricing
from agent_review.errors import ReviewError
from agent_review.questions import Question, QuestionBank
from agent_review.report import Report

pytestmark = pytest.mark.unit

SERVED = "jev-1.14.0"

BANK = QuestionBank(
    model="test-model-v1",
    questions=(Question(id="repo.error-handling", priority="critical", ask="Errors handled"),),
)


def table(tmp_path, models, *, version=1):
    """A price table on disk, in the form a caller declares: a version and a rate per served model."""
    lines = [f"version: {version}", "models:"]
    for model, (input_rate, output_rate) in models.items():
        lines += [
            f"  {model}:",
            f"    input_per_million: {input_rate}",
            f"    output_per_million: {output_rate}",
        ]
    path = tmp_path / "pricing.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def priced(tmp_path, models, *, served=SERVED, input_tokens=1000, output_tokens=20):
    """The report one screening wrote: the served model, its tokens and the table the caller declared."""
    return Report(
        bank=BANK,
        base="a" * 40,
        head="b" * 40,
        resolved_model=served,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        price_table=pricing.load(table(tmp_path, models)),
    )


def usage(report):
    return report.as_dict()["usage"]


@pytest.mark.spec("Screening cost")
def test_S6_the_cost_is_the_declared_tables_rate_for_the_model_the_provider_served(tmp_path):
    # 1000 input tokens at 0.042 per million plus 20 output tokens at zero per million.
    written = usage(priced(tmp_path, {SERVED: (0.042, 0.0)}))

    assert written["cost_usd"] == pytest.approx(0.000042)
    assert written["cost_basis"] == {
        "pricing": str(tmp_path / "pricing.yaml"),
        "version": 1,
        "model": SERVED,
        "input_per_million": 0.042,
        "output_per_million": 0.0,
    }, "the report carries what priced it, in the table's own terms"
    assert written["cost_unknown"] is None


@pytest.mark.spec("Screening cost")
def test_S6_an_unlisted_served_model_is_unknown_never_zero(tmp_path):
    written = usage(priced(tmp_path, {SERVED: (0.042, 0.0)}, served="jev-9.9.9"))

    assert written["cost_usd"] is None, "a model nobody priced is unknown, never free"
    assert written["cost_basis"] is None
    assert "jev-9.9.9" in written["cost_unknown"], "the report says which model it could not price"


@pytest.mark.spec("Screening cost")
def test_S6_no_declared_table_leaves_the_cost_unknown_never_zero():
    written = usage(
        Report(
            bank=BANK,
            base="a" * 40,
            head="b" * 40,
            resolved_model=SERVED,
            input_tokens=1000,
            output_tokens=20,
        )
    )

    assert written["cost_usd"] is None, "an absent table is unknown, never zero"
    assert written["cost_basis"] is None
    assert "price table" in written["cost_unknown"]


@pytest.mark.parametrize(
    ("document", "field"),
    [
        (
            "version: 1\nmodels:\n  jev-1.14.0:\n    input_per_million: -1\n"
            "    output_per_million: 0\n",
            "models['jev-1.14.0'].input_per_million",
        ),
        (
            "version: 1\nmodels:\n  jev-1.14.0:\n    input_per_million: 0.042\n",
            "models['jev-1.14.0'].output_per_million",
        ),
        ("version: 1\nmodels: []\n", "models"),
        ("models: {}\n", "version"),
    ],
    ids=["negative-rate", "missing-output-rate", "models-not-a-mapping", "no-version"],
)
@pytest.mark.spec("Screening cost")
def test_S6_a_table_a_rule_cannot_read_is_refused_by_its_field_path(tmp_path, document, field):
    path = tmp_path / "pricing.yaml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ReviewError) as refused:
        pricing.load(path)

    assert field in str(refused.value), (
        "a refusal names the field by its path, so a table is fixable"
    )
