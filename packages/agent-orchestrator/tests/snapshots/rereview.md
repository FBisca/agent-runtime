Re-review T1: the fix only. The worker fixed what you and CI raised and reported again; only the fix is new: `git -C <ROOT>/work diff abc1234..def5678`. Read that diff; the rest of the change stays as you reviewed it.

The worker's report:
```json
{
  "head": "def5678",
  "task_id": "t1",
  "status": "COMPLETE"
}
```

What the worker was asked to fix (review findings and CI failures): <ROOT>/run/t1r1/findings.json
You blocked the last round: check each of your earlier findings against the fix, and raise it again when it still holds.
A reviewer that holds no earlier review of this change reads <ROOT>/run/t1-review/task.md first.
Judge the fix by the same rules and hand in a verdict of the same shape, as the text of your agent-runtime `report` call, kind=complete.
