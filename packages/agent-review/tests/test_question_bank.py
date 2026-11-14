"""The curated global question bank.

00-agent-review-screening-cli/S1: the tool evaluates a curated global engineering and contract question
bank. The bank is the packaged YAML baseline or a configured one; the providers it is asked through live
in test_screening_transport.py.
"""

import textwrap

import pytest
from agent_review import questions
from agent_review.errors import ReviewError

pytestmark = pytest.mark.unit

CONFIG = textwrap.dedent(
    """\
    model: test-model-v1
    questions:
      - id: contract.error-handling
        priority: critical
        type: noul
        ask: Does the change keep the documented error-handling contract
      - id: engineering.names-meaning
        priority: advisory
        ask: Do the new names say what they mean
    """
)


def test_S1_the_baseline_bank_is_the_curated_global_contract_and_engineering_bank():
    bank = questions.baseline()
    assert bank.model, "the bank names the screening model the run is made with (D26)"
    assert len(bank.questions) >= 16, (
        "the bank starts from the sixteen engineering principles (D49)"
    )
    ids = [question.id for question in bank.questions]
    assert len(ids) == len(set(ids)), "question ids are unique and stable (D51)"
    assert {question.id.split(".")[0] for question in bank.questions} == {
        "contract",
        "engineering",
    }, "the bank carries the contract and the engineering perspective independently (D03)"
    assert any(question.priority == "critical" for question in bank.questions), (
        "critical questions are always asked, so the baseline carries them (D09)"
    )
    for question in bank.questions:
        assert question.ask.strip(), f"{question.id} asks nothing"


def test_S1_a_configured_bank_is_read_from_yaml_with_its_model_and_questions(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    bank = questions.load(path)
    assert bank.model == "test-model-v1"
    assert [question.id for question in bank.questions] == [
        "contract.error-handling",
        "engineering.names-meaning",
    ]
    assert bank.questions[0].priority == "critical"
    assert bank.questions[1].type == "noul", "a question without a type is a noul (D50)"


@pytest.mark.parametrize(
    ("body", "named"),
    [
        ("questions: []\n", "model"),
        (
            "model: m\nquestions:\n  - id: contract.a\n    priority: critical\n    ask: Is it\n"
            "  - id: contract.a\n    priority: high\n    ask: Is it again\n",
            "questions[1].id",
        ),
        (
            "model: m\nquestions:\n  - id: contract.a\n    priority: urgent\n    ask: Is it\n",
            "questions[0].priority",
        ),
        (
            "model: m\nquestions:\n  - id: contract.a\n    priority: critical\n    type: prose\n"
            "    ask: Is it\n",
            "questions[0].type",
        ),
        ("model: m\nquestions:\n  - id: contract.a\n    priority: critical\n", "questions[0].ask"),
        ("model: m\nquestions:\n  - priority: critical\n    ask: Is it\n", "questions[0].id"),
    ],
)
def test_S1_an_invalid_bank_is_refused_naming_the_field(tmp_path, body, named):
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ReviewError) as refused:
        questions.load(path)
    assert named in str(refused.value), "a refusal names the field it refuses"
    assert refused.value.code == 2, "an invalid configuration is a usage error"


def test_S1_a_bank_that_asks_nothing_is_refused_naming_the_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("model: test-model-v1\nquestions: []\n", encoding="utf-8")
    with pytest.raises(ReviewError) as refused:
        questions.load(path)
    assert "config.yaml" in str(refused.value), "the refusal names the file it refused"
    assert "questions" in str(refused.value)
    assert refused.value.code == 2, "a screening that asks nothing is never exit 0"


def test_S1_a_missing_configuration_is_refused_naming_its_path(tmp_path):
    with pytest.raises(ReviewError) as refused:
        questions.load(tmp_path / "absent.yaml")
    assert "absent.yaml" in str(refused.value)


def test_S1_a_score_question_carries_the_levels_it_is_graded_on(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "model: jev-latest\nquestions:\n  - id: design.one-reason\n    priority: high\n"
        "    type: score\n    criteria:\n      - none\n      - severe\n    ask: How severe\n",
        encoding="utf-8",
    )
    (question,) = questions.load(path).questions
    assert question.type == "score"
    assert question.criteria == ["none", "severe"], "the levels travel with the question it grades"


def test_S1_a_question_type_outside_the_instruments_is_refused_naming_them(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "model: jev-latest\nquestions:\n  - id: contract.a\n    priority: critical\n"
        "    type: boolean\n    ask: Is it\n",
        encoding="utf-8",
    )
    with pytest.raises(ReviewError) as refused:
        questions.load(path)
    assert "questions[0].type" in str(refused.value)
    assert "noul" in str(refused.value), "a refusal names the instruments the bank may ask"
