Contract: $contract

# Answers only for $unit (task $task_id)

The review passed. These findings need your answer, nothing else (also in $findings_path):

$items

Change no code unless an answer needs it. Report with task_id `$task_id`; `responses` holds one `{"id", "status": "fixed|disputed", "commit", "note", "changes_code"?}` per finding above. A `disputed` answer that needs a code change sets `changes_code` true and names the commit: the reviewer sees it.
Same checkout and branch: $checkout on $branch. Start from: $scope (edit any other file the change needs, except one another unfinished ticket lists in its paths).
Checkout contents: $checkout_contents
Scope contents: $scope_contents
Re-run only what a change touches, then the final gate if you changed code. Open no new pull request.
