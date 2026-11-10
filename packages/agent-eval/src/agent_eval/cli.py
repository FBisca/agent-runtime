"""agent-eval: freeze a coding-agent run into a local case (outside every repo, 0700/0600, immutable)."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agent_eval.capture import capture
from agent_eval.errors import CaptureError
from agent_eval.label import KINDS, add_label

CAPTURE_EPILOG = """\
--session  a Claude session id, or a unique prefix; the sessions its job's resume and fork links
           reach and every session's subagents join the case. Repeat it for sessions the job state
           cannot link.
--case     the case slug; default the root session id: the oldest session of the first --session
           chain, even when it is excluded.
--exclude  a session of the chain to leave out; the manifest lists it under `excluded`.
--repo, --base, --head  store `git diff base..head` as changes.patch and the resolved shas.

Cases live in $AGENT_EVALS_HOME (default ~/.local/state/agent-evals)/cases/<slug>/. Claude deletes a
session's files after 30 days: capture warns when one is more than 25 days old.
"""


LABEL_EPILOG = """\
FILE is a label/1 JSON object; it is written to <case>/labels/<kind>.json. An invalid file exits 2
naming each failing field; a case without a label of that kind is the only thing that accepts one.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-eval", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    cap = commands.add_parser(
        "capture",
        help="freeze a run into a case",
        epilog=CAPTURE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cap.add_argument("--session", action="append", default=[], metavar="ID", required=True)
    cap.add_argument("--case", metavar="SLUG")
    cap.add_argument("--exclude", action="append", default=[], metavar="ID")
    cap.add_argument("--repo", metavar="PATH")
    cap.add_argument("--base", metavar="REV")
    cap.add_argument("--head", metavar="REV")
    label = commands.add_parser(
        "add-label",
        help="store a judge's label/1 JSON beside a case",
        epilog=LABEL_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    label.add_argument("case", metavar="CASE")
    label.add_argument("--kind", required=True, choices=KINDS)
    label.add_argument("file", metavar="FILE", type=Path)
    return parser


def warn(message: str) -> None:
    print(f"agent-eval: warning: {message}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "add-label":
            path = add_label(args.case, args.kind, args.file)
        else:
            path = capture(
                args.session,
                args.case,
                args.exclude,
                args.repo,
                args.base,
                args.head,
                warn=warn,
            )
    except CaptureError as error:
        print(f"agent-eval: {error}", file=sys.stderr)
        return error.code
    print(path)
    return 0
