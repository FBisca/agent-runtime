"""Measure the reviewer on the defect bank: how many of the 12 known defects a review names.

For each case it renders the reviewer prompt from the driver's own template (`prompts/review.md`), runs it
headless at the case's head commit in a temporary git worktree, and scores each defect as named when a finding's
file matches the defect's file and its line is within LINE_SLACK lines of the defect's location. Scoring is
deterministic: no judge model.

    uv run python evals/review-bank/run.py --dry-run             # print the prompts, spend nothing
    uv run python evals/review-bank/run.py --model opus          # run every case (costs money)
    uv run python evals/review-bank/run.py --cases 89bba2f-flaky # run the named cases only

`cases.json` lists changes (`id`, `base`, `head`, `kind` bad|clean); `defects.json` maps a case id to the
`locations` of its defect ({file: [[first line, last line], ...]}) and a `description` of the change.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_orchestrator.driver.prompts import block, render

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # evals/review-bank/ sits two levels under the repository root
LINE_SLACK = 15
RULEBOOK = "docs/rulebook.md"
# Read-only: the reviewer reads files and the repository's documentation; the diff is in its prompt.
TOOLS = "Read,Grep,Glob"
HEADLESS = (
    "\n\nThe diff of the change follows, so no command is needed. Answer with the verdict JSON only, no prose and "
    "no code fence.\n\n"
)


def prompt_for(case, defects, checkout):
    """The driver's review brief for one case, then the diff of the change."""
    entry = defects.get(case["id"]) or {}
    report = {"summary": entry.get("description") or case.get("note", ""), "head": case["head"]}
    brief = render(
        "review",
        unit=case["id"],
        task_md="(none: the change is described by its report)",
        reported="```json\n%s\n```" % json.dumps(report, indent=2),
        checkout=checkout,
        checkout_contents="the repository at the change's head",
        plan="(none)",
        base=case["base"],
        head=case["head"],
        rulebook=block("review-rulebook", rulebook=Path(checkout) / RULEBOOK),
        rules=Path(checkout) / "AGENTS.md",
    )
    diff = subprocess.run(
        ["git", "diff", case["base"], case["head"]],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return brief + HEADLESS + diff


def named(findings, locations):
    """True when a finding is in the defect's file and within LINE_SLACK lines of one of its locations."""
    for finding in findings:
        file = str(finding.get("file", "")).removeprefix("./")
        for path, spans in locations.items():
            if not (file == path or file.endswith("/" + path)):
                continue
            line = finding.get("line")
            if isinstance(line, int) and any(
                a - LINE_SLACK <= line <= b + LINE_SLACK for a, b in spans
            ):
                return True
    return False


def verdict_of(output):
    """The verdict object in `claude -p --output-format json` output: its `result` text holds one JSON object."""
    text = json.loads(output).get("result") or ""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group(0)) if match else {"unparsed": text[:500]}


def review(case, defects, model):
    """One headless review at the case's head, in a temporary worktree removed afterwards."""
    parent = Path(tempfile.mkdtemp(prefix="review-bank-"))
    checkout = parent / "checkout"
    subprocess.run(
        ["git", "worktree", "add", "--detach", "--quiet", str(checkout), case["head"]],
        cwd=REPO,
        check=True,
    )
    try:
        shutil.copy(REPO / RULEBOOK, checkout / RULEBOOK)  # the head may predate the rulebook
        done = subprocess.run(
            [
                "claude",
                "-p",
                "--model",
                model,
                "--setting-sources",
                "",
                "--strict-mcp-config",
                "--no-session-persistence",
                "--tools",
                TOOLS,
                "--allowedTools",
                TOOLS,
                "--output-format",
                "json",
            ],
            input=prompt_for(case, defects, checkout),
            capture_output=True,
            text=True,
            cwd=checkout,
            timeout=1800,
        )
        return verdict_of(done.stdout)
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(checkout)], cwd=REPO, check=False
        )
        shutil.rmtree(parent, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="opus")
    parser.add_argument("--cases", nargs="*", help="case ids; default every case")
    parser.add_argument("--dry-run", action="store_true", help="print the prompts and run nothing")
    args = parser.parse_args(argv)
    cases = json.loads((HERE / "cases.json").read_text())
    defects = json.loads((HERE / "defects.json").read_text())
    cases = [c for c in cases if not args.cases or c["id"] in args.cases]
    if args.dry_run:
        for case in cases:
            print("=== %s ===\n%s\n" % (case["id"], prompt_for(case, defects, REPO / "<checkout>")))
        return 0
    hits, total = [], 0
    for case in cases:
        locations = (defects.get(case["id"]) or {}).get("locations")
        verdict = review(case, defects, args.model)
        findings = verdict.get("findings") or []
        if locations:
            total += 1
            if named(findings, locations):
                hits.append(case["id"])
        print(
            "%-22s %-5s verdict=%s findings=%d%s"
            % (
                case["id"],
                case["kind"],
                verdict.get("verdict", "?"),
                len(findings),
                "  NAMED" if case["id"] in hits else "",
            )
        )
    print("named %d of %d" % (len(hits), total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
