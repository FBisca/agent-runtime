# Acceptance evidence

`test_smart_screening_cli.py` proves S1–S12 at the CLI seam P0 established (`acceptance/screening/test_screening_cli.py`
in the package, `baseline: fail` there). It drives the `agent-review` console script as a subprocess over a real
repository, a configuration it writes, a canned screening provider and a price table, so it imports nothing from the
package and collects cleanly before the phase exists.

The fixture repository is one change that exercises every routing decision at once: `store.py` gains error handling,
a migration is added under `db/migrations/`, a large module is added to force chunking, and `docs/guide.md` is renamed
to `server/guide.md` — a rename *into* a mandatory global sensitive path, which is what makes D13's rename rule
observable from the outside.

The canned provider answers in the order the tool calls it (P1-D2): once for whole-diff applicability, then once per
chunk. Each class carries a control that passes at base and after; every scenario's tests fail at base.

At base (commit `97cc227`): **31 failed, 3 passed** — the two controls and the invariant that an unknown configuration
field stays refused. Each `-k "S<n> or control"` selection selects a failing test, so every scenario's check is red
before the phase.

The archive promotes this file to `packages/agent-review/acceptance/screening/`, beside P0's, where it keeps proving
the requirements the archive writes into `docs/specs/agent-review.md`. Do not weaken a check to make a ticket pass: the
failures here are the safeguards P1 exists for.

Two couplings are deliberate and should not be "fixed" by loosening a test:

- The baseline question ids (`contract.requirement-traceability`, `engineering.elegance`,
  `engineering.traceable-cause`, `engineering.hard-decisions`) are named because D51 makes an id stable and an id whose
  meaning changed gets a new one. A test that discovered them by importing the package could not notice a bank that
  silently lost them.
- The group ids (`contract`, `engineering`) come from the dotted prefix of a question id (P1-D3), so applicability is
  deterministic without configuration.
