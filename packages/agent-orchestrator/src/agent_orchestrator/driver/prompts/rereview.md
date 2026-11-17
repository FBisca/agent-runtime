Re-review $unit: the fix only. The worker fixed what you and CI raised and reported again; only the fix is new: `git -C $checkout diff $since..$head`. Read that diff; the rest of the change stays as you reviewed it.

The worker's report:
$reported

What the worker was asked to fix (review findings and CI failures): $findings
$earlier
A reviewer that holds no earlier review of this change reads $brief first.
Judge the fix by the same rules and hand in a verdict of the same shape, as the text of your agent-runtime `report` call, kind=complete.
