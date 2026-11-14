"""agent-review: screen the change between two Git revisions against a configured question bank."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agent_review import questions, revisions
from agent_review.errors import ReviewError, ScreeningError
from agent_review.screening import (
    FixtureScreenings,
    ProviderScreenings,
    ScreeningPort,
    ScreeningRequest,
)

EPILOG = """\
`code` resolves two revisions to their commit shas, reads the change between them, and screens it once
against the configured question bank. Without --config it evaluates the packaged baseline bank; a
configuration replaces that bank in this phase, and additive repository questions, which extend the
baseline and never override or disable it, are a later phase's (D05). A bank that asks nothing is refused
rather than screened. The screening answer is printed as JSON.

A screening is made once (D40): a provider error, a timeout, a missing credential or an answer that is
not a judgment list is an incomplete execution, named on stderr with a nonzero exit, never a retry, a
model fallback or a clean result. Exit 2 is a refusal that screens nothing and names what it refused: an
invalid configuration, a malformed fixture or a revision this repository does not have.

The provider is the direct screening API, OpenAI-compatible: AGENT_REVIEW_API_KEY (or OPENAI_API_KEY)
authenticates it, and AGENT_REVIEW_BASE_URL defaults to https://api.openai.com/v1. --screening-fixture
replaces the API with canned responses, which is how a provider failure is injected.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-review",
        description=__doc__,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    code = commands.add_parser("code", help="screen the change between two Git revisions")
    code.add_argument(
        "--base", required=True, metavar="REV", help="the revision the change starts at"
    )
    code.add_argument(
        "--head", required=True, metavar="REV", help="the revision the change ends at"
    )
    code.add_argument(
        "--output",
        required=True,
        metavar="DIR",
        type=Path,
        help="the directory this review writes into",
    )
    code.add_argument(
        "--config",
        metavar="PATH",
        type=Path,
        help=(
            "the question configuration, which replaces the packaged baseline bank; "
            "absent, the baseline is evaluated"
        ),
    )
    code.add_argument(
        "--screening-fixture",
        metavar="PATH",
        type=Path,
        help="a canned-response provider replacing the screening API",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        answer = screen(args)
    except ReviewError as error:
        print(f"agent-review: {error}", file=sys.stderr)
        return error.code
    except ScreeningError as error:
        print(f"agent-review: screening failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(answer, indent=2))
    return 0


def screen(args: argparse.Namespace) -> dict[str, Any]:
    """One screening of the configured bank over `args.base..args.head`, as the answer to print."""
    bank = questions.load(args.config) if args.config else questions.baseline()
    repo = Path.cwd()
    base, head = revisions.resolve(repo, args.base, args.head)
    change = revisions.diff(repo, base, head)
    args.output.mkdir(parents=True, exist_ok=True)
    reply = _port(args, bank.model).screen(
        ScreeningRequest(
            model=bank.model, questions=bank.questions, diff=change, base=base, head=head
        )
    )
    return {
        "model": bank.model,
        "input": {"base": base, "head": head},
        "questions": [
            {"id": question.id, "priority": question.priority, "type": question.type}
            for question in bank.questions
        ],
        "judgments": [dict(judgment) for judgment in reply.judgments],
        # One screening is one request: nothing retries or falls back, so a retry would show here (D40).
        "usage": {
            "requests": 1,
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
        },
    }


def _port(args: argparse.Namespace, model: str) -> ScreeningPort:
    """The provider this invocation screens through: the canned one when one is named (P0-D2)."""
    if args.screening_fixture is not None:
        return FixtureScreenings(args.screening_fixture)
    return ProviderScreenings(model=model)
