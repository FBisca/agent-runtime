# Plan: agent-eval — a label carries its reasons and evidence

Slug: `agent-eval-label-evidence`. Living spec: `docs/specs/agent-eval.md`. Status: ARCHIVED 2026-11-10.
Contract twin: `2026-11-10-agent-eval-label-evidence.plan.yaml` (same rows; the driver runs the YAML).
Follows `2026-11-10-agent-eval-p0` (D17 there).

## Why now

The first judged label (`orchestrator-silent-idle`, FAIL 0.66, 2026-11-10) stored four numbers and nothing else. The
judge's rationale and citations lived only in a chat, so the score cannot be checked or disputed later. `label/1` could
not hold them: it is a strict model that refuses extra fields (agent-eval-p0 D10) and was cut to numbers because the
judge's output was out of scope; it also cannot hold a BLOCKED or INCOMPLETE verdict. The score-agent rubrics already
require a citation for every deduction; this plan keeps them, structured and checkable.

## Sources

| Source | What it settles |
|---|---|
| `agent-skills/skills/score-agent/SKILL.md`, `rubrics/*.md` | output fields, anchors 1.0/0.8/0.4/0.2/N/A, weights, verdict rule, hard gates, "cite every deduction", proven vs risk |
| `packages/agent-eval/src/agent_eval/label.py` | `label/1` model, `add-label`, strict Pydantic |
| `~/.local/state/agent-evals/cases/orchestrator-silent-idle/` | the first case and its evidence-less `label/1` |
| agent-eval-p0 gaps report | label format undocumented in the spec and `--help`; spec still says standard library only |
| Felippe, 2026-11-10 (this session) | the `label/2` field list below; "make it part of the judging mechanism itself" |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | `label/2` holds the rubric's whole output: subject, judge (skill, rubric sha256, model), verdict, score, gates, per dimension label/score/rationale/findings, top issue, smallest improvement, probable cause with confidence | widen `label/1` with a free-text `notes` | a score must be traceable; free text cannot be checked | Felippe, in session |
| D2 | Evidence ref = `file` in the case + optional `line` + required verbatim `quote` (≤300 chars), checked by `add-label` | file and line only; no check | a judge cannot cite what is not there; a line alone can silently point at the wrong text | Felippe, in session |
| D3 | `add-label` recomputes the score and the verdict from the rubric's rule and refuses a mismatch; INCOMPLETE when quality or tests is N/A | trust the judge's arithmetic | the rule is mechanical; the rubric's "essential dimension" read as the two the change is judged on | assumption (stated) |
| D4 | `label/1` stays for `source: transcribed` only; a judged label is `label/2` | drop `label/1` | a transcribed score has no case evidence to cite | assumption (stated) |
| D5 | The judge writes `label/2` itself (score-agent case mode, agent-skills); its printed block is rendered from the label | a second step re-typing the score | one source of truth for the score and its reasons | Felippe, in session |
| D6 | The `label/1` on `orchestrator-silent-idle` is removed by Felippe before re-judging; immutability (agent-eval-p0 D14) holds for every label written after this plan | a second label kind; a re-captured case | that label predates the format and carries no evidence; a duplicate case doubles the transcripts | assumption (stated) |
| D7 | No per-finding severity, no free-text notes | add them | the rubric has no severity; notes become a dumping ground | Felippe, in session |

## Scenarios

- **S1** WHEN `add-label` reads a `label/2`, THEN a valid one is written to `labels/agent-judge.json`; an invalid one exits 2 naming each failing field by path, nothing written; unknown fields are refused.
- **S2** WHEN an evidence ref is checked, THEN `file` must be a manifest-listed case file or `changes.patch` and `quote` must occur verbatim in its decompressed text (on `line` when given); an absolute path, a path leaving the case, an unlisted file, a line past the end or a missing quote exits 2 naming the ref.
- **S3** WHEN a dimension is checked, THEN a scored one has a finding with evidence, one below 1.0 has a `deduction: true` finding, `kind` is proven|risk|strength, and a null score has label `insufficient_evidence` and a rationale.
- **S4** WHEN score and verdict are checked, THEN the score is the renormalised weighted mean rounded to 2 decimals (null only when nothing is scored), and the verdict is BLOCKED iff gates, else INCOMPLETE when quality or tests is null, else PASS when every scored dimension and the score are ≥0.8, else FAIL; a mismatch exits 2 naming the expected value.
- **S5** WHEN a label names its subject or source, THEN a `subject.session` outside the manifest exits 2; `label/1` with `source: judged` exits 2 pointing at `label/2`; `label/1` transcribed is accepted as before.
- **S6** WHEN `add-label --help` or the spec's case layout is read, THEN both show `labels/<kind>.json` and a complete `label/2` example, and the spec names Pydantic as the one dependency.
- **S6a** WHEN `score-agent` is given a case directory, THEN it judges from the case's files, writes a `label/2` whose refs point into the case with verbatim quotes, and prints its block rendered from it.
- **S7** WHEN `orchestrator-silent-idle` is judged again, THEN `labels/agent-judge.json` holds a score-agent `label/2` accepted by `add-label`, every deduction's quote findable in the case.

## Out of scope

- An `agent-eval judge` command that runs the judge itself; judge isolation; a second judge.
- Rendering a label with its quotes resolved (`agent-eval show`); this plan only stores and checks.
- The svc-platform case (`org-chart-custodian-leaves`), still deferred by agent-eval-p0 D16.

## Risks

- **Security:** quotes copy transcript text into the label; the label lives in the case, 0600, under the same 0700 home. Covered by agent-eval-p0 S11.
- **Performance:** each quote check decompresses one case file; cases are a few MB. n/a.
- **Migration:** `label/1` files stay valid; `label/2` is a new schema value.
- **Rollback:** revert the package change; labels already written stay readable as JSON.

## Seams

- `packages/agent-eval/tests/test_add_label.py` style: `main([...])` in process against a case built in `tmp_path` with a manifest and gzipped files (`integration`: writes files).
- `label.py` beside `label/1`; the manifest reader from `capture.py`/`store.py`.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | `label/2` and the `add-label` checks; spec layout, example, dependency line; `--help` example | S1–S6 | none | packages/agent-eval/, docs/specs/agent-eval.md | regular | complex | agent | COMPLETE 2026-11-10 (#130) |
| T2 | score-agent case mode (agent-skills), by the Lead | S6a | T1 | agent-skills `skills/score-agent/` | | | Felippe | COMPLETE 2026-11-10 (agent-skills#72) |
| T3 | Re-judge `orchestrator-silent-idle` with `label/2` after removing its `label/1` | S7 | T1, T2 | (state only) | | | Felippe | COMPLETE 2026-11-10 |
