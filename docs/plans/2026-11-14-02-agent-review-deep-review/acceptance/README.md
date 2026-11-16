# Acceptance evidence

`acceptance/test_deep_review.py` is the phase's one acceptance file. The runner places it at
`packages/agent-review/acceptance/deep-review/` and each scenario's `check` runs its own tests plus its controls
(`-k "S<n>"`), so a scenario's evidence never passes on another scenario's tests.

It drives the `agent-review` console script as a subprocess over a throwaway repository: the screening is answered by
the existing canned provider (`--screening-fixture`) and the deep review by a canned reviewer
(`--deep-review-fixture`, `answers.json` in and `spawns.json` out). No check reaches a model, a provider or a harness,
so the evidence runs wherever the suite runs and stays repeatable.

Controls are named `test_S<n>_control_...`: each one passes at base and after, and none of them needs the deep review
to exist yet, which is what makes each scenario's red a calibrated one. Behavioral checks belong here; unit-test counts
are not evidence.
