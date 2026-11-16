# Field notes: jev as a gate, measured on real work

Shadow run over six pull requests of the `structure-budget` plan, 2026-11-16. Nothing gated on it:
every PR was reviewed and merged by its own process, and jev's verdict was recorded beside it.

Why these six: they are all behaviour-preserving extractions (move helpers out of one file into a
module, re-export name for name), which is the shape most likely to break a question bank that asks
about behaviour.

## What was measured

```
uv run --frozen --all-packages python -m agent_review code \
  --base <merge-base of the PR head with main> --head <PR head> --output /tmp/shadow/<pr>
```

Packaged baseline bank (21 questions), `model: jev-latest` resolved to `jev-1.14.0`, bank digest
`964455a8`. No repository `.agent-review.yaml`. Raw reports were left in `/tmp/shadow/<pr>/report.json`
and are not committed; the numbers below are the whole of what they said that matters.

Second column of evidence: the drive's own review gate (`review*.json` per attempt — verdict, items,
kinds) and the Lead's verification, which was done by running checks rather than by reading.

## Results

| PR | ticket | change | jev | asked | requests | drive review | defect found by |
|---|---|---|---|---|---|---|---|
| #394 | T4 records | +410/−264 | escalated | test-evidence 0.56, req-trace 0.71 | 1 | pass, 5 concerns | — |
| #396 | T5 runtime+report | +424/−224 | escalated | test-evidence 0.67, req-trace 0.67 | 1 | pass, 7 concerns | — |
| #399 | T6 book | +498/−241 | escalated | test-evidence 0.55, req-trace 0.64, survival 0.70 | 1 | pass, 6 concerns | **Lead** — `owned_paths` left the public facade (85 → 84 names), `public-surface-compatibility` scored **0.86** |
| #401 | T7 gates | +1177/−809 | escalated | 4 questions | 2 | pass, 6 items | — (concern: `Gates.__getattr__`) |
| #407 | T8 spawn | +769/−474 | escalated | test-evidence 0.52, req-trace 0.63 | 1 | **blocked** once | **review** — report and PR body described a change the branch does not carry, at a head that is not the branch's |
| #409 | T18 tree membership | +213/−36 | escalated | **safeguard only** | **0** | **blocked** twice | **review** — the budget asked git only for `--cached`, so a file the branch adds but has not committed is unmeasured; and S23's untracked half was not delivered |

## Findings

1. **The scored path was a constant.** Five of five model-answered changes escalated, on the same two
   questions, with `test-evidence` at 0.52–0.67 against a 0.71 threshold and `requirement-traceability`
   at 0.52–0.71 against 0.75. A behaviour-preserving extraction has no behavioural evidence *by
   construction* — its evidence is the absence of a change, which a diff cannot show. This is not a
   calibration miss to tune: five labelled points say the question is unanswerable for the shape.

2. **The safeguard path was the only cheap, factual one.** T18 escalated with **zero model requests**:
   `target-outside-root` at `tests/test_structure_budget.py:384`, an `.unlink()` on a computed target
   with nothing confining it to the review root. A rule fired on a fact and named the line and the
   remedy. It was a **false positive** — the target is a temporary root the test built a few lines
   earlier — because the rule cannot tell scaffolding from production code. A one-line confinement
   (`.is_relative_to(root)`) would clear it, which is a good sign: the finding is cheap to resolve
   rather than argumentative.

3. **Every real defect of the run was a fact, not a quality.** A stale report head (T8), a git listing
   that omits uncommitted files (T18), a public name that left the facade (T6). None was decidable by a
   score; all three were decidable by a rule. The scored bank named none of them.

4. **Absence is invisible to a diff.** `contract.public-surface-compatibility` scored 0.77–0.92 across
   all six — including the one that dropped `owned_paths`. In the diff that name does not disappear, it
   appears in a new file, and its absence from the re-export block is a hole. The question needs
   symbol-level input (exports defined vs exports published) rather than more prompting.

5. **Two questions are dead for this shape.** `contract.error-handling` scored 0.05–0.12 and
   `engineering.type-contracts` ~0.25 on all six, without varying with anything anyone did. Questions
   that do not move carry no signal and only dilute a threshold.

6. **The drive's review is a gate, not a commenter.** It blocked 2 of 6, and both blocks were the
   strongest catches of the run — one process-integrity, one functional. An earlier reading of four
   tickets concluded otherwise; four was too few.

## What a full jev would need

- **Applicability by change shape.** A behaviour-preserving move asks structural questions only. Gate
  the shape from facts (renames, moves, symbol sets) before choosing the bank subset.
- **Facts before scores.** Answer what is decidable with rules — new files committed, report head equals
  the PR head, paths touched inside the ticket's fence, public names before vs after — and spend the
  model only on what is left. Tonight that rule layer would have caught two of the three defects for
  nothing, and the model would have been asked only about T6.
- **Symbol-level input.** The diff is the wrong unit for "did anything leave the surface"; the export
  sets before and after are the right one.
- **A labelled ground-truth set.** Six PRs with a known answer tonight (T6 facade, T8 stale head, T18
  `--cached`) plus T9–T12 as they land. Store (change, questions asked, answers, real defect, who found
  it) so a change to the bank can be scored instead of argued about.
- **Calibration per shape, not per question.**
- **Report the failing fact and the remedy**, as the safeguard did. The scored path reports a number and
  a threshold, which a reader cannot act on.

## Limits

`n = 6`, one repository, one model version, one bank digest. The six tickets are unusually homogeneous —
all behaviour-preserving extractions — which is exactly why the scored path looks constant. Do not
generalise this to mixed-shape changes without the labelled set above: the same bank may behave
differently where behaviour is actually in play.
