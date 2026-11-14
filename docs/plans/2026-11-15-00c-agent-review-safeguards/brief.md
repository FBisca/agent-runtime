# Brief: the deterministic safeguards

Slug: `00c-agent-review-safeguards`. Contract: `plan.yaml`. Lane: **Full** (three tickets, every one risk-tagged for
security). Archive: **promote** into `docs/specs/agent-review.md`, so each scenario becomes a requirement there.

## Why this plan is the half that can be built

`2026-11-15-agent-review-jev-evidence/HOLDOUT.md` says the model gate does not separate a defective change from a
clean one — 1 missed and 9 false on 13 held-out cases, and the missed change scores *better* than 8 of the clean ones
because its defect is absent evidence, which no diff shows. The same document names the half that does hold: a change
that deletes a path outside the configured root, writes into a protected path, or adds a capability a member must not
have is decidable from the diff, with no threshold, no confidence and no false positives. `429625b` shipped
`shutil.rmtree(self.plan_path.parent)` with nothing confining it; `109c98b` fixed it. No model was needed to see that.

## What changes

- **T1** (S1, S2, S8): `packages/agent-review/` — the change is read as paths and hunks beside the frozen diff, and a
  delete or overwrite on a computed target that nothing in the change confines escalates with rule
  `target-outside-root`, the changed path, `status: completed`, `verdict: escalated`, `usage.requests: 0`, exit 0, and a summary that names the rule, the
  path and the way out.
- **T2** (S3, S4, S5, S9): the mandatory sensitive-path baseline ships with the member and `.agent-review.yaml` extends
  it — discovered without a flag — so a protected path escalates as `protected-path` with its pattern and `source`
  (`baseline` or `repository`), on either side of a rename and for an added, modified or deleted file; a malformed rule
  configuration is refused by field path before any change is read.
- **T3** (S6, S7): a member declared standard-library-only that imports outside it escalates as `stdlib-only`, naming
  the member's path and the module, at no request cost.

## Decisions

- **SG-D1** Three rules ship, not the five the handoff asks for. Rejected: all five. `handoff-02` wants each rule proved
  by a real historical change with a positive and a control case; the corpus does not carry them — 0 renames, 0 added
  credential reads and 0 added network calls across all 10 bad changes, and the single "weakened comparison"
  (`stale-team/t1r1`) *moves* the comparison rather than removing it, so a detection rule's first act would be a false
  positive on a change whose ground truth is something else entirely. Rules 3-as-capability and 4 are deferred to a plan
  that has the corpus for them; the third rule here is `stdlib-only`, which `AGENTS.md` already states as a repository
  contract and an import line decides exactly. (`HOLDOUT.md` §Verdict, `SCORES.md`, the gather's per-rule counts.)
- **SG-D2** A safeguard hit is `verdict: escalated` with `status: completed`, exit 0, and `usage.requests: 0`. Rejected:
  leaving the verdict at `screened` and carrying only `escalation.reasons[]` (P1's shape, which cannot say "nothing was
  screened"), and a new `status`. D34 keeps the status about the execution, D39 keeps `screened` ≠ `passed`, D35 makes
  exit 0 mean the review ran, and `requests: 0` is the observable that the model was never asked.
- **SG-D3** A reason is `{rule, path, pattern, source}` — plus `module` for `stdlib-only` — which is P1's
  `escalation.reasons[].{rule, pattern, source}` plus the path a person acts on. Rejected: a prose reason. P2 consumes
  this, so the fields are the contract, not the wording.
- **SG-D4** This plan owns sensitive paths. The mandatory baseline ships with the member (`.github/workflows/**`,
  `server/**`, `hooks/**`, `pyproject.toml`, `uv.lock` — the five the handoff names and `D13` requires), and a
  repository's `.agent-review.yaml` extends it. Rejected: a second key name (`protected_paths` beside P1's
  `sensitive_paths`), and waiting for P1. P1 is paused and its S2 describes exactly this behaviour; when P1 is rewritten,
  S2 is this plan's, and this brief is the record of that debt rather than a silent duplicate. **The plan does not edit
  P1.**
- **SG-D5** The reach of the root rule is diff-only and written down: a literal target outside the root fires, and a
  computed target fires unless the same change adds a confinement test for it (`is_relative_to`/`relative_to`). The rule
  proves that nothing confines the target, not that the target is outside the root — deciding the second needs the value
  at run time, which a diff does not carry. Rejected: literal targets only (it never fires on the evidence — the defect
  is `shutil.rmtree(folder)`), data-flow analysis (a research project in a tracer bullet), and wording the rule as
  "outside the root" (it would claim more than the evidence can prove). A fact a diff cannot show is not this plan's to
  catch.
- **SG-D6** The rules are code; the paths and the members they apply to are configuration. Rejected: rules in YAML. D29
  is about the question bank, priorities and sensitive paths; a matching rule needs a tested implementation, and a rule
  language would be a second thing to get wrong.
- **SG-D7** The checks live in `packages/agent-review`, not in a hook and not in `server/`. Rejected: a `PreToolUse`
  hook (AGENTS.md makes a hook defence in depth and never the only thing that refuses) and `server/` (three shims that
  re-exec `packages/agent-runtime`; a safeguard there would be a new entry point, not a reuse).
- **SG-D8** Questions keep today's replacement rule and paths are additive. Rejected: making the whole file additive
  here. D13 already says repository patterns *augment* a mandatory baseline, so paths are additive now; D05's additive
  questions are P1's, and pulling them forward would put P1's S1 in this plan.
- **SG-D9** This plan lands **after** `00b-agent-review-provider-contract`. The positive cases need no provider at all —
  they run with no credential and no canned provider, so a tool that asked anyway would stop incomplete — but every
  control screens through the canned provider, and 00b changes that provider's shape. Rejected: landing first (two
  fixture shapes to maintain, and 00b would rewrite the controls). The plan format has no cross-plan `blocked_by`, so
  this ordering is recorded here and in each control's docstring.
- **SG-D10** A hit short-circuits the screen, so a safeguard reason and a threshold-based reason never appear in one
  report. Rejected: screening anyway and merging signals, which spends the request the hit was meant to save — 0.2–0.9 s
  and $0.0017 per hit (`JEV.md`). The consequence is stated because P1's escalation reads judgments that only exist
  when nothing was safeguarded.

## Out of scope

A capability manifest for subprocess, network and credential reads; weakened-authorization detection; defect-class
questions; chunking; the price table; the deep reviewer that consumes an escalation (P2); any orchestrator or hook
integration (D02). No check reaches a real provider.

## Risks

- `security` (T1, T2, T3): a rule that misses is worse than no rule. Each rule carries the historical case it was
  learned from and a negative control (`109c98b` for the fix of the defect it was learned on; a stdlib import and an
  undeclared member for the capability rule), and the checks run before the model, so a model answer cannot mask a
  rule's failure.
- `state` (T2): the escalation block is additive in a frozen report — a P0 report keeps its meaning (D26) — and the
  reason fields are what P2 will read, so a wrong `pattern` or `source` is a defect in the contract, not a wording.

## Hand-off

1. Write `acceptance/test_safeguards_cli.py` first; it is red at base because the tool has no escalation verdict, no
   rule set and no zero-request path.
2. T1, then T2, then T3. T2 and T3 both extend the rule engine T1 builds; T2's configuration work is what T3's member
   declarations join.
3. The two historical runs pin `429625b`/`109c98b`, so the check needs a checkout with that history; a shallow clone
   fails loudly rather than skipping.
