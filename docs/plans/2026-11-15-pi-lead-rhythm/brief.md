# Brief: Pi lead rhythm

Slug: `pi-lead-rhythm`. Contract: `plan.yaml`. Lane: Full, because the work cuts
across the runtime package, the hook layer and a new Pi adapter, it is more than
two tickets, and it carries `state`, `external` and `retry-idempotency`.

## What changes

- `packages/agent-runtime/`: the stop predicate lives in the hook layer, with a
  copy of the store's snapshot rule and of `Store.unfinished_count` beside it →
  one rhythm policy in the runtime package, and one entry point that every host
  asks (T1)
- `bin/agent-runtime-hook`: one Claude/Codex dialect → a `--dialect json` answer
  `{"block", "reason"}` for a harness whose own output shape differs (T1)
- `docs/architecture.md`: the "what a new host provides" table omits the stop
  guard → the hook entry is the documented surface, with a capability row per
  harness (T1)
- `packages/agent-runtime/hc_tools.py`: a lead may spawn, message, resume and
  stop with reports still unread → refused, naming the count and `wait(all)`
  (T2)
- `extensions/agent-runtime/`: Pi has no rhythm adapter → an extension that turns
  a block into one more provider request, pushes a wake into an idle session, and
  refuses a sleep of 60 seconds or more (T3)
- `hooks/stop_guard.py`: the guard knows only the lead's team → it also knows a
  worker's own assignment, on Claude, Codex and Pi (T4)

## Decisions

- D1 Put the policy in the runtime package, leave `hooks/` as the dialect, and
  make `bin/agent-runtime-hook` the single entry, over keeping it in the hook
  layer: `hook_lib.working()` re-implements the store's snapshot rule and
  `unfinished()` duplicates `Store.unfinished_count`, so two readers that must
  agree sit in two codebases (gather finding 4; round 1 Q1)
- D2 Make the Pi adapter a repository-versioned extension that calls that entry,
  over a runtime-side push or a prompt instruction: Pi's only in-session push is
  a message with a triggered turn, and an instruction is the defect (round 2 Q6)
- D3 Give the nudge an authority: the server refuses the mutating verbs while
  reports are undelivered, over the extension being the only refuser, because a
  hook is never the only refuser (AGENTS.md; round 2 Q7)
- D4 Carry the capabilities as rows of the harness contract — refuse a stop, park
  a wait, push a wake, request cap — over per-host prose: no source carries the
  matrix, and each host's abilities are only discoverable in four files (gather
  finding 20; round 1 Q2)
- D5 Keep the three-block budget and the `seen` fingerprint for Pi, over a
  per-host budget: a session that switches host must not get fresh blocks
  (round 1 Q3)
- D6 Leave the driver as a driven run's rhythm and let the extension watch
  `wakes.jsonl`, over a second scheduling path: the driver already books a wake
  per escalation, and `await` stays the fallback for a host without push
  (round 1 Q4)
- D7 Give the worker guard to all three hosts in one ticket, over Pi alone: the
  "ended without a report" hole is on all three, and both hook payloads are
  already recorded in `tests/test_hooks.py` (round 2 Q9)
- D8 Fix the tests the authority invalidates inside T2, over a waiver that lets
  them fail: five tests in `tests/test_peer_dispatch.py`, `tests/test_event_wait.py`
  and `tests/test_operations.py` publish a report and then mutate without
  acknowledging it, so they encode the behaviour D3 removes. They gain a
  `wait(all)` and a receipt before the lead's mutating verb and keep every
  assertion, because the refusal is right and a test that has to weaken to pass
  is the defect. The same pattern is fixed wherever it appears, not in the three
  instances the impacted check happened to report (t2 request, decision `amend`)

## Out of scope

- A batch, non-interactive Pi spawn and the gather-through-the-runtime transport;
  R190's amendment is approved and belongs to its own plan, because the skill it
  serves lives outside this repository.

## Risks

- `state` (T1, T2, T4): the predicate moves and the authority begins refusing
  verbs; verified by `tests/test_rhythm.py`, `tests/test_rhythm_authority.py` and
  `tests/test_hooks.py`
- `external` (T3): the adapter is written against Pi's extension API; verified by
  the recorded `turn_end` payloads in `tests/test_pi_extension.py`
- `retry-idempotency` (T3): a wake pushed twice after a restart; verified by the
  `seen` cursor case in `tests/test_pi_extension.py` and S7's acceptance check
