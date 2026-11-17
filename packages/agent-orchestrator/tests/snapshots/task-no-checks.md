Contract: <ROOT>/run/contract.md

# T2: a quiet log

Report with task_id `t1`: the driver refuses a report whose task_id is not this one.

Plan: <ROOT>/plan.md (read its Decisions and Seams).
Checkout: <ROOT>/work on branch feat/demo-t1 (new, from origin/main). Work only here.
Checkout contents: 2 files in <ROOT>/work
Scope contents: src/a/: 2 files in <ROOT>/work/src/a
Owns (edit nothing else): src/a/: 2 files in <ROOT>/work/src/a, write <ROOT>/work/docs/note.md.
Shared databases are read-only; schema changes go through migrations in your own branch.

Scenarios, verbatim. Work red-first: a test per scenario, seen failing before the code exists; keep that failing output for the report's `red`.
Read the `tdd` skill first when available; otherwise follow the TDD rules in the contract.

- **S1** WHEN x THEN y

Before you hand in, run: the repository's full CI gate.
Run the checks above, then run only the test files you wrote or changed; leave every other test to CI. Run each command in the foreground under a timeout, read its failures from that run's output or log, and re-run only the tests that failed.
`uv run pytest -n auto` is the whole suite: do not run it; CI runs it.
Then open a draft pull request against main.
