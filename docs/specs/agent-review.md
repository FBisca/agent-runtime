# agent-review

A standalone `agent-review` workspace member that screens a code change with an inexpensive, bounded, provider-agnostic
semantic review, and escalates to a read-only deep review only where a safeguard or a screening signal says it must.
It is a pilot: not integrated into the live orchestrator, and its evidence is frozen historical cases rather than live
shadow reviews.

<!--
TODO(agent-review): delete "## Decision register" (and the engineering principles section, once they are questions)
when every D<n> below is expressed as a requirement in this file. Until then the register is the record of what was
agreed, and a requirement that contradicts it is a defect.
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

## Requirements

### Requirement: Screened verdict and report
`agent-review code` SHALL screen two Git revisions against the curated question bank through a direct screening API, emit the verdict `screened` when nothing escalates, and write `report.json`.

#### Scenario: a completed screening
- WHEN a caller invokes `agent-review code` on two Git revisions and the screening completes without requiring deep review
- THEN the tool evaluates the question bank over the frozen diff between the revisions, emits a `screened` verdict, preserves each native judgment with deterministic question and revision provenance, and writes `report.json`; the terminal summary reads the judgments without the report

### Requirement: Incomplete on screening failure
The execution SHALL be incomplete, never passed, when the provider fails or answers with an invalid judgment.

#### Scenario: a provider failure or an invalid judgment
- WHEN the provider fails, returns an invalid judgment, or judges a question the run did not ask
- THEN the execution is incomplete with an observable failure reason naming the question where there is one

### Requirement: Report provenance
A completed report SHALL identify the exact input, configuration, provider and usage so the same frozen input and configuration reproduce the same report.

#### Scenario: what the report names
- WHEN a screening completes
- THEN the report names the revisions, the question configuration, the model the provider served, the provider endpoint, the input limit in force, whether long context was requested, a digest of the bank screened (which follows the bank, not the run) and the usage measurements, and the same frozen input and configuration produce the same report

### Requirement: System One transport
The tool SHALL screen through one System One request to the provider it is configured with, authenticated by `TYPESAFE_API_KEY`.

#### Scenario: the typed request
- WHEN a caller screens two revisions without a fixture
- THEN the tool posts the state and the questions as System One's typed request and reads the answers, sending `TYPESAFE_API_KEY` when a fallback credential is also present (the fallback only when it is absent)

#### Scenario: a missing credential
- WHEN no credential is configured
- THEN the run stops incomplete naming `TYPESAFE_API_KEY`

### Requirement: Question configuration validation
The tool SHALL refuse a question configuration the provider contract cannot carry before any request is made.

#### Scenario: a malformed question
- WHEN a score question has no ladder of levels (a mapping, an empty list, one level or a blank level), a noul question carries a level list, or a choice question lacks its options
- THEN the configuration is refused before any request is made, naming the question and the field by path

### Requirement: Score ladder bounds
A score question's ladder SHALL bound the provider's answer without snapping it to a rung.

#### Scenario: an answer on the ladder
- WHEN a score question declares a ladder of levels and the provider answers a number between its first and last rung
- THEN the judgment carries that number as the provider answered it, with its confidence, and the screening completes

#### Scenario: an answer outside the ladder
- WHEN the provider answers a score below the first rung or above the last, a non-finite or non-numeric score, or a confidence outside zero and one
- THEN the judgment is refused naming the question and the range the ladder allows, and the execution is incomplete rather than screened

### Requirement: Provider input limit
The tool SHALL never truncate a change to fit the provider's input limit.

#### Scenario: a request over the limit
- WHEN the state and its questions exceed the limit in force
- THEN the execution is incomplete with the provider's own reason (`max_tokens_exceeded`), and the same change completes when long context is requested

### Requirement: Refusal and incomplete report
A refused execution SHALL write no report, and an incomplete one SHALL keep what the provider already answered.

#### Scenario: a refusal
- WHEN an execution is refused, such as an output that is not a directory, one that denies writing, or a report that cannot be written
- THEN no report is written and the refusal names any report already standing in the output directory, before the provider is asked where it can be

#### Scenario: an incomplete run
- WHEN an execution stops after the provider answered
- THEN the report keeps the usage and the judgments it already learned

### Requirement: Offline canned provider
Every offline check SHALL be carried by the canned provider, with no credential and no network.

#### Scenario: a fixture run
- WHEN a screening runs against the canned provider with no credential and every socket refused
- THEN the run completes and writes its report, the provider answers the canned responses in order, and a fixture in another shape or a malformed one is refused naming the file

### Requirement: Computed destructive target safeguard
The tool SHALL escalate a change that adds a destructive call on a computed target without a containment test.

#### Scenario: an unguarded destructive call
- WHEN a change adds a destructive call (a delete or an overwrite) on a target the change computes rather than spells out, and adds no containment test for that target
- THEN the execution completes with the verdict `escalated`, exactly one escalation reason names the rule `target-outside-root` and the changed path, `judgments` is empty, and `usage.requests` is zero because the provider was never asked

#### Scenario: a confined or absent destructive call
- WHEN the change confines the target with a containment test in the same change (directly or through a guard it adds, bound to its parameter by position), or adds no destructive call
- THEN no `target-outside-root` reason appears and the screening proceeds

### Requirement: Protected path safeguard
The tool SHALL escalate a change to a path the baseline or the repository marks sensitive, recommending a full deep review.

#### Scenario: a matched path
- WHEN a changed path matches a mandatory baseline pattern, as an added, modified or deleted file, or on either side of a rename
- THEN the report carries one `protected-path` reason per matched change, naming the changed path, the pattern and `source: baseline`, and recommends full deep review

#### Scenario: a repository extends the baseline
- WHEN a repository ships `.agent-review.yaml` naming its own sensitive paths, discovered without a flag
- THEN the run is `escalated` with `usage.requests: 0`, a change to a matching path carries `source: repository` and a baseline path still carries `source: baseline`: the repository's list extends the baseline instead of replacing it

#### Scenario: no match
- WHEN a change touches only paths that match no pattern, including a near miss under a matching directory
- THEN no `protected-path` reason appears and the report is written with the verdict `screened`

### Requirement: Standard-library-only safeguard
The tool SHALL escalate a change that adds a non-standard-library import to a member the rule set declares standard-library-only.

#### Scenario: an outside import
- WHEN a declared member adds an import outside the standard library
- THEN the report carries one `stdlib-only` reason per outside module naming the member's path and the module, and no request is made

#### Scenario: no outside import
- WHEN a declared member adds a standard-library import, or a member no declaration covers adds a third-party one
- THEN no `stdlib-only` reason appears and the screening proceeds

### Requirement: Safeguard escalation report
A fired safeguard SHALL complete the run as `escalated` and tell a person what to do.

#### Scenario: a safeguard fires
- WHEN a safeguard fires
- THEN `report.json` is written with `status: completed`, `verdict: escalated` and no judgments, the command exits 0, and the terminal summary names the rule, the path it matched and what a person should do about it

### Requirement: Safeguard configuration validation
The tool SHALL refuse a malformed safeguard configuration before any change is read.

#### Scenario: a malformed list
- WHEN a configuration's `sensitive_paths` is not a list of patterns, or `safeguards.stdlib_only` is not a list of paths
- THEN the configuration is refused naming the field by its path, no report is written and the command exits 2

### Requirement: Additive repository configuration
A repository's configuration SHALL extend the mandatory baseline without replacing it, and the screening's numbers SHALL be declared in configuration, not chosen by the model.

#### Scenario: a configured bank
- WHEN a repository provides `.agent-review.yaml` or a caller specifies `--config`
- THEN its questions extend the baseline (a repeated baseline id is refused by name, an empty list asks the baseline alone), a score question carries its levels, and the weights, per-question thresholds, calibration and chunk budget are declared in configuration and carried with the loaded bank, so a recalibrated threshold is a different bank

### Requirement: Deterministic chunking
The tool SHALL split a change larger than one request's token budget into deterministic file and hunk chunks, each screened once with the whole bank.

#### Scenario: an oversized change
- WHEN a change is larger than the budget in force (the declaration or the provider's own limit)
- THEN chunks are built within it, every changed region is carried by exactly one chunk (a hunk larger than the budget whole and said so, a rename without a hunk as a rename), the same frozen change chunks the same way, each chunk is screened once, a missing chunk answer is incomplete, and the report counts the requests actually made

### Requirement: Cross-file coverage gap
The tool SHALL record a coverage gap rather than read a screen as complete when the change is wider than the cross-file limit.

#### Scenario: a change wider than the limit
- WHEN a question needs a file the chunk does not carry and the change is wider than the cross-file limit
- THEN the report records the gap with its reason and recommends full deep review; a change inside the limit records no gap

### Requirement: Calibrated escalation signals
The tool SHALL record an escalation signal when a critical or high judgment falls short of its question's declared calibration.

#### Scenario: a judgment below threshold or confidence
- WHEN a critical or high judgment falls below its question's calibrated threshold (a score escalates only above it), or its confidence falls below the question's declared band
- THEN the tool records a signal with the question, the scope, the value, the confidence and the rule that fired, advisory uncertainty is recorded without escalating, and a raw probability is never read as a calibrated confidence

### Requirement: Screening cost
The report SHALL carry the cost of a screening from a declared price table, and an unpriced model SHALL be unknown, not zero.

#### Scenario: a price table
- WHEN a screening completes and a price table names the model the provider served
- THEN the report carries the cost from that table with its basis; an unlisted model or no table leaves the cost unknown, and a table a rule cannot read is refused by its field path
