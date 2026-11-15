"""agent-review: screen the change between two Git revisions against a configured question bank."""

import argparse
import contextlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from agent_review import questions, revisions, safeguards
from agent_review.change import read as read_change
from agent_review.errors import ReviewError, ScreeningError
from agent_review.judgments import collected
from agent_review.report import REPORT, Report
from agent_review.screening import (
    FixtureScreenings,
    ScreeningPort,
    ScreeningRequest,
    SystemOneScreenings,
)

# The configuration a repository ships at its root, discovered without a flag (D30).
DISCOVERED = ".agent-review.yaml"

EPILOG = """\
`code` resolves two revisions to their commit shas, reads the change between them, and screens it once
against the configured question bank. Without --config it discovers the checkout's root `.agent-review.yaml`
and, absent one, evaluates the packaged baseline bank; a configuration replaces that bank in this phase, and
additive repository questions, which extend the baseline and never override or disable it, are a later
phase's (D05). Its sensitive paths are different: the packaged baseline ships the mandatory ones, and a
configuration's own list adds to them and never replaces them (D13), so a protected path escalates however
the repository is configured. A bank that asks nothing, or a declaration no rule can read, is refused
rather than screened. The invocation writes `report.json` under --output either way and prints a summary
for a person; the report, not the exit status, carries the verdict.

The report names what produced it: the provider endpoint it screened through (or the fixture it answered
from), the model the provider served beside the configured alias, the input limit in force, whether long
context was requested, and a digest of the bank it screened. An execution that stops after the provider
answered keeps the usage it paid for and the judgments it already learned, and a refusal writes no report
at all: it names the report an earlier invocation left in --output, so that verdict is never read as this
run's.

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
            "the question configuration; absent, a root .agent-review.yaml is discovered and, without "
            "one, the packaged baseline bank is evaluated. A configuration's sensitive paths extend the "
            "mandatory baseline and never replace it"
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
        _name_standing_report(args)
        return error.code
    try:
        path = report.write(args.output)
    except OSError as error:
        # The screening was paid for: what it learned is printed, never lost behind a traceback (S6).
        print(report.summary())
        print(
            f"agent-review: {_unwritable(args.output, report.base, report.head, error)}",
            file=sys.stderr,
        )
        _name_standing_report(args)
        return 2
    print(report.summary(path))
    if report.failure is not None:
        print(f"agent-review: screening failed: {report.failure}", file=sys.stderr)
        return 1
    return 0


def _name_standing_report(args: argparse.Namespace) -> None:
    """Name the report an earlier invocation left in --output, so its verdict cannot pass as this run's."""
    output = getattr(args, "output", None)
    if output is None:
        return
    standing = Path(output) / REPORT
    if not standing.is_file():
        return
    revision = _revision(standing)
    screened = f"; it screened {revision}" if revision else ""
    print(
        f"agent-review: the report at {standing} is not this invocation's: an earlier one left it "
        f"standing{screened}",
        file=sys.stderr,
    )


def _revision(standing: Path) -> str:
    """The revision a standing report screened, or nothing when it cannot be read."""
    try:
        screened = json.loads(standing.read_text(encoding="utf-8")).get("input")
    except (OSError, ValueError, AttributeError):
        return ""
    if not isinstance(screened, Mapping):
        return ""
    base, head = screened.get("base"), screened.get("head")
    return f"{base}..{head}" if isinstance(base, str) and isinstance(head, str) else ""


def screen(args: argparse.Namespace) -> Report:
    """One screening of the configured bank over `args.base..args.head`, as the report it produces."""
    repo = Path.cwd()
    bank = _configuration(args, repo)
    base, head = revisions.resolve(repo, args.base, args.head)
    change = revisions.diff(repo, base, head)
    _prepare_output(args.output, base, head)
    # The safeguards read the frozen diff beside the rules configuration declares: a hit decides the change,
    # so no port is built, no credential is read and no request is spent (SG-D2, SG-D6, SG-D10). The
    # mandatory baseline's paths apply whatever was configured, and the configuration's own paths add to
    # them (D13); the members it declares standard-library-only are read beside them.
    rules = safeguards.rule_set(
        baseline_paths=questions.baseline().sensitive_paths,
        repository_paths=bank.sensitive_paths,
        stdlib_only_paths=bank.safeguards.get(safeguards.STDLIB_ONLY_SETTING, ()),
    )
    escalation = safeguards.decide(read_change(change), rules)
    if escalation:
        return Report(
            model=bank.model,
            base=base,
            head=head,
            questions=bank.questions,
            long_context=args.long_context,
            requests=0,
            escalation=escalation,
        )
    port = _port(args, bank.model)
    request = ScreeningRequest(
        model=bank.model, questions=bank.questions, diff=change, base=base, head=head
    )
    try:
        reply = port.screen(request)
    except ScreeningError as error:
        # An incomplete run keeps what it knows and records why it stopped; no verdict is inferred (D34, D40).
        return Report(
            model=bank.model,
            base=base,
            head=head,
            questions=bank.questions,
            provider=port.provider,
            long_context=args.long_context,
            failure=str(error),
        )
    # A refused judgment is the run's failure, but the judgments it learned before it and the usage it
    # paid for stay in the report: it is the only record of a screening that happened (S6, D47).
    held = collected(bank.questions, reply.judgments, base=base, head=head)
    return Report(
        model=bank.model,
        base=base,
        head=head,
        questions=bank.questions,
        provider=port.provider,
        resolved_model=reply.model,
        judgments=held.judgments,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        long_context=args.long_context,
        failure=None if held.error is None else str(held.error),
    )


def _configuration(args: argparse.Namespace, repo: Path) -> questions.QuestionBank:
    """The bank this run evaluates: the named configuration, the discovered one, or the packaged baseline.

    A root `.agent-review.yaml` is discovered without a flag (D30); an explicit --config overrides it, and
    it is read before any revision is resolved, so a refusal names the field and no change is read for it.
    """
    if args.config is not None:
        return questions.load(args.config)
    discovered = repo / DISCOVERED
    if discovered.is_file():
        return questions.load(discovered)
    return questions.baseline()


def _prepare_output(output: Path, base: str, head: str) -> None:
    """Make --output writable before anything is charged for, refusing loudly when it is not.

    A directory that already exists can still deny writing, so the report's own temporary file is
    written and removed here: a screening is never paid for when it could not be recorded (S6).
    """
    probe = output / f"{REPORT}.tmp"
    try:
        output.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
    except OSError as error:
        raise ReviewError(_unwritable(output, base, head, error)) from None
    with contextlib.suppress(OSError):
        # A probe left behind is harmless: the report's own write replaces it.
        probe.unlink()


def _unwritable(output: Path, base: str, head: str, error: OSError) -> str:
    """A refusal naming the path it cannot write and the change that was not screened."""
    return (
        f"cannot write the report under {output}: {error.strerror or error}; point --output at a "
        f"directory this invocation may write, so the change {base[:7]}..{head[:7]} is not mistaken "
        "for screened"
    )


def _port(args: argparse.Namespace, model: str) -> ScreeningPort:
    """The provider this invocation screens through: the canned one when one is named (P0-D2)."""
    if args.screening_fixture is not None:
        return FixtureScreenings(args.screening_fixture)
    return SystemOneScreenings(model=model, long_context=args.long_context)
