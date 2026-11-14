# Brief: The Lead's decisions and the gates around them

Slug: `lead-decision-gates`. Contract: `plan.yaml`. Lane: **Full** (three tickets, risk-tagged work, an evidence
review). Archive: **promote** into `docs/specs/orchestrator-engine.md`, under the existing `driver` domain.

Issue #306 is split: claim 3 (the whole-suite check inherits the agent pane's environment) is already fixed by
`38b5c5e` (#303/#307) and is out of scope. Claims 1, 2 and 4 are this plan.

## What changes

- **T1** (S1, S2, S3): `packages/agent-orchestrator/` — the decision the Lead writes: it now carries the contract
  version of the request it answers (a contract-2 request gets a contract-2 decision, not a contract-3 one a
  contract-2 driver refuses); `decide --json` refuses a decision that names a version the request does not carry,
  naming both; and a driver that meets a decision it cannot read asks the Lead again with the way out (restart the
  driver on the current code) instead of only the raw error.
- **T2** (S4, S5): `packages/agent-orchestrator/` — `plan check` (and `plan approve`, `plan amend`): a scenario
  that carries no evidence and that only person-owned tickets satisfy is refused, naming the scenario, the tickets
  that satisfy it and every way out (`human` evidence, an agent ticket, a node through `complete --cover`); a
  scenario an agent ticket satisfies, and a person-only scenario that carries `human` evidence, are still accepted.
  The reference plan fixture gains the `human` evidence its two person-only scenarios need.
- **T3** (S6, S7): `packages/agent-orchestrator/` — the archive: the Lead's `retry` for the archive unit is refused
  while the plan's state is neither ACCEPTED nor ACCEPTED_WITH_HUMAN, naming the state and the way out, and the
  driver builds no archive and opens no pull request while the plan is rejected — it books a request for the Lead
  (the shape the missing node uses) and waits. T3 branches after T1: both change the Lead's decision path in
  `driver.py`.

## Decisions

- **D1** The decision carries the **request's own** contract version, over the driver naming the restart alone: the
  restart alone leaves the unit stuck until the Lead restarts the driver, which is the bug (#306 claim 1; the
  driver writes contract-2 requests today for every `intent` request, so the skew is reachable on current code).
- **D2** A driver that cannot read a decision **also** names the restart as the way out, over silence: a refusal
  names the reason and the way out (`AGENTS.md`); the refusal loop — refuse, ask again, refuse — is what the unit
  felt.
- **D3** `decide --json`'s `contract` field is optional and **must equal the request's version**, over accepting and
  ignoring it (or dropping the field): nothing the Lead wrote is silently contradicted, and both forms write the
  same thing (R130).
- **D4** The lint refuses a scenario that carries **no evidence** and whose **only** satisfying tickets are
  person-owned, over every scenario without evidence and over every scenario a person ticket satisfies: the first
  would refuse the repository's own reference plan (its agent scenarios carry their node in the ticket report's
  `covers`, not in the plan), the second would flag scenarios an agent ticket proves (#306 claim 2).
- **D5** The refusal names **all three** ways out — attach `human` evidence, let an agent ticket satisfy the
  scenario, or record a node through the person ticket's `complete --cover` — over `human` evidence alone: the
  archive's own way out (a node) is one of them, and the acceptance checks each of the three.
- **D6** A person-only scenario that already carries `human` evidence is **not** flagged, over flagged: the archive
  never asks it for a node (`archive_missing` skips human evidence), so it is not the gap #306 found.
- **D7** The archive's `retry` is refused in **both** the CLI and the driver, over either alone: the CLI tells the
  Lead at the moment of the decision, and the driver is the authority, because the plan's state can change between
  the decision and the driver's tick — which is exactly how #303 was opened (#306 claim 4). The driver's refusal
  books a request for the Lead (the shape the missing node uses) and waits: the run ends when the Lead answers,
  never with an archive built over a rejected plan.
- **D8** One plan with three tickets, promoting into `docs/specs/orchestrator-engine.md` under the existing
  `driver` domain, over three plans and over `docs/specs/plan-contract.md`: the three claims share the package, the
  spec and the reviewer, and R114 — the lint requirement T2 narrows — lives in `orchestrator-engine.md`.

## Out of scope

- Claim 3 of #306 (the whole-suite check inherits the agent pane's environment): fixed by `38b5c5e`.
- A scenario that no ticket satisfies: the lint leaves it alone; only the person-only case is this plan's.
- The archive's `skip`: it releases the archive to the Lead, builds nothing, and stays the escape hatch.
- The archive's **merge**: a person merging the archive pull request still archives the plan. This plan is about the
  archive being *opened* over a plan its acceptance rejects (the goal's words), and the review left the merge gate
  open: say the word and it becomes a fourth scenario.

## Risks

- `state` (T1): the decision's contract version is persisted state a driver of another version reads — verified by
  S1, S2 and S3's acceptance evidence.
- `state`, `retry-idempotency` (T3): the plan's state gates the archive, and the archive's retry is what T3
  changes — verified by S6 and S7's acceptance evidence.
