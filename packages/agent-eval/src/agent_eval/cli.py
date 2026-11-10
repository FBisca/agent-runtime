"""agent-eval: freeze a coding-agent run into a local case (outside every repo, 0700/0600, immutable)."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from agent_eval.capture import capture
from agent_eval.errors import CaptureError
from agent_eval.label import KINDS, add_label
from agent_eval.label2 import EXAMPLE

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


LABEL_EPILOG = (
    """\
FILE is a JSON object, label/1 (source `transcribed`) or label/2; it is written to
<case>/labels/<kind>.json, and a case takes one label of each kind. An invalid file exits 2 naming
each failing field by its path (dimensions.quality.findings[0].evidence[1].quote), and nothing is
written.

A label/2 is the judge's whole output, and agent-eval holds it against the case:
  evidence   every ref names a `file` the case manifest lists (a transcript, an orchestrate file) or
             changes.patch, a `quote` of at most 300 characters that occurs verbatim in that file's
             decompressed text, and optionally the `line` it starts on (1-based).
  dimensions a scored dimension (1.0, 0.8, 0.4 or 0.2) has a finding with evidence; below 1.0 it has a
             finding with `deduction: true`; a null score has label `insufficient_evidence`, a
             rationale and no deduction.
  score      the weighted mean (quality .35, tests .25, efficiency .25, communication .15) of the
             scored dimensions, renormalised, rounded to 2 decimals; null only when none is scored.
  verdict    BLOCKED when `gates` is not empty, else INCOMPLETE when quality or tests is null, else
             PASS when every scored dimension and the score are at least 0.8, else FAIL.
  subject    `subject.session` is a session of the case manifest.

A complete label/2:

"""
    + json.dumps(EXAMPLE, indent=2)
    + "\n"
)


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
    label = commands.add_parser(
        "add-label",
        help="store a judge's label/1 or label/2 JSON beside a case",
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
                args.orchestrate_run,
                args.orchestrate_state_dir,
                warn=warn,
            )
    except CaptureError as error:
        print(f"agent-eval: {error}", file=sys.stderr)
        return error.code
    print(path)
    return 0
