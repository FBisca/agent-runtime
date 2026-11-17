Contract: <ROOT>/run/contract.md
Report: <ROOT>/run/t1/report.json

# Answer to T1 (task t1)

What happened: the worker asked which flag to use
The Lead's instruction: continue
The Lead's answer: use the second flag

Findings still open:
- B1 (blocking), answer required: src/a/one.py:12 retry loop never ends. Fix: stop after three tries. Evidence: `pytest tests/test_one.py` exited 1 (log /logs/one.log): FAILED test_one
- N1: src/a/two.py name the helper

This is the same attempt, not a new one: keep using task_id `t1` in your report, and write it to the path above (files are write-once; an earlier report stays where it is).
Original task: <ROOT>/run/t1/task.md. Same checkout and branch: <ROOT>/work on feat/demo-t1. Work only here; start from: src/a/: 2 files in <ROOT>/work/src/a, write <ROOT>/work/docs/note.md (edit any other file the change needs, except one another unfinished ticket lists in its paths).
Checkout contents: 2 files in <ROOT>/work
Scope contents: src/a/: 2 files in <ROOT>/work/src/a
Commit and push to the same branch. Run before you report COMPLETE: the repository's full CI gate.
Report each required check with its command exactly as written above, whatever your shell runs it through.
Run the checks above, then run only the test files you wrote or changed; leave every other test to verify and CI. Run each command in the foreground under a timeout, read its failures from that run's output or log, and re-run only the tests that failed.
Report review_rounds 0: the driver counts failed rounds.
