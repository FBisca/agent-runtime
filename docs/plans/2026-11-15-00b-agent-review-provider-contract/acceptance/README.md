# Acceptance evidence

`test_provider_contract.py` proves S1–S6 at the CLI seam P0 established, and fails at base. It drives the
`agent-review` console script as a subprocess over a real two-revision repository, with a local System One endpoint
that records the one request the tool sends (S1, S5) and a canned provider for the paths that do not need the wire.

At base the whole file is red: the tool has no System One transport, no `criteria` in its bank, and no provenance in
its report. Each class carries a control that passes at base and after, so a green control alone cannot hide a weak
scenario.

Two couplings are deliberate:

- The credential is `AGENT_REVIEW_API_KEY` for the wire test even though a real run uses `TYPESAFE_API_KEY`; the test
  pins that the tool sends a bearer token and names the variable it is missing, not which variable a person exports.
- The served model is `jev-1.14.0` in the canned response because that is what TypeSafe returns beside the configured
  alias `jev-latest`; a report that echoed only the alias would be indistinguishable after a provider rollout.

Do not reach the network and do not weaken a scenario to make a ticket pass: the refusals here are the ones a report
later relies on.
