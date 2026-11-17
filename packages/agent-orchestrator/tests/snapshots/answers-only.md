Contract: <ROOT>/run/contract.md

# Answers only for T1 (task t1a1)

The review passed. These findings need your answer, nothing else (also in <ROOT>/run/t1a1/findings.json):

- B1 (blocking), answer required: src/a/one.py:12 retry loop never ends. Fix: stop after three tries. Evidence: `pytest tests/test_one.py` exited 1 (log /logs/one.log): FAILED test_one
- N1: src/a/two.py name the helper

Change no code unless an answer needs it. Report with task_id `t1a1`; `responses` holds one `{"id", "status": "fixed|disputed", "commit", "note", "changes_code"?}` per finding above. A `disputed` answer that needs a code change sets `changes_code` true and names the commit: the reviewer sees it.
Same checkout and branch: <ROOT>/work on feat/demo-t1. Start from: src/a/: 2 files in <ROOT>/work/src/a, write <ROOT>/work/docs/note.md (edit any other file the change needs, except one another unfinished ticket lists in its paths).
Checkout contents: 2 files in <ROOT>/work
Scope contents: src/a/: 2 files in <ROOT>/work/src/a
Re-run only what a change touches, then the final gate if you changed code. Open no new pull request.
