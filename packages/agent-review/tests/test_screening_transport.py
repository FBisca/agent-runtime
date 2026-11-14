"""The default provider transport over a real socket: the request, the credential, the answer.

No check reaches a real screening provider: that costs a credential and a real charge, and the plan keeps
it human evidence. This drives the real `urllib` transport (`ProviderScreenings` with its own `post_json`)
against a local HTTP server that answers the way the provider does, so the code between the port and the
network - the URL, the Authorization header, the request body, the answer and the error path - is verified
without spending anything.
"""

import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from agent_review.errors import ScreeningError
from agent_review.questions import Question
from agent_review.screening import ProviderScreenings, ScreeningRequest

pytestmark = pytest.mark.integration

ANSWER = {
    "choices": [
        {
            "message": {
                "content": json.dumps(
                    {"judgments": [{"question": "contract.error-handling", "value": True}]}
                )
            }
        }
    ],
    "usage": {"prompt_tokens": 11, "completion_tokens": 3},
}

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
def _no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A local endpoint must be reached directly, whatever the machine's proxy variables say."""
    for variable in PROXY_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
        monkeypatch.delenv(variable.upper(), raising=False)


@pytest.fixture(name="serve")
def serve_fixture() -> Iterator[Callable[..., tuple[str, Canned]]]:
    """Start local provider endpoints; `serve(status, payload)` returns a URL and the request's record."""
    servers: list[tuple[ThreadingHTTPServer, threading.Thread]] = []

    def start(status: int = 200, payload: Any = None) -> tuple[str, Canned]:
        canned = Canned(status, ANSWER if payload is None else payload)

        class Handler(ProviderHandler):
            answer = canned

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_address[1]}/v1", canned

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def request() -> ScreeningRequest:
    """One screening of one question over a change."""
    return ScreeningRequest(
        model="test-model-v1",
        questions=(
            Question(
                id="contract.error-handling",
                priority="critical",
                type="boolean",
                ask="Does the change keep the documented error-handling contract",
            ),
        ),
        diff="+    except OSError:\n+        return None\n",
        base="a" * 40,
        head="b" * 40,
    )


def test_S1_the_default_transport_posts_the_bank_with_the_credential_and_reads_the_answer(
    serve,
):
    base_url, canned = serve()
    port = ProviderScreenings(
        model="test-model-v1", api_key="sk-local-test", base_url=base_url, timeout=10
    )
    reply = port.screen(request())

    assert canned.path == "/v1/chat/completions", "the port reaches the provider's completion path"
    assert canned.headers["Authorization"] == "Bearer sk-local-test", "the key travels as a bearer"
    sent = json.loads(canned.body)
    assert sent["model"] == "test-model-v1"
    assert "contract.error-handling" in json.dumps(sent), "the question travels in the request"
    assert "except OSError" in json.dumps(sent), "the change travels in the request"
    assert reply.judgments == ({"question": "contract.error-handling", "value": True},)
    assert (reply.input_tokens, reply.output_tokens) == (11, 3)


def test_S1_an_error_from_the_default_transport_keeps_the_provider_reason(serve):
    base_url, _ = serve(500, {"error": {"message": "screening backend exploded"}})
    port = ProviderScreenings(
        model="test-model-v1", api_key="sk-local-test", base_url=base_url, timeout=10
    )
    with pytest.raises(ScreeningError) as failed:
        port.screen(request())
    reason = str(failed.value)
    assert "500" in reason, "the failure names what the provider answered (D40)"
    assert "screening backend exploded" in reason, "the provider's own reason is kept"


def test_S1_a_transport_that_cannot_be_reached_is_a_screening_failure():
    port = ProviderScreenings(
        model="test-model-v1", api_key="sk-local-test", base_url="http://127.0.0.1:1/v1", timeout=2
    )
    with pytest.raises(ScreeningError) as failed:
        port.screen(request())
    assert "unreachable" in str(failed.value) or "failed" in str(failed.value)
