# Plan: agent-eval — capture a session by name, on any harness

Slug: `agent-eval-named-capture`. Living spec: `docs/specs/agent-eval.md`.
Status: AWAITING APPROVAL.

Contract:
[`2026-11-11-agent-eval-named-capture.plan.yaml`](2026-11-11-agent-eval-named-capture.plan.yaml)
(scenarios, tickets, evidence). Oracle:
[`2026-11-11-agent-eval-named-capture.oracle/`](2026-11-11-agent-eval-named-capture.oracle/).
This file is the narrative and is not covered by the approval digest.

## Why now

The 2026-11-10 eval batch drew ten sessions and captured six. The other
four were Pi sessions, and `agent-eval capture --session` could not name
them at all: the ref is resolved by `claude.resolve`, which searches
`<cdir>/projects` alone, so a Pi id fails with "not found under
…/.claude/projects". The CLI says so itself — `--session  a Claude session
id, or a unique prefix`.

Beside it, a second gap: the chain branch writes the entry without `cwd`
and without `assignment_id`, while the worker branch sets both. So a
session captured by name loses the only link between a worker and its
orchestrator's record of it, and it loses it exactly when the naming
becomes common.

This is a missing capability, not a violated requirement: R218 promises a
Pi session's id and cwd, but its only entry path is the run. The fix has a
shape the code already supports — `pi.scan` and `codex.scan` return the
same `Found` the worker branch consumes — so the work is in `plan`, not in
a new reader.

## Sources

| Source | What it settles |
| --- | --- |
| `docs/plans/2026-11-11-agent-eval-pi-capture.handoff.md` | The two gaps, the measured shapes, the do-nots, the four Pi sessions of the batch |
| `docs/specs/agent-eval.md` (R207–R227) | The ledger: R210 (root and slug), R214 (duplicate refusal), R216 (worker, assignment id), R217 (Codex rollouts), R218 (Pi id and cwd), R225 (help and spec together) |
| Fresh-context gather, 2026-11-11 | The sources table, the nine open decisions, the code sites, the local counts |
| Fresh-context oracle review, 2026-11-11 | The assertion behind each scenario, the counterexample for the outcome, and the eleven promised behaviours no assertion checked |
| The local data | 130 Pi sessions, 654 Codex sessions, 1058 Claude ids; no full-id collision across harnesses; 8-character prefixes collide inside Codex and Pi (197 of 242 November Codex ids); 29 Pi sessions carry the marker in their first user message; the two dispatched Pi sessions of the batch carry none |
| Grill, 2026-11-11 | The decisions marked "grill" below |

## Decisions

| # | Choice | Rejected | Why | Source |
| --- | --- | --- | --- | --- |
| D1 | One new requirement, R228, carries the cross-harness `--session` resolution. R216 and R218 stay as they are | Extending R207 and R218 in place | The ledger numbers contracts; a new capability gets a new number. R218 already holds on any road, so its Coverage row gains a test instead of its text | grill Q1 |
| D2 | A ref matching ids in more than one harness is refused (exit 2), naming each candidate and its harness. No harness has precedence | Claude, then Codex, then Pi | A refusal names the reason and the way out. Precedence would silently capture the wrong session, and the spec already refuses an id found under two harnesses | grill Q2; `orchestrate.workers` |
| D3 | The prefix rule is the same for every harness: any unique prefix, refused when ambiguous | Full ids only for non-Claude; a minimum length | One rule, and the refusal names the candidates. Codex and Pi ids are UUIDv7, so short prefixes collide often — that is what the refusal is for | grill Q3; the local data |
| D4 | The first `--session`'s group names the root and the default case: a Claude chain gives its oldest session, a Codex or Pi session gives itself; a later `--session` has role `session` | The oldest session overall wins | R210 generalised in one line, and it is what the handoff proposes | grill Q4; R210 |
| D5 | Every captured session records the `cwd` and the `assignment_id` of its own first prompt, whatever its role and whatever road it came in by; only the first prompt is searched | Workers only; searching every user message | One rule, derived from the session itself. The marker is injected in the first prompt by construction; the two dispatched Pi sessions of the batch carry none at all, so their id stays null | grill Q5; R216 |
| D6 | The naming wins over the run: a named session that is also a worker joins once, keeping the role the naming gave it, and `--exclude` names it like any other named session | The run wins | R216's own rule: a worker the chain already holds stays what the chain made it | grill Q6 |
| D7 | A case captured by name alone holds its transcript, its cwd and its assignment id, and nothing else — no patch, no task, no report. A run named beside it is copied as it is today. The help text and the spec say so | A narrow selector that brings a worker's own task and report; leaving it unsaid | The selector was measured during the batch and rejected for size (5.3 MB, 101 `task.md`, an ambiguous subject). `tests: null` is already the honest verdict on transcript-only evidence | grill Q7; handoff §4 |
| D8 | The plan covers the two gaps. The batch's ten judgements stay outside it; the four captures are the plan's human-checked outcome | Judging the ten in this plan | The judgements are model calls and a sitting; they belong to the batch | grill Q8 |
| D9 | The new tests are `packages/agent-eval/tests/test_capture_named.py`, integration-marked like their two siblings (the plan's frozen suite is D14's `test_capture_named_outcome.py`) | `test_capture_pi.py` (it covers Codex too); unit-marked, as the handoff says | The handoff's "in-process and unit-marked" is contradicted by the code: the capture tests drive the CLI through `subprocess.run`, which AGENTS.md calls an integration test | grill (folded); the gatherer |
| D10 | The requirement numbers continue from R227: R228 (naming a session of any harness) and R229 (the manifest's own fields) | One requirement for both | Two behaviours, two contracts, two Coverage rows | the ledger's numbering; grill Q1 |
| D11 | The cwd and the first prompt are read from the session's own file. Only the id lookup needs a directory scan, and only for Codex and Pi | A global scan of every harness session | The work stays proportional to the capture. A Claude id is in the file name; a Codex or Pi id is in the record | the gatherer |
| D12 | One code ticket, not two: the two gaps are one change in `capture.plan`, and the spec's case-layout prose covers both fields at once | A ticket for the resolution and another for the manifest fields | The prose sentence the two gaps share cannot be split between two PRs without one of them contradicting the spec. The handoff says the same: fix the second gap as part of the same change | handoff §3 |
| D13 | The refusal has three words: "not found" with the three roots, "ambiguous" with every candidate and its harness, "two harnesses" with the two places a session was found | One message for every refusal | A refusal names the reason and the way out (AGENTS.md), and the last two are the two shapes the code already refuses (R214, `orchestrate.workers`) | the oracle review |
| D14 | Two paths, one per role: the ticket's own tests are `packages/agent-eval/tests/test_capture_named.py`, and the plan's frozen oracle is `test_capture_named_outcome.py`, placed by the driver at `packages/agent-eval/tests/` and promoted at archive | One file for both | The driver reserves an oracle's place: a ticket may never touch it (integrity), while the covers gate wants nodes that exist in the ticket's checkout. A file cannot be both, so the ticket's regression tests and the plan's frozen suite live apart, as in the sibling plans | the engine's integrity and covers gates; the first run, 2026-11-11 |

## Scenarios

The WHEN/THEN text lives in the contract. In summary:

- **S1:** a named Codex or Pi session joins the case with its harness, its
  cwd, its transcript and its rollouts, and no subagent entry.
- **S2:** a ref that matches nothing, several sessions in one harness, or
  the same session under two harnesses, is refused with exit 2 and nothing
  written.
- **S3:** the first `--session`'s session is the root and names the case,
  whatever the other sessions' age; a later one is role `session`; a named
  session that is also a run worker joins once; `--exclude` leaves a named
  session out and lists it.
- **S4:** every captured session records its own `cwd` and `assignment_id`,
  whichever road it came in by, and null when its first prompt carries no
  marker.
- **S5 (outcome):** the four Pi sessions of the batch are captured by name.

## The oracle review

A fresh-context reviewer read the contract, the oracle and the code, and
named eleven promised behaviours that no assertion checked. The oracle now
covers all of them: the subagent-free entry and the empty run record of a
named case, the bytes of a later Codex rollout, the pairing of a candidate
with its harness, the harness name in a one-harness ambiguity, the null
`assignment_id` and the injected-context trap, the chain road, the first
`--session` against the oldest session (the second fixture is now the
older one), the exclude of a named session, the prefix rule on Codex, and
the `capture --help` text. The two controls were strengthened too: the run
control now asserts that the run's files reached the case.

The review also left two questions open, both closed in the contract: D7
now says a run named beside a session is copied as it is today, and the
outcome's human check asks for the role and the whole transcript, not only
the harness and the cwd.

## Out of scope

- Judging the ten cases of the 2026-11-10 batch, and the second round of
  the six labels already stored. A label is written once (R219), so a
  comparison across harnesses means new cases, never a rewrite.
- A selector that brings a worker's own `task.md` and report into a case
  (D7). It is a separate feature, and the size argument is measured.
- Making the Pi worker prompt carry the assignment marker. The runtime's Pi
  dispatch is not this repository's, and the marker is absent from the two
  dispatched Pi sessions of the batch.
- The 30-day retention warning stays Claude-only: only Claude prunes its
  session files.
- The `usage.py` binder and the worker-to-session link it leaves empty.

## Risks

- **Security:** the capture now reads the Pi and Codex session directories
  and copies their transcripts into the case store. Covered: the same
  store rules as today (0700/0600, outside every git working tree,
  R215), and the manifest records only the session's own path. The
  symlink rule holds: a link is never followed.
- **Performance:** resolving a Codex or Pi id reads that harness's session
  files (130 and 654 locally: one record each for the id, the head for
  the first prompt). A Claude id is read from the file name, so the
  common path costs nothing new. The capture is manual.
- **Migration:** the manifest's `cwd` and `assignment_id` stop being
  null-unless-worker. `schema: case/1` is unchanged and no stored case is
  rewritten — cases are immutable — but the spec's case-layout prose
  changes with the requirement, and a case captured before this and one
  captured after it differ in those two fields.
- **Rollback:** T1 is one revertable PR. Reverting it restores today's
  capture: Claude ids only, and `cwd` and `assignment_id` null unless the
  session joined as a worker. The four captured cases stay: they are
  evidence.

## Seams

- The `agent-eval` CLI, driven as a subprocess from
  `packages/agent-eval/tests/`: every root overridable
  (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `PI_CODING_AGENT_DIR`,
  `AGENT_EVALS_HOME`) and pointed at the fixtures in
  `tests/harness_fixture.py` (`pi_session`, `codex_rollout`,
  `claude_session`, `orchestrate_run`) and `tests/claude_fixture.py`.
- The manifest (`schema: case/1`): `sessions[].harness`, `role`, `parent`,
  `cwd`, `assignment_id`, and `case` for the slug.
- The CLI help and the spec's case layout, held together by R225.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T1 | `--session` resolves across Claude, Codex and Pi; a named Codex or Pi session joins with its harness, cwd and rollouts; the refusals, the root and role rules, every session's own cwd and assignment id, R228, R229, the Coverage rows, the spec's prose and the help text | S1, S2, S3, S4 | none | capture.py, sessions.py, cli.py, tests/test_capture_named.py, spec | regular | simple | agent | TO-DO |
| T2 | The four Pi sessions of the batch are captured by name and checked against S5 | S5 | T1 | spec | | | Felippe | TO-DO |
