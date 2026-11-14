"""The screening port: the one injected point where a provider is reached (D37, P0-D2).

The provider is TypeSafe System One: one POST of a typed request - the model, the state and an id ->
question map - authenticated by `TYPESAFE_API_KEY`. The port takes the frozen change with the bank, and
returns the provider's native judgments and its token usage. It never retries, never falls back to another
model and never escalates (D40): a provider error, a timeout or an answer that is not an answers map is a
`ScreeningError` whose message is the reason.

The state always travels whole. The provider applies its input budget and refuses an oversized request with
its own reason (`max_tokens_exceeded`); the tool never shortens a change to make it fit, because a truncated
state screens a change nobody made. Long context (`TypeSafe-Danger-Zone-Long-Context`) is the caller's
choice and is never the default.
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

DEFAULT_BASE_URL = "https://api.typesafe.ai/v1"
SYSTEM_ONE_ROUTE = "/systemone"
CREDENTIAL_VARIABLES = ("TYPESAFE_API_KEY", "AGENT_REVIEW_API_KEY")
DEFAULT_TIMEOUT = 120.0
LONG_CONTEXT_HEADER = "TypeSafe-Danger-Zone-Long-Context"


@dataclass(frozen=True)
class Limits:
    """The provider's input budget, in tokens: what one question may add, and what a request may."""

    per_question: int
    per_request: int


# The provider's own budgets: its default window, and the danger-zone window the header unlocks. They are not
# enforced here - the tool never shortens a change to fit - and they are what a report records as in force.
DEFAULT_LIMITS = Limits(per_question=32_000, per_request=64_000)
LONG_CONTEXT_LIMITS = Limits(per_question=262_000, per_request=262_000)


def limits(long_context: bool) -> Limits:
    """The budget in force for one invocation: the long-context one only when the caller asked for it."""
    return LONG_CONTEXT_LIMITS if long_context else DEFAULT_LIMITS


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

    A noul answer is the probability that the change satisfies the question, a score answer is the level
    it was given, and a choice answer is the option. The caller attaches the question's provenance and
    refuses an answer the question's declared range does not allow.
    """

    judgments: tuple[Mapping[str, Any], ...]
    input_tokens: int = 0
    output_tokens: int = 0


class ScreeningPort(Protocol):
    """The one seam a screening reaches: the direct API, or a canned provider replacing it."""

    # `...` is a protocol member's body: it carries the signature and no caller runs it.
    def screen(self, request: ScreeningRequest) -> ScreeningReply: ...


class SystemOneScreenings:
    """TypeSafe System One: one typed request to the provider this invocation is configured with."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        long_context: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
        post: PostJson | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = _base_url(base_url)
        self.long_context = long_context
        self.timeout = timeout
        self.post: PostJson = post if post is not None else post_json

    def screen(self, request: ScreeningRequest) -> ScreeningReply:
        api_key = self.api_key or _credential()
        if not api_key:
            raise ScreeningError(
                "no screening credential: set TYPESAFE_API_KEY to call the screening provider "
                "(AGENT_REVIEW_API_KEY is sent only as its fallback), or --screening-fixture to "
                "screen from canned answers"
            )
        payload = {
            "model": request.model,
            "state": state(request),
            "questions": question_map(request.questions),
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if self.long_context:
            headers[LONG_CONTEXT_HEADER] = "true"
        answer = self.post(f"{self.base_url}{SYSTEM_ONE_ROUTE}", payload, headers, self.timeout)
        return _reply(answer, "the screening provider's answer")


class FixtureScreenings:
    """A canned System One provider behind the same port, so a failure is injectable and stays offline."""

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
        return _reply(response, f"the screening fixture {self.path}")


def state(request: ScreeningRequest) -> dict[str, str]:
    """The provider's state: the change as one object, whole and never shortened to fit."""
    return {"base": request.base, "head": request.head, "diff": request.diff}


def question_map(asked: Sequence[Question]) -> dict[str, dict[str, Any]]:
    """The bank as System One's id -> typed question map, in configuration order.

    A score question's criteria are the levels it is graded on; a noul's criteria are optional and a
    choice's say what each option means. They are the provider's own input, so they travel as configured.
    """
    asked_by_id = {}
    for question in asked:
        typed: dict[str, Any] = {"type": question.type, "instructions": question.ask}
        if question.criteria is not None:
            typed["criteria"] = question.criteria
        asked_by_id[question.id] = typed
    return asked_by_id


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
            f"the screening provider answered {error.code}: {_provider_reason(error)}"
        ) from None
    except urllib.error.URLError as error:
        raise ScreeningError(f"the screening provider is unreachable: {error.reason}") from None
    except (TimeoutError, OSError) as error:
        raise ScreeningError(f"the screening provider failed: {error}") from None
    except json.JSONDecodeError:
        raise ScreeningError("the screening provider's answer is not JSON") from None


def _provider_reason(error: urllib.error.HTTPError) -> str:
    """The provider's own words for a refusal - its error type or message - never the whole response."""
    try:
        body = error.read(2000)
    except OSError:
        return str(error.reason)
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return "no detail"
    try:
        answer = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(answer, Mapping):
        return text
    detail = answer.get("detail")
    for candidate in (
        detail.get("error_type") if isinstance(detail, Mapping) else None,
        detail if isinstance(detail, str) else None,
        answer.get("error", {}).get("message")
        if isinstance(answer.get("error"), Mapping)
        else None,
        answer.get("message"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return text


def _reply(answer: Any, source: str) -> ScreeningReply:
    """The provider's answer as a reply: its native judgments and its usage."""
    if not isinstance(answer, Mapping):
        raise ScreeningError(f"{source} is not an object")
    return ScreeningReply(
        judgments=_judgments(answer.get("answers"), source),
        input_tokens=_count(answer.get("usage"), "input_tokens"),
        output_tokens=_count(answer.get("usage"), "output_tokens"),
    )


def _judgments(answers: Any, source: str) -> tuple[Mapping[str, Any], ...]:
    """One judgment per answered question, carrying the provider's native value for it."""
    if not isinstance(answers, Mapping):
        raise ScreeningError(
            f"{source} has no answers map; System One answers "
            '{"answers": {"<question id>": {"type": ..., ...}}}'
        )
    judgments: list[Mapping[str, Any]] = []
    for identifier, answer in answers.items():
        if not isinstance(identifier, str) or not isinstance(answer, Mapping):
            raise ScreeningError(f"{source}: the answer to {identifier!r} is not an object")
        judgments.append({"question": identifier, "value": _native(identifier, answer, source)})
    return tuple(judgments)


def _native(identifier: str, answer: Mapping[str, Any], source: str) -> Any:
    """The one bounded number or option a noul, score or choice answer carries."""
    kind = answer.get("type")
    if kind not in ("noul", "score", "choice") or kind not in answer:
        raise ScreeningError(
            f"{source}: the answer to {identifier!r} is not a noul, score or choice answer, "
            f"so it carries no value a question can be judged by"
        )
    return answer[kind]


def _base_url(configured: str | None) -> str:
    """The provider's endpoint, refused when it is not http(s): the screening never reads a file URL."""
    url = (configured or os.environ.get("AGENT_REVIEW_BASE_URL") or DEFAULT_BASE_URL).strip()
    if not url.startswith(("https://", "http://")):
        raise ReviewError(
            f"the screening API base URL must be http(s), not {url!r}; "
            "set AGENT_REVIEW_BASE_URL to the provider's endpoint"
        )
    return url.rstrip("/")


def _credential() -> str | None:
    """The documented credential first, its fallback only when it is absent (00b-D5)."""
    for variable in CREDENTIAL_VARIABLES:
        value = os.environ.get(variable, "").strip()
        if value:
            return value
    return None


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
