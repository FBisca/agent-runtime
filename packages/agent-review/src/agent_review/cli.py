"""agent-review: screen the change between two Git revisions against a configured question bank."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agent_review import questions, revisions
from agent_review.errors import ReviewError, ScreeningError
from agent_review.judgments import attributed
from agent_review.report import Report
from agent_review.screening import (
    FixtureScreenings,
    ScreeningPort,
    ScreeningRequest,
    SystemOneScreenings,
)

EPILOG = """\
`code` resolves two revisions to their commit shas, reads the change between them, and screens it once
against the configured question bank. Without --config it evaluates the packaged baseline bank; a
configuration replaces that bank in this phase, and additive repository questions, which extend the
baseline and never override or disable it, are a later phase's (D05). A bank that asks nothing is refused
rather than screened. The invocation writes `report.json` under --output either way and prints a summary
for a person; the report, not the exit status, carries the verdict.

A screening is made once (D40): a provider error, a timeout, a missing credential or an answer that is
not a judgment the question's type allows is an incomplete execution: the report says so, its
`failure.reason` and stderr name why, and the exit is nonzero. It is never a retry, a model fallback or a
clean verdict. Exit 2 is a refusal that screens nothing and names what it refused: an invalid
configuration, a malformed fixture or a revision this repository does not have.

The provider is TypeSafe System One (D37): TYPESAFE_API_KEY authenticates it, AGENT_REVIEW_API_KEY is
sent only as its fallback, and AGENT_REVIEW_BASE_URL defaults to https://api.typesafe.ai/v1, whose
/systemone route is the endpoint. The tool never truncates the change to fit: the provider's own
`max_tokens_exceeded` is an incomplete execution. --long-context requests the provider's danger-zone
budget (262k tokens instead of 32k per question and 64k per request), which reads a large change more
forgivingly, so it is a caller's choice and never the default. --screening-fixture replaces the API with
canned answers, which is how every check stays offline and how a provider failure is injected.
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
        help="a canned-answer provider replacing the screening API",
    )
    code.add_argument(
        "--long-context",
        action="store_true",
        help=(
            "request the provider's long-context budget (262k tokens instead of 32k per question "
            "and 64k per request); it reads a large change more forgivingly and costs accuracy"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = screen(args)
    except ReviewError as error:
        print(f"agent-review: {error}", file=sys.stderr)
        return error.code
    path = report.write(args.output)
    print(report.summary(path))
    if report.failure is not None:
        print(f"agent-review: screening failed: {report.failure}", file=sys.stderr)
        return 1
    return 0


def screen(args: argparse.Namespace) -> Report:
    """One screening of the configured bank over `args.base..args.head`, as the report it produces."""
    bank = questions.load(args.config) if args.config else questions.baseline()
    repo = Path.cwd()
    base, head = revisions.resolve(repo, args.base, args.head)
    change = revisions.diff(repo, base, head)
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        reply = _port(args, bank.model).screen(
            ScreeningRequest(
                model=bank.model, questions=bank.questions, diff=change, base=base, head=head
            )
        )
        judgments = attributed(bank.questions, reply.judgments, base=base, head=head)
    except ScreeningError as error:
        # An incomplete run keeps what it knows and records why it stopped; no verdict is inferred (D34, D40).
        return Report(
            model=bank.model,
            base=base,
            head=head,
            questions=bank.questions,
            long_context=args.long_context,
            failure=str(error),
        )
    return Report(
        model=bank.model,
        base=base,
        head=head,
        questions=bank.questions,
        judgments=judgments,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        long_context=args.long_context,
    )


def _port(args: argparse.Namespace, model: str) -> ScreeningPort:
    """The provider this invocation screens through: the canned one when one is named (P0-D2)."""
    if args.screening_fixture is not None:
        return FixtureScreenings(args.screening_fixture)
    return SystemOneScreenings(model=model, long_context=args.long_context)
