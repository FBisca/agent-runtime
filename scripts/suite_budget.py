"""The suite's per-module budget: measure a junit, compare it with the committed baseline.

R208's whole-suite time is a person's check. This is the machine check that keeps it true: CI writes
the junit its run already produces, and a module that grew past `baseline * tolerance + floor` fails
the build naming the module, its seconds and its budget. A new module under the floor is free, so a
ticket can start a file without a baseline row; a module that grows materially must be measured and
recorded on purpose.
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

BASELINE = Path(__file__).resolve().parents[1] / "acceptance" / "suite" / "suite_budget.json"
TOLERANCE = 2.0  # a module may take twice its share of the suite before it is a regression
FLOOR = 5.0  # new or tiny modules are not compared; the noise is larger than the signal
NEW_SHARE = 0.01  # a new module over the floor is free up to one percent of the suite
HARD_SECONDS = 25.0  # a grown module fails only at this size; below it the runner is the noise


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


def measure(source):
    """({module: summed seconds}, total seconds) from a junit path or an Element."""
    root = ET.parse(source).getroot() if not isinstance(source, ET.Element) else source
    modules, total = {}, 0.0
    for case in root.iter("testcase"):
        seconds = float(case.get("time") or 0)
        module = module_of(case)
        modules[module] = modules.get(module, 0.0) + seconds
        total += seconds
    return modules, total


def over_budget(actual, baseline):
    """[(module, seconds, budget)] for every module whose share of the suite grew, slowest first.

    A share, not an absolute second: a slower CI runner moves every module together, so the
    comparison stays honest across machines. `tolerance` is the factor a known module may grow;
    a new module over the floor is free up to `NEW_SHARE` of the suite."""
    total = sum(actual.values()) or 1.0
    tolerance = float(baseline.get("tolerance", TOLERANCE))
    floor = float(baseline.get("floor_seconds", FLOOR))
    new_share = float(baseline.get("new_share", NEW_SHARE))
    known = baseline.get("modules") or {}
    rows = []
    for module, seconds in actual.items():
        if seconds <= floor:
            continue
        share = seconds / total
        base = float(known.get(module, 0.0))
        allowed = base * tolerance if base else new_share
        if share > allowed:
            rows.append((module, seconds, allowed * total))
    return sorted(rows, key=lambda row: -row[1])


def failing(rows):
    """The grown modules big enough to fail the build; the rest are advisory."""
    return [row for row in rows if row[1] >= HARD_SECONDS]


def write(path, modules, total, measured_at=None):
    """The baseline document: each module's share of the suite, in a stable order."""
    data = {
        "measured_at": measured_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "tolerance": TOLERANCE,
        "floor_seconds": FLOOR,
        "new_share": NEW_SHARE,
        "sum_seconds": round(total, 1),
        "modules": {
            module: round(seconds / total, 6) if total else 0.0
            for module, seconds in sorted(modules.items())
        },
    }
    Path(path).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", required=True, help="the junit XML the suite run wrote")
    parser.add_argument("--baseline", default=str(BASELINE), help="the committed budget JSON")
    parser.add_argument(
        "--write", action="store_true", help="record this run as the new baseline and exit 0"
    )
    args = parser.parse_args(argv)
    modules, total = measure(args.junit)
    if args.write:
        write(args.baseline, modules, total)
        print(
            "suite budget: wrote %s (%d modules, %.1fs summed)"
            % (args.baseline, len(modules), total)
        )
        return 0
    try:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print("suite budget: cannot read %s: %s" % (args.baseline, error), file=sys.stderr)
        return 2
    rows = over_budget(modules, baseline)
    if not rows:
        print("suite budget: %d modules within budget (%.1fs summed)" % (len(modules), total))
        return 0
    print("suite budget: %d module(s) grew past their share:" % len(rows))
    for module, seconds, budget in rows:
        print("  %-70s %8.1fs  budget %8.1fs" % (module, seconds, budget))
    if not failing(rows):
        print("all below %.0fs: advisory only on a shared runner" % HARD_SECONDS)
        return 0
    print(
        "re-measure on an idle machine and record it with --write, or make the %s cheaper" % "tests"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
