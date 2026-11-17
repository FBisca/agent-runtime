Contract: <ROOT>/run/contract.md
Report: <ROOT>/run/t1r2/report.json

# Fix round 2 for T1 (task t1r2): review findings

Original task: <ROOT>/run/t1/task.md. Use task_id `t1r2` in your report.
Same checkout and branch: <ROOT>/work on feat/demo-t1. Work only here; start from: src/a/: 2 files in <ROOT>/work/src/a, write <ROOT>/work/docs/note.md (edit any other file the change needs, except one another unfinished ticket lists in its paths).
Checkout contents: 2 files in <ROOT>/work
Scope contents: src/a/: 2 files in <ROOT>/work/src/a
Findings, verbatim, also in <ROOT>/run/t1r2/findings.json. Fix only these:

- B1 (blocking), answer required: src/a/one.py:12 retry loop never ends. Fix: stop after three tries. Evidence: `pytest tests/test_one.py` exited 1 (log /logs/one.log): FAILED test_one
- N1: src/a/two.py name the helper

The reviewer already ran these; re-run only what your fix touches, then the final gate:
- `uv run pytest -m unit` exit 0 at abc1234

Items marked answer_required need an answer: add `responses` to your report, one `{"id", "status": "fixed|disputed", "commit", "note", "changes_code"?}` each. Without them the driver asks you once more.

The Lead's answer: keep the public name

Commit and push to the same branch; open no new pull request. Run before you report COMPLETE: uv run pytest -m unit.
Report each required check with its command exactly as written above, whatever your shell runs it through.
Run the checks above, then run only the test files you wrote or changed; leave every other test to verify and CI. Run each command in the foreground under a timeout, read its failures from that run's output or log, and re-run only the tests that failed.
Report review_rounds 0: the driver counts failed rounds.
