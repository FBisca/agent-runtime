"""The screening port: the one injected point where a provider is reached (D37, P0-D2).

The port takes the frozen change with the bank, and returns the provider's native judgments and its token
usage. It never retries, never falls back to another model and never escalates (D40): a provider error,
a timeout or an answer that is not a judgment list is a `ScreeningError` whose message is the reason.
"""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent_review.errors import ReviewError, ScreeningError
from agent_review.questions import Question

DEFAULT_BASE_URL = "https://api.openai.com/v1"
API_KEY_VARIABLES = ("AGENT_REVIEW_API_KEY", "OPENAI_API_KEY")
DEFAULT_TIMEOUT = 120.0

SYSTEM_PROMPT = (
    "You screen one code change against a configured question bank. Answer every question that the "
    "change lets you assess with its native bounded value: true or false for a boolean question, the "
    "declared option for a choice question, a number between 0 and 1 for a score question. You never "
    "invent reasons, citations or findings, and you never guess: leave out a question you cannot "
    'assess. Answer with one JSON object, {"judgments": [{"question": "<id>", "value": <value>}]}.'
)

PostJson = Callable[[str, Mapping[str, Any], Mapping[str, str], float], Any]


@dataclass(frozen=True)
class ScreeningRequest:
    """One screening: the model, the questions in configuration order, and the frozen change."""

    model: str
    questions: tuple[Question, ...]
    diff: str
    base: str
    head: str


@dataclass(frozen=True)
class ScreeningReply:
    """What the provider answered as it answered it: native values, unnormalized (D44).

    A judgment is the provider's object as returned; the caller attaches the question's provenance and
    refuses a value the question's type does not allow.
    """

    judgments: tuple[Mapping[str, Any], ...]
    input_tokens: int = 0
    output_tokens: int = 0


class ScreeningPort(Protocol):
    """The one seam a screening reaches: the direct API, or a canned provider replacing it."""

    # `...` is a protocol member's body: it carries the signature and no caller runs it.
    def screen(self, request: ScreeningRequest) -> ScreeningReply: ...


class ProviderScreenings:
    """The direct provider API: one bounded request, over an OpenAI-compatible chat completion (D37)."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        post: PostJson | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = _base_url(base_url)
        self.timeout = timeout
        self.post: PostJson = post if post is not None else post_json

    def screen(self, request: ScreeningRequest) -> ScreeningReply:
        api_key = self.api_key or _env_api_key()
        if not api_key:
            raise ScreeningError(
                "no screening API key: set AGENT_REVIEW_API_KEY (or OPENAI_API_KEY) to call the "
                "provider, or --screening-fixture to screen from canned responses"
            )
        payload = {
            "model": request.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": render_prompt(request)},
            ],
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        answer = self.post(f"{self.base_url}/chat/completions", payload, headers, self.timeout)
        return ScreeningReply(
            judgments=_judgments(answer),
            input_tokens=_tokens(answer, "prompt_tokens"),
            output_tokens=_tokens(answer, "completion_tokens"),
        )


class FixtureScreenings:
    """A canned-response provider behind the same port, so a failure is injectable (P0-D2, D40)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.responses = _responses(self.path)
        self.asked: list[ScreeningRequest] = []

    def screen(self, request: ScreeningRequest) -> ScreeningReply:
        self.asked.append(request)
        if not self.responses:
            raise ScreeningError(
                f"the screening fixture {self.path} has no response left for request "
                f"{len(self.asked)}"
            )
        response = self.responses.pop(0)
        if not isinstance(response, Mapping):
            raise ScreeningError(
                f"the screening fixture {self.path} holds a response that is not an object"
            )
        if "error" in response:
            raise ScreeningError(str(response["error"]))
        judgments = response.get("judgments")
        if not isinstance(judgments, list):
            raise ScreeningError(
                f"the screening fixture {self.path} holds a response with no judgments list"
            )
        usage = response.get("usage")
        return ScreeningReply(
            judgments=_objects(judgments, f"the screening fixture {self.path}"),
            input_tokens=_count(usage, "input_tokens"),
            output_tokens=_count(usage, "output_tokens"),
        )


def render_prompt(request: ScreeningRequest) -> str:
    """The request's user turn: the questions, how to answer them, and the frozen diff."""
    lines = [
        f"# Questions ({len(request.questions)})",
        "Answer each question in its native value; leave out a question the change cannot answer.",
    ]
    for question in request.questions:
        lines.append(f"- {question.id} ({question.type}, {question.priority}): {question.ask}")
    lines.extend(
        [
            "",
            "Answer with one JSON object: "
            '{"judgments": [{"question": "<id>", "value": <native value>}]}',
            "",
            f"# The change between {request.base} and {request.head}",
            "```diff",
            request.diff.rstrip("\n"),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def post_json(
    url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float
) -> Any:
    """POST `payload` as JSON and read the JSON answer; every transport failure is a screening failure."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise ScreeningError(
            f"the screening provider answered {error.code}: {_detail(error)}"
        ) from None
    except urllib.error.URLError as error:
        raise ScreeningError(f"the screening provider is unreachable: {error.reason}") from None
    except (TimeoutError, OSError) as error:
        raise ScreeningError(f"the screening provider failed: {error}") from None
    except json.JSONDecodeError:
        raise ScreeningError("the screening provider's answer is not JSON") from None


def _detail(error: urllib.error.HTTPError) -> str:
    """A bounded excerpt of the provider's error body; never the whole response."""
    try:
        body = error.read(2000)
    except OSError:
        return error.reason
    return body.decode("utf-8", "replace").strip() or "no detail"


def _base_url(configured: str | None) -> str:
    """The provider's endpoint, refused when it is not http(s): the screening never reads a file URL."""
    url = (configured or os.environ.get("AGENT_REVIEW_BASE_URL") or DEFAULT_BASE_URL).strip()
    if not url.startswith(("https://", "http://")):
        raise ReviewError(
            f"the screening API base URL must be http(s), not {url!r}; "
            "set AGENT_REVIEW_BASE_URL to the provider's endpoint"
        )
    return url.rstrip("/")


def _env_api_key() -> str | None:
    for variable in API_KEY_VARIABLES:
        value = os.environ.get(variable, "").strip()
        if value:
            return value
    return None


def _judgments(answer: Any) -> tuple[Mapping[str, Any], ...]:
    """The provider's judgments, from the completion's JSON message."""
    try:
        content = answer["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ScreeningError("the screening provider's answer has no message content") from None
    if not isinstance(content, str):
        raise ScreeningError("the screening provider's message content is not text")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        raise ScreeningError("the screening provider's answer is not JSON") from None
    judgments = parsed.get("judgments") if isinstance(parsed, Mapping) else None
    if not isinstance(judgments, list):
        raise ScreeningError("the screening provider's answer has no judgments list")
    return _objects(judgments, "the screening provider's answer")


def _objects(judgments: Sequence[Any], source: str) -> tuple[Mapping[str, Any], ...]:
    for index, judgment in enumerate(judgments):
        if not isinstance(judgment, Mapping):
            raise ScreeningError(f"{source}: judgment {index} is not an object")
    return tuple(judgments)


def _tokens(answer: Any, key: str) -> int:
    usage = answer.get("usage") if isinstance(answer, Mapping) else None
    return _count(usage, key)


def _count(usage: Any, key: str) -> int:
    value = usage.get(key) if isinstance(usage, Mapping) else None
    return value if isinstance(value, int) else 0


def _responses(path: Path) -> list[Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReviewError(
            f"cannot read the screening fixture {path}: {error.strerror}; "
            "point --screening-fixture at a JSON file"
        ) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ReviewError(f"the screening fixture {path} is not JSON: {error}") from None
    if not isinstance(data, dict) or not isinstance(data.get("responses"), list):
        raise ReviewError(
            f'the screening fixture {path} must be an object with a "responses" list of canned answers'
        )
    return list(data["responses"])
