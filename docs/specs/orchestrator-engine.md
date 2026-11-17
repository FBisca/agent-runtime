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

### Requirement: A review is read after a bounded grace
The driver SHALL wait a bounded grace for the file a reviewer's report names and tell the reviewer before it asks the Lead.

#### Scenario: a file that lands late
- WHEN a reviewer's report reaches the driver before the review file it names is usable (absent, half written, or breaking its contract)
- THEN the driver waits a bounded grace and reads the file when it lands, and ends the wait at the deadline when it never does

#### Scenario: a payload that stays unreadable
- WHEN the review file is still unreadable after the grace
- THEN the reviewer is told what the driver needs, twice at most, with the gate kept open, and only then is the Lead asked, naming the warnings; an agent that is gone is not warned

### Requirement: A checkpoint needs its checkout
`checkpoint()` SHALL refuse a report whose unit has no checkout, and SHALL hold no check outcome against COMPLETE: CI judges the tests.

#### Scenario: failed or unrun checks
- WHEN a COMPLETE report carries a `failed` check, a `not_run` check, or omits a command the brief named
- THEN `checkpoint()` accepts it; the driver compares no reported command with the brief and CI is the judge of the tests

#### Scenario: a PARTIAL checkpoint without a checkout
- WHEN a PARTIAL checkpoint runs and `packet.checkout` does not exist
- THEN `checkpoint()` raises

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

### Requirement: A failed check is re-run once
The driver SHALL re-run the failed jobs of a failing check once before it acts on the failure, treat a failure that then passes as flaky, and ask the Lead when a job fails twice with no step failing.

#### Scenario: a flaky failure
- WHEN a unit's checks fail and the one automatic `gh run rerun --failed` of those runs ends green
- THEN the unit proceeds to merge-ready with no fix round and a `flaky` event names the checks; the events are listed in the archive pull request at plan close

#### Scenario: a failure that stays
- WHEN the checks fail again after the re-run
- THEN the failure goes back to the worker as a fix round (the re-run is not repeated on that head); a check still running is waited for, never treated as a failure

#### Scenario: an infrastructure failure
- WHEN a job fails twice and its log holds no failed step (no test or check step failed)
- THEN the Lead is asked; when the driver cannot tell the two apart it treats the failure as a test failure

### Requirement: A failed check passing on main goes back to the worker
A CI failure that does not also fail on the base branch SHALL go back to the unit's worker with the evidence and the way to reproduce it.

#### Scenario: the fix round
- WHEN a check fails that passes on the base branch
- THEN the worker gets a fix round whose findings carry the failed job names, the run id and the last 50 lines of each failed step, and whose brief tells it to re-run those tests locally to reproduce, fix them and re-run them, and to hand in BLOCKED with an `environment:` blocker when they pass locally and fail in CI

#### Scenario: failing three times
- WHEN the same gate has failed three times
- THEN the Lead is asked

### Requirement: A failed check that also fails on main asks the Lead once
A check that fails on the base branch as well as in a ticket SHALL cost the Lead one request for the whole run, not one per ticket.

#### Scenario: main is red
- WHEN a ticket's check fails that fails in the base branch's latest completed run (read once per run with `gh run list` and `gh run view`)
- THEN one `ci` request says main is red, no worker round and no re-run is spent, and every other ticket failing a check that fails on main waits on that request; when the Lead decides it the waiting tickets look again

### Requirement: A pending CI log is not a finding
The driver SHALL wait on a failing check whose workflow run is still in progress.

#### Scenario: the placeholder log
- WHEN a check fails while its workflow run is in progress, so `gh run view --log-failed` answers that logs will be available when it is complete
- THEN the CI state stays pending and the unit is not asked; once the failing step's log is readable the gate fails and the request quotes that log, never the placeholder

### Requirement: A conflicting pull request is rebased
A unit whose pull request conflicts SHALL go back to draft and be rebased by a fresh worker, and no ready or update call SHALL run on it meanwhile; when another open ticket changes the same files, the rebase waits for that ticket to merge.

#### Scenario: a conflict while waiting for the merge
- WHEN a unit at `awaiting-merge` has a pull request that turns `DIRTY` or `CONFLICTING`
- THEN the PR goes back to draft and a conflict fix round gives a fresh worker a rebase brief quoting the merge state; the rebase goes through a delta re-review and CI, and `gh pr ready` runs again before the unit waits again

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

#### Scenario: a disputed change
- WHEN a disputed answer with `changes_code` sends a unit back from `ready-for-ci` or `awaiting-merge`
- THEN the unit's readiness is forgotten across its attempts and its checks cleared, the PR is a draft through the delta re-review, and `gh pr ready` runs again on its return

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
- THEN it is a live agent, not an exit: a reviewer advances only on its own `complete`, a worker stays pending, an answerer stays working, none is resumed or replaced, and only the idle watchdog asks the Lead, with one `idle` request and one decision wake naming the agent, the review file a reviewer owes, how long it has been quiet and `decide <plan> <unit> retry | answer | skip`

#### Scenario: quiet for --idle-minutes
- WHEN a unit's agent state, gate and CI show nothing new for `--idle-minutes` and its agent is not reported `working`
- THEN the Lead is asked once; any change restarts the quiet time, and a shorter quiet time asks nobody

#### Scenario: the reviewer brief
- WHEN a reviewer is briefed
- THEN the brief says the reviewer writes the review once the diff is read and then reports `complete` with the brief's assignment id

#### Scenario: a complete that cannot be read
- WHEN a reviewer's `complete` announces a review file the driver cannot read
- THEN the grace and capped recovery apply, and each recovery message names the file to write or repair and says to report complete under the current assignment

#### Scenario: a working agent
- WHEN `wait` reports a unit's agent with `runtime_state: working`
- THEN the idle watchdog does not count it until it has been `working` for `--working-minutes`, when the request says how long; the window is in memory and starts empty at each drive start

#### Scenario: a skipped unit
- WHEN the idle watchdog skips a unit (its gate is `requested`, `awaiting-merge` or parked)
- THEN that unit's working window and quiet time are cleared, so a unit that returns to `working` starts fresh

#### Scenario: a late reviewer complete
- WHEN a reviewer whose idle request is still undecided reports `complete`
- THEN the review is taken as for any reviewer (the unit goes on to `reviewed`) and the request closes as resolved by the worker with a decision that says so; once the Lead has decided, a late `complete` is ignored

#### Scenario: the answers record
- WHEN the Lead decides a request that did not name a busy answerer, such as a `ci` request answered `fresh worker`
- THEN the answers record is not failed and the answerer's later `complete` is received; when a decision does fail the record because the request named the answerer, an `answers-dropped` event says why `pr-ready` stopped waiting on the answers

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
- WHEN a ledger is written, or captured and read with `ledger check`, with two open attempts naming one review file, a park carrying no release key, or a gate the driver does not know
- THEN the write is refused naming the invariant, the task and the way out, `agent-orchestrator ledger check` reports the same on a captured ledger with exit 2, and a ledger the driver itself finishes on passes

### Requirement: A reworked unit writes a new review
A unit reviewed before and worked again SHALL write its next review to the next write-once file.

#### Scenario: a second review
- WHEN a unit that was already reviewed is worked again after the Lead answers its request (the same task id)
- THEN the next review is written to the next write-once review file, the ledger's review names it, and the earlier review stays on disk with the blocker ids it named

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

### Requirement: Review items assert violations as blocking
A contract-2 review item that asserts a requirement violation SHALL be blocking, and older contracts SHALL read as before.

#### Scenario: a violation item
- WHEN a contract-2 review item carries `requirement_violation` with `source`, `id`, `expected` and `observed` (each nonempty text)
- THEN its `kind` must be blocking with the item's `file`, `line`, `fix` and `evidence`; an item that is not blocking, whose metadata is missing, blank or not text, or whose `requirement_violation` is neither an object nor `null` is refused by `validate_review` and `ReviewV2` naming the item, before the unit can advance to CI; an explicit `null` reads as absent; the reviewer brief asks for the blocking item and its metadata

#### Scenario: no violation
- WHEN a review item carries no `requirement_violation`, or a clean contract-2 or a contract-1 review is read
- THEN a `deferred` item does not block, the unit goes on to CI, and both readers still read the older contract unchanged

### Requirement: Open blocking findings persist
An earlier blocking finding SHALL stay open until a supported resolution closes it, held by a record and not by process memory.

#### Scenario: an omitted finding
- WHEN an earlier blocking finding is omitted, downgraded, or lies outside a later fix delta without an explicit supported resolution
- THEN its stable id stays open with its first payload at `<run>/<unit>/open-findings.json` as `{contract: 1, unit, items[]}`; a review that passes while one is open is refused before CI naming the ids, the record and the way out, and a blocking review hands every open item to the fix round with the new ones

#### Scenario: a restart
- WHEN the driver restarts after recording a blocking finding and processes a passing re-review that omits it
- THEN the persisted open finding still holds the unit before CI or delivery, and a missing record is rebuilt from the unit's own reviews

#### Scenario: a legacy item
- WHEN a legacy `major` item is carried by an open set into a contract-2 round
- THEN it keeps its payload there and the findings file is written as contract 2, so the round reads instead of becoming a Lead request

### Requirement: Only a supported resolution closes a finding
A reviewer SHALL close an open blocking finding only with a top-level `resolutions` entry backed by evidence.

#### Scenario: a valid resolution
- WHEN a contract-2 review has a `resolutions` entry for the id with `status` fixed or disproven, the reviewed commit, a nonempty note, and evidence whose command ran with exit 0 and appears in `checks_run` with that command, that exit and the reviewed commit
- THEN the finding closes

#### Scenario: an invalid resolution
- WHEN a resolution is a bare acknowledgment, has an unsupported status or a blank note, names an unknown, duplicate or conflicting id, also raises the id blocking, names a commit other than the one reviewed, or has failed or unmatched evidence, or a contract-1 review carries `resolutions`
- THEN `validate_review` and `ReviewV2` refuse it naming the resolution and the finding stays open

### Requirement: The reviewer brief gives one pass
The first-review brief SHALL give the reviewer's pass as numbered steps in one order, with every path it works from named.

#### Scenario: the pass
- WHEN a reviewer receives the brief for a unit's first review
- THEN the brief gives the pass as numbered steps — read the whole diff first; the ticket delivers what the task file's `What it delivers` says, each satisfied scenario proved by a test the diff adds or changes that asserts the THEN's behaviour on the real code path, carries its spec tag and fails when the change is reverted (a test that would pass without the change is blocking); the implementer's report matches the code at the reviewed commit (`covers` nodes that exist, `checks` outcomes the log shows, `head`, `files`); the requirements the change touches still hold; the repository's rules hold — and only after that judges severity

#### Scenario: the reviewer reads and reports correctness bugs only
- WHEN a reviewer receives the brief
- THEN it asks for a correctness bug a user or caller would hit, or a test that would still pass without the change, judged by reading the diff, the test and the report's `red` output; the reviewer runs no test and no command other than git reads, and CI and the red-first gate are the executed evidence

#### Scenario: the paths
- WHEN the steps are printed
- THEN they follow the paths they work from: the implementer's task file, the report path the ledger recorded for the attempt under review (its exact path, mtime and claimed head), the run's `contract.md`, the plan and the repository's own rules files, with no glob over the unit's run directory and no fallback file read under another name

### Requirement: The re-review brief starts at the delta
A re-review brief SHALL print the same pass starting at the delta.

#### Scenario: a re-review
- WHEN the brief is for a re-review
- THEN its first step reads the delta `<from>..HEAD` with the whole change beside it, checks each earlier finding (resolved or not, with evidence), raises new blocking items only inside the delta, defers a blocking item outside the changed files unless it is about an earlier finding (reusing that finding's `id` or setting `finding` to it), and states that rule once, in that step

### Requirement: The review file path is named
The reviewer's assignment SHALL name the absolute path of the review file it writes.

#### Scenario: the assignment
- WHEN the driver starts the reviewer of a unit
- THEN the assignment names the absolute path of the review file, beside the brief it must read

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
