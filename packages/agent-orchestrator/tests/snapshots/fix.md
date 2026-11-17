Contract: <ROOT>/run/contract.md

# Fix round 2 for T1 (task t1r2): review findings

Original task: <ROOT>/run/t1/task.md. Use task_id `t1r2` in your report.
Same checkout and branch: <ROOT>/work on feat/demo-t1. Work only here; start from: src/a/: 2 files in <ROOT>/work/src/a, write <ROOT>/work/docs/note.md (edit any other file the change needs, except one another unfinished ticket lists in its paths).
Checkout contents: 2 files in <ROOT>/work
Scope contents: src/a/: 2 files in <ROOT>/work/src/a
Findings, verbatim, also in <ROOT>/run/t1r2/findings.json. Fix only these:

- R1: src/a/one.py:12 `while True:` - the retry loop never ends (rule: A failure surfaces as an error a caller can act on.)
- R2: src/a/two.py:3 `cache = {}` - the cache is never cleared
- ci lint: E501 line too long (exit 1 of `ruff check`; full log /logs/ci.log)

These are CI failures; each finding names the failed job, the run id and the last lines of its failed step. Decide what each one is. A real failure: reproduce it locally, fix it, and re-run the tests. A flaky check or a CI machine problem (it passes locally, or its log shows no failing test or check step): re-run the failed jobs with `gh run rerun <run id> --failed`, wait for the run to finish, and when it is green hand in COMPLETE with a `notes` line naming the flaky check. When it still fails and you cannot make it pass, hand in BLOCKED with a blocker that starts `environment:` and says what differs.
The Lead's answer: keep the public name

Fix each finding you agree with. A finding you disagree with: leave the code and say why in your report's `notes`; the re-review judges it.
Commit and push to the same branch; open no new pull request. Run before you hand in: uv run pytest -m unit.
Run the checks above, then run only the test files you wrote or changed; leave every other test to CI. Run each command in the foreground under a timeout, read its failures from that run's output or log, and re-run only the tests that failed.
Report review_rounds 0: the driver counts failed rounds.
