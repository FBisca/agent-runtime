# Brief: The review file the gate reads

Slug: `review-payload-path`. Contract: `plan.yaml`. Lane: **Small** (one ticket, no risk tag). Archive: **promote**
into `docs/specs/orchestrator-engine.md`, under the existing `driver` domain.

## Why this plan

A reviewer that writes its review under the wrong name costs the run a whole turn. The reviewer's own directory is
`<run>/<unit>-review/` — named after the agent — and the file the driver reads is `<run>/<unit>/review.json`, one
directory away and one suffix apart. The brief named the path twice, but the reviewer's *assignment* was only "Read
`<run>/<unit>-review/task.md` and follow it", so a reviewer that did not carry the path out of the brief wrote the
file beside itself. The driver then found nothing at its own path, warned the reviewer (R18), and the unit waited
for a second turn to deliver a review that had been delivered all along.

The run of 2026-11-15 lost exactly that turn: the review landed in `t1-review/review.json`, the driver asked for
`t1/review.json`, and the reviewer had to be told to copy its own file.

## What changes

- **T1** (S1, S2): `packages/agent-orchestrator/` — the reviewer's assignment names the file it must write, beside
  the brief it must read; and a review the reviewer wrote under another name inside the run directory is the review
  the gate reads: the file is copied into the driver's own path, the reviewer is not warned, and the unit advances.

## Decisions

- **RP-D1** The assignment names the path; it does not restate the brief. The reviewer is told "Read
  `<brief>` and follow it. Write the review to `<path>` (JSON, contract 2)." — the path is the one thing the reviewer
  cannot infer, and the brief still holds the contract. Rejected: writing the whole contract twice, and a separate
  message with only the path (the reviewer reads one message).
- **RP-D2** The driver reads the file the reviewer *named*, and only inside the run directory. The brief already
  orders a report of `<n> blocking - <the review file you wrote>`; the driver reads that name. Rejected: searching the
  run directory for a file that looks like a review (the driver would guess), and reading a name outside the run
  directory (the driver reads its own state and nothing else).
- **RP-D3** An announced file is copied into the driver's own path, not read from where it lies. The open set, the
  deferred findings and a re-review's delta all name `<run>/<unit>/review.json`; one file per round keeps them
  consistent, and the copy is atomic, so a reader never sees half a review. Rejected: reading the announced file in
  place (the round's file would be somewhere else, and the next round's name would collide with it).
- **RP-D4** The driver's own path still wins. A review at `<run>/<unit>/review.json` is read whether or not the
  reviewer named it, and a named file that breaks the contract is refused exactly as the driver's own path would be.
  The change opens no hole: R18 still warns the reviewer twice, and then the Lead is asked.
- **RP-D5** The worker side is untouched. The worker's assignment is the task file itself, whose first lines already
  name the report it must write, and the driver's own report path is the newest `report*.json` in the worker's
  directory. The same trap needs the same two changes there only if the worker's assignment ever stops naming it.
