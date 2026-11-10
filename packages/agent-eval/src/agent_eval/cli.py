"""agent-eval: freeze a coding-agent run into a local case (outside every repo, 0700/0600, immutable)."""

import argparse
import sys
from collections.abc import Sequence

from agent_eval.capture import capture
from agent_eval.errors import CaptureError

CAPTURE_EPILOG = """\
--session  a Claude session id, or a unique prefix; the sessions its job's resume and fork links
           reach and every session's subagents join the case. Repeat it for sessions the job state
           cannot link. Required unless --orchestrate-run is given.
--case     the case slug; default the root session id: the oldest session of the first --session
           chain, even when it is excluded.
--exclude  a session of the chain or the run to leave out; the manifest lists it under `excluded`.
--orchestrate-run  an orchestrate run slug: its run directory is copied into the case, and every
           Claude, Codex or Pi session that started inside the run's window with the cwd of one of its
           ticket worktrees joins as a `worker`, with its assignment id when its first prompt names one.
           Alone, it names the case after the run. --orchestrate-state-dir is where the run lives
           (default ~/.local/state/orchestrate).
--repo, --base, --head  store `git diff base..head` as changes.patch and the resolved shas.

Cases live in $AGENT_EVALS_HOME (default ~/.local/state/agent-evals)/cases/<slug>/. Claude deletes a
session's files after 30 days: capture warns when one is more than 25 days old.
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
    cap.add_argument("--session", action="append", default=[], metavar="ID")
    cap.add_argument("--case", metavar="SLUG")
    cap.add_argument("--exclude", action="append", default=[], metavar="ID")
    cap.add_argument("--repo", metavar="PATH")
    cap.add_argument("--base", metavar="REV")
    cap.add_argument("--head", metavar="REV")
    cap.add_argument("--orchestrate-run", metavar="SLUG")
    cap.add_argument(
        "--orchestrate-state-dir", metavar="DIR", help="the orchestrator's --state-dir"
    )
    return parser


def warn(message: str) -> None:
    print(f"agent-eval: warning: {message}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        path = capture(
            args.session,
            args.case,
            args.exclude,
            args.repo,
            args.base,
            args.head,
            args.orchestrate_run,
            args.orchestrate_state_dir,
            warn=warn,
        )
    except CaptureError as error:
        print(f"agent-eval: {error}", file=sys.stderr)
        return error.code
    print(path)
    return 0
