"""The curated global question bank and the screening port it is asked through.

00-agent-review-screening-cli/S1: the tool evaluates a curated global engineering and contract question
bank through a direct screening API. The bank is the packaged YAML baseline or a configured one, and the
port is the one point where a provider (the API, or a canned fixture) is reached.
"""

import json
import textwrap

import pytest
from agent_review import questions
from agent_review.errors import ReviewError, ScreeningError
from agent_review.screening import (
    FixtureScreenings,
    ProviderScreenings,
    ScreeningRequest,
)

pytestmark = pytest.mark.unit

CONFIG = textwrap.dedent(
    """\
    model: test-model-v1
    questions:
      - id: contract.error-handling
        priority: critical
        type: boolean
        ask: Does the change keep the documented error-handling contract
      - id: engineering.names-meaning
        priority: advisory
        ask: Do the new names say what they mean
    """
)


def request(diff="+    except OSError:\n+        return None\n") -> ScreeningRequest:
    """One screening of the bank this module's CONFIG declares."""
    return ScreeningRequest(
        model="test-model-v1",
        questions=(
            questions.Question(
                id="contract.error-handling",
                priority="critical",
                type="boolean",
                ask="Does the change keep the documented error-handling contract",
            ),
        ),
        diff=diff,
        base="a" * 40,
        head="b" * 40,
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
    assert bank.questions[1].type == "boolean", "a question without a type is a boolean (D50)"


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


def test_S1_the_fixture_provider_answers_the_canned_responses_in_order(tmp_path):
    fixture = tmp_path / "screening.json"
    fixture.write_text(
        json.dumps(
            {
                "responses": [
                    {
                        "judgments": [{"question": "contract.error-handling", "value": True}],
                        "usage": {"input_tokens": 40, "output_tokens": 7},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    port = FixtureScreenings(fixture)
    reply = port.screen(request())
    assert reply.judgments == ({"question": "contract.error-handling", "value": True},)
    assert (reply.input_tokens, reply.output_tokens) == (40, 7)
    with pytest.raises(ScreeningError) as exhausted:
        port.screen(request())
    assert "no response left" in str(exhausted.value)


def test_S1_a_canned_provider_error_is_a_screening_failure_with_its_reason(tmp_path):
    fixture = tmp_path / "screening.json"
    fixture.write_text(json.dumps({"responses": [{"error": "provider answered 500"}]}), "utf-8")
    with pytest.raises(ScreeningError) as failed:
        FixtureScreenings(fixture).screen(request())
    assert "500" in str(failed.value), "the failure reason is observable (D40)"


def test_S1_a_malformed_fixture_is_refused_naming_the_file(tmp_path):
    fixture = tmp_path / "screening.json"
    fixture.write_text(json.dumps({"answers": []}), encoding="utf-8")
    with pytest.raises(ReviewError) as refused:
        FixtureScreenings(fixture)
    assert "responses" in str(refused.value)
    assert refused.value.code == 2


def test_S1_the_provider_posts_the_bank_and_the_change_to_the_screening_api():
    posted = {}

    def post(url, payload, headers, timeout):
        posted.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"judgments": [{"question": "contract.error-handling", "value": False}]}
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 9},
        }

    port = ProviderScreenings(model="test-model-v1", api_key="sk-test", post=post)
    reply = port.screen(request())

    assert posted["url"] == "https://api.openai.com/v1/chat/completions"
    assert posted["headers"]["Authorization"] == "Bearer sk-test"
    assert posted["payload"]["model"] == "test-model-v1"
    sent = json.dumps(posted["payload"])
    assert "contract.error-handling" in sent, "the question travels in the request"
    assert "except OSError" in sent, "the change travels in the request"
    assert reply.judgments == ({"question": "contract.error-handling", "value": False},)
    assert (reply.input_tokens, reply.output_tokens) == (120, 9)


@pytest.mark.parametrize(
    "answer",
    [
        {"choices": [{"message": {"content": "not json"}}]},
        {"choices": []},
        {"choices": [{"message": {"content": json.dumps({"answers": []})}}]},
    ],
)
def test_S1_an_answer_that_is_not_a_judgment_list_is_a_screening_failure(answer):
    port = ProviderScreenings(model="test-model-v1", api_key="sk-test", post=lambda *_: answer)
    with pytest.raises(ScreeningError) as failed:
        port.screen(request())
    assert "judgments" in str(failed.value) or "answer" in str(failed.value)


def test_S1_a_missing_api_key_is_a_screening_failure_naming_the_variable(monkeypatch):
    monkeypatch.delenv("AGENT_REVIEW_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def post(*_args):
        raise AssertionError("a request was sent without a key")

    with pytest.raises(ScreeningError) as failed:
        ProviderScreenings(model="test-model-v1", post=post).screen(request())
    assert "AGENT_REVIEW_API_KEY" in str(failed.value)


def test_S1_a_base_url_that_is_not_http_is_refused_naming_the_variable(monkeypatch):
    monkeypatch.setenv("AGENT_REVIEW_BASE_URL", "file:///etc/passwd")
    with pytest.raises(ReviewError) as refused:
        ProviderScreenings(model="test-model-v1", api_key="sk-test", post=lambda *_: {})
    assert "AGENT_REVIEW_BASE_URL" in str(refused.value), "the refusal names the way out"
