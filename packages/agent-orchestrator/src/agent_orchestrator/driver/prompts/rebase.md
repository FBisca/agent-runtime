Contract: $contract

# Rebase $unit (task $task_id): the branch conflicts with its base

This is a rebase round: the pull request of $unit conflicts with its base. Rebase the branch onto the base, resolve the conflicts, and push the result to the same branch. What `gh` said (also in $findings_path):

$items

Original task: $task_md. Use task_id `$task_id` in your report.
Same checkout and branch: $checkout on $branch. Work only here; start from: $scope (edit any other file the change needs, even one another ticket lists in its paths: keep that change, the driver tells the Lead).
Checkout contents: $checkout_contents
Scope contents: $scope_contents
Commit and push to the same branch; open no new pull request. Run before you hand in: $checks.
$check_instructions
Report review_rounds 0: the driver counts failed rounds.
