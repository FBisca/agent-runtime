Contract: <ROOT>/run/contract.md

# Answer to T1 (task t1)

What happened: the worker asked which flag to use
The Lead's instruction: continue
The Lead's answer: use the second flag
This is the same attempt, not a new one: keep using task_id `t1` in your report.
Original task: <ROOT>/run/t1/task.md. Same checkout and branch: <ROOT>/work on feat/demo-t1. Work only here; start from: src/a/: 2 files in <ROOT>/work/src/a, write <ROOT>/work/docs/note.md (edit any other file the change needs, except one another unfinished ticket lists in its paths).
Checkout contents: 2 files in <ROOT>/work
Scope contents: src/a/: 2 files in <ROOT>/work/src/a
Commit and push to the same branch. Run before you hand in: uv run pytest -m unit.
Run the checks above, then run only the test files you wrote or changed; leave every other test to CI. Run each command in the foreground under a timeout, read its failures from that run's output or log, and re-run only the tests that failed.
Report review_rounds 0: the driver counts failed rounds.
