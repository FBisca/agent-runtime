# Brief: Working screening CLI

Slug: `00-agent-review-screening-cli`. Contract: `plan.yaml`. Lane: **Full** (multiple tickets / risk-tagged work).

## What changes
- **`packages/agent-review/`, `pyproject.toml`, `uv.lock`, the affected tier's source roots** (T1, S1): nothing → a
  workspace member with an `agent-review` console script that reviews two Git revisions, a screening port that calls
  a provider, a fixture provider behind the same port, and a baseline YAML question bank; the member joins the
  workspace members, `testpaths`, `impacted_map` and `ROOTS` as `agent-eval` does.
- **`packages/agent-review/`** (T2, S2, S3, S4): a raw provider answer → `report.json` carrying the execution's
  status apart from the review's verdict, native judgments attributed to their question, priority, scope and
  revision, and a terminal summary a person reads.

## Decisions
- The source of truth for the agreed behavior is `docs/specs/agent-review.md`, whose decision register records D01–D57
  (D58 open). Do not re-grill approved decisions.
- **P0-D1** The CLI is `agent-review code --base <rev> --head <rev> --output <dir> [--config <path>]
  [--screening-fixture <path>]`, over `.agent-review.yaml` discovery and over inferring revisions from the working
  tree: discovery and repository additions are P1's additive configuration (D30, D05), and a check that freezes
  questions must own its own bank (D26, D51) (source: this plan's review, 2026-11-14).
- **P0-D2** The screening provider is a port the CLI selects: the real provider API by default, and
  `--screening-fixture <json>` a canned-response provider that replaces it — one injection point, over a model plugin
  registry or an environment toggle: D37 wants replaceable models without a framework, D40's failure path is only
  testable when the failure can be injected, and P4's frozen replay needs the same seam (D37, D40, D55) (source: this
  plan's review, 2026-11-14).
- **P0-D3** `report.json` carries `status` (`completed` | `incomplete`), `verdict` (`screened` | null), `input`
  (`base`, `head`), `model`, `usage` (`requests`, `input_tokens`, `output_tokens`), `questions` (`id`, `priority`,
  `type`), `judgments` (`question`, `priority`, `scope`, `revision`, `value`) and `failure` (`reason` | null), over a
  verdict-only output: D34 requires the execution's health and the review's verdict to stay separately legible,
  `usage.requests` is the observable that shows a retry or a model fallback, and D44 requires each judgment to name
  its provenance (D34, D35, D39, D40, D44, D53) (source: this plan's review, 2026-11-14).
- **P0-D4** The question configuration is `model: <string>` plus `questions: [{id, priority, type, ask}]`, validated
  only for what this phase uses, over the full declarative surface: applicability, sensitive paths and reviewer
  instructions are P1's and P2's (D07, D29, D50) (source: this plan's review, 2026-11-14).
- **P0-D5** Acceptance for S2, S3 and S4 is a contract-first test at that CLI seam
  (`acceptance/test_screening_cli.py`, baseline `fail`), over the human-only evidence this plan first carried: the
  decisions fix the seam, and the safeguards the failures exist for are the ones a person cannot re-run
  deterministically — no retry, no fallback, never a clean verdict for an incomplete run (source: this plan's review,
  2026-11-14). S1 stays human: it needs a real credential and a real charge.
- This phase archives by **promote** into `docs/specs/agent-review.md`, the spec the register at the top of it
  belongs to: the archive appends `## Requirements: 00-agent-review-screening-cli`, so each scenario becomes a
  requirement, and the acceptance test moves to `packages/agent-review/acceptance/screening/` instead of being
  deleted with the folder. The register's TODO goes once every D<n> is a requirement there.

## Out of scope
P0 specifically excludes repository additions, applicability, chunking, deep review, JSON-RPC, and the historical benchmark. Use a few curated baseline questions and one provider adapter; do not build a model plugin framework.

## Risks
- `external` (T1): verified by S1's human run against a real provider and by S3's fixture-backed checks; no check
  reaches a real provider, so credentials and pricing stay a person's step.
- `state` (T2): the report is a durable artifact written either way; verified by the S2, S3 and S4 checks at T2's
  gate, which read the report on both the completed and the incomplete path.
- Cost and quality assertions must use actual metrics, not assumptions. No model or harness behavior is presumed.

## Hand-off
1. Inspect the current agent-runtime checkout and run `plan check docs/plans/2026-11-14-00-agent-review-screening-cli/plan.yaml`.
2. Build T1, then T2. The runner places the acceptance test at `packages/agent-review/acceptance/`; a ticket never
   writes it.
3. Seek plan approval before invoking `/orchestrate`; do not silently build the next phase.
