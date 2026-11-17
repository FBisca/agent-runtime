Contract: <ROOT>/run/contract.md

# Fix round 1 for T1 (task t1r1): ci findings

Original task: <ROOT>/run/t1/task.md. Use task_id `t1r1` in your report.
Same checkout and branch: <ROOT>/work on feat/demo-t1. Work only here; start from: src/a/: 2 files in <ROOT>/work/src/a, write <ROOT>/work/docs/note.md (edit any other file the change needs, except one another unfinished ticket lists in its paths).
Checkout contents: 2 files in <ROOT>/work
Scope contents: src/a/: 2 files in <ROOT>/work/src/a
Findings, verbatim, also in <ROOT>/run/t1r1/findings.json. Fix only these:

- ci lint: E501 line too long (exit 1 of `ruff check`; full log /logs/ci.log)

These are CI failures; each finding names the failed job, the run id and the last lines of its failed step. Re-run those tests locally to reproduce each failure, fix it, and re-run them. When they pass locally and still fail in CI, hand in BLOCKED with a blocker that starts `environment:` and says what differs.
Commit and push to the same branch; open no new pull request. Run before you hand in: the repository's full CI gate.
Run the checks above, then run only the test files you wrote or changed; leave every other test to CI. Run each command in the foreground under a timeout, read its failures from that run's output or log, and re-run only the tests that failed.
Report review_rounds 0: the driver counts failed rounds.
