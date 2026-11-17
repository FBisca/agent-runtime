# Plan contract

A plan is a `plan/2` YAML contract: the requirement changes it makes to one spec, with scenarios under each, and the tickets whose tests prove them. This capability covers checking, approving and re-approving a plan, gating its tickets, its lifecycle and ledger, archiving its change set into its spec, and holding every spec requirement to a test.

## Requirements

### Requirement: Plan format
`plan check` SHALL accept a plan only in the `plan/2` shape (`requirements: {added, modified, removed}` with plan-local scenario ids, and tickets) and SHALL refuse any other shape naming each defect and its fix.

#### Scenario: a defective plan is named defect by defect
- WHEN `plan check <plan.yaml>` reads a plan with an unknown or duplicate key, a missing required field, a duplicate id, a reference to an undeclared scenario or ticket, a dependency cycle, or a scenario carrying an `evidence:` block
- THEN it exits 2 naming each defect with its YAML path and the fix; a valid plan exits 0, and `plan schema` prints the closed `plan/2` JSON Schema (`additionalProperties: false` at the top level)

#### Scenario: a plan/1 plan names the conversion
- WHEN `plan check` reads a plan whose schema is `plan/1`, or that carries no schema
- THEN it refuses the plan whole, naming `plan/2` and the conversion: set `schema: plan/2` and move the top-level scenarios under the requirements they prove

#### Scenario: a Markdown plan is refused by run
- WHEN a Markdown plan is passed to `run`
- THEN it is refused naming the YAML format and `plan check`

### Requirement: Requirement changes fit the spec
`plan check` SHALL refuse a change set the plan's `spec` cannot take, naming the title, the spec file and the fix.

#### Scenario: an added title the spec holds
- WHEN a plan adds a requirement whose title already heads a `### Requirement:` block in its spec
- THEN `plan check` refuses it naming the title, the spec file and the fix (move it to `modified`, or rename it)

#### Scenario: a modified or removed title the spec lacks
- WHEN a plan modifies or removes a requirement whose title heads no block in its spec
- THEN `plan check` refuses it naming the title, the spec file and the fix (use the exact title from the spec, or add it)

#### Scenario: a change set that fits
- WHEN every added title is new to the spec and every modified or removed title is in it
- THEN `plan check` exits 0

### Requirement: Plan approval
`plan approve` SHALL bind the plan alone to its repository and base, run nothing, and make every later command refuse a plan whose bytes differ from what was approved.

#### Scenario: approve writes the approval and its snapshot
- WHEN `plan approve` runs on a valid plan
- THEN it writes `approval: {by, at, base, digest}` (the digest over the repository's identity, the base and the plan alone, the `approval` block excluded) and snapshots the plan under the state dir; it spawns no worker and makes no scratch worktree

#### Scenario: an edit after approval is refused
- WHEN the plan's YAML changes after approval, outside `approval`
- THEN `plan check` and `run` refuse it, naming the changed file and `plan approve`

#### Scenario: files beside the plan are outside the contract
- WHEN a file in the plan's folder other than plan.yaml changes
- THEN the approval holds: only plan.yaml is digested

### Requirement: Only the Lead approves
`plan approve` SHALL refuse a caller the runtime's dispatch store knows as a spawned worker, naming the reason and that approval is the Lead's, and write nothing.

#### Scenario: a worker runs plan approve
- WHEN a spawned worker runs `plan approve`, on an unapproved plan or on one a driver is running
- THEN it is refused naming the reason and the Lead, and nothing is written

### Requirement: Re-approval under a live driver
`plan approve` on an approved plan SHALL re-approve it at the approval's own base while a driver runs, and the drive SHALL adopt the new digest without restarting.

#### Scenario: the drive drains on the edit and continues on the new digest
- WHEN, while a driver runs, the Lead edits the approved plan (a running ticket's `paths` and a new ticket) and runs `plan approve <plan> --reason <why>`
- THEN the re-approval keeps the current approval's `base`, never `origin/main`, runs nothing and replaces the `approval` block keeping the author's comments; the same drive drains on the edit, wakes the Lead naming `plan approve`, records an `amended` event with the previous and new digest, and dispatches the new ticket only after it adopts the new digest

#### Scenario: a COMPLETE ticket is frozen
- WHEN the edit changes or drops a ticket the ledger marks COMPLETE, or changes the `when` or `then` of a scenario a COMPLETE ticket satisfies
- THEN the re-approval is refused naming each such ticket or scenario and the way out (add a new ticket), and nothing is written

#### Scenario: one re-approval at a time
- WHEN a re-approval runs while the driver holds the plan's run lock
- THEN it goes through on a lock of its own, so a second concurrent re-approval is refused naming the holder; it writes the approval snapshot before it replaces the plan file, so a driver reading in between sees either the previous approved plan or the new one

### Requirement: Drain on an unapproved edit
A live driver that reads a plan differing from its approved snapshot, or one that does not load, SHALL keep running on the last approved snapshot and start no worker attempt until the plan is re-approved or restored.

#### Scenario: the drive drains
- WHEN the live driver reads a plan whose YAML differs from its approved snapshot or does not load
- THEN it starts no worker attempt (first dispatch, fix round, redo, re-dispatch) while running attempts, reviews, CI watching and the COMPLETE-on-merge bookkeeping go on, and records one `drain` event and one `drain` wake naming the cause (with the load error, if any) and `plan approve`; an edit to the brief alone does not drain

#### Scenario: the drain ends
- WHEN a draining driver next reads the plan and it carries a valid approval matching its bytes, or its bytes are the approved ones again
- THEN the drain ends: an `amended` event and gating against the new contract, or a `drain` event with state `ended`; a drain has no timeout

### Requirement: A re-approval reaches running workers
When the drive adopts a re-approval that changes the `paths`, `satisfies` or `delivers` of a ticket with an open attempt, it SHALL hand that attempt's worker the new contract and leave every unchanged ticket alone.

#### Scenario: a changed ticket's worker gets its next packet
- WHEN the adopted re-approval changes a ticket with an open attempt (working or parked)
- THEN the driver writes the attempt's next packet `task.v<n>.md`, sets the ledger's ownership reservation to the new paths, and resumes the same worker with a message naming that packet and saying its contract changed; a ticket the re-approval did not change is left alone

### Requirement: Agent tickets hold a test path
`plan check` and `plan approve` SHALL refuse a plan in which an agent-owned ticket satisfies a scenario and none of its `paths` is a directory or a test file.

#### Scenario: a ticket with no test path
- WHEN an agent-owned ticket satisfies a scenario and none of its `paths` is a directory or a test file
- THEN the plan is refused naming the ticket, the scenarios it satisfies and the way out (add a test file, or a directory that holds its tests, to its `paths`), and no approval is written; a person-owned ticket is never linted

#### Scenario: what a test file is
- WHEN the lint decides whether a path is a test file
- THEN a path is a test file when its file name matches `test_*`, `*_test.*`, `*.test.*` or `*.spec.*`, or one of its directories is `tests`; `.orchestrate/config.yaml` may replace the patterns with `tests.patterns` (a list of file-name globs), and a non-list or empty value is refused naming its dotted path

#### Scenario: the driver's load does not lint
- WHEN the driver loads an approved plan the lint would refuse
- THEN the run proceeds: the lint runs in `plan check` and `plan approve` only

### Requirement: Scenario evidence
Verify SHALL pass a ticket report only when every scenario the ticket satisfies is mapped in `covers` to a test node the ticket changed and that was seen failing before the fix.

#### Scenario: an unmapped or unchanged covering node
- WHEN a ticket report (contract 3) leaves a satisfied scenario unmapped in `covers`, or maps it to a test node that does not exist or did not change in the ticket
- THEN verify fails the gate with a fix round naming the scenario, the node and the reason; nothing in the plan exempts a scenario, and CI green on the same head proves the tests pass

#### Scenario: a missing red excerpt
- WHEN a report carries no `red` excerpt for a satisfied scenario, or one that names none of that scenario's `covers` nodes
- THEN verify fails the gate with a fix round naming the scenario; the reviewer judges whether each excerpt is a genuine assertion failure of the behaviour, not an import error or a test that cannot fail

#### Scenario: paths that can hold no test
- WHEN the covers gate fails a report because the ticket's `paths` hold no test file or directory
- THEN the Lead's request says the ticket's paths can hold no test and tells it to edit the plan and re-approve it with `plan approve`; the lint and the gate decide it with one shared function

### Requirement: Ticket scope and plan integrity
Verify SHALL fail a ticket whose diff touches a file that another unfinished ticket lists in its `paths`, or any file of its plan; a ticket's `paths` are its starting map and the conflict map between tickets, never a fence.

#### Scenario: a file no ticket owns
- WHEN a ticket's diff touches a file outside its `paths` that no other unfinished ticket lists
- THEN verify passes the scope rule: a worker edits any file the change needs

#### Scenario: a file another unfinished ticket owns
- WHEN a ticket's diff touches a file outside its `paths` that another ticket, not COMPLETE in the ledger, lists in its `paths`
- THEN verify fails the gate naming the file, that ticket and the way out (revert it, and report BLOCKED naming that ticket if the change is needed); a path prefix is a directory, not a string prefix, and a file inside the ticket's own `paths` is never refused

#### Scenario: a file of the plan
- WHEN a ticket's diff touches the plan file or any file of its plan folder
- THEN verify fails the gate naming the file and the integrity rule, even inside the ticket's paths

### Requirement: Plan discovery
Every plan command SHALL find plans and read the repository's `.orchestrate/config.yaml` the same way, creating nothing.

#### Scenario: plans list
- WHEN `plans list --json` runs with an explicit path, in a repo whose plans sit in one directory (`docs/plans/`, hidden `.plans/`, or the config's `plans.dir`, inside or outside the repository), in a repo with plan files in two directories, and in a repo with none
- THEN it lists the explicit plan; that directory's plans with their slugs; refuses naming both directories and the explicit-path way out unless the config names one of them; an empty list; no directory or file is created

#### Scenario: the repo config
- WHEN any plan command (plans list, plan check, plan approve, run, status) reads `<repo>/.orchestrate/config.yaml`
- THEN it applies `plans.dir` and `archive.mode` (promote | discard), a relative path being under the repo root and `~` and absolute paths allowed; an unknown key or a bad mode fails the command naming its dotted path and the fix; a missing file means docs/plans and promote

#### Scenario: a plan outside the repository
- WHEN a plan.yaml outside the repository is passed explicitly with `--repo` to `plan check`, `plan approve` and `run`
- THEN all three work, and nothing is written next to the plan except the approval block

### Requirement: Plan layouts
Every command SHALL read a plan's files through one resolver, built from the plan path and the repository, that answers for both layouts: a folder plan (`<folder>/plan.yaml`) and a sibling plan (`X.plan.yaml` beside `X.plan.md`).

#### Scenario: one resolver answers for the layout
- WHEN any command reads, snapshots, guards or copies a plan's files
- THEN it asks the resolver (the kind, the folder, the contract files, a path's place relative to the repository, whether a path belongs to the plan, whether the folder may be deleted), and no other code derives the layout from the plan's file name

#### Scenario: a sibling plan
- WHEN a sibling plan is checked, approved, run, verified or archived, with or without a repo config
- THEN it behaves as a folder plan does and is listed beside folder plans, and an approval digest of a plan with no files beside it verifies

### Requirement: Archive mode
A folder plan SHALL declare how it archives (`archive: promote | discard`, defaulting to the repo config's `archive.mode`), a promoting plan SHALL name its `spec`, and a discarding plan SHALL sit directly under `plans.dir`.

#### Scenario: the archive key
- WHEN `plan check` reads a folder plan naming `archive: promote | discard`, or a sibling plan naming `archive`
- THEN the folder plan is accepted and the sibling plan is refused naming the key and the way out

#### Scenario: a promoting plan needs a spec
- WHEN `plan check` reads a folder plan that promotes, by its own key or the repo config, and names no `spec`
- THEN it refuses naming `spec` and the fix (set it, or `archive: discard`); a discard plan needs no `spec`

#### Scenario: a discard folder outside plans.dir
- WHEN `plan check` or `plan approve` reads a folder plan that discards and whose folder's resolved parent is not the resolved `plans.dir` (nested deeper, or reached through a symlink)
- THEN it refuses naming the folder, `plans.dir` and the fix; a folder directly under a `plans.dir` outside the repository passes

### Requirement: Archive applies the change set
When a promoting plan is ACCEPTED, the driver SHALL archive it with no agent by applying the plan's requirement changes to its spec and opening the archive pull request.

#### Scenario: the change set lands in the spec
- WHEN the driver writes the archive
- THEN in the archive worktree it appends each added requirement as a `### Requirement:` block with its scenarios, replaces each modified block whole (from its heading to the next heading), deletes each removed block, and deletes the plan's folder (a sibling plan's files are copied byte-identical instead); a missing spec file is created holding the added blocks alone

#### Scenario: the archive pull request
- WHEN the archive tree is written
- THEN the driver commits on `feat/<slug>-archive` with the plan's goal and each removed requirement's `why` in the commit message, pushes, runs `gh pr create`, and the plan is ARCHIVED when that pull request merges

#### Scenario: a change set the spec cannot take
- WHEN the spec at the base cannot take the change set (an added title it holds, a modified or removed title it lacks), or the archive pull request conflicts
- THEN the driver asks the Lead (`retry`, `skip`) naming the spec and the titles, before any commit or push, and spawns no worker

### Requirement: Discard archive
When a discarding plan is ACCEPTED, the driver SHALL delete its plan folder and mark it ARCHIVED with no archive worker, deleting only a folder directly under `plans.dir`.

#### Scenario: a discard plan outside the repository
- WHEN a discard plan kept outside the repository, with no spec, is approved, run and verified
- THEN no archive worker or pull request runs, the plan folder is gone, the ledger reads ARCHIVED, and origin's tree outside the ticket's paths is unchanged

#### Scenario: a discard plan tracked in the repository
- WHEN a discard plan's folder is tracked in the repository
- THEN it is deleted by a one-commit pull request that changes nothing else

#### Scenario: the deletion guard
- WHEN the driver is about to delete a plan folder (a discard, or the plan folder in a promote's archive tree) whose resolved parent is not the resolved `plans.dir`, checked again at the deletion itself, even when the config changed mid-run
- THEN it deletes nothing, the plan is not ARCHIVED, and the Lead is told the folder and `plans.dir`

### Requirement: Archive has no agent route
The driver SHALL refuse, before any spawn, a route config or `--route` override that names the `archive` or `gaps` step.

#### Scenario: a route naming the archive
- WHEN a route config or a `--route` override names the `archive` step, or the `gaps` step
- THEN the driver refuses before any spawn, naming the step and the fix (remove that route); the `implementer` and `reviewer` routes work

### Requirement: Plan lifecycle
A plan SHALL move APPROVED → RUNNING → ACCEPTED → ARCHIVED, ACCEPTED once every agent ticket is COMPLETE, with plan.yaml unchanged since approval.

#### Scenario: every agent ticket COMPLETE
- WHEN every agent ticket of a plan is COMPLETE
- THEN the plan is ACCEPTED: each ticket reached COMPLETE only through its gate and its pull request merged with CI green, so nothing is re-run

#### Scenario: a person ticket is open
- WHEN the agent tickets are COMPLETE while a person ticket is open
- THEN the plan is ACCEPTED, the archive does not start until every ticket is COMPLETE, and the run ends rather than wait on the person; after `complete`, the next run archives

#### Scenario: a plan driven to the end
- WHEN a plan (one ticket, or a small real plan driven by real agents in a scratch repository) is approved, launched, its work reaches the default branch and it archives
- THEN `status` history is APPROVED → RUNNING → ACCEPTED → ARCHIVED, plan.yaml is unchanged since approval, and `plan check` accepts its approval digest

### Requirement: Person tickets
The Lead SHALL close a person-owned ticket with `complete <plan> <T#> --note TEXT`, and the driver SHALL keep `attention.md` in step with the ledger.

#### Scenario: complete records the ticket
- WHEN `complete` runs for a person-owned ticket
- THEN its status becomes `COMPLETE <date> · <note>` and it is recorded in the ledger under the ledger lock, so a live driver keeps it; it is refused, naming why, for a caller the runtime does not verify as the Lead, an agent ticket, or an unknown ticket

#### Scenario: attention.md follows the ledger
- WHEN a person ticket is set COMPLETE and the driver runs again
- THEN the driver rewrites `attention.md` from the ledger on every tick: the completed ticket's line is gone, a ticket waiting is listed once, and a run with nothing waiting has the header only

### Requirement: Ledger and status
Each plan SHALL have one ledger per repository, and `status <plan> --json` SHALL report the run from it while plan.yaml stays byte-identical.

#### Scenario: status reads the ledger
- WHEN a plan runs through dispatch, a pause, a `decide` answer and a merge
- THEN plan.yaml stays byte-identical, and `status <plan> --json` reports the run dir, the plan state and its history, and each ticket's state, reason and PR, before and during the run

#### Scenario: one ledger per repository
- WHEN two repositories each run a plan with the same slug, and a ledger under the unkeyed `<state>/<slug>.json` path exists
- THEN each gets its own ledger under `<state>/<repo-key>/<slug>`, and the unkeyed ledger is imported once into the repository that owns it, never while a driver holds it

### Requirement: Ticket dispatch
An approved plan's agent tickets SHALL dispatch with the class, effort and model their rows set, in their blocking order.

#### Scenario: rows decide routes and order
- WHEN an approved plan is launched
- THEN each agent ticket dispatches on the route its row names, in blocking order, and the plan stays byte-identical

### Requirement: Lead decisions
A request SHALL offer the Lead a typed menu, and `decide` SHALL take the Lead's answer as flags or as one JSON object, validated and written the same way.

#### Scenario: a request's menu
- WHEN the driver writes a request
- THEN it is contract 3: each menu option is `{action, summary, inputs}`, `inputs` mapping each input name to `{type, required}` (`reason` always, `answer` for `answer`); a contract 2 request with a string menu is read and decided too

#### Scenario: decide --json
- WHEN an agent Lead runs `decide <plan> <unit> --json <file|->` with a decision object
- THEN the decision is validated and written exactly as the flag form writes it and stdout is one JSON object naming the action; a refusal is one JSON object `{error, reason, way_out}` on stderr with exit 2 and nothing written

### Requirement: A decision reaches its worker
The driver SHALL hand the Lead's decision to the worker it concerns, and SHALL ask again when it cannot.

#### Scenario: a hand-over that fails
- WHEN the driver cannot apply the Lead's decision (the hand-over fails, e.g. the pane is blocked on a login)
- THEN the unit gets a new request naming the failure and the way out; a `complete` from the unit's worker that lands while that request is open is held, and the next decision acts on it without handing the unit over again

#### Scenario: an answer to the reviewer
- WHEN the Lead answers a request raised while the reviewer owed its review
- THEN the answer resumes the reviewer on its review, not the implementer; a reviewer whose pane is gone is replaced by a fresh reviewer whose brief carries the answer

### Requirement: A new attempt keeps the higher class
A new attempt of a unit SHALL run on the higher of its last attempt's class and the class the routes compute now.

#### Scenario: a re-dispatch, a reserved attempt or a reopened unit
- WHEN the driver starts a new attempt of a unit that had one (an orphan re-dispatch, a reserved attempt, or a reopened parked unit)
- THEN the attempt's class is the higher of the last attempt's class and the one the routes compute now, with that class's model and effort

### Requirement: Spec coverage
`scripts/spec_coverage.py` SHALL hold every `### Requirement:` title in `docs/specs/*.md` to at least one tagged test, and every test tag to an existing title.

#### Scenario: a title with no tagged test
- WHEN a spec holds a requirement title that no test file tags (`@pytest.mark.spec("<title>")`, `describe("spec: <title>")` or a `spec: <title>` comment)
- THEN the script exits 1 naming the title and the way out (tag a test with `spec("<title>")`)

#### Scenario: a tag naming no title
- WHEN a test file tags a title no spec holds
- THEN the script exits 1 naming the tag and the way out (fix the tag to an existing title, or add the requirement)

#### Scenario: titles and tags match
- WHEN every title has a tag and every tag names a title
- THEN the script exits 0; a spec with no `### Requirement:` heading asks for nothing

### Requirement: Archived plans leave no state
The state dir SHALL hold only plans that are not yet archived: a driver start and `plan approve` SHALL delete every ledger already in ARCHIVED with its lock, its run dir and the approval snapshots its digests name (the ledger's and every `amended` event's). It deletes nothing at the moment of archive, so the finishing driver and `await` keep their run dir. A snapshot no ledger names (approved but not started, or superseded before any driver started) stays: no safe rule exists for it.

#### Scenario: an archived plan is swept
- WHEN a driver starts or `plan approve` runs and the state dir holds a ledger in ARCHIVED
- THEN its ledger, lock, run dir and snapshots are gone

#### Scenario: a plan not archived is kept
- WHEN the state dir holds a ledger in any other state, or a snapshot no ledger names
- THEN the sweep leaves them

#### Scenario: a live archived plan is skipped
- WHEN an archived ledger's lock is held, or the newest `start` in its run dir names a live process
- THEN the sweep leaves it for the next one
