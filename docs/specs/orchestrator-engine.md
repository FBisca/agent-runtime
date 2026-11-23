# Orchestrator engine

The engine is the orchestrator's driver: it reads a plan, dispatches tickets, gates their reports, and writes the ledger the Lead reads. `packages/agent-orchestrator/` owns the engine and its suite. This capability covers how the driver routes, briefs, gates, recovers and narrates a run; the plan format, approval, lifecycle and archive are in [plan-contract.md](plan-contract.md).

## Requirements

### Requirement: One engine, one suite
The repository SHALL hold exactly one copy of the engine, its suite SHALL exercise that copy, and CI SHALL run the whole suite and the plain gates.

#### Scenario: the suite drives the member's driver
- WHEN the engine's own suite runs
- THEN it imports the driver from `packages/agent-orchestrator`, and no other source tree holds an engine copy

#### Scenario: CI runs the member's suite
- WHEN the repository's CI workflow runs
- THEN it resolves the workspace and runs the whole suite, the member's own suite included, and the plain gates

### Requirement: Runtime refusals are reported
The driver SHALL report a runtime refusal as a refusal, not crash on it.

#### Scenario: a refused spawn
- WHEN the runtime refuses a spawn
- THEN the driver sees a `ValueError`, leaves no open intent, keeps the reservation and exits 1 with the refusal

### Requirement: Launch runs the member's CLI
`launch` SHALL start the run in a pane that runs the member's own CLI.

#### Scenario: a named pane
- WHEN `launch` starts a run
- THEN the pane runs the member's CLI

### Requirement: Budgeted runs are terminated for good
A run terminated for exceeding its budget SHALL be recorded as a failure only after the tree it killed has stopped.

#### Scenario: a stalled run
- WHEN a run is terminated for exceeding its budget
- THEN the harness waits, within a bounded window, for the killed tree to stop before it decides the tree survived, and a tree that really outlived the run still fails the check

### Requirement: Route refusals name the value
The driver SHALL refuse a route it cannot run before any worker is spawned, naming the value the route gave and the way out.

#### Scenario: an unknown class, backend, effort or step
- WHEN a route or a `--route` or `--step` override names a class, backend, model, effort or step the driver cannot run, or a step route names no model
- THEN the run fails before any worker is spawned, naming the value it found and the way out

#### Scenario: an invalid route config
- WHEN the selected route config is invalid
- THEN the run fails before any worker is spawned, naming the file and the defect

### Requirement: Backend and effort routes
A route SHALL be able to name the backend `pi` and the efforts `xhigh` and `max`, and the worker's spawn SHALL carry what the route names.

#### Scenario: pi and the top efforts
- WHEN a route or an override names `pi`, or the effort `xhigh` or `max`
- THEN the worker's spawn carries that backend and that effort

#### Scenario: no effort named
- WHEN a route names no effort
- THEN the size and difficulty matrix still decides the effort

### Requirement: Step routes
A step route SHALL set the backend, model and effort of the `implementer` or `reviewer` step, and a gate without one SHALL keep its default effort.

#### Scenario: a step answers for itself
- WHEN a step route names `implementer` or `reviewer`
- THEN that step's spawn carries the step's backend, model and effort, and an `implementer` route overrides the class route

#### Scenario: the brain backend
- WHEN `--brain-cli` names a backend
- THEN every gate step runs there, and a step route overrides it

#### Scenario: the default effort
- WHEN no step route names a gate
- THEN the reviewer keeps the effort `high`

#### Scenario: a brain without a reviewer route
- WHEN a new run names `--brain-cli` and its medium route does not run on that backend
- THEN the run stops before any worker is spawned, naming the `--step reviewer=BACKEND/MODEL[@EFFORT]` argument and the medium route on the brain backend that would also answer, and explicit step routes keep a brain on another backend usable

### Requirement: A typed route config is required
A new run SHALL be routed by a typed route config, and the driver SHALL read no model table.

#### Scenario: no config
- WHEN a new run has no applicable typed route config
- THEN it stops before any worker is spawned and says how to supply one: `--routes-file FILE`, the repository's `.orchestrate/routes.json`, or the user's profile for its backend

#### Scenario: no model table
- WHEN a run starts, or an operator passes `--models-file`
- THEN no model table is read, neither the 'Model classes' table of `~/.claude/CLAUDE.md` nor one of the driver's own, and `--models-file` is refused with the same way out

#### Scenario: the docs say so
- WHEN the orchestrator documents and this spec describe routing
- THEN they describe typed configuration, precedence, migration and restart behaviour, and no model table

### Requirement: Route precedence
Dispatch SHALL choose the route by one precedence and log the route it chose.

#### Scenario: the order
- WHEN routes are given in several places
- THEN a command-line `--route` or `--step` override wins, then the file named by `--routes-file`, then the repository's `.orchestrate/routes.*`, then the user's profile for the run's backend (`~/.config/orchestrate/routes.json` for Claude, `routes-codex.json` for `--cli codex`, `routes-pi.json` for `--cli pi`)

#### Scenario: the first file is the only one
- WHEN the first file found is invalid, or the profile is missing
- THEN the run stops, naming the expected file, and never falls back to another file

#### Scenario: the choice is logged
- WHEN dispatch chooses a route
- THEN it logs the route in the route ledger and the event log

### Requirement: Routing leaves the state machine alone
A change of provider or step routing SHALL NOT change the plan schema or the gate, review and pull-request state machine.

#### Scenario: a new provider
- WHEN a run is routed to a different provider
- THEN the gate, review and pull-request states behave as before and the worker really runs on that provider

### Requirement: A resumed run keeps its routes
An existing run SHALL resume with the routes, `--cli` and `--brain-cli` its ledger recorded, whatever the policy now says.

#### Scenario: a conflicting argument
- WHEN a resumed run is given a `--route`, `--step`, `--routes-file` or `--cli` that differs from the record
- THEN it fails before any worker is spawned or resumed, naming the argument and the recorded value, and the ledger is unchanged; an argument that matches the record is accepted and an omitted one is no conflict

#### Scenario: a missing or incomplete record
- WHEN the ledger holds no complete route record
- THEN the run needs an explicit `--routes-file`, which records the complete resolved routes before the run resumes; a restart never reads a route file the ledger did not record

### Requirement: The report contract matches Report
The contract the run writes SHALL describe a report that `Report` accepts, and each unit brief SHALL name the task id the report carries.

#### Scenario: the contract example
- WHEN a worker hands in the report the contract describes, `context` included
- THEN `Report` accepts it, and every top-level key of the contract's example is a `Report` field

#### Scenario: the task id
- WHEN a unit's brief is written
- THEN it names the `task_id` its report must carry

#### Scenario: asking the Lead and red evidence
- WHEN the contract is written
- THEN it says how to ask the Lead a question (a plain message; the question does not stop the work a worker can do meanwhile; a hard stop is a BLOCKED report with `blocker`), and that red is an assertion failure of the named test, reached by first writing the minimal compiling skeleton so a test file that cannot import or compile is not offered as red

### Requirement: Whole-tree git calls get a large timeout
The driver SHALL size the timeout of a git call that walks or writes the whole tree for a large repository.

#### Scenario: worktree add
- WHEN the driver runs `git worktree add` or `status`
- THEN the call's timeout is sized for a large repository, not `run()`'s 15-second default

### Requirement: Lead wakes
A run launched inside an hc terminal SHALL send each new wake to that terminal once, and a run launched outside a terminal SHALL send nothing.

#### Scenario: a wake is sent once
- WHEN `launch` runs inside an hc terminal
- THEN the run is given that terminal, and each new wake (`stranded`, `not-dispatched`, `person`, `idle`, `crash`) is sent to it once with `hc send`, never with `--force` or `interrupt`

#### Scenario: a failed send
- WHEN a send fails
- THEN the driver logs `notify-failed` and the run goes on

#### Scenario: no terminal
- WHEN a run is launched outside a terminal
- THEN it sends nothing

### Requirement: One driver per plan
A second driver SHALL NOT run a plan another live driver runs in the same repository.

#### Scenario: a second driver
- WHEN a `run` starts on a plan another live driver runs, whatever `--state-dir` either names
- THEN it is refused before any worker is spawned, naming the holder's pid and start time and the way out (`await` the plan, or stop that driver)

#### Scenario: other plans and finished runs
- WHEN a run of another plan in the same repository starts, or a run ended or crashed
- THEN it is not held up, and no lock stays behind

### Requirement: A checkpoint needs its checkout
`checkpoint()` SHALL refuse a report whose unit has no checkout, and SHALL hold no check outcome against COMPLETE: CI judges the tests.

#### Scenario: failed or unrun checks
- WHEN a COMPLETE report carries a `failed` check, a `not_run` check, or omits a command the brief named
- THEN `checkpoint()` accepts it; the driver compares no reported command with the brief and CI is the judge of the tests

#### Scenario: a PARTIAL checkpoint without a checkout
- WHEN a PARTIAL checkpoint runs and `packet.checkout` does not exist
- THEN `checkpoint()` raises

### Requirement: A worker's message reaches the Lead
The drive SHALL put a message from an agent that is not a report (`complete`, `blocked`, `decision`, `failed`) in front of the Lead, and SHALL never drop one silently.

#### Scenario: a plain message from a worker
- WHEN a message of any other kind arrives from the assignment of an open step
- THEN it is handled as that agent's `decision`: one request carrying the unit's name and the text (cut to 500 characters), the ticket's `needs you` status, `attention.md`, one wake and the terminal notice, and the Lead's `decide ... answer` reaches the same live worker

#### Scenario: a message from no open step
- WHEN such a message names an assignment no open step holds, or none
- THEN a `message-unmatched` event and a warning record it, and nothing else changes

### Requirement: A report is handed in through the runtime
A worker SHALL hand in its report as the text of its agent-runtime `report` call, and the driver SHALL validate it the moment it arrives; there is no report file.

#### Scenario: an accepted hand-in
- WHEN a worker's `complete` carries a report that `Report` accepts, names the task it works, and (COMPLETE) says it met its acceptance and names the commit its branch is at
- THEN the driver appends a `report` event with the full JSON to `events.jsonl`, the ticket moves to its gates, and the ledger keeps the checkpoint; the reviewer's brief carries the report's content

#### Scenario: notes never block
- WHEN a COMPLETE report carries `notes`
- THEN the driver records them with the `report` event and lists them where it summarises the run at plan close; no rule turns a note into a block

#### Scenario: a blocked report
- WHEN a worker hands in a BLOCKED report
- THEN its `blocker` goes to the Lead as a request

#### Scenario: no check outcome is held
- WHEN a COMPLETE report carries a `failed` or `not_run` check
- THEN it is accepted; CI judges the tests

### Requirement: A refused hand-in goes back to its sender
The driver SHALL return a hand-in whose shape is wrong to the same worker at once with the exact errors, and ask the Lead only after the third refusal.

#### Scenario: a wrong shape
- WHEN a hand-in is not JSON, breaks the contract (an unknown field such as `remaining`, a BLOCKED report without a `blocker`), names another task, fails the COMPLETE rules, or names a commit other than the branch head
- THEN the driver appends a `report-refused` event with the reason and the payload, messages the same agent the exact errors, writes no Lead request and counts no fix round, and the corrected hand-in is gated as usual

#### Scenario: the third refusal
- WHEN the same task's hand-in is refused for the third time
- THEN the Lead is asked

### Requirement: A verdict is handed in through the runtime
A reviewer SHALL hand in its verdict as the text of its agent-runtime `report` call, and the driver SHALL validate it the moment it arrives; there is no review file.

#### Scenario: an accepted verdict
- WHEN a reviewer's `complete` carries `{"verdict": "pass"|"block", "findings": [{"file", "line", "quote", "claim", "rule"?}]}` that `Verdict` accepts (`block` exactly when it names a finding)
- THEN the driver appends a `review` event with the commit reviewed, the verdict and the findings to `events.jsonl`, the unit moves to `reviewed`, and `rule` is recorded and never read by the driver: a finding with it and one without it block equally


### Requirement: A refused verdict goes back to its reviewer
The driver SHALL return a verdict whose shape is wrong to the same reviewer at once with the exact errors, and ask the Lead only after the third refusal.

#### Scenario: prose beside the JSON
- WHEN a reviewer's report text is the verdict JSON followed by prose (a worker's "files changed, checks, decisions, remaining work" block)
- THEN the verdict is refused like any wrong shape, the review brief and the refusal both tell the reviewer the report text is the verdict JSON alone, and the parser stays strict

#### Scenario: a wrong shape
- WHEN a verdict is not JSON, has an unknown field, a finding without `quote`, `claim`, `file` or a positive `line`, or blocks with no finding
- THEN the driver appends a `review-refused` event with the reason and the payload, messages the same reviewer every error, writes no Lead request, and the corrected verdict is read as usual

#### Scenario: the third refusal
- WHEN the same review's verdict is refused for the third time
- THEN the Lead is asked


### Requirement: A reviewer that ends without a verdict is resumed once
A reviewer whose turn ended or whose process exited without a verdict SHALL be answered by the driver at once, and the Lead SHALL be asked only when it happens again.

#### Scenario: the first end
- WHEN a reviewer's agent exits, fails or its turn ends while the unit waits at `reviewing`
- THEN the driver appends a `review-ended` event with the reason, resumes the same reviewer with "You ended without a verdict; hand it in", and writes no request

#### Scenario: the second end
- WHEN the resumed reviewer ends without a verdict again
- THEN the Lead is asked


### Requirement: Review and CI run together
A reported commit SHALL be reviewed while its CI runs, and the worker SHALL hear both once.

#### Scenario: both are in
- WHEN a report is accepted
- THEN the reviewer starts at once, CI is polled meanwhile, the worker is told nothing, and when the verdict is in and CI has settled on the reported commit the worker gets one fix round carrying the review's findings and the CI failures together, or the unit goes on to the pull request's readiness when the verdict passes and CI is green

#### Scenario: CI is not in yet
- WHEN the verdict is in and CI is pending, absent or not yet on the reported commit
- THEN the unit waits at `reviewed`, no fix round is raised, and the idle watchdog is the deadline

#### Scenario: no skip
- WHEN any ticket reports
- THEN it gets the same review


### Requirement: A repository names its rulebook
A repository SHALL name one rulebook file in `.orchestrate/config.yaml` that both its implementer's and its reviewer's briefs point at.

#### Scenario: a named rulebook
- WHEN the config names `rulebook: <path>`
- THEN the worker's task brief and the reviewer's brief each name that file, which must exist in the checkout, and a finding that breaks one of its rules names the rule's title in `rule`

#### Scenario: no rulebook
- WHEN the config has no `rulebook` key
- THEN neither brief has a rulebook line


### Requirement: A merged unit completes
A unit whose pull request merged SHALL complete.

#### Scenario: an early merge
- WHEN a PR merges early (`merged_early`), including on a drive with a named `--gate`
- THEN the unit completes and the ledger task keeps the checks of the worker's stored report

#### Scenario: a missing checkout
- WHEN a PR is merged and `packet.checkout` no longer exists
- THEN the task is COMPLETE with `checkout_at_checkpoint: null`, a log line names the missing path, and `(task, "awaiting-merge")` is not in the stuck set

#### Scenario: a shared checkout
- WHEN cleanup runs for a COMPLETE task whose checkout a non-closed task under another worker name also uses
- THEN the worktree is kept

### Requirement: CI findings are capped per job
A CI fix round SHALL carry, for each failing job, the job name, the run id and the last 50 lines of each failed step, and stay small.

#### Scenario: several long logs
- WHEN several CI checks fail with long logs
- THEN each CI finding carries only its own job's name, its run id and the last lines of each failed step (never the start of the log), the full log is at `log_path`, and the fix-round findings file stays under 64 KiB

### Requirement: A failed check is never re-run by the driver
The driver SHALL send every CI failure that does not also fail on the base branch to the worker as a fix round, re-run nothing itself, and leave to the worker the decision whether the failure is real or flaky.

#### Scenario: a failure goes straight to the worker
- WHEN a unit's checks fail and the same checks pass on the base branch
- THEN the worker gets a fix round at once and the driver runs no `gh run rerun`; a check still running is waited for, never treated as a failure

#### Scenario: the worker re-ran it
- WHEN the worker re-runs the failed jobs itself and hands in with its head unchanged
- THEN the driver accepts the hand-in and reads the checks afresh for that head, never the failed result it held before

### Requirement: A failed check passing on main goes back to the worker
A CI failure that does not also fail on the base branch SHALL go back to the unit's worker with the evidence and the way to reproduce it.

#### Scenario: the fix round
- WHEN a check fails that passes on the base branch
- THEN the worker gets a fix round whose findings carry the failed job names, the run id and the last 50 lines of each failed step, and whose brief tells it to decide: reproduce a real failure locally, fix it and re-run the tests; re-run the failed jobs with `gh run rerun <run id> --failed` for a failure that looks flaky or like a CI machine problem, and when they go green hand in COMPLETE with a `notes` line naming the flaky check; hand in BLOCKED with an `environment:` blocker when it still fails and cannot be made to pass

#### Scenario: failing three times
- WHEN the same gate has failed three times
- THEN the Lead is asked

### Requirement: Failures are counted per gate
The driver SHALL ask the Lead for a unit once one gate (one source: verify, review, ci, conflict) has failed more often than the unit's fix-round cap, counting each gate's failures apart from the others.

#### Scenario: different gates do not add up
- WHEN a unit's review blocks once and a later gate of another source (a conflict) fails once, with a cap of one fix round
- THEN each failure takes its own fix round, the second round is numbered 2, and no request is raised

#### Scenario: the same gate failing again
- WHEN one gate fails a unit for the third time under the default cap of two fix rounds
- THEN the Lead is asked, and the request names the gate and its count

### Requirement: A fix round never reuses a task id
The driver SHALL name a fix round with a task id the ledger does not hold, and keep that id across a restart of the step.

#### Scenario: the round's number is taken
- WHEN the ledger already holds a record under `<unit>r<round>` (left by a driver restart)
- THEN the fix round takes the next free `<unit>r<n>`, the Lead's decisions never fail on "task ID already has a different assignment", and a restart of the step reuses the id it booked

### Requirement: A failed check that also fails on main asks the Lead once
A check that fails on the base branch as well as in a ticket SHALL cost the Lead one request for the whole run, not one per ticket.

#### Scenario: main is red
- WHEN a ticket's check fails that fails in the base branch's latest completed run (read once per run with `gh run list` and `gh run view`)
- THEN one `ci` request says main is red, no worker round is spent, and every other ticket failing a check that fails on main waits on that request; when the Lead decides it the waiting tickets look again

### Requirement: A pending CI log is not a finding
The driver SHALL wait on a failing check whose workflow run is still in progress.

#### Scenario: the placeholder log
- WHEN a check fails while its workflow run is in progress, so `gh run view --log-failed` answers that logs will be available when it is complete
- THEN the CI state stays pending and the unit is not asked; once the failing step's log is readable the gate fails and the request quotes that log, never the placeholder

### Requirement: A conflicting pull request is rebased
A unit whose pull request conflicts SHALL go back to draft and be rebased by a fresh worker, and no ready or update call SHALL run on it meanwhile; when another open ticket changes the same files, the rebase waits for that ticket to merge, unless that ticket's pull request conflicts too and comes later in the plan.

#### Scenario: a conflict while waiting for the merge
- WHEN a unit at `awaiting-merge` has a pull request that turns `DIRTY` or `CONFLICTING`
- THEN the PR goes back to draft and a conflict fix round gives a fresh worker a rebase brief quoting the merge state; the rebase goes through a delta re-review and CI, and `gh pr ready` runs again before the unit waits again

#### Scenario: two conflicting tickets over one file
- WHEN two open tickets' pull requests both conflict with their base and change the same file
- THEN the ticket earlier in the plan's ticket order is rebased now and the later one waits for it to merge; neither waits on the other forever

#### Scenario: a conflict at ready-for-ci
- WHEN a unit at `ready-for-ci` has a `DIRTY` or `CONFLICTING` pull request
- THEN neither `gh pr ready` nor `gh pr update-branch` runs, the PR stays a draft and the conflict fix round is raised directly

#### Scenario: a conflict past the cap
- WHEN the pull request conflicts after the fix rounds hit the cap
- THEN the unit escalates to the Lead as other capped rounds do and the PR is left a draft

#### Scenario: the draft undo fails
- WHEN `gh pr ready --undo` fails as a unit leaves for a fix round
- THEN no fix worker is dispatched, the undo is logged and tried again on a later round

#### Scenario: an update-branch failure without a conflict
- WHEN `gh pr update-branch` fails and a fresh `gh pr view` shows no conflict
- THEN no conflict round is raised and the Lead is asked with the `gh` error and the way out

#### Scenario: a conflict with another open ticket
- WHEN a pull request conflicts and another open ticket's pull request changes a file it changes
- THEN no rebase round is raised; the unit waits until that ticket has merged and is then rebased onto main

### Requirement: A pull request that is behind is updated
A unit whose pull request is behind its base SHALL be updated without a fix round.

#### Scenario: behind
- WHEN a unit's PR is `BEHIND` its base at `ready-for-ci` or `awaiting-merge`
- THEN `gh pr update-branch` runs, the unit goes back to `ready-for-ci` with its checks cleared and its PR still ready, only checks read on the moved head count as green, and the new `pr-ready` wake is keyed on the PR's live `headRefOid`

#### Scenario: an unknown state
- WHEN the merge state is `UNKNOWN`, or `gh pr view` fails, at `ready-for-ci` or `awaiting-merge`
- THEN no `gh pr ready`, no wake and no fix round happen, and the unit is polled again

### Requirement: Red checks while waiting for the merge
A unit at `awaiting-merge` SHALL go back to a CI fix round when its checks turn red, and keep waiting otherwise.

#### Scenario: red checks
- WHEN a unit at `awaiting-merge` has red checks
- THEN the PR goes back to draft and a CI fix round is raised with the failed check's log

#### Scenario: a quiet wait
- WHEN a unit at `awaiting-merge` has merge state `CLEAN`, `BLOCKED`, `UNSTABLE` or `HAS_HOOKS` and checks green or pending
- THEN it keeps waiting with no mutating `gh` call and no idle request

#### Scenario: a merge wins
- WHEN a pull request is `MERGED` or `CLOSED` while it also conflicts, is behind or has red checks
- THEN the merge or close handling wins and no fix round is raised

### Requirement: A conflicting archive pull request is rebuilt
The driver SHALL rebuild the archive branch from the current base before it asks anyone about a conflicting archive pull request.

#### Scenario: a conflict
- WHEN the archive unit's pull request conflicts
- THEN the driver rebuilds the branch from the current base and pushes it again, twice per unit, spawning no worker

#### Scenario: past the rebuilds
- WHEN the conflict remains after those rebuilds
- THEN the Lead is asked with `retry` and `skip`, and `retry` rebuilds again

### Requirement: Orphaned tasks are recovered
A drive SHALL settle an open task whose agent the runtime no longer holds.

#### Scenario: an orphan
- WHEN a drive starts and an open, unparked ledger task's assignment is not in `wait`'s `unfinished`, and `status` for its agent answers `exited`, or `unknown` with hc's `not_found`
- THEN an `orphaned` event names the task and the status answer

#### Scenario: an implementer whose report has not landed
- WHEN an orphaned implementer attempt's report has not landed
- THEN the attempt is RELEASED with reason `orphaned: <answer>`, the unit is dispatched again as `t<N>o<k>` on the same worktree and branch under the same worker name, and the plan cell reads `IN PROGRESS`

#### Scenario: a report that has landed
- WHEN the orphaned attempt's report has landed
- THEN the gate moves to `reported` and nothing is spawned

#### Scenario: a reviewer or an answerer
- WHEN the orphaned agent is a reviewer or an answerer
- THEN it takes the `exited` path

#### Scenario: a failing status
- WHEN `status` for an open task's agent raises or answers `unknown` with an error other than `not_found`
- THEN nothing is released or spawned, and a `stranded` wake names the unit and the error

#### Scenario: a settled task is not dispatched again
- WHEN `dispatch` is given a task id the ledger holds as COMPLETE or RELEASED
- THEN it refuses before any `spawn` or `resume`

#### Scenario: a released ticket
- WHEN a ticket's cell reads `IN PROGRESS`, the ledger holds at least one attempt of its unit, every attempt is RELEASED, and `status` for its worker answers definitively not live
- THEN the ticket is dispatchable again; a ticket whose unit has no attempt in the ledger is not dispatched

### Requirement: A run does not finish over open work
The loop SHALL NOT log `finish` while an open task is unsettled.

#### Scenario: a task the loop cannot settle
- WHEN the loop would finish while an open, unparked task's assignment is not in `wait`'s `unfinished` and the loop cannot settle it, including a task held by a live terminal this drive did not bind
- THEN a `stranded` wake names the task and the runtime's answer, and the run exits non-zero instead of logging `finish`

#### Scenario: a finished turn with its report
- WHEN the loop would finish while an open task's agent has ended its turn (`idle` or `done`) and its report has landed for that attempt
- THEN the gate moves to `reported`, nothing is spawned, and the run does not finish over it

### Requirement: Not-dispatched tickets
A failed dispatch SHALL wake the Lead once per ticket and reason, and only the exact cell `TO-DO` retries it.

#### Scenario: the wake
- WHEN a dispatch or re-dispatch fails for any reason
- THEN a `not-dispatched` wake names the ticket, the reason and the way out, "set the ticket's Status cell back to exactly `TO-DO`", once per ticket and reason

#### Scenario: the retry
- WHEN the Lead sets the ticket's cell back to exactly `TO-DO`
- THEN the next round tries it again; any other value (for example `TO-DO · retry`) does not retry within the run, and a new drive reads a cell that starts with `TO-DO` as TO-DO

### Requirement: An answer resumes the same attempt
`answer` SHALL resume the worker on the same task id, and an answer SHALL name no other attempt of the unit.

#### Scenario: the resume
- WHEN the Lead answers a unit's request with `answer`
- THEN the worker is resumed on the same task id with the answer, no new attempt is created, and a report written after the answer under that id passes the gate

#### Scenario: a foreign attempt id
- WHEN `decide … answer` names an attempt id of the unit other than the request's `task` (the unit followed by a round letter `r`, `d`, `o`, `p` or `a` and a number, in any case, so `T1R1` names `t1r1`)
- THEN it exits 2, writes no decision, and names the id it expects; an uppercase form of the expected id is accepted, and one shared `attempt_pattern` recognises every suffix

### Requirement: One idle watchdog
The Lead SHALL be asked about a quiet agent only when its process is gone or it has shown no activity for `--idle-minutes` (default 60), never earlier and never twice.

#### Scenario: a turn that ended while the runtime lists the assignment as working
- WHEN an agent's turn ends while the runtime still lists its assignment as working (`idle` or `done`)
- THEN it is a live agent, not an exit: a worker stays pending and is not resumed or replaced, and only the idle watchdog asks the Lead, with one `idle` request and one decision wake naming the agent, how long it has been quiet and `decide <plan> <unit> retry | answer | skip`; a reviewer whose turn ended is answered by the driver (A reviewer that ends without a verdict is resumed once)

#### Scenario: quiet for --idle-minutes
- WHEN a unit's agent state, gate and CI show nothing new for `--idle-minutes` and its agent is not reported `working`
- THEN the Lead is asked once; any change restarts the quiet time, and a shorter quiet time asks nobody

#### Scenario: a working agent
- WHEN `wait` reports a unit's agent with `runtime_state: working`
- THEN the idle watchdog does not count it until it has been `working` for `--working-minutes`, when the request says how long; the window is in memory and starts empty at each drive start

#### Scenario: a skipped unit
- WHEN the idle watchdog skips a unit (its gate is `requested`, `awaiting-merge` or parked)
- THEN that unit's working window and quiet time are cleared, so a unit that returns to `working` starts fresh

#### Scenario: a late reviewer complete
- WHEN a reviewer whose idle request is still undecided reports `complete`
- THEN its verdict is taken as for any reviewer and the request closes as resolved by the worker with a decision that says so; once the Lead has decided, a late `complete` is ignored

### Requirement: Not-started attempts reach the Lead
The drive SHALL act on the runtime's `not_started` attention verdict on the wait that delivers it.

#### Scenario: a resumed attempt that produced nothing
- WHEN `wait` reports an assignment with an `attention` verdict `not_started`
- THEN the Lead is asked naming the agent, the attempt, the runtime's own `next` sentence and the driver's way out (look at its terminal and, if it has finished, stop it and put a fresh worker on the round), and the unit is never logged `waiting` on that agent

#### Scenario: an attempt that advances
- WHEN a resumed attempt advances its turn and reports
- THEN no request is raised; a verdict on an assignment no step of the drive owns ends nothing, and the drive does not re-derive the verdict from screen or output freshness

### Requirement: A worker that ends without a report is resumed once
A worker whose process ended or whose turn needs a person without a report SHALL be resumed once with the reminder to hand it in, and the Lead SHALL be asked only when it happens again.

#### Scenario: the first end
- WHEN a worker's agent fails, exits or reports `unreported` or `error` without having handed in a report
- THEN the same agent is resumed with "You ended without a report; hand it in", and no request is written

#### Scenario: the second end
- WHEN the resumed worker ends without a report again
- THEN the Lead is asked

### Requirement: The ledger lock is bounded
The driver and every Lead command SHALL take the ledger lock through one helper and one deadline rule.

#### Scenario: a held lock
- WHEN the driver books a change while a Lead command holds the ledger lock
- THEN it waits with jittered backoff (0.2 s growing to 5 s) under one monotonic 60 s deadline that counts the lock waits and the sleeps alike, then books the change, and the drive does not exit

#### Scenario: one helper
- WHEN the driver and a Lead command both write the ledger
- THEN they take the lock through `ledger.locked` under the same deadline rule

### Requirement: Ledger kind marker
A ledger SHALL carry its kind before any file a worker reads exists.

#### Scenario: a new or unmarked ledger
- WHEN the orchestrator creates a ledger, or makes its next locked write to a ledger that lacks the marker
- THEN the ledger carries `"kind": "agent-orchestrator/ledger"` beside `version: 1`, on disk before the contract or a brief exists

#### Scenario: another kind
- WHEN the ledger is of another kind
- THEN it is refused

### Requirement: Lead reads are snapshots
A Lead read command SHALL read an unlocked snapshot, and any write it makes SHALL revalidate under the lock.

#### Scenario: a read that then writes
- WHEN `show`, the read half of `complete`, or `evidence` runs during a drive and then writes
- THEN the write takes the lock and revalidates against the current ledger, refusing with `the ledger changed since it was read` when the keys it decided on moved, and writes nothing

### Requirement: A crashed driver is visible
A driver that fails SHALL log the crash, wake the Lead and exit non-zero, and `await` SHALL say so.

#### Scenario: a crash
- WHEN the ledger stays busy past the deadline, or any other exception escapes the driver during startup, the loop or the finalization
- THEN the driver logs `crashed <reason>` (`crashed ledger busy >60s` for the ledger), writes a `crash` wake without taking the ledger lock, and exits non-zero; a `run` refused because another driver holds the run lock logs no crash

#### Scenario: await on a dead driver
- WHEN `await` runs while no driver holds the run lock and the run has not finished
- THEN it returns `crashed -> <log line>` when the driver logged a crash, and `driver gone without a crash log` when a driver started for the run and died without one; a wake the Lead has not seen is printed first, and a run whose driver has not started or holds the run lock is still awaited

### Requirement: Intents before sends
The driver SHALL book an intent before it spawns or resumes an agent.

#### Scenario: booking
- WHEN the driver is about to spawn or resume an agent (a first spawn, an attach to a live pane, a fix round's, an answers round's or a chase's hand-over, a payload repair)
- THEN it books an intent in the ledger's `intents` with a fresh UUID `operation_id` before it calls the runtime with it, and binds the intent to the assignment the runtime answers

#### Scenario: a runtime that refuses operation ids
- WHEN the runtime refuses operation ids
- THEN no unkeyed send is made, and the Lead gets a request (`retry` or `skip`); a first spawn gets the ticket's `not-dispatched` wake

### Requirement: A restart resolves open intents
A restarted driver SHALL look up an open intent before it acts on it.

#### Scenario: a known operation
- WHEN the driver restarts and an agent's intent is open, and `agent-runtime call operation` answers `absent`
- THEN it acts again under a new intent

#### Scenario: an adopted assignment
- WHEN the lookup answers `bound`, `working`, `blocked`, `decision`, `complete` or `failed`
- THEN the driver adopts that assignment without sending

#### Scenario: an unknown operation
- WHEN the lookup answers `pending`, `unknown`, an answer the driver does not know, or fails
- THEN it raises a Lead request ("the resume may or may not have reached <worker>: retry or skip") and sends nothing on its own, looking the key up once; the Lead's `retry` runs the held step again under a new intent, and any decision settles the held intent

### Requirement: A failed gate is booked once
A gate failure SHALL be booked in one ledger write before any git or gh call.

#### Scenario: a failing gate
- WHEN a gate fails
- THEN the round increment, the report's checkpoint, the next phase (a fix round, or the Lead's request past the cap) and the draft the pull request still owes (`fix.draft`) are booked in one ledger write, and a restart after a crash anywhere later counts the round once

### Requirement: Ledger invariants
A ledger write SHALL refuse a state that breaks an invariant.

#### Scenario: a broken ledger
- WHEN a ledger is written, or captured and read with `ledger check`, with a park carrying no release key, or a gate the driver does not know
- THEN the write is refused naming the invariant, the task and the way out, `agent-orchestrator ledger check` reports the same on a captured ledger with exit 2, and a ledger the driver itself finishes on passes

### Requirement: Request files are repaired
The driver SHALL rewrite an unusable request file from the ledger before it waits on the Lead.

#### Scenario: an empty request
- WHEN a unit's request file is missing or does not validate (a crash left it empty)
- THEN the driver writes it again from the ledger before it waits, so `decide` can still answer it; a readable request is never rewritten

### Requirement: Decisions match the request's contract
A decision SHALL be written in the contract version of the request it answers, and the driver SHALL refuse one it cannot read.

#### Scenario: the version follows the request
- WHEN the Lead answers an open request
- THEN the decision is written in the contract version of that request

#### Scenario: a mismatched decide --json
- WHEN `decide --json` names a contract version the request does not carry
- THEN the decision is refused naming both versions and the way out, and nothing is written

#### Scenario: an unreadable version
- WHEN the driver reads a decision whose contract version it does not read
- THEN it asks the Lead again naming the reason and the way out (restart the driver on the current code), and applies no decision

### Requirement: Archive retry needs ACCEPTED
A `retry` for the archive unit SHALL wait for the plan to be ACCEPTED.

#### Scenario: decide
- WHEN the Lead answers the archive unit with `retry` while the plan's state is not ACCEPTED
- THEN the decision is refused naming the state and the way out, the request stays open and no decision is written

#### Scenario: the driver
- WHEN the driver applies a `retry` for the archive unit while the plan's state is not ACCEPTED
- THEN it builds no archive and opens no pull request for that retry, and books a request for the Lead naming the state and the way out

### Requirement: Check tiers are read from the config
`.orchestrate/config.yaml` SHALL be able to declare `checks.fast`, `checks.affected` and `checks.full.run`.

#### Scenario: the keys
- WHEN the config carries a `checks` node
- THEN `checks.fast`, `checks.affected` and `checks.full.run` (strings) are read, each optional; an unknown key, or a placeholder in `affected` other than `{base}` and `{paths}`, is refused naming its dotted path and the fix

#### Scenario: no tiers
- WHEN `plan check` runs in a repo whose config declares no `checks` node
- THEN it prints one warning on stderr naming `.orchestrate/config.yaml` and the fields, exits 0, and workers are briefed with `--gate`, or the full-CI-gate default

### Requirement: Check tiers brief the worker
A ticket worker's required checks SHALL be the repository's check tiers, or the named gate alone.

#### Scenario: tiers
- WHEN a ticket worker is briefed in a repo whose config declares check tiers and the run names no `--gate`
- THEN its required checks are `checks.fast` and `checks.affected` with `{base}` rendered as the run's `--base` ref as given and `{paths}` as the ticket's paths, each shell-quoted and space-joined; the "Before you hand in, run:" line lists exactly those, one other line names `checks.full` as the check CI runs, and the brief no longer asks for the repository's full CI gate

#### Scenario: a named gate
- WHEN a run names `--gate` in a repo whose config declares check tiers
- THEN the required checks are the named gate commands alone and the tiers are not applied

#### Scenario: overlapping tiers
- WHEN `checks.fast`, the rendered `checks.affected` and `checks.full.run` render the same command, whitespace apart
- THEN the required checks list it once, the brief neither lists it twice nor tells the worker not to run it

#### Scenario: a distinct full
- WHEN `checks.fast` and the rendered `checks.affected` differ from `checks.full.run`
- THEN the assignment keeps `fast` and the rendered `affected` as its required checks, and a distinct `full` is named only as the whole suite CI runs

### Requirement: This repository declares check tiers
An orchestrate run in this repository SHALL be briefed with check tiers.

#### Scenario: the repo config
- WHEN an orchestrate run starts in this repository
- THEN its config declares `fast` as the unit suite, `affected` selecting through pytest-impacted from `{base}`, and `full` as the whole suite in parallel, and `plan check` prints no tiers warning

### Requirement: Workers run tests in the foreground
A brief that carries required checks SHALL say how to run them.

#### Scenario: the run discipline
- WHEN a worker receives a brief that carries its required checks
- THEN it tells the worker to run those checks and only the test files it wrote or changed, each in the foreground under a timeout, to read failures from that run and to re-run only the tests that failed, naming no runner, flag or file of any one repository

### Requirement: The reviewer brief gives one pass
The first-review brief SHALL give the reviewer's pass as numbered steps in one order, with every path it works from named.

#### Scenario: the pass
- WHEN a reviewer receives the brief for a unit's first review
- THEN the brief names the ticket's scenarios (the task file), the diff `base...head`, the implementer's report, the plan and the repository's rules files, and gives the pass in order: read the whole diff first; name what a user or caller would hit (wrong behaviour, data loss, a lost wake, a run that cannot finish or restart, a satisfied scenario no test proves, a test that still passes with the change reverted); read a library's or tool's documentation when a claim depends on it; make each finding one checkable claim about one quoted line

#### Scenario: the reviewer reads and judges only
- WHEN a reviewer receives the brief
- THEN it is told to run no command other than git reads, to leave formatting, lint, failing tests and conflicts to CI, and to hand in the verdict as the text of its `report` call

#### Scenario: the paths
- WHEN the brief is printed
- THEN every path it names is a file that exists or a collection with its number of files, with no glob over the unit's run directory

### Requirement: A re-review resumes the reviewer on the fix
A re-review SHALL go to the reviewer session that holds the change, with the fix diff and what the fix was asked to answer, and nothing else.

#### Scenario: the same session
- WHEN a unit that was reviewed reports again and its reviewer's pane is live
- THEN the same reviewer is resumed with `since..head` (the last reviewed commit to the reported head), the worker's report and the findings file of the round it answers; the message holds no instruction to re-read the whole change, and when the last verdict blocked it says to check each earlier finding against the fix

#### Scenario: a reviewer that is gone
- WHEN the reviewer's pane is gone at a re-review
- THEN a fresh reviewer gets the first-review brief

#### Scenario: still blocked
- WHEN a re-review of a unit whose last verdict blocked blocks again
- THEN the Lead is asked with the findings, and no further fix round is raised

### Requirement: Workers are briefed on the tdd skill
A worker's contract SHALL name the `tdd` skill and inline its admission rules for a backend with no skill loader.

#### Scenario: the worker
- WHEN a YAML plan's worker is briefed
- THEN its packet names the `tdd` skill and inlines that skill's admission and cost rules for a backend with no skill loader; its report carries no test cost and no scenario ID names a test

### Requirement: A drive parks on a plan it cannot use
A live drive SHALL park, not give up the plan, when it reads a plan it cannot use.

#### Scenario: the park
- WHEN a live drive reads a plan it cannot use (its bytes changed, it does not load, or it lost its `approval` span) and nothing is in flight
- THEN it records the park in its ledger under `park` (`cause`, `error`, the `digest` of the approval it holds and the `since` of the park), shows it in `attention.md`, wakes the Lead naming a way out that works (`plan approve --base` at the approval's own base, or the snapshot to restore from), and starts no new work while parked

#### Scenario: a repeated wake
- WHEN a drive has been parked for 15 minutes with nothing in flight
- THEN it wakes the Lead again naming the way out, and every 30 minutes until the park ends

#### Scenario: a lost approval span
- WHEN a live drive reads the plan it holds with no `approval` span
- THEN it recovers the approval from the snapshot it holds and keeps running, and the plan it reads is the approved one

#### Scenario: a re-approval
- WHEN the Lead runs `plan approve` on a plan whose `approval` span was lost while a drive held it
- THEN the re-approval succeeds at the approval's own base and the plan file carries its `approval` span again

#### Scenario: a run that ends parked
- WHEN a run ends while units are parked
- THEN the ledger and `attention.md` name the park, its cause and the way out, and the run's exit says the units are parked rather than that it had nothing to do

### Requirement: Pointers name one file
Every instruction the engine renders SHALL point at a file that exists, the file the reader writes, or a collection with its number of files.

#### Scenario: a pointer
- WHEN the engine renders an instruction for an agent or a person
- THEN each pointer names a file that exists, the file the reader is asked to write, or a collection with its number of files; no pointer is a glob, a bare directory to search, or a request for the newest file

#### Scenario: a pointer that resolves to nothing or several
- WHEN a pointer resolves to zero or several files
- THEN it is refused naming the pointer, the expected file and the way out

### Requirement: Launch hands over the Lead's environment
`launch` SHALL start the driver with the Lead's environment and leak no value.

#### Scenario: the environment
- WHEN `launch` starts the driver
- THEN the driver runs with the Lead's environment minus the pane and session variables (the terminal id, `CLAUDECODE`, `CLAUDE_CODE_*`), and no value is written to argv or logs: the environment travels in a mode 0600 file the pane deletes at start

### Requirement: Narration
The driver SHALL narrate a run on stderr, opening with a banner.

#### Scenario: a line
- WHEN the driver narrates a run
- THEN each line carries the time, the unit, the step (one of `narrate.STEPS`), a kind symbol and the text, the attempt last; colour is used only on a terminal with `NO_COLOR` unset; a `wait` the unit already said is repeated only every 5 minutes as `(still, Nm)`, and any other line of the unit resets it; run-dir paths are relative, the home is `~`, a GitHub PR URL is `#N`, and the text is cut at 200 characters

#### Scenario: the banner
- WHEN a drive starts
- THEN before any worker line the driver writes the `narrate.LOGO` wordmark, then the plan's slug and goal, its ticket count by status, the repository and base, `--max-workers`, the `--gate` commands when any, the route source and one line per class route (`backend · model · effort`, `effort by matrix` when the matrix decides) and one per step route

### Requirement: Usage rolls up
Usage figures SHALL be summed over a ticket's assignments and `null` when any assignment lacks them.

#### Scenario: the rollup
- WHEN `events.rollup` totals a ticket whose assignments reported usage dicts with different keys, or one of whose assignments never reported usage
- THEN each key is the sum over the ticket's assignments when every assignment reported it, and `null` when any did not; a ticket none of whose assignments reported usage stays `unknown`

#### Scenario: attempts
- WHEN the driver writes `attempts.jsonl`
- THEN each record is schema 3: `tokens` carries `input`, `output`, `cached`, `cache_write` and `reasoning`, the record carries `cost_usd`, and `counters.model_calls` and `counters.tool_calls` are summed from usage, each `null` when any of the attempt's assignments lacks it; `evidence-manifest.json` names the new fields

#### Scenario: a missing cost
- WHEN a ticket's worker reports usage with `cost_usd` and its reviewer reports usage without it
- THEN `metrics.json` and `attempts.jsonl` sum the tokens and calls with `cost_usd` null; when both report `cost_usd`, both files sum it
