Contract: $contract

# $ticket: $delivers

Report with task_id `$task_id`: the driver refuses a report whose task_id is not this one.

Plan: $plan (read its Decisions and Seams).
Checkout: $checkout on branch feat/$worker (new, from $base). Work only here.
Checkout contents: $checkout_contents
Scope contents: $scope_contents
Owns (edit nothing else): $owns.
$rulebook
Shared databases are read-only; schema changes go through migrations in your own branch.

Scenarios, verbatim. Work red-first: a test per scenario, seen failing before the code exists; keep that failing output for the report's `red`.
$tdd

$scenarios

Before you hand in, run: $checks.
$check_instructions
$full_suite
Then open a draft pull request against $pr_base.
