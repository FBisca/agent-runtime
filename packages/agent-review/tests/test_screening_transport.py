"""The System One transport over a real socket: the request, the credential, the header and the answer.

No check reaches a real screening provider: that costs a credential and a real charge, and the plan keeps
it human evidence. This drives the real `urllib` transport (`SystemOneScreenings` with its own `post_json`)
against a local HTTP server that answers the way System One does, so the code between the port and the
network - the route, the Authorization header, the typed request body, the answers and the error path - is
verified without spending anything.
"""

import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler
from typing import Any

import pytest
from agent_review.errors import ReviewError, ScreeningError
from agent_review.questions import Question
from agent_review.screening import FixtureScreenings, ScreeningRequest, SystemOneScreenings
from local_http import LocalServer

pytestmark = pytest.mark.integration

ANSWER = {
    "model": "jev-1.14.0",
    "answers": {"contract.error-handling": {"type": "noul", "noul": 0.83}},
    "usage": {"input_tokens": 11, "output_tokens": 3},
}

DIFF = "+    except OSError:\n+        return None\n"

PROXY_VARIABLES = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")


class Canned:
    """One canned answer the local server gives, and the request it received."""

    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.payload = payload
        self.path = ""
        self.headers: dict[str, str] = {}
        self.body = b""


class ProviderHandler(BaseHTTPRequestHandler):
    """Answers one POST the way the provider does, and records what it received."""

    answer: Canned

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.answer.path = self.path
        self.answer.headers = dict(self.headers.items())
        self.answer.body = self.rfile.read(length)
        body = json.dumps(self.answer.payload).encode("utf-8")
        self.send_response(self.answer.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """The server's access log is noise in a test run."""


@pytest.fixture(autouse=True)
def _no_ambient_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """The machine's own credential or proxy must never decide what a check sends."""
    for variable in PROXY_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
        monkeypatch.delenv(variable.upper(), raising=False)
    for variable in ("TYPESAFE_API_KEY", "AGENT_REVIEW_API_KEY", "AGENT_REVIEW_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture(name="serve")
def serve_fixture() -> Iterator[Callable[..., tuple[str, Canned]]]:
    """Start local provider endpoints; `serve(status, payload)` returns a URL and the request's record."""
    servers: list[tuple[LocalServer, threading.Thread]] = []

    def start(status: int = 200, payload: Any = None) -> tuple[str, Canned]:
        canned = Canned(status, ANSWER if payload is None else payload)

        class Handler(ProviderHandler):
            answer = canned

        server = LocalServer(("127.0.0.1", 0), Handler)
        thread = server.serve_in_background()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_address[1]}/v1", canned

    yield start
    for server, thread in servers:
        server.stop(thread)


def scored_request() -> ScreeningRequest:
    """One screening asking a graded question with its levels and a bounded yes/no question beside it."""
    return ScreeningRequest(
        model="jev-latest",
        questions=(
            Question(
                id="design.one-reason",
                priority="high",
                type="score",
                ask="How severely does the change violate one reason to change",
                criteria=["none", "minor", "severe"],
            ),
            Question(
                id="contract.error-handling",
                priority="critical",
                type="noul",
                ask="Does the change keep the documented error-handling contract",
            ),
        ),
        diff=DIFF,
        base="a" * 40,
        head="b" * 40,
    )


def header(headers: dict[str, str], name: str) -> str | None:
    """One header by name, whatever case it travelled in."""
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def request() -> ScreeningRequest:
    """One screening of one question over a change."""
    return ScreeningRequest(
        model="jev-latest",
        questions=(
            Question(
                id="contract.error-handling",
                priority="critical",
                type="noul",
                ask="Does the change keep the documented error-handling contract",
            ),
        ),
        diff=DIFF,
        base="a" * 40,
        head="b" * 40,
    )


@pytest.mark.spec("System One transport")
def test_S1_the_transport_posts_the_typed_request_and_reads_the_answers(serve):
    base_url, canned = serve()
    port = SystemOneScreenings(
        model="jev-latest", api_key="sk-local-test", base_url=base_url, timeout=10
    )
    reply = port.screen(request())

    assert canned.path == "/v1/systemone", "the port reaches System One's route"
    assert header(canned.headers, "Authorization") == "Bearer sk-local-test", (
        "the key travels as a bearer"
    )
    sent = json.loads(canned.body)
    assert sent["model"] == "jev-latest"
    assert sent["state"] == {"base": "a" * 40, "head": "b" * 40, "diff": DIFF}, (
        "the state is the change as one object, whole"
    )
    assert sent["questions"] == {
        "contract.error-handling": {
            "type": "noul",
            "instructions": "Does the change keep the documented error-handling contract",
        }
    }, "the questions are an id -> typed question map"
    assert reply.judgments == ({"question": "contract.error-handling", "value": 0.83},)
    assert (reply.input_tokens, reply.output_tokens) == (11, 3)


def test_S1_each_answer_type_keeps_the_provider_its_native_value(serve):
    base_url, _ = serve(
        200,
        {
            "model": "jev-1.14.0",
            "answers": {
                "contract.error-handling": {"type": "noul", "noul": 0.5},
                "design.one-reason": {"type": "score", "score": 2.0, "confidence": 0.8},
            },
            "usage": {"input_tokens": 1, "output_tokens": 2},
        },
    )
    reply = SystemOneScreenings(
        model="jev-latest", api_key="sk-local-test", base_url=base_url, timeout=10
    ).screen(request())
    assert reply.judgments == (
        {"question": "contract.error-handling", "value": 0.5},
        {"question": "design.one-reason", "value": 2.0, "confidence": 0.8},
    ), "a noul keeps its probability, a score its level and the confidence beside it (D44)"


@pytest.mark.spec("System One transport")
def test_S1_the_typesafe_credential_is_sent_when_a_fallback_is_also_present(serve, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-key")
    monkeypatch.setenv("AGENT_REVIEW_API_KEY", "fallback-key")
    base_url, canned = serve()
    SystemOneScreenings(model="jev-latest", base_url=base_url, timeout=10).screen(request())
    assert header(canned.headers, "Authorization") == "Bearer typesafe-key"


@pytest.mark.spec("System One transport")
def test_S1_the_fallback_credential_is_sent_only_when_typesafe_is_absent(serve, monkeypatch):
    monkeypatch.setenv("AGENT_REVIEW_API_KEY", "fallback-key")
    base_url, canned = serve()
    SystemOneScreenings(model="jev-latest", base_url=base_url, timeout=10).screen(request())
    assert header(canned.headers, "Authorization") == "Bearer fallback-key"


@pytest.mark.spec("System One transport")
def test_S1_a_missing_credential_names_typesafe_api_key():
    port = SystemOneScreenings(model="jev-latest", base_url="http://127.0.0.1:1/v1", timeout=2)
    with pytest.raises(ScreeningError) as failed:
        port.screen(request())
    reason = str(failed.value)
    assert "TYPESAFE_API_KEY" in reason, "the refusal names the credential to set"
    assert "fixture" in reason, "the refusal names the way out"


@pytest.mark.spec("Provider input limit")
def test_S5_a_long_context_request_carries_the_danger_zone_header(serve):
    base_url, canned = serve()
    SystemOneScreenings(
        model="jev-latest",
        api_key="sk-local-test",
        base_url=base_url,
        long_context=True,
        timeout=10,
    ).screen(request())
    assert header(canned.headers, "TypeSafe-Danger-Zone-Long-Context") == "true"


@pytest.mark.spec("Provider input limit")
def test_S5_the_default_request_carries_no_long_context_header(serve):
    base_url, canned = serve()
    SystemOneScreenings(
        model="jev-latest", api_key="sk-local-test", base_url=base_url, timeout=10
    ).screen(request())
    assert header(canned.headers, "TypeSafe-Danger-Zone-Long-Context") is None


@pytest.mark.spec("Provider input limit")
def test_S5_an_oversized_request_keeps_the_provider_reason(serve):
    base_url, _ = serve(400, {"detail": {"error_type": "max_tokens_exceeded"}})
    port = SystemOneScreenings(
        model="jev-latest", api_key="sk-local-test", base_url=base_url, timeout=10
    )
    with pytest.raises(ScreeningError) as failed:
        port.screen(request())
    assert "max_tokens_exceeded" in str(failed.value), "the provider's own reason is kept"


def test_S1_an_error_from_the_transport_keeps_the_provider_reason(serve):
    base_url, _ = serve(500, {"error": {"message": "screening backend exploded"}})
    port = SystemOneScreenings(
        model="jev-latest", api_key="sk-local-test", base_url=base_url, timeout=10
    )
    with pytest.raises(ScreeningError) as failed:
        port.screen(request())
    reason = str(failed.value)
    assert "500" in reason, "the failure names what the provider answered (D40)"
    assert "screening backend exploded" in reason, "the provider's own reason is kept"


def test_S1_an_answer_without_answers_is_a_screening_failure(serve):
    base_url, _ = serve(200, {"model": "jev-1.14.0", "usage": {"input_tokens": 1}})
    port = SystemOneScreenings(
        model="jev-latest", api_key="sk-local-test", base_url=base_url, timeout=10
    )
    with pytest.raises(ScreeningError, match="answers"):
        port.screen(request())


def test_S1_a_transport_that_cannot_be_reached_is_a_screening_failure():
    port = SystemOneScreenings(
        model="jev-latest", api_key="sk-local-test", base_url="http://127.0.0.1:1/v1", timeout=2
    )
    with pytest.raises(ScreeningError) as failed:
        port.screen(request())
    assert "unreachable" in str(failed.value) or "failed" in str(failed.value)


def test_S1_a_base_url_that_is_not_http_is_refused(monkeypatch):
    from agent_review.errors import ReviewError

    monkeypatch.setenv("AGENT_REVIEW_BASE_URL", "file:///etc/passwd")
    with pytest.raises(ReviewError, match="http"):
        SystemOneScreenings(model="jev-latest", api_key="sk-local-test")


def canned(tmp_path, *responses):
    """A screening fixture holding `responses`, in the order the canned provider answers them."""
    path = tmp_path / "screening.json"
    path.write_text(json.dumps({"responses": list(responses)}), encoding="utf-8")
    return path


@pytest.mark.spec("Offline canned provider")
def test_S7_the_canned_provider_answers_the_canned_responses_in_order(tmp_path):
    port = FixtureScreenings(canned(tmp_path, ANSWER))
    reply = port.screen(request())
    assert reply.judgments == ({"question": "contract.error-handling", "value": 0.83},)
    assert (reply.input_tokens, reply.output_tokens) == (11, 3)
    with pytest.raises(ScreeningError, match="no response left"):
        port.screen(request())


def test_S7_a_canned_provider_error_is_a_screening_failure_with_its_reason(tmp_path):
    with pytest.raises(ScreeningError, match="500"):
        FixtureScreenings(canned(tmp_path, {"error": "provider answered 500"})).screen(request())


@pytest.mark.spec("Offline canned provider")
def test_S7_a_fixture_answer_in_another_shape_is_refused(tmp_path):
    with pytest.raises(ScreeningError, match="answers"):
        FixtureScreenings(
            canned(tmp_path, {"judgments": [{"question": "contract.error-handling", "value": 1}]})
        ).screen(request())


@pytest.mark.spec("Offline canned provider")
def test_S7_a_malformed_fixture_is_refused_naming_the_file(tmp_path):
    path = tmp_path / "screening.json"
    path.write_text(json.dumps({"answers": []}), encoding="utf-8")
    with pytest.raises(ReviewError) as refused:
        FixtureScreenings(path)
    assert "responses" in str(refused.value)
    assert refused.value.code == 2


def test_S1_a_graded_question_carries_its_levels_to_the_provider(serve):
    base_url, canned = serve(
        200,
        {
            "model": "jev-1.14.0",
            "answers": {
                "design.one-reason": {"type": "score", "score": 1.0, "confidence": 0.8},
                "contract.error-handling": {"type": "noul", "noul": 0.6},
            },
            "usage": {"input_tokens": 1, "output_tokens": 2},
        },
    )
    SystemOneScreenings(
        model="jev-latest", api_key="sk-local-test", base_url=base_url, timeout=10
    ).screen(scored_request())
    asked = json.loads(canned.body)["questions"]
    assert asked["design.one-reason"]["criteria"] == ["none", "minor", "severe"]
    assert asked["design.one-reason"]["type"] == "score"
    assert "criteria" not in asked["contract.error-handling"], "a noul needs none declared"


@pytest.mark.spec("Report provenance")
def test_S4_the_reply_carries_the_model_the_provider_served(serve):
    base_url, _ = serve()
    reply = SystemOneScreenings(
        model="jev-latest", api_key="sk-local-test", base_url=base_url, timeout=10
    ).screen(request())
    assert reply.model == "jev-1.14.0", (
        "the model the provider served is read from its answer, never inferred from the alias"
    )


@pytest.mark.spec("Report provenance")
def test_S4_the_transport_names_the_endpoint_it_posts_to(serve):
    base_url, _ = serve()
    port = SystemOneScreenings(
        model="jev-latest", api_key="sk-local-test", base_url=base_url, timeout=10
    )
    assert port.provider == base_url, "the report names the endpoint it called, not a default"


@pytest.mark.spec("Report provenance")
def test_S4_the_canned_provider_names_the_fixture_it_answers_from(tmp_path):
    path = canned(tmp_path, ANSWER)
    assert FixtureScreenings(path).provider == f"fixture:{path}", (
        "a canned screening names its own source, so it is never read as a provider's"
    )
