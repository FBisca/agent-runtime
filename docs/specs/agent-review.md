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

## Requirements: 00-agent-review-screening-cli

- **R1** WHEN a caller invokes `agent-review code` on two Git revisions, THEN the tool evaluates a curated global engineering and contract question bank through a direct screening API (from 00-agent-review-screening-cli/S1)
- **R2** WHEN the screening completes without requiring deep review, THEN the tool emits a `screened` verdict, preserves native judgments with deterministic question/revision provenance, and writes `report.json` (from 00-agent-review-screening-cli/S2)
- **R3** WHEN the provider fails or returns an invalid judgment, THEN the execution is incomplete rather than passed, with an observable failure reason (from 00-agent-review-screening-cli/S3)
- **R4** WHEN the same frozen input and model configuration are evaluated, THEN the report identifies the exact revision, question configuration, model and usage measurements (from 00-agent-review-screening-cli/S4)

## Coverage: 00-agent-review-screening-cli

| Requirement | Scenario | Test nodes |
|---|---|---|
| R1 | S1 | human: Run a Git revision review using a configured real screening API; confirm the expected flags and reproducible `report.json`. Then deliberately break credentials and confirm an incomplete execution, not a clean verdict. |
| R2 | S2 | `packages/agent-review/tests/test_screening_report.py::test_S2_a_completed_screening_is_screened_with_native_attributed_judgments`<br>`packages/agent-review/tests/test_screening_report.py::test_S2_the_terminal_summary_reads_the_judgments_without_the_report`<br>`packages/agent-review/acceptance/screening/test_screening_cli.py` |
| R3 | S3 | `packages/agent-review/tests/test_screening_report.py::test_S3_a_provider_error_is_incomplete_with_an_observable_reason`<br>`packages/agent-review/tests/test_screening_report.py::test_S3_an_invalid_judgment_is_incomplete_naming_the_question`<br>`packages/agent-review/tests/test_screening_report.py::test_S3_a_judgment_on_a_question_the_run_did_not_ask_is_incomplete`<br>`packages/agent-review/acceptance/screening/test_screening_cli.py` |
| R4 | S4 | `packages/agent-review/tests/test_screening_report.py::test_S4_the_report_names_revisions_questions_model_and_usage`<br>`packages/agent-review/tests/test_screening_report.py::test_S4_the_same_frozen_input_and_configuration_produce_the_same_report`<br>`packages/agent-review/acceptance/screening/test_screening_cli.py` |

## Requirements: 00b-agent-review-provider-contract

- **R5** WHEN a caller screens two revisions without a fixture, THEN the tool posts one System One request to the provider it is configured with, authenticated by TYPESAFE_API_KEY - which is the credential it sends when a fallback is also present - carrying the state and the questions as System One's typed request, and a missing credential stops the run incomplete naming TYPESAFE_API_KEY (from 00b-agent-review-provider-contract/S1)
- **R6** WHEN a configured score question has no levels, or a noul question carries a level list, THEN the configuration is refused before any request is made, naming the question and the field by path (from 00b-agent-review-provider-contract/S2)
- **R7** WHEN the provider answers a score below its ladder's first rung or above its last, or a confidence outside zero and one, THEN the judgment is refused naming the question and the range the ladder allows, and the execution is incomplete rather than screened (from 00b-agent-review-provider-contract/S3)
- **R8** WHEN a screening completes, THEN the report names the provider endpoint, the model the provider served, the input limit in force, whether long context was requested, and a digest of the bank it screened (from 00b-agent-review-provider-contract/S4)
- **R9** WHEN the state and its questions exceed the limit in force, THEN the execution is incomplete with the provider's own reason (`max_tokens_exceeded`), the change is never truncated to fit, and the same change completes when long context is requested (from 00b-agent-review-provider-contract/S5)
- **R10** WHEN an execution is refused, or stops after the provider answered, THEN a refusal writes no report and names any report already standing in the output directory, while an incomplete run keeps the usage and the judgments it already learned (from 00b-agent-review-provider-contract/S6)
- **R11** WHEN a screening runs against the canned provider with no credential and every socket refused, THEN the run completes and writes its report, so every offline check is carried by the fixture and no check needs the network (from 00b-agent-review-provider-contract/S7)

## Coverage: 00b-agent-review-provider-contract

| Requirement | Scenario | Test nodes |
|---|---|---|
| R5 | S1 | `packages/agent-review/acceptance/screening/test_provider_contract.py::TransportTests::test_S1_the_tool_posts_the_typed_request_to_systemone`<br>`packages/agent-review/acceptance/screening/test_provider_contract.py::TransportTests::test_S1_a_missing_credential_is_incomplete_naming_the_variable`<br>`packages/agent-review/tests/test_screening_transport.py::test_S1_the_transport_posts_the_typed_request_and_reads_the_answers`<br>`packages/agent-review/tests/test_screening_transport.py::test_S1_the_typesafe_credential_is_sent_when_a_fallback_is_also_present`<br>`packages/agent-review/tests/test_screening_transport.py::test_S1_the_fallback_credential_is_sent_only_when_typesafe_is_absent`<br>`packages/agent-review/tests/test_screening_transport.py::test_S1_a_missing_credential_names_typesafe_api_key`<br>`packages/agent-review/acceptance/screening/test_provider_contract.py` |
| R6 | S2 | `packages/agent-review/tests/test_question_bank.py::test_S2_a_score_question_without_levels_is_refused_by_path`<br>`packages/agent-review/tests/test_question_bank.py::test_S2_a_noul_that_carries_a_level_list_is_refused_by_path`<br>`packages/agent-review/tests/test_question_bank.py::test_S2_a_score_criteria_that_is_not_a_ladder_of_levels_is_refused_by_path[mapping]`<br>`packages/agent-review/tests/test_question_bank.py::test_S2_a_score_criteria_that_is_not_a_ladder_of_levels_is_refused_by_path[empty]`<br>`packages/agent-review/tests/test_question_bank.py::test_S2_a_score_criteria_that_is_not_a_ladder_of_levels_is_refused_by_path[one-level]`<br>`packages/agent-review/tests/test_question_bank.py::test_S2_a_score_criteria_that_is_not_a_ladder_of_levels_is_refused_by_path[blank-level]`<br>`packages/agent-review/tests/test_question_bank.py::test_S2_a_noul_may_declare_criteria_as_a_mapping`<br>`packages/agent-review/tests/test_question_bank.py::test_S2_a_choice_question_carries_its_options_as_criteria`<br>`packages/agent-review/tests/test_question_bank.py::test_S2_a_choice_without_its_options_is_refused_by_path[missing]`<br>`packages/agent-review/tests/test_question_bank.py::test_S2_a_choice_without_its_options_is_refused_by_path[level-list]`<br>`packages/agent-review/tests/test_question_bank.py::test_S2_a_choice_without_its_options_is_refused_by_path[one-option]`<br>`packages/agent-review/tests/test_question_bank.py::test_S2_a_refused_bank_never_reaches_a_provider`<br>`packages/agent-review/acceptance/screening/test_provider_contract.py` |
| R7 | S3 | `packages/agent-review/tests/test_screening_judgments.py::test_S3_a_score_judgment_keeps_its_confidence_beside_the_value`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S3_a_confidence_outside_zero_and_one_is_refused_naming_the_question[above-one]`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S3_a_confidence_outside_zero_and_one_is_refused_naming_the_question[below-zero]`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S3_a_confidence_outside_zero_and_one_is_refused_naming_the_question[text]`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S3_a_confidence_outside_zero_and_one_is_refused_naming_the_question[boolean]`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S3_a_score_with_no_confidence_is_refused_naming_the_question`<br>`packages/agent-review/tests/test_screening_report.py::test_S3_a_score_outside_its_declared_levels_is_incomplete_naming_the_question`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S1_a_score_between_two_rungs_is_the_providers_own_number`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S1_a_score_outside_its_ladder_is_refused_naming_the_question_and_the_range[past-the-last]`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S1_a_score_outside_its_ladder_is_refused_naming_the_question_and_the_range[far-below-the-first]`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S1_a_score_outside_its_ladder_is_refused_naming_the_question_and_the_range[integer-too-large-for-a-float]`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S1_a_score_outside_its_ladder_is_refused_naming_the_question_and_the_range[infinite]`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S1_a_score_outside_its_ladder_is_refused_naming_the_question_and_the_range[not-a-number]`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S1_a_score_outside_its_ladder_is_refused_naming_the_question_and_the_range[text]`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S1_a_score_outside_its_ladder_is_refused_naming_the_question_and_the_range[boolean]`<br>`packages/agent-review/acceptance/screening/test_provider_contract.py` |
| R8 | S4 | `packages/agent-review/tests/test_screening_report.py::test_S4_the_report_names_the_provider_served_model_limits_and_bank_digest`<br>`packages/agent-review/tests/test_screening_report.py::test_S4_the_bank_digest_follows_the_bank_and_not_the_run`<br>`packages/agent-review/tests/test_screening_transport.py::test_S4_the_reply_carries_the_model_the_provider_served`<br>`packages/agent-review/tests/test_screening_transport.py::test_S4_the_transport_names_the_endpoint_it_posts_to`<br>`packages/agent-review/tests/test_screening_transport.py::test_S4_the_canned_provider_names_the_fixture_it_answers_from`<br>`packages/agent-review/acceptance/screening/test_provider_contract.py` |
| R9 | S5 | `packages/agent-review/acceptance/screening/test_provider_contract.py::BudgetTests::test_S5_an_oversized_request_is_incomplete_with_the_provider_reason`<br>`packages/agent-review/acceptance/screening/test_provider_contract.py::BudgetTests::test_S5_long_context_completes_and_sends_the_header`<br>`packages/agent-review/tests/test_screening_transport.py::test_S5_a_long_context_request_carries_the_danger_zone_header`<br>`packages/agent-review/tests/test_screening_transport.py::test_S5_the_default_request_carries_no_long_context_header`<br>`packages/agent-review/tests/test_screening_transport.py::test_S5_an_oversized_request_keeps_the_provider_reason`<br>`packages/agent-review/acceptance/screening/test_provider_contract.py` |
| R10 | S6 | `packages/agent-review/tests/test_screening_report.py::test_S6_an_incomplete_run_keeps_the_usage_and_the_judgments_it_learned`<br>`packages/agent-review/tests/test_screening_report.py::test_S6_a_refusal_names_the_report_it_leaves_standing`<br>`packages/agent-review/tests/test_screening_report.py::test_S6_an_output_that_is_not_a_directory_is_a_refusal_naming_the_path`<br>`packages/agent-review/tests/test_screening_report.py::test_S6_an_existing_output_that_denies_writing_is_refused_before_the_provider_is_asked`<br>`packages/agent-review/tests/test_screening_report.py::test_S6_a_report_that_cannot_be_written_is_a_refusal_that_keeps_what_was_screened`<br>`packages/agent-review/acceptance/screening/test_provider_contract.py` |
| R11 | S7 | `packages/agent-review/acceptance/screening/test_provider_contract.py::OfflineTests::test_S7_a_screening_through_the_canned_provider_opens_no_socket`<br>`packages/agent-review/tests/test_screening_transport.py::test_S7_the_canned_provider_answers_the_canned_responses_in_order`<br>`packages/agent-review/tests/test_screening_transport.py::test_S7_a_fixture_answer_in_another_shape_is_refused`<br>`packages/agent-review/tests/test_screening_transport.py::test_S7_a_malformed_fixture_is_refused_naming_the_file`<br>`packages/agent-review/acceptance/screening/test_provider_contract.py` |

## Requirements: 01-score-levels-are-bands

- **R12** WHEN a score question declares a ladder of levels and the provider answers a number between its first and last rung, THEN the judgment carries that number as the provider answered it and the screening completes, while an answer below the first rung or above the last is refused naming the question and the range the ladder allows, so the ladder bounds the answer without snapping it to a rung (from 01-score-levels-are-bands/S1)

## Coverage: 01-score-levels-are-bands

| Requirement | Scenario | Test nodes |
|---|---|---|
| R12 | S1 | `packages/agent-review/tests/test_screening_judgments.py::test_S1_a_score_between_two_rungs_is_the_providers_own_number`<br>`packages/agent-review/tests/test_screening_judgments.py::test_S1_a_score_outside_its_ladder_is_refused_naming_the_question_and_the_range`<br>`packages/agent-review/tests/test_screening_report.py::test_S1_a_score_between_two_rungs_completes_with_the_providers_own_number`<br>`packages/agent-review/acceptance/screening/test_score_ladder.py`<br>`packages/agent-review/acceptance/screening/test_provider_contract.py::NativeAnswerTests::test_S3_a_score_between_the_declared_levels_is_carried_as_answered` |

## Requirements: 00c-agent-review-safeguards

- **R13** WHEN a change adds a destructive call - a delete or an overwrite - on a target the change computes rather than spells out, and adds no containment test for it (the archive driver's unguarded `shutil.rmtree(self.plan_path.parent)`, `429625b`), THEN the execution completes with the verdict `escalated`, exactly one escalation reason names the rule `target-outside-root` and the changed path, `judgments` is empty, and `usage.requests` is zero because the provider was never asked (from 00c-agent-review-safeguards/S1)
- **R14** WHEN a change adds a destructive call on a computed target and confines it with a containment test in the same change, or adds no destructive call at all (the fix `109c98b`), THEN no `target-outside-root` reason appears and the screening proceeds through the canned provider (from 00c-agent-review-safeguards/S2)
- **R15** WHEN a changed path matches a mandatory baseline pattern, as an added, modified or deleted file, or on either side of a rename, THEN the report carries one `protected-path` reason per matched change, naming the changed path, the pattern it matched and `source: baseline` (from 00c-agent-review-safeguards/S3)
- **R16** WHEN a change touches only paths that match no baseline or repository pattern, including a near miss under a matching directory, THEN no `protected-path` reason appears and the report is written with the verdict `screened` (from 00c-agent-review-safeguards/S4)
- **R17** WHEN a repository ships `.agent-review.yaml` naming its own sensitive paths, discovered without a flag, THEN the run is `escalated` with `usage.requests: 0`, a change to a matching path carries `source: repository` while a baseline path still carries `source: baseline`: the repository's list extends the mandatory baseline instead of replacing it (from 00c-agent-review-safeguards/S5)
- **R18** WHEN a member the rule set declares standard-library-only adds an import outside the standard library, THEN the report carries a `stdlib-only` reason naming the member's path and the imported module, and no request is made (from 00c-agent-review-safeguards/S6)
- **R19** WHEN a declared standard-library-only member adds a standard-library import, or a member no declaration covers adds a third-party one, THEN no `stdlib-only` reason appears and the screening proceeds (from 00c-agent-review-safeguards/S7)
- **R20** WHEN a safeguard fires, THEN report.json is written with `status: completed`, `verdict: escalated` and no judgments, the command exits 0, and the terminal summary names the rule, the path it matched and what a person should do about it (from 00c-agent-review-safeguards/S8)
- **R21** WHEN a configuration's `sensitive_paths` is not a list of patterns, or `safeguards.stdlib_only` is not a list of paths, THEN the configuration is refused before any change is read, naming the field by its path, writing no report and exiting 2 (from 00c-agent-review-safeguards/S9)

## Coverage: 00c-agent-review-safeguards

| Requirement | Scenario | Test nodes |
|---|---|---|
| R13 | S1 | `packages/agent-review/tests/test_safeguards.py::test_S1_a_computed_delete_with_no_confinement_escalates_as_target_outside_root`<br>`packages/agent-review/tests/test_safeguards.py::test_S1_the_defect_as_it_shipped_escalates_once_for_its_path`<br>`packages/agent-review/tests/test_safeguards.py::test_S1_the_defect_escalates_once_for_its_path_in_a_change_that_also_has_a_test_helper`<br>`packages/agent-review/tests/test_safeguards_report.py::test_S1_the_report_carries_one_reason_naming_the_rule_the_path_and_where_it_came_from`<br>`packages/agent-review/tests/test_safeguards.py::test_S1_confinement_over_another_relationship_leaves_the_delete_unconfined`<br>`packages/agent-review/tests/test_safeguards.py::test_S1_one_confinement_test_confines_only_the_target_it_names`<br>`packages/agent-review/tests/test_safeguards.py::test_S1_a_guard_confines_only_the_target_it_is_handed`<br>`packages/agent-review/tests/test_safeguards.py::test_S1_a_delete_of_the_root_a_target_is_confined_into_is_not_confined_by_it`<br>`packages/agent-review/tests/test_safeguards.py::test_a_guards_confinement_of_an_attribute_does_not_answer_for_a_callers_local`<br>`packages/agent-review/acceptance/safeguards/test_safeguards_cli.py` |
| R14 | S2 | `packages/agent-review/tests/test_safeguards.py::test_S2_a_computed_delete_confined_in_the_same_change_is_not_escalated`<br>`packages/agent-review/tests/test_safeguards.py::test_S2_a_confinement_factored_into_a_guard_the_change_adds_is_not_escalated`<br>`packages/agent-review/tests/test_safeguards.py::test_S2_a_change_with_no_destructive_call_is_not_escalated`<br>`packages/agent-review/tests/test_safeguards.py::test_S2_a_containment_test_on_the_resolved_target_is_not_escalated`<br>`packages/agent-review/tests/test_safeguards.py::test_S2_a_target_handed_to_a_guard_binds_to_its_parameter_by_position`<br>`packages/agent-review/tests/test_safeguards.py::test_S2_a_local_handed_to_a_guard_binds_to_its_parameter_by_position`<br>`packages/agent-review/tests/test_safeguards.py::test_S2_a_guard_that_confines_with_commonpath_confines_the_target_it_is_handed`<br>`packages/agent-review/tests/test_safeguards.py::test_S2_a_guard_that_tests_an_attribute_confines_that_attribute_in_its_caller`<br>`packages/agent-review/tests/test_safeguards.py::test_a_commonpath_test_in_an_added_function_is_decided_rather_than_raising`<br>`packages/agent-review/tests/test_safeguards_path_join.py::test_S2_a_change_that_joins_paths_is_screened_rather_than_crashing`<br>`packages/agent-review/acceptance/safeguards/test_safeguards_cli.py` |
| R15 | S3 | `packages/agent-review/tests/test_safeguards_protected_path.py::test_S3_a_baseline_path_matched_as_added_modified_deleted_or_renamed_carries_one_reason_per_path`<br>`packages/agent-review/acceptance/safeguards/test_safeguards_cli.py` |
| R16 | S4 | `packages/agent-review/tests/test_safeguards_protected_path.py::test_S4_a_near_miss_under_a_matching_directory_matches_no_pattern`<br>`packages/agent-review/tests/test_safeguards_discovery.py::test_S4_a_change_that_matches_no_pattern_is_screened`<br>`packages/agent-review/acceptance/safeguards/test_safeguards_cli.py` |
| R17 | S5 | `packages/agent-review/tests/test_safeguards_protected_path.py::test_S5_a_repository_pattern_extends_the_baseline_instead_of_replacing_it`<br>`packages/agent-review/tests/test_safeguards_discovery.py::test_S5_a_discovered_configuration_extends_the_baseline_without_a_flag`<br>`packages/agent-review/acceptance/safeguards/test_safeguards_cli.py` |
| R18 | S6 | `packages/agent-review/tests/test_safeguards_stdlib.py::test_S6_a_declared_member_that_imports_outside_the_standard_library_escalates`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S6_the_reason_reads_as_the_report_contract_holds_it_with_the_module_beside_the_rule`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S6_a_from_import_outside_the_standard_library_names_the_module_not_the_name`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S6_the_module_is_the_top_level_name_a_dotted_import_reaches_beside`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S6_one_line_naming_several_modules_names_the_first_outside_the_standard_library`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S6_a_member_declared_as_a_file_covers_that_file_and_no_sibling`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S6_a_declared_directory_covers_what_lies_below_it_and_no_near_name`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S6_a_member_written_without_its_trailing_slash_is_the_same_member`<br>`packages/agent-review/tests/test_safeguards_stdlib_report.py::test_S6_a_declared_member_that_imports_outside_the_standard_library_escalates_without_a_request`<br>`packages/agent-review/tests/test_safeguards_stdlib_report.py::test_S6_the_summary_names_the_member_the_module_and_the_way_out`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S6_every_outside_module_the_member_adds_is_its_own_reason`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S6_one_module_the_member_imports_twice_is_one_reason`<br>`packages/agent-review/tests/test_safeguards_stdlib_report.py::test_S6_each_package_the_member_adds_is_its_own_reason_in_the_report`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S6_a_trailing_comment_on_an_import_line_cannot_hide_the_module`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S6_a_second_statement_on_the_added_line_is_read_as_an_import_too`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S6_an_import_quoted_in_a_docstring_is_not_a_dependency_the_member_adds`<br>`packages/agent-review/tests/test_safeguards_stdlib_report.py::test_S6_a_commented_import_line_escalates_without_a_request`<br>`packages/agent-review/acceptance/safeguards/test_safeguards_cli.py` |
| R19 | S7 | `packages/agent-review/tests/test_safeguards_stdlib.py::test_S7_a_declared_member_that_adds_a_standard_library_import_is_not_escalated`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S7_an_undeclared_member_that_adds_a_third_party_import_is_not_escalated`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S7_a_declaration_that_covers_no_member_escalates_nothing`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S7_a_relative_import_is_the_members_own_module_rather_than_a_dependency`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S7_a_member_that_only_removes_a_third_party_import_is_not_escalated`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S7_an_added_file_in_a_declared_member_is_read_for_its_imports`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S7_a_deleted_file_in_a_declared_member_escalates_nothing`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S7_an_import_inside_a_function_of_a_declared_member_is_still_read`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S7_a_line_that_mentions_an_import_without_being_one_is_not_read`<br>`packages/agent-review/tests/test_safeguards_stdlib_report.py::test_S7_a_declared_member_that_adds_a_standard_library_import_is_screened`<br>`packages/agent-review/tests/test_safeguards_stdlib_report.py::test_S7_an_undeclared_member_that_adds_a_third_party_import_is_screened`<br>`packages/agent-review/tests/test_safeguards_stdlib.py::test_S7_a_rule_set_carries_the_declared_members_and_decides_with_them`<br>`packages/agent-review/acceptance/safeguards/test_safeguards_cli.py` |
| R20 | S8 | `packages/agent-review/tests/test_safeguards_report.py::test_S1_the_report_carries_one_reason_naming_the_rule_the_path_and_where_it_came_from`<br>`packages/agent-review/tests/test_safeguards_report.py::test_the_summary_names_the_rule_the_path_and_the_way_out`<br>`packages/agent-review/tests/test_safeguards_discovery.py::test_S8_a_hit_writes_completed_escalated_and_names_what_a_person_should_do`<br>`packages/agent-review/acceptance/safeguards/test_safeguards_cli.py` |
| R21 | S9 | `packages/agent-review/tests/test_safeguards_protected_path.py::test_S9_sensitive_paths_that_is_not_a_list_is_refused_by_path`<br>`packages/agent-review/tests/test_safeguards_protected_path.py::test_S9_sensitive_paths_that_holds_something_else_than_patterns_is_refused_by_path`<br>`packages/agent-review/tests/test_safeguards_protected_path.py::test_S9_a_stdlib_only_declaration_that_is_not_a_list_of_paths_is_refused_by_path`<br>`packages/agent-review/tests/test_safeguards_protected_path.py::test_S9_a_stdlib_only_entry_that_is_not_a_path_is_refused_by_path`<br>`packages/agent-review/tests/test_safeguards_discovery.py::test_S9_a_malformed_rule_configuration_is_refused_before_any_change_is_read`<br>`packages/agent-review/tests/test_safeguards_discovery.py::test_S9_a_malformed_stdlib_only_declaration_is_refused_by_field_path`<br>`packages/agent-review/acceptance/safeguards/test_safeguards_cli.py` |
