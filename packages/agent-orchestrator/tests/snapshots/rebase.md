Contract: <ROOT>/run/contract.md

# Rebase T1 (task t1r1): the branch conflicts with its base

This is a rebase round: the pull request of T1 conflicts with its base. Rebase the branch onto the base, resolve the conflicts, and push the result to the same branch. What `gh` said (also in <ROOT>/run/t1r1/findings.json):

- conflict merge: CONFLICT in src/a/one.py (exit 1 of `ruff check`; full log /logs/ci.log)

Original task: <ROOT>/run/t1/task.md. Use task_id `t1r1` in your report.
Same checkout and branch: <ROOT>/work on feat/demo-t1. Work only here; start from: src/a/: 2 files in <ROOT>/work/src/a, write <ROOT>/work/docs/note.md (edit any other file the change needs, except one another unfinished ticket lists in its paths).
Checkout contents: 2 files in <ROOT>/work
Scope contents: src/a/: 2 files in <ROOT>/work/src/a
Commit and push to the same branch; open no new pull request. Run before you hand in: uv run pytest -m unit.
Run the checks above, then run only the test files you wrote or changed; leave every other test to CI. Run each command in the foreground under a timeout, read its failures from that run's output or log, and re-run only the tests that failed.
Report review_rounds 0: the driver counts failed rounds.
