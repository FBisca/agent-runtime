"""The tree's structure budget: five ceilings, one committed baseline, a ratchet on growth.

The tree's other gates count ruff and ty diagnostics and the suite's seconds. None of them sees a
module grow, a class collect methods or a function collect parameters, and a file nobody reads whole
is where the repository's own "fix the class, not the instance" rule stops working. This measures
every Python file under `--root` — source and test alike — against five ceilings and prints the
growth as one line per offender:

    <path>: <metric> <measured> > recorded <row> (ceiling <ceiling>)
    <path>: <metric> <measured> > ceiling <ceiling>
    <path>: <metric> paid (recorded <row>)

Exit 0 is a tree at its baseline, 1 an offender, 2 a refusal: a baseline that cannot be read, a
`--paid` path that measures no file, or `--paid` without `--metric`. A deliberate change is recorded
with `--write`, which re-measures every recorded row and adds one for any file over a ceiling.
"""

import argparse
import ast
import json
import os
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

BASELINE = (
    Path(__file__).resolve().parents[1] / "acceptance" / "structure" / "structure_budget.json"
)

# One ceiling per metric, the number a file needs no row to stay under. An area may record a tighter
# number than the ceiling on purpose; `--paid` is how such a row becomes a debt to pay.
CEILINGS = {
    "module-lines": 1000,
    "class-methods": 25,
    "function-lines": 50,
    "function-branches": 12,
    "parameters": 7,
}
METRICS = tuple(CEILINGS)

# A walk skips a virtualenv, a git store and a package tree, and nothing else: an exemption list is
# what a new offender would use to enter the tree unmeasured.
SKIP = frozenset({".git", ".venv", "node_modules", "__pycache__"})

# The nodes one branch point each; a boolean operator counts one per extra operand and a
# comprehension one per `if`, so a function's count is the number of ways through it.
BRANCHES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert)


class Refused(Exception):
    """A run that could not be made honestly: no number was compared, and nothing is reported."""


def branches(function):
    """The decision points of one function's own body.

    A nested definition keeps its own count, so an outer function is not charged for the work of the
    one it holds.
    """
    total = 0
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, BRANCHES):
            total += 1
        elif isinstance(node, ast.BoolOp):
            total += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            total += len(node.ifs)
        stack.extend(ast.iter_child_nodes(node))
    return total


def parameters(function):
    """How many arguments a function declares, positional, keyword-only, variadic alike."""
    args = function.args
    return (
        len(args.posonlyargs)
        + len(args.args)
        + len(args.kwonlyargs)
        + (1 if args.vararg else 0)
        + (1 if args.kwarg else 0)
    )


def metrics_of(source):
    """The five numbers of one module's source: its size and the worst case its definitions hold."""
    tree = ast.parse(source)
    numbers = dict.fromkeys(METRICS, 0)
    numbers["module-lines"] = len(source.splitlines())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            public = sum(
                1
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not child.name.startswith("_")
            )
            numbers["class-methods"] = max(numbers["class-methods"], public)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            numbers["function-lines"] = max(numbers["function-lines"], end - node.lineno + 1)
            numbers["function-branches"] = max(numbers["function-branches"], branches(node))
            numbers["parameters"] = max(numbers["parameters"], parameters(node))
    return numbers


def walk(root):
    """Every Python file under `root`, as a POSIX path relative to it, sorted."""
    root = Path(root)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP)
        for filename in filenames:
            if filename.endswith(".py"):
                found.append((Path(dirpath) / filename).relative_to(root).as_posix())
    return sorted(found)


def measure_tree(root):
    """{path: {metric: number}} for every Python file under `root`.

    A file that cannot be decoded or parsed is skipped: the syntax gates own that failure, and a
    budget that refused the whole tree over one file would hide every other number.
    """
    measured = {}
    for name in walk(root):
        try:
            source = (Path(root) / name).read_text(encoding="utf-8")
            measured[name] = metrics_of(source)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            continue
    return measured


def ceilings_of(baseline):
    """The ceilings the baseline declares, a metric it left out falling back to the module's own."""
    declared = baseline.get("ceilings") or {}
    return {metric: int(declared.get(metric, CEILINGS[metric])) for metric in METRICS}


def growth_lines(measured, baseline, skip: Iterable[tuple[str, str]] = ()):
    """One line per growth: past the number recorded for the file, or past the ceiling with no row.

    `skip` holds the (path, metric) pairs another check already reports, so a paid row is named once.
    """
    skip = set(skip)
    ceilings = ceilings_of(baseline)
    rows = baseline.get("files") or {}
    lines = []
    for name in sorted(measured):
        row = rows.get(name) or {}
        for metric in METRICS:
            value = measured[name].get(metric)
            if value is None or (name, metric) in skip:
                continue
            if metric in row:
                if value > row[metric]:
                    lines.append(
                        "%s: %s %s > recorded %s (ceiling %s)"
                        % (name, metric, value, row[metric], ceilings[metric])
                    )
            elif value > ceilings[metric]:
                lines.append("%s: %s %s > ceiling %s" % (name, metric, value, ceilings[metric]))
    return lines


def paid_rows(measured, baseline, path, metrics):
    """[(file, metric, recorded)] for a tracked row a `--paid` path declares as debt to pay.

    The path is checked against the tree it names: a path that measures no file is refused rather
    than passed, so a misspelt one cannot report a paid debt.
    """
    prefix = Path(path).as_posix().strip("/")
    if prefix in ("", "."):
        selected = sorted(measured)
    else:
        selected = [name for name in sorted(measured) if name.startswith(prefix + "/")]
    if not selected:
        raise Refused(
            "structure budget: --paid %s matches no Python file under the root: nothing was paid; "
            "name a path the walk measures, such as a package directory" % path
        )
    rows = baseline.get("files") or {}
    found = []
    for name in selected:
        row = rows.get(name) or {}
        for metric in metrics:
            if metric in row:
                found.append((name, metric, row[metric]))
    return found


def paid_line(row):
    """One paid row as the offender line a reader sees."""
    name, metric, recorded = row
    return "%s: %s paid (recorded %s)" % (name, metric, recorded)


def paid_lines(measured, baseline, path, metrics):
    """The paid offender lines one `--paid` path declares: every tracked row it names is a debt."""
    return [paid_line(row) for row in paid_rows(measured, baseline, path, metrics)]


def baseline_document(measured, rows=None, measured_at=None):
    """The baseline document: the five ceilings, and a row only where a file needs one.

    A file under every ceiling needs no row, so a new file under the ceiling is free. A recorded row
    is re-measured and kept even under its ceiling: a row is how an area declares a tighter limit
    than the tree's own, and what `--paid` measures the debt against.
    """
    rows = rows or {}
    files = {}
    for name in sorted(measured):
        numbers = measured[name]
        keep: set[str] = set(rows.get(name) or ())
        keep.update(metric for metric in METRICS if numbers.get(metric, 0) > CEILINGS[metric])
        if keep:
            files[name] = {metric: numbers[metric] for metric in METRICS if metric in keep}
    return {
        "measured_at": measured_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "ceilings": dict(CEILINGS),
        "files": files,
    }


def summary(measured, baseline):
    """The clean run's line: the five ceilings read, then how much of the tree carries a row."""
    ceilings = ceilings_of(baseline)
    rows = baseline.get("files") or {}
    return "structure budget: ceilings %s, %d files measured, %d carry a recorded row" % (
        ", ".join("%s %s" % (metric, ceilings[metric]) for metric in METRICS),
        len(measured),
        len(rows),
    )


def read_baseline(path):
    """The committed baseline, or a refusal naming it."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused("structure budget: cannot read %s: %s" % (path, error)) from error


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="measure a tree's five structure ceilings against its committed baseline"
    )
    parser.add_argument("--root", default=".", help="the tree to measure")
    parser.add_argument("--baseline", default=str(BASELINE), help="the committed budget JSON")
    parser.add_argument(
        "--write", action="store_true", help="record today's numbers as the baseline and exit 0"
    )
    parser.add_argument(
        "--paid",
        action="append",
        default=[],
        metavar="PATH",
        help="a path whose recorded rows are a debt to pay; repeatable",
    )
    parser.add_argument(
        "--metric",
        action="append",
        default=[],
        choices=METRICS,
        help="a metric the paid paths must clear; repeatable, and required with --paid",
    )
    args = parser.parse_args(argv)
    measured = measure_tree(args.root)
    try:
        if args.write:
            rows = (
                (read_baseline(args.baseline).get("files") or {})
                if Path(args.baseline).exists()
                else {}
            )
            document = baseline_document(measured, rows)
            Path(args.baseline).write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
            print(
                "structure budget: wrote %s (%d files measured, %d carry a recorded row)"
                % (args.baseline, len(measured), len(document["files"]))
            )
            return 0
        baseline = read_baseline(args.baseline)
        if args.paid and not args.metric:
            raise Refused(
                "structure budget: --paid needs at least one --metric: name the metrics whose "
                "recorded rows are the debt, such as --metric module-lines"
            )
        skip: set[tuple[str, str]] = set()
        lines = []
        for path in args.paid:
            for row in paid_rows(measured, baseline, path, args.metric):
                lines.append(paid_line(row))
                skip.add((row[0], row[1]))
        lines.extend(growth_lines(measured, baseline, skip))
    except Refused as error:
        print(error, file=sys.stderr)
        return 2
    if lines:
        for line in lines:
            print(line)
        print(
            "structure budget: %d file(s) grew; record a deliberate change with --write"
            % len({line.split(":", 1)[0] for line in lines}),
            file=sys.stderr,
        )
        return 1
    print(summary(measured, baseline))
    return 0


if __name__ == "__main__":
    sys.exit(main())
