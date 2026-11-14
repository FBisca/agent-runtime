# Acceptance evidence

`test_safeguards_cli.py` proves S1–S9 through the `agent-review` console script, and is red at base: the tool has no
promotion of its own — no `escalated` verdict, no rule set, no `usage.requests: 0` path.

Two runs are over this repository's history, because that is where the rules came from: `429625b` introduced the
unguarded `shutil.rmtree` and `109c98b` confined it. Every other run builds a scratch repository with a member of each
kind the rule set names (`pkg/`, `server/`, `hooks/`, `pyproject.toml`), so a rule is proved against the shape it
refuses instead of against a fixture that already agrees with it.

Three couplings are deliberate:

- **Positives name no provider at all.** A positive case runs with no credential and no `--screening-fixture`, and the
  report it asserts on says `usage.requests: 0`. A tool that reached for the provider would stop `incomplete` and fail
  the assertion rather than pass it.
- **At base every run stops at the bank.** `type: noul` is not a P0 question type, so the configuration is refused with
  exit 2 before a rule is reached; that is why the checks are `baseline: fail` and why 00b, which makes `noul` a type,
  is what lets a builder see a safeguard's own failures.
- **Every control screens through the canned provider in 00b's shape** (`{responses: [{model, answers, usage}]}`), so
  the controls are red until `00b-agent-review-provider-contract` lands. It lands first; the controls exist so a
  scenario cannot pass on a run that never screened.
- **The historical runs need a checkout with `429625b` and `109c98b`.** A shallow clone fails loudly in
  `repository_root()`; skipping would turn a rule's only real-case proof into a green line that proves nothing.

Do not weaken a scenario to make a ticket pass, and do not widen a rule to make a case fire: the report's `pattern` and
`source` are what P2 reads.
