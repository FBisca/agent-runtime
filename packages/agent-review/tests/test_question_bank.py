"""The curated global question bank.

00-agent-review-screening-cli/S1: the tool evaluates a curated global engineering and contract question
bank. The bank is the packaged YAML baseline or a configured one; the providers it is asked through live
in test_screening_transport.py.
"""

import textwrap

import pytest
from agent_review import cli, questions, report
from agent_review.errors import ReviewError

pytestmark = pytest.mark.unit

CONFIG = textwrap.dedent(
    """\
    model: test-model-v1
    questions:
      - id: repo.error-handling
        priority: critical
        type: noul
        ask: Does the change keep the documented error-handling contract
      - id: repo.names-meaning
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


@pytest.mark.spec("Additive repository configuration")
def test_S1_a_configured_bank_is_read_from_yaml_with_its_model_and_questions(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    bank = questions.load(path)
    assert bank.model == "test-model-v1"
    assert [question.id for question in bank.questions][-2:] == [
        "repo.error-handling",
        "repo.names-meaning",
    ], "a configuration's questions follow the baseline's, in its own order"
    assert bank.questions[0].id == questions.baseline().questions[0].id
    assert bank.questions[-1].type == "noul", "a question without a type is a noul (D50)"


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


def test_a_safeguards_block_is_carried_as_configured(tmp_path):
    """A run's safeguards declaration belongs to the run, so the bank carries it beside the questions."""
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG + "safeguards:\n  stdlib_only:\n    - hooks/\n", encoding="utf-8")
    bank = questions.load(path)
    assert bank.safeguards == {"stdlib_only": ["hooks/"]}, (
        "the declaration is carried whole, so a rule reads it from one place"
    )
    assert [question.id for question in bank.questions][-2:] == [
        "repo.error-handling",
        "repo.names-meaning",
    ], "the safeguards block is not read as a question"


def test_a_bank_without_a_safeguards_block_carries_none(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    assert questions.load(path).safeguards == {}
    assert questions.baseline().safeguards == {}, "the packaged bank declares no safeguards"


def test_a_safeguards_block_that_is_not_a_mapping_is_refused_naming_it(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG + 'safeguards: "hooks/**"\n', encoding="utf-8")
    with pytest.raises(ReviewError) as refused:
        questions.load(path)
    assert "safeguards" in str(refused.value), "a refusal names the field it refuses"
    assert "mapping" in str(refused.value)
    assert refused.value.code == 2, "an invalid configuration is a usage error"


@pytest.mark.spec("Additive repository configuration")
def test_S1_an_empty_additions_list_asks_the_baseline_alone(tmp_path):
    """A configuration that adds no question asks the baseline, so an empty list is legal (D05)."""
    path = tmp_path / "config.yaml"
    path.write_text("model: test-model-v1\nquestions: []\n", encoding="utf-8")
    bank = questions.load(path)
    assert [question.id for question in bank.questions] == [
        question.id for question in questions.baseline().questions
    ], "a screening that adds nothing still asks every mandatory question"
    assert bank.model == "test-model-v1", (
        "the configuration still names the model it is screened with"
    )


@pytest.mark.spec("Additive repository configuration")
def test_S1_a_configuration_that_names_no_questions_asks_the_baseline_alone(tmp_path):
    """A configuration that only protects a path or recalibrates a threshold needs no question of its own."""
    path = tmp_path / "config.yaml"
    path.write_text("model: test-model-v1\nsensitive_paths:\n  - pkg/**\n", encoding="utf-8")
    bank = questions.load(path)
    assert len(bank.questions) == len(questions.baseline().questions)
    assert bank.sensitive_paths == ("pkg/**",), "the configuration's own protection travels"


@pytest.mark.spec("Additive repository configuration")
def test_S1_a_repository_question_that_repeats_a_baseline_id_is_refused_by_name(tmp_path):
    """Extending the baseline is not replacing it: a repeat is refused by the id it repeats (D05, D13)."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "model: test-model-v1\nquestions:\n  - id: contract.error-handling\n"
        "    priority: critical\n    ask: Is the error handling contract kept\n",
        encoding="utf-8",
    )
    with pytest.raises(ReviewError) as refused:
        questions.load(path)
    assert "contract.error-handling" in str(refused.value), "the refusal names the id it refuses"
    assert "questions[0].id" in str(refused.value), "the refusal names the field by its path"
    assert "thresholds" in str(refused.value), (
        "the refusal names the way out for a calibrated question"
    )
    assert refused.value.code == 2, "an invalid configuration is a usage error"


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
    (question,) = [
        item for item in questions.load(path).questions if item.id == "design.one-reason"
    ]
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
    - id: repo.hidden-state
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


@pytest.mark.spec("Question configuration validation")
def test_S2_a_score_question_without_levels_is_refused_by_path(tmp_path):
    refused = _refused(tmp_path, _bank(SCORE.replace(LEVELS, "")))
    assert "questions[0].criteria" in str(refused), "the refusal names the field by its path"
    assert "design.one-reason" in str(refused), "the refusal names the question it refuses"
    assert refused.code == 2, "an invalid configuration is a usage error"


@pytest.mark.spec("Question configuration validation")
def test_S2_a_noul_that_carries_a_level_list_is_refused_by_path(tmp_path):
    refused = _refused(tmp_path, _bank(SCORE, NOUL.replace("      ask:", LEVELS + "      ask:")))
    assert "questions[1].criteria" in str(refused), "the misplaced field is named where it stands"
    assert "repo.hidden-state" in str(refused)
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
@pytest.mark.spec("Question configuration validation")
def test_S2_a_score_criteria_that_is_not_a_ladder_of_levels_is_refused_by_path(tmp_path, criteria):
    refused = _refused(tmp_path, _bank(SCORE.replace(LEVELS, criteria)))
    assert "questions[0].criteria" in str(refused)
    assert refused.code == 2


@pytest.mark.spec("Question configuration validation")
def test_S2_a_noul_may_declare_criteria_as_a_mapping(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_bank(NOUL.replace("      ask:", OPTIONS + "      ask:")), encoding="utf-8")
    (question,) = [
        item for item in questions.load(path).questions if item.id == "repo.hidden-state"
    ]
    assert question.criteria == {
        "bounded": "the state it owns is named",
        "hidden": "it reaches for state it does not own",
    }, "a noul's criteria are optional and travel as configured"


@pytest.mark.spec("Question configuration validation")
def test_S2_a_choice_question_carries_its_options_as_criteria(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        _bank(
            "    - id: change.kind\n      priority: critical\n      type: choice\n"
            f"{OPTIONS}      ask: What kind of change is this\n"
        ),
        encoding="utf-8",
    )
    (question,) = [item for item in questions.load(path).questions if item.id == "change.kind"]
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
@pytest.mark.spec("Question configuration validation")
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


@pytest.mark.spec("Question configuration validation")
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


# The numbers a screening is decided with are configuration, declared beside the bank and never chosen by
# the model (D07, D26, D48): per-question weights, and the value and confidence band each answer is read
# against. The packaged baseline declares its calibration; a repository lays its own over it per question.
@pytest.mark.spec("Additive repository configuration")
def test_S1_the_baseline_declares_the_thresholds_it_is_decided_with():
    bank = questions.baseline()
    calibrated = bank.threshold("contract.error-handling")
    assert calibrated is not None, (
        "a calibrated question declares the numbers its answer is read against"
    )
    assert (calibrated.value, calibrated.confidence) == (1.5, 0.7), (
        "the numbers are the reference set's operating point, frozen beside the bank (D26)"
    )
    assert bank.threshold("engineering.names-meaning") is None, (
        "a question no calibration moved carries no threshold, so it is recorded and never escalated alone"
    )
    assert bank.weight("contract.error-handling") == questions.DEFAULT_WEIGHT, (
        "a question no configuration weighs carries the default"
    )


@pytest.mark.spec("Additive repository configuration")
def test_S1_a_repository_lays_its_weights_and_thresholds_over_the_baseline(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            model: test-model-v1
            weights:
              repo.one-reason: 3
            thresholds:
              contract.error-handling:
                value: 2.5
              repo.one-reason:
                value: 1.5
                confidence: 0.6
            questions:
              - id: repo.one-reason
                priority: high
                type: score
                criteria: [none, severe]
                ask: How severely does the change violate the one-reason rule
            """
        ),
        encoding="utf-8",
    )
    bank = questions.load(path)
    assert bank.weight("repo.one-reason") == 3, "the repository weighs its own question"
    assert bank.threshold("repo.one-reason") == questions.Threshold(value=1.5, confidence=0.6)
    recalibrated = bank.threshold("contract.error-handling")
    assert recalibrated is not None and recalibrated.value == 2.5, (
        "a repository recalibrates the question it names"
    )
    assert bank.threshold("engineering.survival-priority") is not None, (
        "a question it does not name keeps the baseline's calibration"
    )


@pytest.mark.spec("Additive repository configuration")
def test_S1_a_threshold_may_name_a_question_that_this_run_does_not_ask(tmp_path):
    """A repository carries the calibration of its whole catalogue and asks part of it per run."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "model: test-model-v1\nquestions: []\nthresholds:\n  repo.not-asked:\n    value: 1.0\n",
        encoding="utf-8",
    )
    bank = questions.load(path)
    assert bank.threshold("repo.not-asked") == questions.Threshold(value=1.0)


@pytest.mark.parametrize(
    ("body", "named"),
    [
        ("thresholds: [1.0]\n", "thresholds"),
        ("thresholds:\n  repo.a: 1.0\n", "thresholds['repo.a']"),
        ("thresholds:\n  repo.a:\n    escalation: 1.0\n", "escalation"),
        ("thresholds:\n  repo.a: {}\n", "thresholds['repo.a']"),
        ("thresholds:\n  repo.a:\n    value: -1\n", "thresholds['repo.a'].value"),
        ("thresholds:\n  repo.a:\n    value: true\n", "thresholds['repo.a'].value"),
        ("thresholds:\n  repo.a:\n    confidence: 1.5\n", "thresholds['repo.a'].confidence"),
        ("weights:\n  repo.a: -2\n", "weights['repo.a']"),
        ("weights:\n  Repo.A: 1\n", "weights"),
        ("weights: [1]\n", "weights"),
    ],
    ids=[
        "not-a-mapping",
        "not-a-threshold",
        "unknown-field",
        "empty-threshold",
        "negative-value",
        "boolean-value",
        "band-above-one",
        "negative-weight",
        "not-an-id",
        "weights-not-a-mapping",
    ],
)
@pytest.mark.spec("Additive repository configuration")
def test_S1_a_calibration_no_rule_can_read_is_refused_by_path(tmp_path, body, named):
    path = tmp_path / "config.yaml"
    path.write_text("model: test-model-v1\nquestions: []\n" + body, encoding="utf-8")
    with pytest.raises(ReviewError) as refused:
        questions.load(path)
    assert named in str(refused.value), "a refusal names the field by its path"
    assert refused.value.code == 2, "a configuration no rule can honour is a usage error"


@pytest.mark.spec("Additive repository configuration")
def test_S1_the_loaded_bank_carries_the_weights_and_thresholds_it_declares(tmp_path):
    """A repository's numbers reach the run through the bank, beside the questions (D07, D48)."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "model: test-model-v1\nquestions: []\nweights:\n  contract.error-handling: 3\n"
        "thresholds:\n  contract.error-handling:\n    value: 1.5\n",
        encoding="utf-8",
    )
    bank = questions.load(path)
    assert bank.weight("contract.error-handling") == 3
    assert bank.threshold("contract.error-handling") == questions.Threshold(value=1.5), (
        "the value an answer must clear is carried beside the questions it grades"
    )
    assert {question.id for question in bank.questions} >= {"contract.error-handling"}


@pytest.mark.spec("Additive repository configuration")
def test_S1_a_recalibrated_threshold_is_a_different_bank():
    """A frozen report is compared with the calibration it was made under, so the digest covers it (D26)."""
    bank = questions.baseline()
    same = report.bank_digest(bank.model, bank.questions, bank.weights, bank.thresholds)
    recalibrated = dict(bank.thresholds)
    recalibrated["contract.error-handling"] = questions.Threshold(value=9.0)
    assert report.bank_digest(bank.model, bank.questions, bank.weights, recalibrated) != same, (
        "the same questions read against other numbers are not the same bank"
    )


@pytest.mark.parametrize(
    ("body", "named"),
    [
        ("chunk: banana\n", "chunk must be a mapping"),
        ("chunk: {max_diff_tokens: banana}\n", "chunk['max_diff_tokens']"),
        ("cross_file: {max_files: 0}\n", "cross_file['max_files']"),
        ("cross_file: [1]\n", "cross_file must be a mapping"),
    ],
    ids=["not-a-mapping", "a-name-not-a-budget", "a-budget-below-one", "a-list"],
)
@pytest.mark.spec("Additive repository configuration")
def test_S1_a_budget_no_stage_can_read_is_refused_by_path(tmp_path, body, named):
    """`chunk` and `cross_file` are honoured by the chunking stage, but their shape is refused here."""
    path = tmp_path / "config.yaml"
    path.write_text("model: test-model-v1\nquestions: []\n" + body, encoding="utf-8")
    with pytest.raises(ReviewError) as refused:
        questions.load(path)
    assert named in str(refused.value), "the refusal names the budget by its path"
    assert refused.value.code == 2, "a budget no stage can read is a usage error"


@pytest.mark.spec("Additive repository configuration")
def test_S1_a_declared_chunk_budget_is_carried_beside_the_bank(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "model: test-model-v1\nquestions: []\nchunk: {max_diff_tokens: 400}\n"
        "cross_file: {max_files: 40}\n",
        encoding="utf-8",
    )
    bank = questions.load(path)
    assert bank.chunk == {"max_diff_tokens": 400}
    assert bank.cross_file == {"max_files": 40}
