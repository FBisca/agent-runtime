#!/usr/bin/env python3
"""Deterministic converge gate: four checks on a repo against an approved plan.

Usage: converge.py <plan.md> (--ticket T<n> | --final) [--base <ref>] [--repo <path>] [--specs docs/specs]
One of --ticket / --final is required. --base defaults to the ref origin/HEAD points to, else `main`.

1. coverage: every scenario in scope has a test named with its ID.
2. tagged:   every test added/changed since base carries a plan S-ID or a qualified spec ID.
3. scope:    every changed file matches a Paths entry in scope. The plan file and living-spec files
             (`<specs>/*.md`) are exempt; under --final so is `docs/plans/<plan file name>` even when the
             plan was passed from outside the repo.
4. spec:     if files under a living-spec area's `Paths:` changed, that spec file changed too.
             Runs only with --final; with --ticket it prints SKIPPED.
Exit 0 only if no check FAILs, else 1. A plan that is not `Status: APPROVED` (or, under --final only, ARCHIVED; the word may sit anywhere on a
line), or has no `- **S<n>**` scenario lines, or no `## Tickets` table is refused before any check: the
missing part goes to stderr, exit 2.

Scope: --ticket T<n> = that ticket's Satisfies IDs and Paths; --final = all scenarios and all tickets'
Paths. Satisfies holds IDs, comma lists, or ranges (`S1-S9`, `S17a\u2013S17e`) expanded over the plan's
declared IDs in document order.

ID convention: plan scenarios are `- **S<n>[a-z]** WHEN ...`; a stable spec ID is `<area>/R<n>`, where
<area> is the spec file's name; write it in the test name or, in Python, the docstring's first line.
Rust test files live under `tests/` (at any depth) or contain `#[cfg(test)]`. Tests are functions
with `#[test]` or a namespaced test attribute such as `#[tokio::test]`; other attributes may be
stacked around it. The name is the function name plus the first `///` doc-comment line directly
above its attribute stack. For example, `fn S1_source_add()` carries S1; `/// evidence/R4: citations`
above `#[test]` and `fn search_citations()` carries evidence/R4. `pub fn` and `async fn` count too.
A bare `R<n>` does not count.
Provenance (--final): a scenario `S<n>` is also covered by a test tagged `<area>/R<m>` when `<area>`'s spec
has a line for `R<m>` containing `(from <slug>/S<n>)`; <slug> is the plan's ``Slug: `<slug>` ``. In --final a
bare `S<n>` test name counts only if the test was added since base or its file changed since base.
A living-spec file needs a line `Paths: a/, src/**/*.ts` (backticks around items allowed), else the spec
check fails and names it. An ID matches as a whole token (neighbours are not letters or digits, so
`_` and `/` separate): `S1` never matches `S12` or `S1a`.

"Changed" = `git diff <base>...HEAD` + working tree vs HEAD + untracked files. A changed test
is an added line opening a test: JS/TS `it(`/`test(`/`describe(` with a quoted name, or Python
`def test_*` (name = function name plus the docstring's first text line: on the opening-quote line, or
the next non-empty line). Rust counts an added function line or test attribute line, using the
full declaration for its name and doc comment. A modified test
body whose opening line is untouched is not seen.
"""

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

TEST_FILE = ("*.test.*", "*.spec.*", "test_*.py", "*_test.py")
JS_NAME = re.compile(r"""\b(?:it|test|describe)(?:\.\w+)?\(\s*(["'`])(.*?)\1""")
PY_DEF = re.compile(r"^\s*(?:async\s+)?def (test_\w+)")
DOC = re.compile(r'''^\s*(?:"""|\'\'\')\s*(.*?)(?:"""|\'\'\')?\s*$''')
RS_ATTR = re.compile(r"^\s*#\[\s*([\w:]+)\b.*\]\s*$")
RS_FN = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)\s*\(")
STABLE = re.compile(r"(?<![A-Za-z0-9])(?:[\w-]+/)?(R\d+)(?![A-Za-z0-9])")


def git(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if p.returncode:
        sys.exit(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


def has_id(text, ident):
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(ident)}(?![A-Za-z0-9])", text) is not None


def is_test_file(path):
    path = Path(path)
    if path.suffix == ".rs":
        return "tests" in path.parts[:-1] or (
            path.is_file()
            and re.search(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]", path.read_text(errors="ignore"))
            is not None
        )
    return any(fnmatch.fnmatch(path.name, g) for g in TEST_FILE)


def matches(path, globs):
    return any(fnmatch.fnmatch(path, g) or path.startswith(g.rstrip("/") + "/") for g in globs)


def expand(cell, ids):
    """Satisfies cell -> IDs; ranges run over the declared `ids` in document order."""
    out = []
    for part in re.split(r"[,;]", cell):
        ends = re.findall(r"S\d+[a-z]?", part)
        if len(ends) == 2 and re.search(r"[-\u2013\u2014]", part) and all(e in ids for e in ends):
            i, j = ids.index(ends[0]), ids.index(ends[1])
            out += ids[i : j + 1]
        else:
            out += ends
    return out


def plan_data(text):
    """(declared IDs, {ticket: (satisfies, paths)})."""
    ids = re.findall(r"^\s*-\s+\*\*(S\d+[a-z]?)\*\*", text, re.M)
    tickets, cols, in_tickets = {}, None, False
    for line in text.splitlines():
        if line.startswith("#"):
            in_tickets = line.lstrip("# ").lower().startswith("tickets")
        elif in_tickets and line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cols is None:
                low = [c.lower() for c in cells]
                cols = (
                    next((i for i, c in enumerate(low) if c.startswith("satisfies")), None),
                    next((i for i, c in enumerate(low) if c.startswith("paths")), None),
                )
            elif not set(cells[0]) <= set("-: "):
                sat, pth = (cells[c] if c is not None and c < len(cells) else "" for c in cols)
                tickets[cells[0].strip("`")] = (
                    expand(sat, ids),
                    [p.strip().strip("`").strip() for p in pth.split(",") if p.strip()],
                )
    return ids, tickets


def names_in(lines, added=None):
    """Test names opened by `lines`; optional added line numbers select Rust declarations."""
    names, pending, want = (
        [],
        None,
        None,
    )  # want: None | "doc" (expect docstring) | "text" (after bare quotes)
    rust_test, rust_doc, rust_added = False, None, False
    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith("///") and not line.lstrip().startswith("////"):
            if rust_doc is None:
                rust_doc = line.lstrip()[3:].strip()
            continue
        attr = RS_ATTR.match(line)
        if attr:
            test_attr = attr.group(1).split("::")[-1] == "test"
            rust_test = rust_test or test_attr
            rust_added = rust_added or (test_attr and added is not None and number in added)
        else:
            fn = RS_FN.match(line)
            if rust_test and fn and (added is None or rust_added or number in added):
                names.append(fn.group(1) + (" " + rust_doc if rust_doc is not None else ""))
            rust_test, rust_doc, rust_added = False, None, False
        if pending is not None and line.strip():
            m = DOC.match(line) if want == "doc" else None
            if want == "text":
                pending += " " + line.strip().strip("\"'").strip()
            elif m:
                pending += " " + m.group(1)
            if m and not m.group(1).strip() and want == "doc":
                want = "text"
                continue
            names.append(pending)
            pending, want = None, None
            if m or want == "text":
                continue
        for m in JS_NAME.finditer(line):
            names.append(m.group(2))
        m = PY_DEF.match(line)
        if m:
            pending, want = m.group(1), "doc"
    if pending:
        names.append(pending)
    return names


def changed(repo, base):
    """(files, {test file: added lines}, Rust names) since base, including the working tree."""
    files, added, rust_names = set(), {}, set()
    untracked = git(repo, "ls-files", "-o", "--exclude-standard").split("\n")
    for ref in (f"{base}...HEAD", "HEAD"):
        files.update(git(repo, "diff", "--name-only", ref).split("\n"))
        cur, number, rust_lines = None, 0, {}
        for line in git(repo, "diff", "-U0", ref).splitlines():
            if line.startswith("+++ "):
                cur = line[6:] if line.startswith("+++ b/") else None
            elif line.startswith("@@ "):
                found = re.search(r"\+(\d+)", line)
                number = int(found.group(1)) if found else number
            elif cur and line.startswith("+"):
                if Path(cur).suffix == ".rs":
                    rust_lines.setdefault(cur, set()).add(number)
                else:
                    added.setdefault(cur, []).append(line[1:])
                number += 1
            elif line.startswith(" "):
                number += 1
        for f, numbers in rust_lines.items():
            path = Path(repo) / f
            if is_test_file(path):
                source = (
                    git(repo, "show", f"HEAD:{f}")
                    if ref != "HEAD"
                    else path.read_text(errors="ignore")
                )
                rust_names.update(names_in(source.splitlines(), numbers))
    for f in filter(None, untracked):
        files.add(f)
        if is_test_file(Path(repo) / f):
            lines = (Path(repo) / f).read_text(errors="ignore").splitlines()
            if Path(f).suffix == ".rs":
                rust_names.update(names_in(lines))
            else:
                added.setdefault(f, []).extend(lines)
    return (
        sorted(filter(None, files)),
        {f: line for f, line in added.items() if is_test_file(Path(repo) / f)},
        rust_names,
    )


def specs_in(repo, specs):
    out = {}
    for f in sorted((Path(repo) / specs).glob("*.md")):
        text = f.read_text()
        m = re.search(r"^[ \t]*(?:[-*][ \t]+)?Paths:[ \t]*(\S.*)$", text, re.M)
        globs = [p.strip().strip("`").strip() for p in m.group(1).split(",")] if m else []
        globs = [g for g in globs if g]
        if any(re.search(r"\s", g) for g in globs):
            globs = []  # prose, not a path list
        out[f.relative_to(repo).as_posix()] = (text, globs)
    return out


def refusal(text, final=False):
    """What is missing from the plan, or []. --final also accepts ARCHIVED: the archive branch sets it."""
    miss = []
    m = re.search(r"\bStatus:\s*\**\s*([A-Za-z-]+)", text)
    if not m:
        miss.append("no `Status:` line")
    elif m.group(1).upper() not in ("APPROVED", "ARCHIVED" if final else "APPROVED"):
        miss.append(f"Status is {m.group(1)}, not APPROVED")
    if not re.search(r"^\s*-\s+\*\*S\d+[a-z]?\*\*", text, re.M):
        miss.append("no `- **S<n>**` scenario lines")
    if not plan_data(text)[1]:
        miss.append("no `## Tickets` table with rows")
    return miss


def provenance(specs, slug):
    """{S-ID: {`<area>/R<m>`}} from spec lines `R<m> ... (from <slug>/S<n>)`."""
    out = {}
    for s, (text, _) in specs.items():
        for line in text.splitlines():
            r = re.search(r"(?<![A-Za-z0-9])(R\d+)(?![A-Za-z0-9])", line)
            for frm in re.findall(r"\(from\s+([^)]*)\)", line):
                for sid in re.findall(rf"(?<![\w-]){re.escape(slug)}/(S\d+[a-z]?)", frm):
                    if r:
                        out.setdefault(sid, set()).add(f"{Path(s).stem}/{r.group(1)}")
    return out


def default_base(repo):
    """The ref origin/HEAD points to (e.g. `origin/main`), else `main`."""
    p = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "origin/HEAD"],
        capture_output=True,
        text=True,
    )
    ref = p.stdout.strip()
    return ref if p.returncode == 0 and ref and ref != "origin/HEAD" else "main"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("plan")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ticket", metavar="T<n>")
    g.add_argument("--final", action="store_true")
    ap.add_argument("--base")
    ap.add_argument("--repo")
    ap.add_argument("--specs", default="docs/specs")
    a = ap.parse_args(argv)
    repo = Path(a.repo or git(".", "rev-parse", "--show-toplevel").strip()).resolve()
    plan = Path(a.plan).resolve()
    text = plan.read_text()
    miss = refusal(text, a.final)
    if miss:
        print(f"REFUSED {plan.name}: {'; '.join(miss)}", file=sys.stderr)
        return 2
    ids, tickets = plan_data(text)
    slug = (re.search(r"Slug:\s*`([^`]+)`", text) or [None, None])[1]
    if a.final:
        scope_ids = ids
        ticket_paths = [p for _, ps in tickets.values() for p in ps]
    elif a.ticket in tickets:
        scope_ids, ticket_paths = tickets[a.ticket]
    else:
        sys.exit(f"no ticket {a.ticket} in the plan's Tickets table")
    files, added, rust_names = changed(repo, a.base or default_base(repo))
    specs = specs_in(repo, a.specs)
    spec_ids = {
        f"{Path(s).stem}/{m.group(1)}"
        for s, (text, _) in specs.items()
        for m in STABLE.finditer(text)
    }
    try:
        plan_rel = plan.relative_to(repo).as_posix()
    except ValueError:
        plan_rel = None
    # The archive commits the plan file itself (Status ARCHIVED); under --final that one file, wherever the
    # driver's copy lives, is exempt by its repo path.
    exempt = {plan_rel, f"docs/plans/{plan.name}" if a.final else None} - {None}

    new_names = {n for ls in added.values() for n in names_in(ls)} | rust_names
    all_names = []  # (name, counts as bare S-ID evidence)
    for f in git(repo, "ls-files", "-co", "--exclude-standard").split("\n"):
        if f and is_test_file(repo / f) and (repo / f).is_file():
            for n in names_in((repo / f).read_text(errors="ignore").splitlines()):
                all_names.append((n, not a.final or f in files or n in new_names))
    prov = provenance(specs, slug) if a.final and slug else {}

    def covered(i):
        return any(ok and has_id(n, i) for n, ok in all_names) or any(
            has_id(n, r) for r in prov.get(i, ()) for n, _ in all_names
        )

    missing = [i for i in scope_ids if not covered(i)]

    untagged = [
        n
        for n in sorted(new_names)
        if not any(has_id(n, i) for i in ids) and not any(has_id(n, s) for s in spec_ids)
    ]
    spec_files = set(specs)
    code_files = [f for f in files if f not in exempt and f not in spec_files]
    outside = [f for f in code_files if not matches(f, ticket_paths)]
    stale = [
        Path(s).stem
        for s, (_, globs) in specs.items()
        if s not in files and any(matches(f, globs) for f in code_files)
    ]
    nopaths = [f"{s} (no parseable Paths: line)" for s, (_, globs) in specs.items() if not globs]
    stale = nopaths + stale  # a spec with no globs is never in `stale`

    results = [
        ("coverage", missing, "every scenario has a test"),
        ("tagged", untagged, "every changed test carries an ID"),
        ("scope", outside, "every changed file is inside ticket Paths"),
        ("spec", stale, "no living-spec area left stale" if specs else "no specs dir, skipped"),
    ]
    if not a.final:
        results[3] = ("spec", [], "SKIPPED")
    for name, bad, ok in results:
        if ok == "SKIPPED":
            print("SKIPPED spec: runs only with --final")
            continue
        print(f"FAIL {name}: {', '.join(bad)}" if bad else f"PASS {name}: {ok}")
    return 1 if any(bad for _, bad, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
