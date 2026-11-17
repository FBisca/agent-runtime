# agent-runtime rulebook

The standard code in this repository is written and reviewed against. An implementer follows it; a reviewer
raises a change that breaks it, naming the rule by its bold title. Each rule says what to do and what fails it.
Style, naming taste and missing comments are not defects unless a rule below says so. `AGENTS.md` holds the
rest of the repository's rules (spec as ledger, invariant over enumeration, hooks and CLI, one-file pointers,
secrets, standard library in `server/` and `hooks/`); a reviewer reads it beside this file. Playbooks, when
they exist, are listed here.

## 1. The contract

**Every behaviour change traces to a requirement.** The spec (`docs/specs/*.md`) holds WHEN/THEN requirements;
a plan's ticket names the scenarios it implements.
- Do: implement every case a scenario names, and nothing it does not.
- Fails: a scenario case with no code that produces its outcome; behaviour that no scenario or stated cause asks for.

**Every changed behaviour has a test that would fail without it.**
- Do: write the test red first; assert the outcome a caller sees, not the implementation.
- Fails: a behaviour added or changed with no test in the change; a test that still passes with the change reverted;
  a test weakened, skipped or reclassified to make the suite pass or run faster.

**The public surface stays compatible.** Names a package exports, function signatures, CLI options and their
parsing, exit codes, file formats, JSON/YAML keys and event shapes are contracts.
- Do: keep the old form working, or change every caller and the spec in the same change.
- Fails: a name dropped from a module or package facade (`__init__` re-exports, `__all__`); an option renamed or
  made required; a key renamed; a value now parsed or interpreted differently.

## 2. Failure

**A failure surfaces as an error a caller can act on.**
- Do: raise, or return an error the caller checks; a refusal names the reason and the way out.
- Fails: an exception caught and dropped; a default substituted for a failed result; success reported after part
  of the operation failed; "this is not allowed" with no reason or way out; a way out the caller cannot follow.

**The running system stays available and correct through its own failure modes.**
- Do: make a step that can fail leave state consistent: complete, or rolled back, or recorded as failed.
- Fails: a crash, timeout or interrupt that leaves a record half-written or a status that lies.

## 3. Things the code selects and destroys

**Code that decides which things to act on selects exactly the intended set.** Listings, filters, matches,
re-runs, required checks, exported names.
- Do: identify a thing by everything that makes it distinct; include every kind of thing the requirement names.
- Fails: a match that treats different things as the same; a listing that silently leaves a kind out; acting on
  more than the requirement asks for; the same item twice.

**A delete or overwrite touches only what the code created or was explicitly handed.**
- Do: resolve the target and check it lies inside the root it belongs to, at the moment of deletion.
- Fails: a target from configuration, a parent directory, a glob or input with no containment check.

**External programs and libraries are asked for exactly what is needed, and their result is checked.**
- Do: know what each flag selects and each default does; check the exit status and the output.
- Fails: a flag that selects too much or too little; a library default assumed; a failed call read as success.

## 4. State

**State is visible and changes are recorded.** The driver's ledger, events and statuses are read by other
processes and by people.
- Do: record every status transition as an event or a field a reader can see; read state from where it is
  held, not from a copy.
- Fails: a status that moves silently; a decision acted on that was not recorded; a branch on three or more
  booleans; a cached copy that can go stale.
