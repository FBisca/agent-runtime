# agent-review

A standalone `agent-review` workspace member that screens a code change with an inexpensive, bounded, provider-agnostic
semantic review, and escalates to a read-only deep review only where a safeguard or a screening signal says it must.
It is a pilot: not integrated into the live orchestrator, and its evidence is frozen historical cases rather than live
shadow reviews.

<!--
TODO(agent-review): delete "## Decision register" (and the engineering principles section, once they are questions)
when every D<n> below is expressed as a requirement in this file. Each phase's plan appends its own
`## Requirements: <slug>` and `## Coverage: <slug>` section at archive; when the last decision is covered by a
requirement, remove the register and this note. Until then the register is the record of what was agreed, and a
requirement that contradicts it is a defect.
-->

## Decision register

> Temporary. Approved in conversation on 2026-11-14 (D01–D57); D58 stays open. It records what was agreed before any
> requirement existed. See the TODO above for when it goes.

### Goal and boundaries

Prove an inexpensive, provider-agnostic **semantic screening → optional deep review** mechanism in a standalone
`agent-review` package in the `agent-runtime` uv workspace. Protect critical behavior through deterministic
safeguards, preserve auditability, and benchmark verified historical code changes. Do not integrate into the live
orchestrator in V1.

| ID | Decision | Agreed rule |
|---|---|---|
| D01 | Jev-first concept with safeguards | Use inexpensive bounded semantic screening; escalate flagged/uncertain or critical-safeguard changes to a deep reviewer. Models are replaceable. |
| D02 | Independent pilot | Evaluate outside live agent-orchestrator; no integration until evidence supports it. |
| D03 | Dual screening perspectives | Evaluate contract compliance and broader engineering risks independently. |
| D04 | Configured questions, not generated | V1 questions derive from a global baseline plus repository configuration; no per-task question-generation agent. This supersedes an earlier dynamic-question interpretation. |
| D05 | Additive repository questions | Repository-specific questions extend the global bank; no override or disabling of global questions. |
| D06 | Priority-and-confidence escalation | Deep-review escalation uses question priority and judgment uncertainty/confidence, plus independent deterministic safeguards. |
| D07 | Static question priority | Priority is declared in configuration, not selected or downgraded by the screening model. |
| D08 | Selective uncertainty escalation | Uncertain critical/high judgments escalate; advisory uncertainty is recorded without automatically escalating. |
| D09 | Two-level review unit | Run whole-diff applicability screening, then file/hunk-specific review. Critical questions are always included. |
| D10 | Conservative applicability fallback | Include uncertain question groups; if applicability evaluation fails, evaluate the full bank. |
| D11 | Focused screening context | Supply diff, relevant requirements and existing evidence; avoid building new retrieval machinery. Record missing context. |
| D12 | Risk-adaptive deep review | Screening signals receive focused investigation; critical safeguards and sensitive paths trigger full deep review. |
| D13 | Global and repository sensitive paths | Keep a small mandatory global path baseline, augmented by repository-specific file/folder/glob patterns; handle renames and deletes. |
| D14 | Composed specialized deep-review instructions | Combine common and all applicable specialized instructions into one deep-review invocation. No specialist swarm. |
| D15 | Structured evidence-backed findings | Deep reviewer reports classification, location, violated requirement/principle, evidence and smallest meaningful correction, reusing current contracts. |
| D16 | Justified dismissal | Deep reviewer can dismiss or downgrade screening signals only with evidence-backed justification; confirmed mandatory-contract violations remain blocking. |
| D17 | Selective concern reconciliation | Give every critical/high signal an individual disposition; advisory concerns may receive grouped disposition. Missing dispositions remain unresolved. |
| D18 | Read-only investigation | Deep reviewer may inspect source, references and tests; cannot modify files, execute commands or run tests. |
| D19 | Reviewer-controlled depth | Deep reviewer chooses investigation depth subject to a global timeout, not fixed token or read budgets. |
| D20 | No deep-review retries | Timeout/invalid result/failure => incomplete; preserve valid evidence, do not retry or switch models. |
| D21 | Historical evaluation only | Use frozen historical cases in V1; no live shadow reviews. |
| D22 | Independently verified ground truth | Combine existing benchmark labels with independently verified historical examples; reviewer narrative alone is not truth. |
| D23 | Separate routing and end-to-end accuracy | Measure screening escalation recall separately from deep-review detection/classification and whole-pipeline success. |
| D24 | No always-deep-review comparator | Compare against verified historical ground-truth labels only; record costs without claiming measured savings over always-deep-review. |
| D25 | Safety-first pass criterion | Zero missed known critical/high defects end-to-end on frozen cases; record cost, latency, false positives, escalation. |
| D26 | Freeze before evaluation | Freeze questions, model, thresholds and rules before benchmark. Each change is a new version/run; do not tune to a particular test. |
| D27 | High-confidence advisory escalation | High-confidence advisory signals may trigger focused deep review; weak advisory signals are only recorded. |
| D28 | Graceful missing requirements | Continue engineering checks; available contracts only. Missing context is unknown, not passed, and material critical/high uncertainty escalates. |
| D29 | Declarative YAML | Configure questions, applicability, priorities, sensitive paths, reviewer instructions in YAML with minimal schema validation. |
| D30 | Config discovery with override | Discover root `.agent-review.yaml`; allow explicit `--config` path; absent config uses the mandatory global baseline. |
| D31 | CLI-first execution | Agents and benchmark scripts call a noninteractive `agent-review` CLI; this did not settle input adapters (see D33). |
| D32 | Optional JSON-RPC stdio | Offer ordinary CLI execution plus optional JSON-RPC 2.0 over stdio for progress/final results, using one engine. |
| D33 | Git and artifact inputs | Accept Git revisions and caller-supplied frozen diff/requirements/evidence through the same review engine. |
| D34 | Execution status separate from verdict | A completed invocation may return a blocked or unresolved review verdict. Do not conflate tool failure with detected defect. |
| D35 | Exit codes reflect execution health | CLI exit 0 means execution completed; review verdict lives in structured output. Nonzero means invocation/processing failure. |
| D36 | Milestone progress events | Stream meaningful screening, applicability, escalation, deep review, finding and completion notifications rather than all individual answers. |
| D37 | API screen, runtime spawn deep | Use direct provider API for screening; spawn the coding harness through existing agent-runtime facilities for deep review. |
| D38 | Enforced read-only harness permissions | Limit to read/search capabilities with existing harness controls; fail when restrictions cannot be enforced. Prompts are insufficient. |
| D39 | `screened` != `passed` | No escalation yields screened; clean completed deep review yields passed; downstream consumer decides sufficiency. |
| D40 | Screening failure incomplete | Provider error, timeout, invalid response => incomplete, without automatic retry, model fallback or deep escalation. Applicability-only failure differs (D10). |
| D41 | Deterministic file/hunk chunking | Split oversized diffs without model summaries; preserve every changed region and line/revision provenance. |
| D42 | Cross-file interaction screening | Add whole-diff questions on interactions alongside per-file questions; no dependency-graph engine. |
| D43 | Cross-file coverage failure escalates | When cross-file interactions cannot be assessed due to context limits, force full deep review and disclose coverage gap. |
| D44 | Native bounded judgments and provenance | Use native Boolean/choice/score values; code attaches question ID, priority, scope and revision. Screening does not invent reasons/citations. |
| D45 | Provider-aware question packing | Batch questions within provider limits to share context; split as needed. |
| D46 | Bounded concurrency | Evaluate independent screening batches concurrently with configurable limit and stable deterministic aggregation. |
| D47 | Partial batch failure policy | Stop scheduling on first failure; let in-flight batches finish, preserve results, mark overall execution incomplete. |
| D48 | Model-specific thresholds | Configurable thresholds and uncertainty bands per screening model; freeze them ahead of evaluation; never weaken safeguards. |
| D49 | Curated global question bank | Start with the 16 engineering principles, existing rubrics and concrete failure modes. Expand only after evidence of gaps. |
| D50 | Boolean / choice / score | Support native bounded judgment types, with Boolean as a sensible default. |
| D51 | No runtime semantic dedup | Require unique stable question IDs; curate meaningful overlap at authoring time, not via model calls during review. |
| D52 | Per-question offline evaluation | Record evaluated/flagged/escalated/confirmed associations and analyze them offline; no automatic question optimization. |
| D53 | Per-review output directory | Write `report.json` and optional `events.jsonl` under caller-selected directory; no database or repository clutter. |
| D54 | Minimal interrupted-run persistence | Persist startup lifecycle and completed screening data, atomically write final report, no resume framework. |
| D55 | Code review first | P0–P4 implement code review only; do not implement plan review or speculative shared abstractions yet. |
| D56 | 20–30 frozen cases | Start with 20–30 independently verified historical cases; do not fabricate labels to hit a target. |
| D57 | Stratified diagnostic sample | Cover severities, failure modes, and clean changes; keep ground truth withheld from review inputs. Do not extrapolate prevalence. |

### Engineering principles supplied by the owner

These principles inform the curated global question bank; they are not a license to fabricate blocking findings.
Blocking conclusions still require evidence.

1. Production is survival. Uptime is the override condition.
2. Every behavior must be traceable to cause.
3. The why outlives the what.
4. Observability is not logging; internal state must be externally legible.
5. Types are contracts, not annotations.
6. Name the implicit.
7. Do not compress before understanding.
8. Hard decisions still need to be made.
9. Elegance is not a luxury.
10. New concepts must earn their keep.
11. Hidden states come from unwatched software; revisit logic with three or more booleans.
12. Name meaning, not mechanism.
13. Abstraction exists for readability; avoid mixing levels.
14. Prefer composition over creation.
15. A one-liner is a consequence, not an achievement.
16. Better is a direction; good is a threshold.

### Delivery stages

| Plan | Outcome | Explicit deferrals |
|---|---|---|
| P0 `00-agent-review-screening-cli` | Testable basic CLI and report using direct screening API | Deep review, repository additions, RPC, historical benchmark |
| P1 `01-agent-review-smart-screening` | Applicability, additive configuration, safeguards, per-file and cross-file screening | Agent spawning |
| P2 `02-agent-review-deep-review` | Read-only risk-adaptive deep review with structured evidence | Live orchestrator integration |
| P3 `03-agent-review-protocol-reliability` | JSON-RPC, progress milestones, and durable results | Evaluation, live driver integration |
| P4 `04-agent-review-evaluation` | Frozen, stratified historical evaluation and safety-first result | Plan review, production rollout |

Evaluation is **P4**, separate from P3 protocol/reliability. Implement and evaluate each stage before authorizing the
next.

### Open questions

- **D58** — Stage-level versus full performance profiling (total, per-stage, or per-request). Proposed default:
  per-stage metrics, but no approval was given.
- Exact provider, credentials, pricing, model names, timeout seconds, concurrency cap, confidence thresholds, complete
  YAML schema, and JSON-RPC method names are implementation details to validate and/or approve before freezing a
  benchmark.
- Plan review support, calibration, automatic policy tuning, always-deep-review comparator, and live orchestrator
  changes are deferred.

### Independent pilot evidence boundaries

- Prior pilot: native $2.105112, original driver $2.953651, revised driver $3.731352, on one task each. The revised
  driver spent $2.581820 (69.2%) on dedicated reviewers. These figures motivate the experiment but are not a new
  `agent-review` performance baseline.
- The observed contract escapes included post-execution persistence/save failure handling and a deep JSON `state.load`
  API exception contract. They are useful frozen historical cases; keep their labels out of model inputs.
- Passing 20–30 stratified examples cannot prove no future false negatives or general production reliability.
