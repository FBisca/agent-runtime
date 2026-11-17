# Review T1: the pass, then one verdict

Read these yourself; only the report is pasted here.
- The task the implementer was given: <ROOT>/run/t1/task.md (its What it delivers, Paths, the scenarios verbatim, the checks it owed)
- What it reported (the worker's hand-in, accepted by the driver):
no report recorded for this attempt yet
- Checkout contents: 2 files in <ROOT>/work
- The rules every task in this run follows: <ROOT>/run/contract.md
- Plan: <ROOT>/plan.md (its Decisions, Scenarios and Seams)
- Commit range: run `git -C <ROOT>/work diff origin/main...HEAD`
- Repo rules: none found; follow the code's own conventions

## The pass, in this order
1. Read the whole diff (`git -C <ROOT>/work diff origin/main...HEAD`) first: every file, every test it adds or changes, the commit messages. Nothing is skimmed, and nothing is judged before the last file is read.
2. The ticket delivers what it says: `What it delivers` in the task file is in the diff, and each scenario the ticket satisfies is proved by a test the diff adds or changes. A promise the code does not keep, or a scenario with no test of its own, is `blocking`.
3. Each test proves its scenario: it asserts the behaviour the scenario's THEN promises, on the real code path rather than on a mock of it. Reverting the change makes it fail. A test that would pass without the change, or that asserts a call happened instead of what the behaviour promises, is `blocking`: that is the scenario not really tested. The report's `red` holds, per scenario, the covering test's failing output from before the fix: judge whether it is a genuine assertion failure of that behaviour. An import or collection error, a missing fixture, or a test that cannot fail is no red, and is `blocking`.
4. The report matches the code: every `covers` node is a test that exists at the commit you review, every `checks` entry is a command with the outcome the log shows, `head` is the commit you are reviewing, and `files` covers the diff. A claim the code contradicts is `blocking`.
5. The numbered requirements it touches still hold: read the spec the plan names, and raise the `requirement_violation` `blocking` item described below when one does not.
6. Only then write the review: each correctness bug the pass found, once.
Report only what a user or caller would hit: wrong behaviour, data loss, a lost wake, a plan or run that cannot finish or restart, a scenario that is not really tested, or a test that would still pass without the change. Each is `blocking`. Style, naming, questions, suggestions and inputs no caller sends (huge numbers, deeply nested data) are left out of the review.
A defect that breaks a numbered requirement is a `blocking` item that also asserts `requirement_violation` {`source`, `id`, `expected`, `observed`}: the spec or plan file, the requirement id, what it promises and what the code does instead, each nonempty, beside the item's own file, line, fix and evidence. A rare or post-launch failure is still a violation: rarity does not make it an observation, and the driver refuses a requirement_violation on a nonblocking item.
Touch nothing in the repository and run no test and no command other than git reads (`git diff`, `git show`, `git log`). CI and the red-first gate are the executed evidence: read their results in the report and the pull request's checks, and judge whether a test would pass without the change by reading it against the diff and the report's `red` output. Write the review once the diff is read, then report complete with the assignment id this brief came with.

Write <ROOT>/run/t1-review/review.json as JSON, contract 2:
{"contract":2,"verdict":"pass|block","items":[...],"checks_run":[{"command","exit","at_commit"}],"clarifications":[{"finding","asked_by":"reviewer","note"}],"commit":"<git rev-parse HEAD of the checkout>"}
Every item has `id`, `kind` (`blocking`), `summary` and `answer_required` (true only when the implementer must reply). A blocking item also needs `file`, `line`, `fix` and `evidence` {command, exit, excerpt, log_path}: the command that shows the defect, its exit code, an excerpt and the path of the full output you saved. `scenario`, `why` and `verify` are optional; any other field you add is passed on verbatim.
The verdict is "block" exactly when the review has an item; a review with none is "pass". Write optional prose to <ROOT>/run/t1-review/review.md.
To close a blocking finding an earlier review raised, add it to `resolutions`: `{"id", "status": "fixed|disproven", "commit" (the commit you reviewed), "note" (what changed, or why the finding was wrong: nonempty), "evidence" {command, exit 0, excerpt, log_path}}`, and list that same command, its exit 0 and that commit in `checks_run`. One resolution per id. A resolution for an id the unit never had, a commit other than the one you reviewed, or evidence that did not pass leaves the finding open.
List in `checks_run` every check you ran, so the implementer does not repeat them. List in `clarifications` each question you asked the implementer directly.
Then call the agent-runtime `report` tool with kind=complete and one line: `<n> blocking - <the review file you wrote>`. The file to write is <ROOT>/run/t1-review/review.json.
