# agent-eval list: which cases exist here, and what a judge said about them

Two eval batches were run on this machine and the only index was a README written by hand beside the
data. `agent-eval` has `capture` and `add-label`, so answering "which cases do I have, and what did
they score?" means `ls` plus opening every `labels/agent-judge.json` by hand — which is how a batch of
ten became eleven without anyone noticing. A pointer in this repository is the wrong fix: the cases are
per-machine, private and uncommittable. The index belongs in the tool.

- **S1** WHEN `agent-eval list` runs THEN it prints one line per case under `<home>/cases` with the slug, the capture date, the session count, the label kind, the verdict and the score, newest first (by `captured`, then slug); a case with no label prints `-` for the verdict and the score, and a directory that holds no `manifest.json` is not a case and is not listed.
- **S2** WHEN `agent-eval list --json` runs THEN it prints the same rows as one JSON array on stdout, with `null` verdict, score and judge for an unlabelled case, and it reads no transcript, so listing a 5 MB case costs what listing an empty one does.
- **S3** WHEN `<home>` or its `cases/` directory does not exist THEN `agent-eval list` prints nothing (`[]` with `--json`), exits 0 and creates nothing.

| # | Decision | Alternative | Why | Source |
|---|---|---|---|---|
| D1 | The index is a subcommand of the tool, not a file in a repository | a `docs/` pointer to `~/.local/state/agent-evals` | the path is per-machine and the data is private (0700/0600, verbatim transcripts); a repo pointer breaks on a fresh checkout | Felippe, in session |
| D2 | The default output is one aligned line per case; `--json` is the machine shape | JSON only, or a table only | a person reads the table, a batch script reads the array; one command serves both without a second binary | assumption |
| D3 | Newest first: `captured` descending, then slug | the filesystem's order, or slug order | a batch is judged against the cases it just made; slug order scatters a day across the list | assumption |
| D4 | An unlabelled case is listed with `-`, not hidden | list only labelled cases | an unjudged case is the one worth noticing; hiding it is how a sample reaches ten with nine judged | assumption |
| D5 | A directory without a `manifest.json`, or a manifest that does not parse, is skipped | list it with unknown fields | `capture` writes a valid manifest before the case exists; anything else under `cases/` is a person's stray directory, not a case | assumption |

| Ticket | What it delivers | Scenarios | Paths | Status |
|---|---|---|---|---|
| T1 | the `list` subcommand (text and `--json`), its spec requirement and Coverage row, and the CLI's own help | S1–S3 | `packages/agent-eval/src/agent_eval/listing.py`, `cli.py`, `packages/agent-eval/tests/test_list.py`, `docs/specs/agent-eval.md` | COMPLETE 2026-11-11 |
