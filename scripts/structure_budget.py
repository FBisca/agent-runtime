"""The tree's structure budget: five ceilings, a base revision, a ratchet on growth.

The tree's other gates count ruff and ty diagnostics and the suite's seconds. None of them sees a
module grow, a class collect methods or a function collect parameters, and a file nobody reads whole
is where the repository's own "fix the class, not the instance" rule stops working. This measures
every Python file under `--root` — source and test alike — against five ceilings and prints the
growth as one line per offender:

    <path>: <metric> <value> > ceiling <ceiling>
    <path>: <metric> <value> > recorded <base value> (ceiling <ceiling>)

One sentence decides every line: a file may exceed a ceiling only if it already did at `--since`, and
only by getting no worse. Nothing per file is stored — `git` holds the base, so a split, a rename or
a move costs the gate nothing.

Exit 0 is a clean tree, 1 an offender, 2 a refusal: a budget that cannot be read, a `--since` that is
not a revision, or a `--paid` path that measures no file. A paid path takes no grandfather from the
base for the metrics it names, so a deliberate debt has to be paid down rather than inherited. The
only thing `--write` can record is the day the ceilings were reviewed: because the base is git, no
run can accept a growth, and an offender has to shrink.
"""

import argparse
import ast
import io
import json
import os
import subprocess
import sys
import tarfile
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path

BASELINE = (
    Path(__file__).resolve().parents[1] / "acceptance" / "structure" / "structure_budget.json"
)

# The revision a change lands on: the base a branch is held to when `--since` names none.
DEFAULT_SINCE = "origin/main"

# One ceiling per metric, the number no file may grow past, grandfathered or not.
CEILINGS = {
    "module-lines": 1000,
    "class-methods": 25,
    "function-lines": 50,
    "function-branches": 12,
    "parameters": 7,
}
METRICS = tuple(CEILINGS)

# A walk skips a virtualenv, a git store and a package tree, and nothing else: an exemption list is
# what a new offender would use to enter the tree unmeasured. Only the fallback walk and the base
# archive need it — in a checkout, git's own listing already says which files the tree holds.
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


def tracked(root):
    """The Python files git says `root` holds, relative to it and POSIX, or None outside a checkout.

    Git is what decides which files the tree holds: it tracks the files a commit carries, it never
    descends into another checkout, and an ignored directory is not source at all. A scratch copy or
    a nested worktree therefore cannot be measured as this tree's source, whatever it holds.
    """
    try:
        done = git(root, "ls-files", "-z", "--cached", text=False)
    except Refused:  # no git at all: the walk is the only answer there is
        return None
    if done.returncode:
        return None
    names = done.stdout.decode("utf-8", "surrogateescape").split("\0")
    return sorted(name for name in names if name.endswith(".py"))


def walk(root):
    """Every Python file under `root`, as a POSIX path relative to it, sorted.

    What belongs to the tree is what git tracks, so a copy another tree holds is neither measured nor
    a growth. A tree outside a repository has no index to ask, and is walked instead.
    """
    root = Path(root)
    listed = tracked(root)
    if listed is not None:
        return listed
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP)
        for filename in filenames:
            if filename.endswith(".py"):
                found.append((Path(dirpath) / filename).relative_to(root).as_posix())
    return sorted(found)


def measured_sources(sources: Iterable[tuple[str, str]]):
    """{path: {metric: number}} for (path, source) pairs, a source no parser can read skipped.

    The syntax gates own a file that will not parse, and a budget that refused the whole tree over
    one file would hide every other number.
    """
    measured = {}
    for name, source in sources:
        try:
            measured[name] = metrics_of(source)
        except (SyntaxError, ValueError):
            continue
    return measured


def tree_sources(root):
    """(path, source) for every Python file under `root`, a file it cannot read skipped."""
    for name in walk(root):
        try:
            yield name, (Path(root) / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue


def measure_tree(root):
    """{path: {metric: number}} for every Python file under `root`, as the working tree holds it."""
    return measured_sources(tree_sources(root))


def git(root, *args, text=True):
    """One git command run in `root`, its output captured; the caller reads the exit code."""
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=text, timeout=300
        )
    except OSError as error:
        raise Refused("structure budget: git is not available, so --since has no base: %s" % error)


def archive_prefix(root):
    """Where `root` sits inside its checkout, as the archive member prefix its files carry."""
    done = git(root, "rev-parse", "--show-prefix")
    if done.returncode != 0:
        raise Refused(
            "structure budget: --root %s is not inside a git checkout, so --since has no base to "
            "read: run it from the checkout, or point --root at one" % root
        )
    prefix = done.stdout.strip()
    return prefix + "/" if prefix else ""


def archive_sources(data: bytes, prefix: str) -> Iterator[tuple[str, str]]:
    """(path relative to the root, source) for every Python file of one git archive."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            name = member.name[len(prefix) :]
            if not name.endswith(".py") or SKIP.intersection(name.split("/")):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            try:
                yield name, handle.read().decode("utf-8")
            except UnicodeDecodeError:
                continue


def measure_at(root, rev):
    """{path: {metric: number}} for the tree under `root` as it stood at `rev`.

    The base is read from git's own archive of that revision, so nothing per file is committed and a
    shallow checkout's history is not walked file by file.
    """
    prefix = archive_prefix(root)
    verify = git(root, "rev-parse", "--verify", "--quiet", rev + "^{commit}")
    if verify.returncode != 0:
        raise Refused(
            "structure budget: --since %s is not a revision of %s: name one the checkout holds, "
            "such as origin/main or a pull request's base commit" % (rev, root)
        )
    done = subprocess.run(
        ["git", "-C", str(root), "archive", "--format=tar", rev],
        capture_output=True,
        timeout=300,
    )
    if done.returncode != 0:
        raise Refused(
            "structure budget: cannot read %s at %s: %s"
            % (root, rev, done.stderr.decode("utf-8", "replace").strip())
        )
    return measured_sources(archive_sources(done.stdout, prefix))


def moved_paths(root, rev):
    """{path now: path at `rev`} for every move git's own rename detection reports.

    `git diff` reads the revision against the working tree, so a staged, uncommitted or committed
    `git mv` is seen once. A file that was copied rather than moved is no move: its original is
    still there, holding its number. Paths come out relative to `root`, as the base's do.

    `-z` keeps each path as the bytes it is: git else C-quotes any name holding a non-ASCII
    character or a quote, a backslash or a tab, and the quoted spelling matches no base row. One
    field per NUL, a rename carrying both its paths and every other status one, is the stride that
    stays aligned beside a modified file in the same diff.
    """
    done = git(
        root,
        "diff",
        "--name-status",
        "--find-renames",
        "--relative",
        "-z",
        rev,
        "--",
        text=False,
    )
    if done.returncode != 0:
        raise Refused(
            "structure budget: cannot read the moves since %s in %s: %s"
            % (rev, root, done.stderr.decode("utf-8", "replace").strip())
        )
    fields = done.stdout.decode("utf-8", "surrogateescape").split("\0")
    moved = {}
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        paths = 2 if status.startswith("R") else 1
        if paths == 2:
            moved[fields[index + 2]] = fields[index + 1]
        index += paths + 1
    return moved


def inherited(base, moved):
    """The base with every moved file's numbers carried to the path that now holds it.

    A move is not a crossing: the content under the new path was measured at `rev` under the old
    one. A path that measured a file of its own at `rev` keeps that number, so a move onto it
    cannot launder growth.
    """
    carried = dict(base)
    for now, before in moved.items():
        if now not in carried and before in carried:
            carried[now] = carried[before]
    return carried


def ceilings_of(budget):
    """The ceilings the committed budget declares, a metric it left out falling back to ours.

    A ceiling that is not a whole number is refused where it is read: a gate that compared a file
    with a typo would fail every run with a traceback instead of naming the budget's own fault.
    """
    declared = budget.get("ceilings") or {}
    ceilings = {}
    for metric in METRICS:
        value = declared.get(metric, CEILINGS[metric])
        try:
            ceilings[metric] = int(value)
        except (TypeError, ValueError) as error:
            raise Refused(
                "structure budget: the budget's %s ceiling is not a whole number: %r: record one "
                "with --write" % (metric, value)
            ) from error
    return ceilings


def paid_paths(measured, paths):
    """The `--paid` paths as the prefixes a file is matched against.

    A path is checked against the tree it names: one that measures no file is refused rather than
    passed, so a misspelt path cannot report a paid debt.
    """
    prefixes = []
    for path in paths:
        prefix = Path(path).as_posix().strip("/")
        if not any(under(name, prefix) for name in measured):
            raise Refused(
                "structure budget: --paid %s matches no Python file under the root: nothing was "
                "paid; name a path the walk measures, such as a package directory" % path
            )
        prefixes.append(prefix)
    return prefixes


def under(name, prefix):
    """Whether one measured file sits under a path, the root itself naming every file."""
    return prefix in ("", ".") or name == prefix or name.startswith(prefix + "/")


def growth_lines(measured, base, ceilings=None, paid_paths=(), paid_metrics=()):
    """One line per ceiling a file may not hold: it crossed one, worsened past its base, or is paid.

    `base` is what the tree measured at `--since`; a file absent from it measured nothing there, so
    every ceiling is one it may not pass. `paid_paths` and `paid_metrics` name the files that take no
    grandfather from the base at all.
    """
    ceilings = ceilings_of({"ceilings": ceilings or {}})
    paid = [Path(path).as_posix().strip("/") for path in paid_paths]
    paid_metrics = set(paid_metrics)
    lines = []
    for name in sorted(measured):
        numbers = measured[name]
        previous = base.get(name) or {}
        for metric in METRICS:
            value = numbers.get(metric)
            ceiling = ceilings[metric]
            if value is None or value <= ceiling:
                continue
            if metric in paid_metrics and any(under(name, prefix) for prefix in paid):
                lines.append("%s: %s %s > paid (ceiling %s)" % (name, metric, value, ceiling))
            elif previous.get(metric, 0) > ceiling:
                if value > previous[metric]:
                    lines.append(
                        "%s: %s %s > recorded %s (ceiling %s)"
                        % (name, metric, value, previous[metric], ceiling)
                    )
            else:
                lines.append("%s: %s %s > ceiling %s" % (name, metric, value, ceiling))
    return lines


def baseline_document(ceilings=None, measured_at=None):
    """The committed budget: the five ceilings and the day they were measured, and nothing per file.

    A file may exceed a ceiling only if it already did at the base revision and by getting no worse,
    so git holds the base and no row is ever rewritten by a split, a rename or a move.
    """
    return {
        "measured_at": measured_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "ceilings": ceilings_of({"ceilings": ceilings or {}}),
    }


def ceilings_phrase(ceilings):
    """The five ceilings as one reader-facing phrase, a ceiling named beside its metric."""
    return ", ".join("%s %s" % (metric, ceilings[metric]) for metric in METRICS)


def over_ceiling(measured, ceilings):
    """Whether the tree still holds a file over a ceiling, on a grandfather only `--paid` ends."""
    return any(
        numbers.get(metric, 0) > ceilings[metric]
        for numbers in measured.values()
        for metric in METRICS
    )


def summary(measured, budget, since):
    """The clean run's line: the five ceilings read, how many files were measured, and the base."""
    return (
        "structure budget: ceilings %s, %d files measured, base %s (no per-file row is stored)"
        % (ceilings_phrase(ceilings_of(budget)), len(measured), since)
    )


def read_baseline(path):
    """The committed budget, or a refusal naming it."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused("structure budget: cannot read %s: %s" % (path, error)) from error


def write_budget(args):
    """Re-stamp the committed budget with today, keeping the ceilings it declares.

    It records nothing about a file and accepts no growth: the base is the `--since` revision, so a
    file over a ceiling it crossed still fails the next run. The day is the only thing it writes.
    """
    existing = read_baseline(args.baseline) if Path(args.baseline).exists() else {}
    document = baseline_document(ceilings_of(existing))
    Path(args.baseline).write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    print(
        "structure budget: wrote %s (ceilings %s, measured %s; it never accepts growth: the base "
        "is the --since revision, so an offender has to shrink)"
        % (args.baseline, ceilings_phrase(document["ceilings"]), document["measured_at"])
    )
    return 0


def check(args):
    """The measured tree, the budget it is held to, and the offender lines of one run."""
    budget = read_baseline(args.baseline)
    if args.paid and not args.metric:
        raise Refused(
            "structure budget: --paid needs at least one --metric: name the metrics whose "
            "grandfather the paid paths must give up, such as --metric module-lines"
        )
    measured = measure_tree(args.root)
    paths = paid_paths(measured, args.paid)
    base = measure_at(args.root, args.since)
    base = inherited(base, moved_paths(args.root, args.since))
    return measured, budget, growth_lines(measured, base, ceilings_of(budget), paths, args.metric)


def parser():
    """The command line: what to measure, what to hold it to, and how."""
    made = argparse.ArgumentParser(
        description="measure a tree's five structure ceilings against the base revision it grew from"
    )
    made.add_argument("--root", default=".", help="the tree to measure")
    made.add_argument("--baseline", default=str(BASELINE), help="the committed budget JSON")
    made.add_argument(
        "--since",
        default=DEFAULT_SINCE,
        help="the revision a file may already have been over a ceiling at (default %(default)s)",
    )
    made.add_argument(
        "--write",
        action="store_true",
        help="stamp the day the ceilings were reviewed and exit 0; it never accepts growth, "
        "which only shrinking below the base or the ceiling clears",
    )
    made.add_argument(
        "--paid",
        action="append",
        default=[],
        metavar="PATH",
        help="a path that takes no grandfather from the base; repeatable",
    )
    made.add_argument(
        "--metric",
        action="append",
        default=[],
        choices=METRICS,
        help="a metric the paid paths must hold, grandfathered or not; repeatable, and needed with --paid",
    )
    return made


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.write:
            return write_budget(args)
        measured, budget, lines = check(args)
    except Refused as error:
        print(error, file=sys.stderr)
        return 2
    if lines:
        for line in lines:
            print(line)
        print(
            "structure budget: %d file(s) are over a ceiling they may not hold: bring each under "
            "the ceiling, or back to the number it had at %s"
            % (len({line.split(":", 1)[0] for line in lines}), args.since),
            file=sys.stderr,
        )
        return 1
    # stdout is the list of what the gate refused. A tree still holding a grandfather is clean but
    # not at its ceilings, and saying so belongs on stderr, where it cannot read as a refusal.
    at_ceilings = not over_ceiling(measured, ceilings_of(budget))
    print(summary(measured, budget, args.since), file=sys.stdout if at_ceilings else sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
