Contract: <ROOT>/run/contract.md

# Retry T1 (task t1d1) after a decision

What happened: the worker asked which flag to use
The Lead's instruction: start over
Original task: <ROOT>/run/t1/task.md. Use task_id `t1d1` in your report.
Same checkout and branch: <ROOT>/work on feat/demo-t1. Work only here; start from: src/a/: 2 files in <ROOT>/work/src/a, write <ROOT>/work/docs/note.md (edit any other file the change needs, even one another ticket lists in its paths: keep that change, the driver tells the Lead).
Checkout contents: 2 files in <ROOT>/work
Scope contents: src/a/: 2 files in <ROOT>/work/src/a
Commit and push to the same branch. Run before you hand in: the repository's full CI gate.
Run the checks above, then run only the test files you wrote or changed; leave every other test to CI. Run each command in the foreground under a timeout, read its failures from that run's output or log, and re-run only the tests that failed.
Report review_rounds 0: the driver counts failed rounds.
