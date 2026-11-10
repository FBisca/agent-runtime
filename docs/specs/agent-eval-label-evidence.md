# Agent-Eval-Label-Evidence

Paths: packages/agent-eval/, packages/agent-eval/tests/

A judge's `label/2` carries the reasons and evidence behind its score, so every deduction traces back to a
verbatim line in the case it judged. `agent-eval add-label` refuses a label whose evidence is not in the case
or whose score and verdict do not follow the rubric. The living spec of `docs/specs/agent-eval.md` holds the
case layout and the wider `label/2` contract; this file holds the requirements of the label evidence plan.

## Requirements

- **R1** WHEN `agent-eval add-label <case> --kind agent-judge <file>` reads a `label/2` JSON, THEN a valid one is written to `labels/agent-judge.json` as given; an invalid one exits 2 naming each failing field by its path (`dimensions.quality.findings[0].evidence[1].quote`), and nothing is written. A valid label has `subject.session`, `judge` (`skill`, 64-hex `rubric_sha256`, `model`), `judged_at` (a real date), `source: judged`, `verdict` PASS|FAIL|BLOCKED|INCOMPLETE, `score` (0..1 or null), `gates`, the four dimensions (each `label`, `score` 1.0|0.8|0.4|0.2|null, `rationale`, `findings`), `top_issue`, `smallest_improvement` and `probable_cause` (`cause` model|harness|repo|instructions|mixed|unknown, `confidence` low|medium|high); any other field is refused (from agent-eval-label-evidence/S1)
- **R2** WHEN a `label/2` evidence ref (`file`, optional `line`, required `quote` of at most 300 characters) is checked, THEN it is accepted only when `file` is a file of the case listed in its manifest (a transcript, an orchestrate file) or `changes.patch`, and `quote` occurs verbatim in that file's decompressed text, on line `line` when given; a ref to an absolute path, a path leaving the case, an unlisted file, a line past the end or a quote that is not there exits 2 naming the ref (from agent-eval-label-evidence/S2)
- **R3** WHEN a dimension of a `label/2` is checked, THEN a scored dimension has at least one finding with evidence; a dimension scored below 1.0 has at least one finding with `deduction: true`; a finding's `kind` is proven|risk|strength; a null score has label `insufficient_evidence`, a rationale and no deduction; anything else exits 2 naming the dimension (from agent-eval-label-evidence/S3)
- **R4** WHEN the score and verdict of a `label/2` are checked, THEN `score` equals the weighted mean (quality .35, tests .25, efficiency .25, communication .15) renormalised over the scored dimensions, rounded to 2 decimals, and is null only when none is scored; `verdict` is BLOCKED exactly when `gates` is non-empty (each gate with `gate`, `rationale` and evidence), else INCOMPLETE when quality or tests is null, else PASS when every scored dimension and the score are at least 0.8, else FAIL; a mismatch exits 2 naming the expected value (from agent-eval-label-evidence/S4)
- **R5** WHEN a label names its subject or source, THEN a `label/2` whose `subject.session` is not a session of the case manifest exits 2 naming it; a `label/1` with `source: judged` exits 2 saying a judged label is `label/2`; a `label/1` with `source: transcribed` is still accepted as before (from agent-eval-label-evidence/S5)
- **R6** WHEN `agent-eval add-label --help` is read, or the spec's case layout, THEN both show `labels/<kind>.json` and a complete `label/2` example, and the spec names Pydantic as the package's one dependency (from agent-eval-label-evidence/S6)
- **R7** WHEN `score-agent` is given a case directory, THEN it judges from the case's own files, writes a `label/2` JSON whose every evidence ref points into the case with a verbatim quote, and prints its score block rendered from that label (from agent-eval-label-evidence/S6a)
- **R8** WHEN the case `orchestrator-silent-idle` is judged again, THEN `labels/agent-judge.json` holds a `label/2` from score-agent with `source: judged`, accepted by `add-label`, whose every deduction's quote can be found in the case (from agent-eval-label-evidence/S7)

## Coverage

| Requirement | Test |
|---|---|
| R1 | `packages/agent-eval/tests/test_add_label2.py`: `test_R1_a_valid_label2_is_written_as_given`, `test_R1_an_invalid_label2_names_each_failing_field_by_its_path`, `test_R1_each_shape_fault_is_refused_and_nothing_is_written` |
| R2 | `packages/agent-eval/tests/test_add_label2.py`: `test_R2_a_ref_into_a_listed_file_with_a_verbatim_quote_is_accepted`, `test_R2_a_ref_that_does_not_resolve_exits_2_naming_it`, `test_R2_a_gate_ref_is_resolved_like_any_other`, `test_R2_a_listed_file_that_is_gone_from_the_case_is_refused` |
| R3 | `packages/agent-eval/tests/test_add_label2.py`: `test_R3_a_dimension_that_breaks_a_rule_exits_2_naming_it`, `test_R3_a_perfect_dimension_needs_evidence_but_no_deduction`, `test_R3_every_dimension_is_named_when_several_break` |
| R4 | `packages/agent-eval/tests/test_add_label2.py`: `test_R4_the_recomputed_score_and_verdict_are_accepted`, `test_R4_a_wrong_score_exits_2_naming_the_expected_value`, `test_R4_a_score_where_nothing_is_scored_must_be_null`, `test_R4_a_wrong_verdict_exits_2_naming_the_expected_value`, `test_R4_a_gate_makes_the_verdict_blocked_and_only_a_gate_does`, `test_R4_a_blocked_verdict_without_a_gate_is_refused` |
| R5 | `packages/agent-eval/tests/test_add_label2.py`: `test_R5_a_subject_that_is_not_a_session_of_the_manifest_exits_2_naming_it`, `test_R5_a_label2_needs_a_readable_manifest`, `test_R5_a_judged_label1_exits_2_saying_a_judged_label_is_label2`, `test_R5_a_transcribed_label1_is_still_accepted`, `test_R5_a_label2_of_another_kind_is_refused`, `test_R5_an_existing_label_still_stays` |
| R6 | `packages/agent-eval/tests/test_add_label2.py`: `test_R6_the_help_shows_where_the_label_goes_and_a_complete_label2`, `test_R6_the_spec_case_layout_and_example_match_the_help`, `test_R6_the_spec_names_pydantic_as_the_one_dependency`, `test_R6_the_example_is_accepted_by_add_label_against_the_case_it_cites` |
| R7 | none yet: score-agent case mode lives in agent-skills (plan T2, owner Felippe) |
| R8 | none yet: re-judging `orchestrator-silent-idle` is a manual step (plan T3, owner Felippe) |
