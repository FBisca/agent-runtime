# Acceptance evidence

`test_provider_contract.py` proves S1–S7 at the CLI seam P0 established, and fails at base. It drives the
`agent-review` console script as a subprocess over a real two-revision repository, with a local System One endpoint
that records the one request the tool sends (S1, S5) and a canned provider for the paths that do not need the wire.

At base the file is red except two controls: the tool has no System One transport, no `criteria` in its bank, and no
provenance in its report. `FixtureControlTests` holds the two controls that pass at base and after, so the fixture's own
setup cannot be mistaken for a scenario that works.

Two couplings are deliberate:

- The credential is `TYPESAFE_API_KEY`, the provider's own variable, and a missing one is refused naming it. The
  wire tests also set `AGENT_REVIEW_API_KEY` because that is P0's fallback, and `S1_the_documented_credential_is_sent`
  pins that the provider's variable wins when both are present.
- The served model is `jev-1.14.0` in the canned response because that is what TypeSafe returns beside the configured
  alias `jev-latest`; a report that echoed only the alias would be indistinguishable after a provider rollout.

Do not reach the network and do not weaken a scenario to make a ticket pass: the refusals here are the ones a report
later relies on.

S7 is the exception to the subprocess seam: it calls `agent_review.cli.main` in process so that
`socket.socket`, `socket.create_connection` and `socket.getaddrinfo` can be patched to refuse. A run that reached the
network there would fail rather than pass, which is the point of the scenario.
