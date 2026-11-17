# Agent workbench

The workbench is a person's view of the orchestrator from one Herdr pane: the plans, each plan's ticket graph, attempts, evidence and live workers, and the week's totals. It has two halves. The read commands (`agent-orchestrator plans list`, `workbench plans`, `workbench plan`, `workbench totals`, all `--json`) read the orchestrator's own plan loader and ledger, write nothing and guess no join. The Herdr mod in `herdr/agent-workbench/` draws their answers; its logic is in pure modules (`model.js`, `data.js` and the views) that the repository suite runs under `node --test`. The only commands the mod gives Herdr beyond the two read verbs are Open and the panel button's layout commands; they change no file and no ledger. The installer side of the mod is in [installer-contract.md](installer-contract.md).

## Requirements

### Requirement: Workbench plan slugs
`plans list` and `workbench plans` SHALL list a YAML plan under its `slug:` field, and a Markdown plan beside a YAML twin of the same stem SHALL NOT be listed.

#### Scenario: a dated file name
- WHEN a YAML plan file is `<date>-<name>.plan.yaml`
- THEN its `slug` is the plan's `slug:` field (the ledger key), not the file name; a YAML plan that does not parse keeps its file-derived slug

#### Scenario: a Markdown twin
- WHEN a `.plan.md` has a `.plan.yaml` of the same stem
- THEN only the YAML plan is listed; a Markdown plan with no twin is listed as legacy

### Requirement: Workbench plan list
`workbench plans --json` SHALL print one JSON object with `schema_version` and one entry per plan, grouped, ordered and counted from the ledger and the plan file.

#### Scenario: an entry per plan
- WHEN it runs in a repository holding YAML and Markdown plans
- THEN each entry carries slug, format, path, state (the ledger's, else APPROVED or DRAFT from the approval block) and ticket counts by state; a Markdown plan carries `legacy: true` and no graph; a plan that does not load is an entry with an `error` and null state and counts, and the other plans are still listed

#### Scenario: groups and order
- WHEN plans are in every state
- THEN each entry's `group` is `active` (RUNNING, APPROVED never driven, DRAFT, or a plan that fails to load with no final ledger state) or `older` (ARCHIVED, ACCEPTED, a Markdown-only plan, or a plan that fails to load with a final ledger state); `last_activity` is the newest `at` in `events.jsonl`, else the plan file's mtime, else null; entries order active before older, newest activity first, null last

### Requirement: Workbench errors
The workbench commands SHALL report a missing repository, a missing plan directory and an ambiguous one as distinct JSON errors with the way out.

#### Scenario: no repository, no plans or several directories
- WHEN the commands run outside a repository, in one with no plan directory, or in one with several and no `.orchestrate/config.yaml` naming one as `plans.dir`
- THEN each is an error of its own kind (`not_a_repo`, `no_plans`, `ambiguous` naming every directory) with the way out (an explicit path or `--repo`), printed as one JSON object on stderr with stdout empty and exit status 2, never a crash and never an empty list standing in for an error

### Requirement: Ticket graph
`workbench plan <plan> --json` SHALL print every ticket with exactly its `blocked_by` edges and a topological layer, deterministically.

#### Scenario: layers and order
- WHEN it runs on an approved YAML plan
- THEN each ticket carries its `blocked_by` edges and a layer (0 for no blockers, else one more than its deepest blocker), tickets are ordered by layer then plan order, each carries its ledger state and reason, and two runs over the same inputs print byte-identical stdout

### Requirement: Invalid plan refused
`workbench plan` SHALL refuse a plan the loader rejects, print no partial view and write nothing.

#### Scenario: a defective plan
- WHEN the plan file has a cycle, a duplicate ticket id, a reference to a missing ticket or malformed YAML
- THEN it exits non-zero with the loader's error naming the YAML path and the offending ids (`error.kind` is `invalid_plan`, `error.path` the file), as one JSON object on stderr with stdout empty

### Requirement: Approved snapshot drift
`workbench plan` SHALL show the approved snapshot of a plan edited after approval, with the drift marked.

#### Scenario: an edited plan
- WHEN the authored plan file changed after approval
- THEN the view shows the approved snapshot's tickets with `revision.drift: true` and both digests (`approved_digest`, `current_digest`); the ledger is read under the snapshot's slug and ticket ids, so plan and ticket states survive an edit of the slug or the tickets, in `workbench plan` and in the `workbench plans` listing; a draft has no approval to drift from

### Requirement: Attempt history
`tickets[].attempts` SHALL list every ledger task of a ticket once, oldest first, and overwrite none.

#### Scenario: fix rounds and retries
- WHEN a ticket has several attempts (fix rounds, retries, orphans, reopens) and a review
- THEN the list carries `task_id`, `kind` (`worker` for the unit's own id, else `fix`, `retry`, `orphan`, `reopen` or `answer`), `status`, `started_at`, `finished_at`, `assignment_id`, `address`, `claimed` and `latest`, ordered by start time then ledger order; the newest is `latest`, no earlier attempt is dropped, another ticket's attempts are not mixed in, and an unmeasured time is null

### Requirement: Attempt addresses
An attempt's `address` SHALL be the runtime address the runtime returned, and null otherwise.

#### Scenario: a fallback is no address
- WHEN a ledger task holds no runtime address, or the fallback the driver wrote when the runtime returned none (the assignment id or the agent name)
- THEN `address` is null; it is never taken from a terminal name, another attempt or a timestamp, and a ledger written before the `address` field maps the fallback in `runtime_id` to null

#### Scenario: the driver records only what it got
- WHEN the runtime returns an address, or none
- THEN the driver records it on `agents[name].address` and on the task, or leaves both null, never the assignment id or the name

### Requirement: Claimed versus verified
A ticket SHALL be `verified` only when it is COMPLETE; an attempt's `claimed` is the worker's own report.

#### Scenario: a worker says complete
- WHEN a worker reported complete but its ticket is not COMPLETE
- THEN the ticket shows `verified: false` and the attempt `claimed: true`; an attempt still running, or one that ended with no complete report, claims nothing

### Requirement: Workbench output is read-only
The workbench commands SHALL write nothing and print no report or message body or environment value.

#### Scenario: reading a repository
- WHEN any workbench command runs
- THEN nothing under the repository or the state directory is written, and the output holds no report or message bodies and no environment values

### Requirement: Needs me list
`workbench plans --json` SHALL carry a top-level `needs_me` list of what waits on a person in active plans.

#### Scenario: the kinds
- WHEN active plans wait on a person
- THEN `needs_me` holds `{plan, ticket, kind}` items for active plans only: `needs_you`, `driver_gone` (RUNNING, last `start` pid not alive, no later `finish`), `error` (an error event since the last start), `ready_to_approve` (a DRAFT that loads), `ready_to_drive` (APPROVED never driven), `fix_plan` (fails to load) and `lead_pending` (last escalation of a unit with no later decision or merged stop); no escalation reason, report or message text is printed

#### Scenario: a gone driver
- WHEN a running plan's driver is gone and its log holds an unanswered escalation and an error
- THEN `driver_gone` is listed and its `lead_pending` and `error` items are dropped, while `needs_you` stays; a plan whose driver is alive keeps all of them

### Requirement: Run metrics
`workbench plan --json` SHALL carry `run_dir` and a `metrics` block computed read-only from the run's event log.

#### Scenario: a driven plan
- WHEN the plan has a run directory
- THEN `metrics` carries `wall_seconds` (each drive from `start` to its `finish` or last event), `first_pass_rate`, `errors.by_gate` and `errors.last` (at most 120 characters), per ticket `elapsed_seconds`, `phases.work|review|ci|merge`, `fix_rounds`, `review_rounds`, `findings` (by source and kind), `ci_failures`, `escalations` by cause, `model` and `tokens` (null when unmeasured), and `timeline` segments; a plan never driven has null `run_dir` and `metrics`; an open phase has no end and no duration

#### Scenario: the merge wait
- WHEN a ticket's task waits at the awaiting-merge gate until the driver sees its merge
- THEN its metrics carry a `merge` phase for that wait, `ci` ends where it starts, and the timeline lane ends with a `merge` segment at the merge

### Requirement: Ticket agents
`workbench plan` and `workbench plans` SHALL list the agents the driver started, joined to tickets by the ledger's unit, never by name.

#### Scenario: agents on tickets and plans
- WHEN the ledger records agents for a ticket's unit
- THEN each ticket carries `agents` (`{name, role, address}`, `address` null when never bound, `[]` for none, an agent of no ticket on no ticket), and each plan in the listing carries `agents` (`{name, role, ticket, address}`) and `awaiting_merge` (`{ticket, pr}`); an older plan carries both empty

### Requirement: Weekly totals
`workbench totals --json` SHALL sum the tickets merged in the last seven days into five cards, with last week's cards and the change.

#### Scenario: the cards
- WHEN it runs over a repository's plans
- THEN `window.days` is 7 and `cards` carries `merged`, `first_pass_rate` (merged with no fix round), `escalations`, `tokens` `{input, output, measured, tickets}` over the measured merged tickets and `median_ticket_seconds` (dispatch to merge); an archived plan counts what it merged in the window; a plan never driven, an unmerged ticket and anything before the window count for nothing; with nothing merged the rate and median are null and tokens is `{null, null, 0, 0}`, never a guess

#### Scenario: last week
- WHEN it runs
- THEN `previous` holds the five cards over the seven days before the window (`window.previous` names them; a plan's log is read even when last written before this window) and `change` per card is `(now - previous) / previous` (tokens over output), null when last week's value is null or 0, so a first week has every change null

#### Scenario: a stable answer
- WHEN the answer gains fields
- THEN `schema_version` stays 1 and every field it carried keeps its name, shape and value, but `merged.by_day`, which lists every date with its `by_plan`; it writes nothing and prints no escalation text

### Requirement: Totals dashboards
`workbench totals --json` SHALL carry each card's dashboard detail.

#### Scenario: the detail
- WHEN it runs
- THEN `time` holds `steps` (work, review, ci, merge: each `{median, max, p90}`) and the five slowest tickets (`time.tickets`, at most twelve, each with `plan`, `ticket`, `pr`, `seconds` and `phases`, an unmeasured phase null); `merged` holds `by_day` and its tickets `{plan, ticket, pr, merged_at}`; `first_pass` holds `by_plan`, the `fixed` tickets `{plan, ticket, fix_rounds, findings, reasons}`, `by_reason` and `rounds` (merged tickets by fix rounds `"0"`, `"1"`, `"2"`, `"3+"`); `escalations` holds `by_cause`, `by_plan`, `by_plan_cause` and the ten `latest`, newest first, each with `unit` (the ticket id, or the unit's own name with `ticket` null) and no reason text; `tokens` holds `by_plan`, `by_model` and the ten `top` merged tickets

### Requirement: Totals series
`workbench totals --json` SHALL carry a per-day series for every card.

#### Scenario: a week with quiet days
- WHEN it sums a week with merges, escalations and quiet days
- THEN `merged`, `first_pass`, `escalations`, `tokens` and `time` each carry `by_day`: every UTC date the window touches (eight when it starts mid-day), oldest first, every date present, so the days sum to the card; merged days carry `count` and `by_plan`, escalation days `count` and `by_cause`, and a day with nothing merged has `rate`, `output` and `median` null, never 0

### Requirement: Driven plan outcome
The workbench commands SHALL read a plan driven by the real driver exactly, and write nothing.

#### Scenario: a fixture plan through the driver
- WHEN a fixture plan is driven with a fake runtime and `gh` (a ticket accepted end to end, a cycle, a ledger holding the address fallback) and read with the workbench commands
- THEN the slug is the plan field, the edges and layers are exactly `blocked_by`, the output is stable, a cycle is a scoped error, the attempt maps to its assignment and real address, the fallback maps to null, no report body appears, and the repository and state directory are byte-identical before and after

### Requirement: Mod plans and tickets
The mod SHALL list the repository's plans and show a selected plan's tickets as a layered list with one inspector.

#### Scenario: a repository with plans
- WHEN the mod is shown for a space whose folder is a repository with plans
- THEN it lists the plans (YAML with state and progress, Markdown marked legacy), and selecting a YAML plan shows its tickets in the layer and plan order the command printed, with blocked-by chips that carry each blocker's state and one inspector for the selected ticket (delivers, blockers, state, verified, attempts); a value the answer does not carry is shown as unknown or not reported, never filled in

### Requirement: Mod failure states
The mod SHALL show a failure in its scope with the way out and draw no placeholder data.

#### Scenario: a command fails
- WHEN the workbench command fails, times out, prints no JSON, returns a scoped plan error, or finds no plans
- THEN the mod shows that state scoped to the plan or project with the way out (for no plans, `/start-planning`) and other plans stay usable; a plan that does not load is a row with its error and no progress, and a failed plan fetch is that plan's error and leaves the list and the other plans as they were

### Requirement: Mod narrow layout
The mod SHALL fit a panel narrower than 720 logical pixels.

#### Scenario: narrow and wide
- WHEN the mod is narrower than 720 logical pixels (before the first measurement it is full width)
- THEN a plan is one compact column with one line per ticket and the inspector as a drill-in with a way back, home's cards are two per row and its plans one per row, dashboards are one column of shorter charts, and an open plan's timeline becomes each ticket's phase split; at full width layers and the inspector sit side by side

### Requirement: Mod refresh
The mod SHALL refresh with at most one fetch in flight, drop stale answers and write nothing.

#### Scenario: changes and the fallback timer
- WHEN the selected plan's ledger or run directory changes (the watcher marks the view dirty and a 2-second flush turns any number of changes into one fetch) or the 30-second fallback timer fires
- THEN the mod fetches with a fixed argv (no shell, the plan path one argument) in the space folder, at most one fetch in flight, drops any answer older than the latest request, and runs only `workbench plans`, `workbench plan` and `workbench totals`

### Requirement: Mod tests run under node
The repository suite SHALL run the mod's pure modules under `node --test` and fail when a scenario's node tests did not run.

#### Scenario: node tests from pytest
- WHEN the suite runs
- THEN each scenario's test requires that its node tests ran and none failed, and a missing `node` fails the test naming node instead of skipping

### Requirement: Live terminal join
The mod SHALL join an attempt or agent to Herdr's live terminals by its exact address only.

#### Scenario: live, not live and unknown
- WHEN the inspector or a ticket row shows an attempt or agent
- THEN an address that is a live terminal shows `live` with Herdr's status and an Open that runs `agent.focus` for that terminal; a local address that is no longer live shows `not live`; a null address, no `terminals()` to ask, or a space on another machine shows `unknown`; the last two and `not live` offer no Open, and a terminal with the same name elsewhere is never used

### Requirement: Panel button
The workbench layout's tab bar SHALL have a Workbench button that opens and closes the side panel on the workbench.

#### Scenario: the button
- WHEN the workbench layout is shown
- THEN its tab bar has a Workbench button beside Herdr's Files and Changes (drawn by the mod, props `{ toggle: true }`): a click opens the panel on the workbench or closes it when it shows the workbench, with `layout.select`, `layout.show` and `layout.hide`; a layout whose side panel holds no workbench gets no button, and the button adds `workbench` once to `workspace.panels`, after the person's own choices

### Requirement: Panel expand
The workbench header SHALL expand the panel over the terminals and collapse it back.

#### Scenario: expand, collapse and close
- WHEN the workbench panel is showing and the person presses expand, then collapse, or closes the panel while expanded
- THEN expand hides `main` and resizes the panel to weight 1; collapse shows `main` and restores the width it had (520 when unknown); closing while expanded shows `main` and restores the width before hiding the panel; no expand is offered when the panel does not show the workbench or has no terminals beside it

### Requirement: Panel size
The shipped workbench layout SHALL let a weight-1 panel fill the work area.

#### Scenario: the layout
- WHEN the shipped workbench layout is installed
- THEN its panel keeps `size.px` 520 and `min` 280 and has no `max`

### Requirement: No Runtime section
The mod SHALL NOT list terminals that no attempt names.

#### Scenario: any screen
- WHEN any screen of the mod is drawn
- THEN there is no Runtime section

### Requirement: Workbench home
The mod SHALL open on a home of weekly cards, Needs me, the active plans and the older plans.

#### Scenario: the home screen
- WHEN the mod opens on a repository with plans and a totals answer
- THEN home draws the header with Expand and refresh, the five cards (merged, first_pass, escalations, tokens, ticket_time; an unmeasured value reads unknown, tokens notes its coverage, no totals answer means no cards), Needs me as one row per plan holding its items (each with a glyph and an action for its kind: lead_pending Answer, needs_you Answer, fix_plan Fix plan, ready_to_drive Drive, any other Open), the active plans in the command's order (each with how recently it moved, its live agents, the count of the others and its PRs to merge as `#n` links), and one collapsed "N older" line that expands to the older plans

#### Scenario: navigation
- WHEN the person opens the mod, a plan, a card, and goes back
- THEN the mod opens on home, a plan or a card opens over it, back from either returns home, and there is no plan picker

### Requirement: Plan header
An open plan's header SHALL be a way back, the plan's name and its whole goal.

#### Scenario: the header
- WHEN a plan is open
- THEN its header is a way back with the plan's name as the title and its whole goal (wrapping fully when wide and in at most three lines when narrow), and the repository's name is not shown

### Requirement: Plan cards
An open plan SHALL show five cards and a card per ticket drawn from its own metrics.

#### Scenario: cards and ticket cards
- WHEN a plan is open with metrics
- THEN five cards show progress (done of total), first pass, wall time, escalations and tokens with coverage, and a value its metrics do not carry reads unknown; each ticket is a card of its measures (rounds or first pass, findings, time, tokens, model), phase split, dependencies, live agents with the count of the others and its PR, a ticket with no metrics shows none, and the timeline lanes run on the plan's clock with each merge marked

### Requirement: Plan measures
The mod SHALL show a plan's measures in its header, inspector and timeline, and never read an unmeasured value as 0.

#### Scenario: a plan with metrics
- WHEN a plan with metrics is selected
- THEN the header shows wall time, first-pass rate and driver errors, the inspector shows the ticket's phase times, rounds, findings, CI failures, escalations and model, unmeasured values read unknown, and when the mod is at least the wide-layout width a timeline shows one lane per ticket with a segment per phase, in ticket order, scaled to the run's span

### Requirement: Card dashboard
A card's dashboard SHALL draw that card's detail from the totals answer.

#### Scenario: opening a card
- WHEN the person opens a card's dashboard
- THEN it opens on the card's value, its change against last week (`▲ n% vs last week`, `▼`, or `first week measured` when the change is null or absent) and the week's sparkline, in two columns when wide and one when narrow, and every scaled chart carries its axis with ticks 0, half and a round top (1, 2 or 5 times a power of ten) that covers its data

#### Scenario: the charts
- WHEN each card's dashboard is drawn
- THEN merged draws day columns split by plan; first pass a ring, the fix-round histogram and the findings by reason in a fixed order; escalations a plan by cause heatmap when wide and bars by cause when narrow; tokens each plan's measured and unmeasured tickets; ticket time a swimlane per slow ticket (an unmeasured phase is no segment) and each step's median and p90; an answer without a section draws no chart for it, never a zero one

### Requirement: Dashboard filters
A dashboard's lists SHALL filter by a clicked day, reason or heatmap cell.

#### Scenario: a click
- WHEN the person clicks a day, a reason or a heatmap cell
- THEN the list keeps only that day's, reason's or plan and cause's rows and says so when none is left, what the filter leaves out is dimmed, the same click clears it, and going back home clears it

### Requirement: Dashboard rows
A dashboard's rows SHALL name their unit, read times relative to the window and open only GitHub pull requests.

#### Scenario: escalation and merged rows
- WHEN a dashboard lists escalations and merged tickets
- THEN a plan-level row names its unit (`alpha archive`), never `null`; times read relative to the window's end (`5m ago`, `3h ago`, then the day); a PR reads `#n` and opens only when it is an https GitHub pull request URL

### Requirement: Dashboard colours
The mod's data colours SHALL come from one fixed palette.

#### Scenario: coloured charts
- WHEN any chart is coloured
- THEN its data colours are the mod's data palette (aqua, violet, magenta, green, in that order), each reason and step keeps the same colour whatever order it arrives in, and the state colours (working, done, blocked) colour states only

### Requirement: Home card pictures
Each home card SHALL carry a picture of its dashboard.

#### Scenario: with and without data
- WHEN home is drawn with a totals answer
- THEN merged shows the week's columns, first pass its ring, escalations its causes (the causes beyond the palette folded into one neutral part), tokens its measured and unmeasured tickets and ticket time its median steps; a card with no data has no picture
