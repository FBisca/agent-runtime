"""agent-review: screen the change between two Git revisions against a configured question bank."""

import argparse
import contextlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_review import (
    calibration,
    chunks,
    deep_review,
    pricing,
    questions,
    readonly,
    revisions,
    safeguards,
)
from agent_review.change import Change
from agent_review.change import read as read_change
from agent_review.chunks import Chunk
from agent_review.errors import ReviewError, ScreeningError
from agent_review.judgments import chunk_scope, collected
from agent_review.questions import QuestionBank
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
`code` resolves two revisions to their commit shas, reads the change between them, and screens it against
the configured question bank. Without --config it discovers the checkout's root `.agent-review.yaml`
and, absent one, evaluates the packaged baseline bank. A configuration is additive: the packaged baseline is
always asked, a repository's own questions extend it and never override or disable one, and its sensitive
paths add to the mandatory ones (D05, D13) - so a protected path escalates however the repository is
configured. The numbers the screening is decided with are configuration too: a configuration's weights and
thresholds are laid over the baseline's per question, and the model never chooses or sees them (D07, D48).
A declaration no stage can read is refused rather than screened.

The change is screened inside the budget in force (D41): a change larger than `chunk.max_diff_tokens` (32k
tokens by default, 262k with --long-context) is split into deterministic file and hunk chunks in path
order, and each chunk is asked the whole bank exactly once - so `usage.requests` is the number of chunks,
never one more. Every changed region is carried by exactly one chunk, and the report's `coverage` counts
the regions the chunks carried against the regions the diff holds. A change wider than
`cross_file.max_files` cannot be read by a cross-file question at once: the report records that coverage
gap, recommends a full deep review and never reads the screen as complete (D42, D43).
The invocation writes `report.json` under
--output either way and prints a summary for a person; the report, not the exit status, carries the verdict.

The report names what produced it: the provider endpoint it screened through (or the fixture it answered
from), the model the provider served beside the configured alias, the input limit in force, whether long
context was requested, and a digest of the bank it screened. The calibrated thresholds and confidence bands
decide an escalation from what the provider answered - a noul below its value, a score above it, or a
confidence below its band - and a critical or high signal recommends a full deep review. --pricing names a
declared price table, so the report's usage carries the cost of the model the provider served and its basis;
a served model the table does not name is unknown, never zero. An execution that stops after the provider
answered keeps the usage it paid for and the judgments it already learned, and a refusal writes no report
at all: it names the report an earlier invocation left in --output, so that verdict is never read as this
run's.

A screening is made once (D40): a provider error, a timeout, a missing credential or an answer that is
not a judgment the question's type allows is an incomplete execution: the report says so, its
`failure.reason` and stderr name why, and the exit is nonzero. It is never a retry, a model fallback or a
clean verdict. Exit 2 is a refusal that screens nothing and names what it refused: an invalid
configuration, a malformed fixture or a revision this repository does not have.

An escalated change is read by one reviewer when the caller names a harness with --deep-review, never by
a swarm (D12, D14). A calibrated signal points where the reviewer should look, so a review of those alone is
`focused`; a sensitive path, a safeguard hit or a coverage gap is about the change itself, so it is `full`
(D43). The instruction sets it is given are configuration: one that declares no `applies` joins every review,
and one matching a fired rule or a changed path joins too, named in the report in declaration order (D07,
D29). The reviewer is read-only (D18): the family answers with the arguments that hold it to reading and
searching, or the harness is refused before the screening starts and nothing is spawned (D38). What the
reviewer answered is judged by the review that reads it; --deep-review-fixture replaces the spawn with a
canned reviewer recording what it was asked in `spawns.json`, which is how a review is checked without a
model.

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
    _change_options(code)
    _screening_options(code)
    _review_options(code)
    return parser


def _change_options(code: argparse.ArgumentParser) -> None:
    """What the review reads and where it writes: the two revisions and the output directory."""
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


def _screening_options(code: argparse.ArgumentParser) -> None:
    """How the change is screened: the configuration, the canned provider, the budget and the prices."""
    code.add_argument(
        "--config",
        metavar="PATH",
        type=Path,
        help=(
            "the question configuration, which extends the packaged baseline; absent, a root "
            ".agent-review.yaml is discovered and, without one, the packaged baseline bank is "
            "evaluated. Its questions and sensitive paths add to the baseline and never replace it"
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
    code.add_argument(
        "--pricing",
        metavar="PATH",
        type=Path,
        help=(
            "a declared price table (version and per-million rates per served model); the report "
            "carries what the served model cost, and an unpriced model is unknown rather than free"
        ),
    )


def _review_options(code: argparse.ArgumentParser) -> None:
    """How an escalated change is reviewed: the harness it is spawned through, or the canned reviewer."""
    code.add_argument(
        "--deep-review",
        metavar="HARNESS",
        help=(
            "spawn one read/search-only reviewer over an escalated change through this harness "
            "family; a family that cannot hold its reviewer to reading and searching is refused "
            "before the screening starts"
        ),
    )
    code.add_argument(
        "--deep-review-fixture",
        metavar="DIR",
        type=Path,
        help=(
            "a canned reviewer replacing the spawn: it records what it was asked for in "
            "spawns.json, so a review is checked without a model"
        ),
    )


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
        print(f"agent-review: execution incomplete: {report.failure}", file=sys.stderr)
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


def _safeguard_report(
    parsed: Change,
    bank: QuestionBank,
    *,
    base: str,
    head: str,
    long_context: bool,
    price_table: pricing.PriceTable | None,
) -> Report | None:
    """The report a safeguard decides before the model is asked, or nothing when no rule fires.

    The safeguards read the frozen diff beside the rules configuration declares: a hit decides the change,
    so no port is built, no credential is read and no request is spent (SG-D2, SG-D6, SG-D10). The
    baseline's paths apply whatever was configured, and the configuration's own paths add to them (D13);
    the members it declares standard-library-only are read beside them.
    """
    rules = safeguards.rule_set(
        baseline_paths=questions.baseline().sensitive_paths,
        repository_paths=bank.sensitive_paths,
        stdlib_only_paths=bank.safeguards.get(safeguards.STDLIB_ONLY_SETTING, ()),
    )
    escalation = safeguards.decide(parsed, rules)
    if not escalation:
        return None
    return Report(
        bank=bank,
        base=base,
        head=head,
        price_table=price_table,
        long_context=long_context,
        requests=0,
        escalation=escalation,
    )


def screen(args: argparse.Namespace) -> Report:
    """One screening of the configured bank over `args.base..args.head`, as the report it produces."""
    repo = Path.cwd()
    # A harness that cannot hold its reviewer is refused here, before a revision is read or a credential
    # looked for: nothing is screened for a review that could only run unrestricted (D38, S3).
    reviewer = _reviewer(args)
    bank = _configuration(args, repo)
    table = None if args.pricing is None else pricing.load(args.pricing)
    base, head = revisions.resolve(repo, args.base, args.head)
    change = revisions.diff(repo, base, head)
    _prepare_output(args.output, base, head)
    parsed = read_change(change)
    decided = _safeguard_report(
        parsed, bank, base=base, head=head, long_context=args.long_context, price_table=table
    )
    report = (
        decided
        if decided is not None
        else _screened(parsed, change, bank, base=base, head=head, table=table, args=args)
    )
    target = deep_review.Target(diff=change, base=base, head=head, paths=parsed.paths, root=repo)
    return _reviewed(report, reviewer, target)


def _screened(
    parsed: Change,
    change: str,
    bank: QuestionBank,
    *,
    base: str,
    head: str,
    table: pricing.PriceTable | None,
    args: argparse.Namespace,
) -> Report:
    """The report a screening produces: the change split inside the budget, each chunk asked the bank once.

    A cross-file question cannot read a change wider than the limit together, so the screen is partial: the
    report records the gap and recommends a full deep review (D42, D43). The calibrated thresholds and
    confidence bands decide the rest from what the provider answered, and the model never sees them (D06,
    D07, D48).
    """
    # The change is split inside the budget in force, and each chunk is asked the whole bank once (D41).
    planned = chunks.split(
        parsed, change, budget=chunks.budget(bank, long_context=args.long_context)
    )
    covered = chunks.coverage(parsed, planned, bank)
    gap = () if covered.gap is None else (covered.gap.as_escalation(),)
    port = _port(args, bank.model)
    screened = _Screened()
    for chunk in planned:
        if not screened.ask(port, bank, chunk, len(planned), base=base, head=head):
            break
    judged = tuple(screened.judgments)
    return Report(
        bank=bank,
        base=base,
        head=head,
        provider=port.provider,
        resolved_model=screened.resolved_model,
        judgments=judged,
        input_tokens=screened.input_tokens,
        output_tokens=screened.output_tokens,
        requests=screened.requests,
        chunks=planned,
        coverage=covered,
        escalation=(*gap, *calibration.escalation(judged, bank)),
        price_table=table,
        long_context=args.long_context,
        failure=screened.failure,
    )


def _reviewed(
    report: Report,
    reviewer: deep_review.Reviewer | None,
    target: deep_review.Target,
) -> Report:
    """The report with the one deep review an escalation asks for, or the report unchanged (D12, D14).

    Nothing is spawned when the caller named no harness, when no reason fired, or when the run stopped
    before it could conclude: a review that never asked for one has no reviewer to spawn.
    """
    if reviewer is None or not report.escalation or report.failure is not None:
        return report
    reviewed = deep_review.review(
        settings=report.bank.deep_review,
        escalation=report.escalation,
        target=target,
        reviewer=reviewer,
    )
    # A reviewer that stopped leaves the screening it paid for standing: the report says incomplete and
    # names the stop, never throwing away judgments and usage the provider already answered (D20, R10).
    return replace(report, deep_review=reviewed, failure=reviewed.failure)


class _Screened:
    """What a chunked screening learned, what it paid for, and why it stopped (D34, D40, D47).

    Each chunk is asked the whole bank exactly once. A run that stops keeps every judgment it learned and
    every token it paid for, including the chunk that stopped it, because the report is the only record of
    a screening that happened; it never retries, never falls back to another model and never guesses.
    """

    def __init__(self) -> None:
        self.judgments: list[dict[str, Any]] = []
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.resolved_model: str | None = None
        self.failure: str | None = None

    def ask(
        self,
        port: ScreeningPort,
        bank: QuestionBank,
        chunk: Chunk,
        total: int,
        *,
        base: str,
        head: str,
    ) -> bool:
        """Screen one chunk with the whole bank; False when this chunk stopped the run."""
        self.requests += 1
        request = ScreeningRequest(
            model=bank.model, questions=bank.questions, diff=chunk.text, base=base, head=head
        )
        try:
            reply = port.screen(request)
        except ScreeningError as error:
            # The request was made and was not answered: the run stops here and says why (D34, D40).
            self.failure = str(error)
            return False
        self.resolved_model = self.resolved_model or reply.model
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens
        held = collected(
            bank.questions,
            reply.judgments,
            base=base,
            head=head,
            scope=chunk_scope(chunk.index, total, chunk.files),
        )
        self.judgments.extend(held.judgments)
        if held.error is not None:
            # A refused judgment is this chunk's answer: the run stops, keeping what it learned (D47, S6).
            self.failure = str(held.error)
            return False
        return True


def _reviewer(args: argparse.Namespace) -> deep_review.Reviewer | None:
    """The reviewer this invocation spawns, or nothing when the caller asked for no deep review (S3).

    The harness's controls are resolved here, before a revision is read or a credential looked for: a family
    that cannot hold its reviewer to reading and searching refuses the run, and nothing is spawned with a
    control the family would drop (D18, D38).
    """
    if args.deep_review is None:
        if args.deep_review_fixture is not None:
            raise ReviewError(
                "--deep-review-fixture names a canned reviewer, but no --deep-review harness asks it "
                "for a review; name the harness the reviewer would be spawned through"
            )
        return None
    try:
        invocation = readonly.invocation(args.deep_review)
    except readonly.ReadOnlyError as refusal:
        raise ReviewError(str(refusal)) from None
    if args.deep_review_fixture is not None:
        return deep_review.FixtureReviewer(
            args.deep_review, invocation.controls, args.deep_review_fixture
        )
    return deep_review.HarnessReviewer(args.deep_review, invocation)


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
