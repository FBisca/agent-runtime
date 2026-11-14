# Brief: Smart configurable screening

Slug: `01-agent-review-smart-screening`. Contract: `plan.yaml`. Lane: **Full** (multiple tickets / risk-tagged work).
Archive: **promote** into `docs/specs/agent-review.md`, as P0 did, so each scenario becomes a requirement there.

## What changes
- **T1** (S1, S2, S3): `packages/agent-review/` — additive repository configuration (questions and sensitive paths extend
  the mandatory baseline and never disable or override it), whole-diff applicability screening with the D10 fallback,
  and sensitive-path routing to full deep review, renames and deletes included.
- **T2** (S4, S5, S6): `packages/agent-review/` — deterministic file/hunk chunking with provider-aware batching and
  bounded concurrency, cross-file screening with an explicit coverage gap, and priority-and-confidence escalation
  signals with full provenance.
- **T3** (S7, S8): `packages/agent-review/` — what a screening cost: the provider's resolved model beside the configured
  alias, a `usage.requests` that counts the calls actually made, and a `usage.cost_usd` computed from a declared price
  table, unknown when the model is unlisted.
- **T4** (S9, S10, S11, S12, S13): `packages/agent-review/` — the refusals and the failure report P0 left rough: an
  unworkable `--output` is a refusal rather than a traceback, a refusal names the report it left standing, an
  incomplete report keeps the usage and the judgments it already learned, a usage count is a whole number of tokens
  or is refused, and a `choice` judgment must be one of the question's declared options.

## Decisions

The source of truth for agreed behaviour is `docs/specs/agent-review.md` (D01–D57, D58 open). Do not re-grill approved
decisions. The decisions below are this plan's own, taken because the register settles *what* must happen and not the
seam that proves it; they are additive to P0's contract and each names the register entry it answers.

- **P1-D1** The configuration is additive and its unknown fields stay refused. `model` and `questions` keep P0's shape;
  a configured question now **extends** the packaged 21-question baseline (D05) and a question whose id repeats a
  baseline id is refused by name rather than replacing it — the alternative, silent replacement, is what D05 forbids.
  New top-level keys, all optional, each traceable to a decision: `sensitive_paths` (D13), `thresholds` (D48),
  `chunk.max_diff_chars` (D41), `concurrency` (D46), `cross_file.max_files` (D42, D43). Because `questions` now means
  *additions*, an empty list is legal and asks the baseline bank alone — P0 refused an empty list when an empty list
  meant an empty screening.
- **P1-D2** The port is called once for whole-diff applicability, then once per chunk in chunk order, and the fixture
  provider answers in that order (D09, D45). `usage.requests` is therefore `1 + len(chunks)` on the completed path: the
  one number a caller reads to see the batching that happened.
- **P1-D3** Applicability asks one question per group, the group being the first dotted segment of a question id
  (`contract`, `engineering`), so grouping is deterministic and needs no configuration (D09, D42). A group is
  `relevant`, `irrelevant` or `uncertain`; an `uncertain` group is retained and its questions are asked (D10). When
  the applicability call fails the report says `status: "fallback"`, every group is asked, and the execution is still
  `completed` — D10's fallback is not D40's screening failure.
- **P1-D4** Sensitive paths are matched against the changed paths *and* both sides of a rename, so a file renamed
  *into* a protected path still routes to full deep review (D13). The mandatory global baseline is
  `.github/workflows/**`, `server/**`, `hooks/**`, `pyproject.toml` and `uv.lock`; `sensitive_paths` adds repository
  patterns and never removes a global one (D05, D13). A match sets `escalation.recommended: true` with a
  `sensitive-path` reason naming the pattern and whether it came from `global` or `repository`.
- **P1-D5** Chunking is deterministic and total: chunks are built from the diff's hunks in path order, each chunk packs
  hunks until `chunk.max_diff_chars` would be exceeded, and `coverage.changed_regions == coverage.covered` must hold on
  the completed path — a hunk that no chunk carries is a defect, not a gap (D41). `chunks[].changed_regions` is the
  hunks the chunk carries, and each judgment's `scope` is the path it was made on (D44).
- **P1-D6** When a change touches more files than `cross_file.max_files`, cross-file screening cannot receive the whole
  change: the report records `coverage.gap` with the reason and adds a `coverage-gap` escalation reason, so the caller
  forces full deep review instead of reading a clean screen as coverage (D42, D43).
- **P1-D7** Escalation is decided by the tool from declared priorities and confidences, never by the model (D07):
  `flagged` when a judgment's value fails a critical/high question, `uncertain` when its confidence is below its
  priority's `thresholds` band, `critical` for a critical question whatever its value, `sensitive-path` and
  `coverage-gap` from P1-D4 and P1-D6. Advisory uncertainty is recorded on the judgment and never escalates on its own
  (D08); a high-confidence advisory flag does (D27). P1 emits signals only: it never spawns a reviewer (P2 consumes
  them) and never treats a raw probability as a calibrated confidence (D06, D48). A question answered in several chunks
  keeps one judgment per scope, and it raises a `flagged` or `uncertain` signal if **any** scope raises it: a defect
  found in one hunk is not averaged away by the hunks that passed.
- **P1-D8** Cost is measured from a declared table, never a constant in the code: `--pricing <path>` reads
  `{version, models: {<model id>: {input_per_million, output_per_million}}}`, prices against the model **the provider
  resolved** (not the configured alias), and writes `usage.cost_usd` rounded to six decimals with
  `usage.cost_basis` naming the table and version. An unlisted model writes `cost_usd: null` and
  `usage.cost_unknown` naming the model: unknown, never zero (D24, D26, D58 stays open — this is per-request only).
- **P1-D9** A refusal costs nothing and leaves nothing behind, and a failure keeps what it paid for (D35, D47, D54). An
  `--output` that cannot be created or written is exit 2 naming the path and the way out, never a traceback: the same
  class of defect as a malformed configuration. A refusal writes no report, and because the directory may still hold the
  report of an earlier invocation it names that report's `base..head` on stderr, so a reader cannot mistake it for this
  one. An incomplete execution keeps the usage and the judgments it already learned instead of discarding them: the
  report is the only record of a screening that was charged for. A usage count is a whole number of tokens: a
  boolean or a fraction is refused naming the field rather than reported as a token count, because the counters are
  what a frozen report is measured with (P1-D8).
- **P1-D10** A `choice` question declares its bounded options (`options: [<value>, ...]`) and a provider answer outside
  them is refused naming the question and the options (D44, D50). Without a declared option list a `choice` question is
  refused at configuration time rather than accepting any string: an unbounded choice is a boolean with extra steps.
- Acceptance for S1–S8 is a contract-first test at the CLI seam, `acceptance/test_smart_screening_cli.py`, baseline
  `fail`, promoting with the plan to `packages/agent-review/acceptance/screening/`. It drives the console script as a
  subprocess over a real repository, its own configuration, a canned provider and a price table, and imports nothing
  from the package, so it collects before the phase exists (P0-D5's seam, now established).

## Out of scope
One P0 finding is deliberately **not** in this plan: the repository's own wheel gates
(`tests/test_workspace_isolation.py::build_wheels` and `.github/workflows/ci.yml`) still build only the runtime and
orchestrator members, so nothing watches the `agent-review` wheel. It cannot be a red-first scenario here — the file
already passes its own tests, so a selection under it exits 5 (no tests ran) instead of failing on behaviour — and it
is a repository-gate change in files this phase does not own. It wants its own chore ticket. One nit is out too: the
git calls carry no timeout and hold out the global and system config but not the checkout's own `.git/config`, so a
hanging git still hangs the CLI — proving it deterministically needs a fake `git` on `PATH` and a wait the seam does
not provide, so it is recorded rather than pinned here.

No deep-review agent is spawned in P1: the phase emits escalation signals for P2 to consume. No JSON-RPC, no benchmark,
no plan review. Applicability never calls the provider more than once, and a screening is still made without retry, model
fallback or a clean verdict for an incomplete run (D40).

## Risks
- `external` (T2, T3): chunk budgets, thresholds and prices are data, so a wrong number must show as a reason in the
  report rather than as a silent default. No check reaches a real provider.
- `concurrency` (T2): bounded parallel batches must aggregate in configuration order; the acceptance test pins the order
  rather than the timing.
- `state` (T1, T3): the report is a durable frozen artifact and every change here is additive, so a report written by
  P0 keeps its meaning (D26). Cost and quality assertions use actual measurements, never assumptions.

## Hand-off
1. Inspect the current agent-runtime checkout and run
   `plan check docs/plans/2026-11-14-01-agent-review-smart-screening/plan.yaml`.
2. Build T1, then T2, then T3, then T4 (they share `packages/agent-review/`; the order is what keeps them from
   colliding). The runner places `acceptance/test_smart_screening_cli.py` at
   `packages/agent-review/acceptance/screening/`; a ticket never writes it, and it fails red until the phase's
   behaviour exists.
3. Seek plan approval before invoking `/orchestrate`; do not silently build the next phase.
