"""The suite's cost budget: a test over eighteen seconds, and the whole suite's summed call time.

R208's whole-suite time is a person's check. This is the machine check that keeps it true: CI writes
the junit its run already produces, and the gate fails the build naming a test over the ceiling, or the
suite when its summed call time passed the ceiling the committed budget names.

Two absolute numbers, and no per-module row. A row was a number every rename, split and move had to
edit, and it was measured on another machine, so it needed a tolerance factor and a floor to keep a
shared runner from failing the build. A ceiling needs neither: it holds on a slow runner because it
holds on every runner, and a file that moves carries nothing to re-record.

Exit 0 is a suite within both ceilings, 1 an offender, 2 a refusal: a budget or a junit that cannot be
read, or a ceiling that is not a number. The ceilings only shrink; `--write` re-stamps the day they
were reviewed and records nothing about any file.
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

BASELINE = Path(__file__).resolve().parents[1] / "acceptance" / "suite" / "suite_budget.json"

# The two ceilings, and the numbers a budget that names neither falls back to. One test may not cost
# eighteen seconds, and the whole suite's cases may not sum past the number the committed file names.
#
# Eighteen is measured on macOS, because that is the slower runner class the gate runs on: the worst
# test in the tree read 13.2 s there against 11.5 s on ubuntu - ten of those seconds are a busy
# deadline the test pays on purpose, so the class that varies is the overhead around it. Thirty
# percent over the slower reading, plus the room a contended three-core runner needs for that
# overhead, is eighteen.
TEST_CEILING = 18.0
SUITE_CEILING = 2700.0


class Refused(Exception):
    """A run that could not be made honestly: no number was compared, and nothing is reported."""


class Run(NamedTuple):
    """What one run decided: the exit code, the lines it reports, and what it says on stderr."""

    code: int
    lines: list[str]
    errors: list[str]


def module_of(case):
    """The test file a junit testcase belongs to, from its dotted classname.

    A test in a class ends with the class name; a plain function's classname is the module alone.
    Only a capitalised last part is a class, so `tests.test_x.MyTests` and `tests.test_x` both name
    `tests/test_x.py`, never `tests.py`.
    """
    parts = (case.get("classname") or "").split(".")
    if len(parts) > 1 and parts[-1][:1].isupper():
        parts = parts[:-1]
    return "/".join(parts) + ".py"


def case_id(case):
    """The pytest node id of one junit testcase: `<module>::<Class>::<test>` or `<module>::<test>`."""
    name = case.get("name") or ""
    classname = (case.get("classname") or "").split(".")
    klass = classname[-1] if len(classname) > 1 and classname[-1][:1].isupper() else ""
    return "::".join(part for part in (module_of(case), klass, name) if part)


def cases(source):
    """({node id: seconds}) for every testcase of a junit path or an Element.

    A node id is how pytest prints one test, so an offender line can be pasted back into a `pytest`
    command line to see the cost of that test alone.
    """
    root = ET.parse(source).getroot() if not isinstance(source, ET.Element) else source
    found = {}
    for case in root.iter("testcase"):
        found[case_id(case)] = found.get(case_id(case), 0.0) + float(case.get("time") or 0)
    return found


def summed(measured):
    """The suite's summed call time: the seconds of every case added up."""
    return round(sum(measured.values()), 1)


def ceilings_of(budget):
    """The two ceilings the committed budget declares, or a refusal naming the budget's fault.

    A ceiling that is not a number is refused where it is read: a gate that compared the suite with a
    typo would fail every run with a traceback instead of naming the budget's own fault.
    """
    ceilings = []
    for key, fallback in (
        ("test_ceiling_seconds", TEST_CEILING),
        ("suite_ceiling_seconds", SUITE_CEILING),
    ):
        value = budget.get(key, fallback)
        try:
            ceilings.append(float(value))
        except (TypeError, ValueError) as error:
            raise Refused(
                "suite budget: the budget's %s is not a number: %r: record one in %s"
                % (key.replace("_seconds", "").replace("_", " "), value, BASELINE)
            ) from error
    return tuple(ceilings)


def offender_lines(measured, test_ceiling, suite_ceiling):
    """One line per test over the test ceiling, then one for the suite's summed time past its own.

    A test at exactly the ceiling is inside it: the ceiling is the number it may cost, and a line is
    what the gate refused, so an offender line always names the number it passed.
    """
    lines = [
        "%s: %.1fs > ceiling %.1fs" % (node, seconds, test_ceiling)
        for node, seconds in sorted(measured.items())
        if seconds > test_ceiling
    ]
    total = summed(measured)
    if total > suite_ceiling:
        lines.append("the whole suite: %.1fs > ceiling %.1fs" % (total, suite_ceiling))
    return lines


def ceilings_phrase(test_ceiling, suite_ceiling):
    """The two ceilings as one reader-facing phrase, each named beside the thing it holds."""
    return "%.1fs per test and %.1fs for the suite" % (test_ceiling, suite_ceiling)


def summary(measured, ceilings):
    """The clean run's line: the two ceilings read, how many tests, and what they summed to."""
    count = len(measured)
    return "suite budget: ceilings %s, %d test%s in %.1fs" % (
        ceilings_phrase(*ceilings),
        count,
        "s" if count != 1 else "",
        summed(measured),
    )


def read_budget(path):
    """The committed budget, or a refusal naming it."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused("suite budget: cannot read %s: %s" % (path, error)) from error


def measure_junit(path):
    """The junit's cases, or a refusal naming the run that wrote nothing readable."""
    try:
        return cases(path)
    except (OSError, ValueError, ET.ParseError) as error:
        raise Refused("suite budget: cannot read %s: %s" % (path, error)) from error


def run(junit, baseline):
    """The decision of one run: what it reports, and the exit code it earns."""
    try:
        ceilings = ceilings_of(read_budget(baseline))
        measured = measure_junit(junit)
    except Refused as error:
        return Run(2, [], [str(error)])
    lines = offender_lines(measured, *ceilings)
    if lines:
        # The closing line carries the run's own measurement, so a red build reads the number it was
        # held to as well as the lines it refused.
        return Run(
            1,
            lines,
            [
                "suite budget: %d test%s in %.1fs against ceilings %s: make the test cheaper, or "
                "lower the suite's cost, rather than raise the ceiling"
                % (
                    len(measured),
                    "s" if len(measured) != 1 else "",
                    summed(measured),
                    ceilings_phrase(*ceilings),
                )
            ],
        )
    return Run(0, [summary(measured, ceilings)], [])


def baseline_document(test_ceiling=None, suite_ceiling=None, measured_at=None):
    """The committed budget: the two ceilings and the day they were reviewed, and nothing per file."""
    return {
        "measured_at": measured_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "test_ceiling_seconds": float(test_ceiling if test_ceiling is not None else TEST_CEILING),
        "suite_ceiling_seconds": float(
            suite_ceiling if suite_ceiling is not None else SUITE_CEILING
        ),
    }


def write_budget(path):
    """Re-stamp the committed budget with today, keeping the ceilings it already names.

    It records nothing about a file and accepts no growth: a ceiling is the number the suite may not
    pass, so only a smaller number makes the next run easier. The day is the only thing it writes.
    """
    existing = read_budget(path) if Path(path).exists() else {}
    test_ceiling, suite_ceiling = ceilings_of(existing)
    document = baseline_document(test_ceiling, suite_ceiling)
    Path(path).write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    print(
        "suite budget: wrote %s (ceilings %s, measured %s; it never accepts growth, which only a "
        "smaller ceiling clears)"
        % (path, ceilings_phrase(test_ceiling, suite_ceiling), document["measured_at"])
    )
    return 0


def parser():
    """The command line: the junit to read, the budget to hold it to, and how."""
    made = argparse.ArgumentParser(description="measure the suite's cost against two ceilings")
    made.add_argument("--junit", help="the junit XML the suite run wrote")
    made.add_argument("--baseline", default=str(BASELINE), help="the committed budget JSON")
    made.add_argument(
        "--write",
        action="store_true",
        help="stamp the day the ceilings were reviewed and exit 0; it records nothing about any "
        "file and never accepts growth, which only a smaller ceiling clears",
    )
    return made


def main(argv=None):
    args = parser().parse_args(argv)
    if args.write:
        try:
            return write_budget(args.baseline)
        except Refused as error:
            print(error, file=sys.stderr)
            return 2
    if not args.junit:
        print(
            "suite budget: --junit is needed: name the junit the suite run wrote, or pass --write to "
            "stamp the day the ceilings were reviewed",
            file=sys.stderr,
        )
        return 2
    decision = run(args.junit, args.baseline)
    for error in decision.errors:
        print(error, file=sys.stderr)
    for line in decision.lines:
        print(line)
    return decision.code


if __name__ == "__main__":
    sys.exit(main())
