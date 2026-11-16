# Brief: Risk-adaptive deep review

Slug: `02-agent-review-deep-review`. Contract: `plan.yaml`. Lane: **Full** (multiple tickets / risk-tagged work).

## What changes
- `packages/agent-review/cli.py`: `agent-review code` gains `--deep-review <harness>` and `--deep-review-fixture <dir>`;
  an escalated run spawns one reviewer, records what it asked for and writes a `deep_review` section → an escalated
  screen stops being the last word (T1).
- `packages/agent-review/` configuration: `.agent-review.yaml` gains `deep_review: {timeout_seconds, instructions[]}`,
  so a repository says what a deep review is told and which instruction sets join it (T1).
- `packages/agent-runtime/`: the harness-family knowledge answers whether read/search-only can be enforced and with
  which arguments, and the spawn path asks the same function → a read-only spawn can be requested and be told no (T1).
- `packages/agent-review/report.py`: the reviewer's findings and dispositions become a validated `deep_review` section,
  with `unresolved` naming every signal nobody answered → a reviewer's answer becomes an auditable result (T2).
- `packages/agent-review/`: a timeout, an invalid answer or a failure leaves `status: incomplete` with the evidence it
  had and the verdicts gain `passed` and `blocked` → a review that stopped is never read as a clean one (T2).

## Decisions
- The source of truth for the agreed behavior is `docs/specs/agent-review.md`, whose decision register records D01–D57.
  Do not re-grill approved decisions.
- This phase keeps the *discard* archive until it is reviewed; when it is approved it promotes into
  `docs/specs/agent-review.md` as P0 does, so its scenarios become requirements there.
- The signal this phase consumes is `(value, confidence, threshold)` from `01-agent-review-smart-screening` (P1-D5); a
  raw probability is never a calibrated confidence, and the deterministic safeguards (`2026-11-15-00c-agent-review-safeguards`)
  run before it, so a safeguard hit escalates without a screening request.
- **P2-D1** The deep review is asked for with `--deep-review <harness>` and answered from a fixture with
  `--deep-review-fixture <dir>` (`answers.json` in, `spawns.json` out), over a real model call in every check: a check
  that needs a model and a credential is unrunnable wherever the suite runs, and a security refusal no check can reach
  is a refusal nobody has tested (`docs/specs/agent-review.md` D37, D38, D20; the screening's `--screening-fixture` is
  the same seam).
- **P2-D2** Whether a harness can enforce read/search-only is answered by one function in the runtime that the spawn
  path calls too, over a flag the caller declares or a field the fixture sets: a family that accepts a control and
  silently drops it is the defect D38 exists to prevent, so the answer must be single and queryable (D38; the
  `launch_options_problem`/`claude_launch_args` pair in `packages/agent-runtime/src/agent_runtime/profiles.py` is the
  shape it extends, and the answer lives in its own module beside that file so the worker spawn path can start asking
  it without either side moving. The families are enumerated once: `read_only.py` iterates `profiles.BUILTINS`, and a
  test iterates that same tuple, so every family either answers with its read-only arguments or with a refusal naming
  what it cannot enforce — never silence, and a family added tomorrow cannot walk past the module).
- **P2-D3** The review's scope is focused when every reason that fired names a judged question, and full when one is
  about the change itself (a sensitive path, a safeguard, a coverage gap), over narrowing `recommends_full_review`:
  `escalation.recommended` is the field P1's report and tests pin, and focused-versus-full is a property of the review,
  so the deep review states its own scope and the escalation keeps its meaning (D12, D43).
- **P2-D4** The review's result is a `deep_review` section of the one report, and the verdict gains `passed` (a deep
  review completed with no blocking finding) and `blocked` (one confirmed) beside `screened` and `escalated`, over a
  second report file: one report per invocation is the frozen contract a consumer reads (`report.json`, D39, D53).
- **P2-D5** Specialized instruction sets are composed from configuration by the signals that fired: a set that declares
  no `applies` joins every review, a set matching a fired rule or a matched changed path joins it too, and the report
  names the composed ids in declaration order, over letting the reviewer pick its own instructions: priority and routing
  are configuration and never the model's choice (D07, D14).
- **P2-D6** A dismissed or downgraded signal must cite evidence, a confirmed finding on a critical-priority question is
  blocking whatever the reviewer's own flag said, and a signal nobody disposed of stays `unresolved` and makes the run
  incomplete, over trusting the reviewer's `blocking` flag: a confirmed mandatory-contract violation cannot be
  downgraded (D16, D17).
- **P2-D7** The run that spawns the reviewer carries the answer into the report as answered, and the rules that judge it
  (dispositions, blocking, verdict) land in the ticket after, over validating inside the spawn: spawning and judging are
  two behaviours with two evidences, and a reviewer's malformed answer is S5's incomplete, not S1's spawn failure (D15,
  D20).

## Out of scope
One deep-review agent per escalation. The agent decides when to stop within a global timeout. No fix loop, no live
orchestrator integration, no code modifications by the reviewer, no test execution.

## Risks
- `security` (T1): the refusal path is S3's check — a harness with no enforceable read/search-only control is refused
  before anything runs, writes no report and spawns nothing; the capability table and each family's arguments are
  unit-tested in the ticket.
- `external` (T1): no check and no acceptance run reaches a model or a provider; the reviewer is canned and the
  screening is canned, so the evidence is offline and repeatable.
- `state` (T2): S5's checks prove an incomplete review keeps the screening's judgments and the findings it had, and that
  it is never retried or fallen back from (`spawns == 1`).

## Hand-off
1. `plan check docs/plans/2026-11-14-02-agent-review-deep-review/plan.yaml` exits 0; the acceptance file is written and
   its controls pass at base while each scenario fails on an assertion (calibrated red).
2. Seek plan approval before invoking `/orchestrate`; do not silently build the next phase.
