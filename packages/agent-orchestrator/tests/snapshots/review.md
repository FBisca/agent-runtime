# Review T1: read, judge, hand in one verdict

You are the reviewer: read the change, judge it, hand in a verdict. The repository stays as it is: your commands are git reads (`git diff`, `git show`, `git log`). CI runs the tests, the format and the lint while you read, and reports a failure itself.

Inputs, each read by you:
- The ticket's scenarios, verbatim in <ROOT>/run/t1/task.md
- The change: `git -C <ROOT>/work diff origin/main...def5678`
- The implementer's report, accepted by the driver:
```json
{
  "head": "def5678",
  "task_id": "t1",
  "status": "COMPLETE"
}
```
- Plan: <ROOT>/plan.md (its Decisions and Seams)
- Checkout contents: 2 files in <ROOT>/work
- Repo rules: none found; follow the code's own conventions

## The pass
1. Read the whole diff first: every file, every test it adds or changes, the commit messages. Judge after the last file.
2. Name what a user or caller would hit: wrong behaviour, data loss, a lost wake, a plan or run that cannot finish or restart, a scenario the ticket satisfies that no test proves, a test that still passes with the change reverted. A test proves its scenario when it asserts the THEN on the real code path; the report's `red` holds each covering test's failing output from before the fix.
3. When a claim depends on how a library or tool behaves, read its documentation (the repository's docs, or context7) and say in the claim what it says.
4. Each finding is one specific, checkable claim about one line, quoted as the file shows it.

## The verdict
Hand it in as the text of your agent-runtime `report` call, kind=complete, with the assignment id of this brief:
{"verdict": "pass" | "block", "findings": [{"file": "<path in the checkout>", "line": <int>, "quote": "<the line as written>", "claim": "<what breaks and for whom>", "rule": "<bold title of the rulebook rule it breaks>"}]}
`rule` is optional: leave it out when the finding is your own judgment; both kinds block equally. The verdict is `block` exactly when `findings` has an entry. The driver checks the JSON on receipt and answers a wrong shape at once with the errors; hand in the corrected verdict.
