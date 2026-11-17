Contract: $contract

# Fix round $attempt for $unit (task $task_id): $source findings

Original task: $task_md. Use task_id `$task_id` in your report.
Same checkout and branch: $checkout on $branch. Work only here; start from: $scope (edit any other file the change needs, except one another unfinished ticket lists in its paths).
Checkout contents: $checkout_contents
Scope contents: $scope_contents
Findings, verbatim, also in $findings_path. Fix only these:

$items

$ci_instructions
$lead_answer
Fix each finding you agree with. A finding you disagree with: leave the code and say why in your report's `notes`; the re-review judges it.
Commit and push to the same branch; open no new pull request. Run before you hand in: $checks.
$check_instructions
Report review_rounds 0: the driver counts failed rounds.
