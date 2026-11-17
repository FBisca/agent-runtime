Contract: $contract
Report: $report

# Answer to $unit (task $task_id)

What happened: $reason
The Lead's instruction: $instruction
The Lead's answer: $answer
$open_findings
This is the same attempt, not a new one: keep using task_id `$task_id` in your report, and write it to the path above (files are write-once; an earlier report stays where it is).
Original task: $task_md. Same checkout and branch: $checkout on $branch. Work only here; start from: $scope (edit any other file the change needs, except one another unfinished ticket lists in its paths).
Checkout contents: $checkout_contents
Scope contents: $scope_contents
Commit and push to the same branch. Run before you report COMPLETE: $checks.
$check_instructions
Report review_rounds 0: the driver counts failed rounds.
