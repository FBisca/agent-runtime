# Brief: Parked drive release

Slug: `parked-drive-release`. Contract: `plan.yaml`. Lane: Full — three tickets, a `state` risk on each, and it amends two promises the living spec already makes (R126 "a drain has no timeout" and R131 "a run whose only remaining units are parked ends").

## What changes

- `packages/agent-orchestrator/src/agent_orchestrator/driver.py`: a park records the approval the run holds → the park names the approval its request was raised under, so an amendment adopted after the request releases the unit (T1, S1)
- `packages/agent-orchestrator/src/agent_orchestrator/driver.py`: a drain is a wake in the terminal → a parked drive is a state the ledger records, `attention.md` shows, and the Lead is woken for again and again (T2, S2, S3)
- `packages/agent-orchestrator/src/agent_orchestrator/driver.py`: a run that ends with parked units exits silently → the exit names the park, its cause and the way out (T2, S6)
- `packages/agent-orchestrator/src/agent_orchestrator/ledger.py`: the ledger holds the plan's state and its receipts → it also holds the drive's approval and the park (T2, T3)
- `packages/agent-orchestrator/src/agent_orchestrator/plan.py`: a plan without an `approval` span is unapproved → the approval is recovered from the snapshot the drive holds, and `plan amend` accepts such a plan (T3, S4, S5)
- `packages/agent-orchestrator/acceptance/park/test_parked_release.py`: the plan's acceptance oracle → the three outcomes, each with a control that holds today

## Decisions

- D1 The release key of a park is the approval its request was raised under, recorded on the request, over the approval the run holds when the park happens: a park that captures an already-adopted amendment can never be released, which stranded a unit in a live drive (the request; driver.py:4926, 1386-1388)
- D2 The task keeps its `amend_from` field and its name, over a new field: four live ledgers already carry it, and the fix changes what it holds, not where it lives (the request's repair; the gatherer's count of four ledgers)
- D3 The park is recorded in the ledger under a top-level `park` key, over a new plan state: the plan-state machine is fixed by plan-contract.md R17, and a drain is not a state the plan reaches (the gatherer's open question, settled here)
- D4 A parked drive asks the Lead after 15 minutes and every 30 minutes after that, over the drain's timeout-free wait: R126 promises the drain never gives up, and it keeps that promise — the deadline is about asking, not about giving up (the owner, 2026-11-15; R126)
- D5 The way out the drive names is `plan approve --base` at the approval's own base, plus the snapshot to restore from, over `plan amend`: `plan amend` refuses a plan whose `approval` span is lost, and `plan approve` at `origin/main` is refused as vacuous once the merged work is in the tree — the base branch is the one that works (the gatherer's finding; plan-contract.md R14, R121)
- D6 The `approval` span is recovered from the snapshot the drive holds, over moving the span out of the plan file: the contract does not change, the plan/1 file keeps its shape, and the recovery repairs the exact instance that stranded a live drive (the owner, 2026-11-15)
- D7 The ledger records the approval the drive holds (its base and its digest), over reading it from the plan file alone: the file is what loses the span, and the ledger is what survives a restart (the gatherer's finding; D6)
- D8 The drain's cause stays the string it is today (`edited`, `unloadable`), over a structured kind: the classification by error prefix mislabels a lint refusal, but no scenario in this plan depends on it, and the span loss stops being a drain at all (D6)

## Out of scope

- `plan approve --base` becoming a requirement in `docs/specs/plan-contract.md`: this plan's spec is the engine's, and the driver's promise to name the command is what the Lead needs
- The drain's cause becoming a structured kind, and the lint refusal mislabelled `unloadable` (D8)
- The repacket's empty "What changed:" list (the plan-amend run's open T5 C4)
- Committing a plan folder when it is approved, so the archive pull request deletes it and a copy survives: a repository convention, not a change in the engine

## Risks

- `state`: the park's release key, the ledger's park record and the drive's held approval decide what a drive does next. S1, S2 and S4 carry the evidence on the integrated tree (the plan's acceptance oracle), and the engine's own tests cover the edges of each park and each drain
