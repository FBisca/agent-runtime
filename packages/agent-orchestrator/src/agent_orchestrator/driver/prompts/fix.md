Contract: $contract
Report: $report

# Fix round $attempt for $unit (task $task_id): $source findings

Original task: $task_md. Use task_id `$task_id` in your report.
Same checkout and branch: $checkout on $branch. Work only here; start from: $scope (edit any other file the change needs, except one another unfinished ticket lists in its paths).
Checkout contents: $checkout_contents
Scope contents: $scope_contents
Findings, verbatim, also in $findings_path. Fix only these:

$items

$reviewer_checks
$answer_required
$lead_answer
Commit and push to the same branch; open no new pull request. Run before you report COMPLETE: $checks.
$check_instructions
Report review_rounds 0: the driver counts failed rounds.
