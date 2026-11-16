# Field notes: jev shadowed on the deep-review delivery

Shadow run over the pull requests of `2026-11-14-02-agent-review-deep-review`, 2026-11-16. Nothing gated on
it: every PR was reviewed and merged by the drive's own gate, and jev's verdict was recorded beside it.
Companion to `FIELD-NOTES-structure-budget.md`, which measured six behaviour-preserving extractions; these are
two additive changes, so the shape differs and so does what the bank says about it.

Why this delivery: it is the phase that *adds* a deep reviewer, which makes it the change a jev-based review
would have to judge first if the screen is ever to replace the drive's gate.

## What was measured

```
zsh -ic 'export TYPESAFE_API_KEY; uv run --frozen --all-packages python -m agent_review code \
  --base <merge-base of the PR head with origin/main> --head <PR head> --output /tmp/shadow-02/<unit>'
```

Packaged baseline bank (21 questions), `model: jev-latest` resolved to `jev-1.14.0`, bank digest `964455a8`. No
repository `.agent-review.yaml`, no price table (so cost is unknown, not zero). Raw reports are in
`/tmp/shadow-02/<unit>/report.json` and are not committed; the numbers below are what they said that matters.

Second column of evidence: the drive's own review rounds (`review*.json` per attempt — verdict, item ids and
kinds, commit reviewed) and the Lead's decisions, which were made by running checks rather than by reading.

## Results

| PR | ticket | change | jev | asked | requests | drive review | defect found by |
|---|---|---|---|---|---|---|---|
| #408 | T1 spawn one read-only reviewer | 10 files, +1797/−29 | escalated | test-evidence 0.63 v 0.71 | 1 (23.9k in, 434 out) | **block** (B1, B2) → **block** (B3) → pass (2 concerns) | **review** ×3; **Lead** ×2 (a stale report that was the driver's pointer bug; the fixture-refusal boundary); none by jev |
| #417 | T2 judge the answer, keep a contract violation blocking | 6 files, +973/−47 | **screened** | none | 1 (16.7k in, 434 out) | **block** (B1) → pass (3 concerns) | **review** ×2 (the false check claim, the promotion's spelling); none by jev |

T2's blocker, in the gate's words: **B1** the report claimed `uv run ruff format --check packages/agent-review`
passed ("45 files already formatted") while at the reviewed commit that command exited 1 with three files
unformatted — a claim about the code that the code contradicts, which is the one thing the gate refuses to take on
trust. It is also, of course, a fact a check answers and a score cannot.

T1's three blockers, in the gate's words, compressed:

- **B1** the reviewer was invoked as `[harness, *controls, prompt]`, and claude's tool flags are variadic, so the
  prompt was read as another deny-tool name — the claude reviewer could never receive its question.
- **B2** a reviewer timeout or spawn failure propagated as a CLI refusal, so `report.json` was never written and a
  screening the provider had already answered lost its judgments, usage and escalation.
- **B3** the fix for B2 set the report's failure, and the terminal summary then said "screened nothing" above a
  usage line for a screening it had just paid for — the report was right and the words were wrong.

## Findings

1. **The two signals sat where the defects were not.** The change jev escalated had three defects, none of them
   about the question it asked; the change it screened clean carried a blocking false check and a promotion rule
   that did not fire. If the screen were the gate, #417 would have passed unread and #408 would have been read for
   the wrong reason. That is `FIELD-NOTES-structure-budget.md` finding 5 (absence is invisible to a screen) with a
   known defect behind a clean verdict, which is the first time this set can say it with an outcome attached.

The escalated question itself: `contract.test-evidence` at 0.63 against 0.71, on a change that *adds* 1,797 lines
of behaviour with its own unit suites and an acceptance file. The same question carried the structure-budget
sample, so it fires on additive and on behaviour-preserving changes alike — insensitive to shape, not just to
behaviour.

2. **Every real defect was a fact.** Same conclusion as the earlier sample, from a different shape: a variadic
   flag, an uncaught error type, a summary branch. A rule that read the argv, the call sites of `subprocess.run`
   or the report's own failure path would have found all three without a model.

3. **The gate is where the value was, again.** Three review rounds, three real blockers, each fixed in the ticket
   and each invisible to CI — the PR was green on 3.11/3.12/3.13 and shellcheck at the commit the fifth round
   blocked. The drive's review blocks on claims about the code; a score cannot make a claim.

4. **One finding was the harness, not the change.** Round 5's `C1` said the report still described an older
   commit; it was true, and it was about the *first attempt's* file: the review packet named the unit directory
   while the implementer packet named the attempt directory, so after a re-dispatch the reviewer could only see the
   stale one. That is now `2026-11-16-locator-invariants`. A shadow table is only as honest as the reader it
   compares against; this row would have been a jev "miss" on a defect that never existed.

5. **What jev would need to be the gate here, measured not argued.** The four blocking defects needed: the argv a
   spawn builds, the call graph around an error type, the report's own terminal rendering, and a check re-run. None
   is a question to a model over a diff; all four are checks over the change — and T2's was answered by running the
   command the report named. The bank is not wrong about what it asks; it is asking a different question than the
   one that decides this code.

## What a full jev would need

- **Ask the change what it can answer.** For this delivery: does every family the runtime knows answer for itself
  (S3 does exactly this — a test, not a question), does an error path keep the evidence, does the terminal say what
  the report holds. Three of the five items the gate raised are already tests in the tree after T1; the shadow
  should read them rather than ask about them.
- **The deep review is the missing half, and it is now buildable.** jev's screening recommends a review; this
  delivery is the reviewer. The next honest shadow run is `--deep-review` over the same PRs with a real harness,
  which would measure the half that produced the found defects here.
- **A labelled set across shapes.** Six extractions plus two additive changes is still one repository and one model
  version. Store (change, questions asked, answers, real defect, who found it) so a bank change can be scored.

## Limits

`n = 2` pull requests (one merged, one open and reviewed), one repository, one model version, one bank digest. The
defect count is four because the gate was strict, not because the code was bad: two of them came from the fix for
the first. Do not read this as a rate of jev misses per change; read it as the shape of what it does and does not
see.
