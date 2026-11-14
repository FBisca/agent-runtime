"""The curated global question bank.

00-agent-review-screening-cli/S1: the tool evaluates a curated global engineering and contract question
bank. The bank is the packaged YAML baseline or a configured one; the providers it is asked through live
in test_screening_transport.py.
"""

import textwrap

import pytest
from agent_review import cli, questions
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


# The criteria an instrument needs are its own shape (00b-D1): a score is graded on a ladder of levels, a
# noul may declare criteria as a mapping, and a choice declares its options. A missing or misplaced one is
# refused by path before any request is made.
LEVELS = """\
      criteria:
        - none
        - severe
"""

SCORE = f"""\
    - id: design.one-reason
      priority: high
      type: score
{LEVELS}      ask: How severely does it violate the one-reason rule
"""

NOUL = """\
    - id: engineering.hidden-state
      priority: high
      ask: Does the change keep its state explicit
"""

OPTIONS = """\
      criteria:
        bounded: the state it owns is named
        hidden: it reaches for state it does not own
"""


def _bank(*questions: str) -> str:
    return "model: jev-latest\nquestions:\n" + "".join(questions)


def _refused(tmp_path, body: str) -> ReviewError:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ReviewError) as refused:
        questions.load(path)
    return refused.value


def test_S2_a_score_question_without_levels_is_refused_by_path(tmp_path):
    refused = _refused(tmp_path, _bank(SCORE.replace(LEVELS, "")))
    assert "questions[0].criteria" in str(refused), "the refusal names the field by its path"
    assert "design.one-reason" in str(refused), "the refusal names the question it refuses"
    assert refused.code == 2, "an invalid configuration is a usage error"


def test_S2_a_noul_that_carries_a_level_list_is_refused_by_path(tmp_path):
    refused = _refused(tmp_path, _bank(SCORE, NOUL.replace("      ask:", LEVELS + "      ask:")))
    assert "questions[1].criteria" in str(refused), "the misplaced field is named where it stands"
    assert "engineering.hidden-state" in str(refused)
    assert refused.code == 2


@pytest.mark.parametrize(
    "criteria",
    [
        "      criteria: {none: nothing}\n",
        "      criteria: []\n",
        "      criteria:\n        - none\n",
        "      criteria:\n        - none\n        - \n",
    ],
    ids=["mapping", "empty", "one-level", "blank-level"],
)
def test_S2_a_score_criteria_that_is_not_a_ladder_of_levels_is_refused_by_path(tmp_path, criteria):
    refused = _refused(tmp_path, _bank(SCORE.replace(LEVELS, criteria)))
    assert "questions[0].criteria" in str(refused)
    assert refused.code == 2


def test_S2_a_noul_may_declare_criteria_as_a_mapping(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_bank(NOUL.replace("      ask:", OPTIONS + "      ask:")), encoding="utf-8")
    (question,) = questions.load(path).questions
    assert question.criteria == {
        "bounded": "the state it owns is named",
        "hidden": "it reaches for state it does not own",
    }, "a noul's criteria are optional and travel as configured"


def test_S2_a_choice_question_carries_its_options_as_criteria(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        _bank(
            "    - id: change.kind\n      priority: critical\n      type: choice\n"
            f"{OPTIONS}      ask: What kind of change is this\n"
        ),
        encoding="utf-8",
    )
    (question,) = questions.load(path).questions
    assert question.criteria == {
        "bounded": "the state it owns is named",
        "hidden": "it reaches for state it does not own",
    }, "a choice declares the options it is answered with"


@pytest.mark.parametrize(
    "criteria",
    [
        "",
        "      criteria:\n        - bounded\n        - hidden\n",
        "      criteria:\n        - bounded\n",
    ],
    ids=["missing", "level-list", "one-option"],
)
def test_S2_a_choice_without_its_options_is_refused_by_path(tmp_path, criteria):
    refused = _refused(
        tmp_path,
        _bank(
            "    - id: change.kind\n      priority: critical\n      type: choice\n"
            f"{criteria}      ask: What kind of change is this\n"
        ),
    )
    assert "questions[0].criteria" in str(refused)
    assert "change.kind" in str(refused)
    assert refused.code == 2


def test_S2_a_refused_bank_never_reaches_a_provider(tmp_path, monkeypatch):
    """The bank is read before the provider is built and before the change, so a refusal costs no request."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("a refused question bank reached a provider")

    monkeypatch.setattr(cli, "_port", refuse)
    path = tmp_path / "config.yaml"
    path.write_text(_bank(SCORE.replace(LEVELS, "")), encoding="utf-8")
    output = tmp_path / "review"
    args = cli.build_parser().parse_args(
        ["code", "--base", "HEAD", "--head", "HEAD", "--output", str(output), "--config", str(path)]
    )
    with pytest.raises(ReviewError) as refused:
        cli.screen(args)
    assert "questions[0].criteria" in str(refused.value)
    assert not output.exists(), "a refusal screens nothing and writes nothing"
