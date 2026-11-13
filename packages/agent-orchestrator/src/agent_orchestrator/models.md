# Routing models with the driver

The driver reads no model table. Not the `## Model classes` table of `~/.claude/CLAUDE.md` (that table stays
the skills' own, and the driver ignores it), and not a table of its own: what each model class runs on comes from
a typed route file. `--models-file` is refused.

## The route file

A route file is JSON (or YAML when PyYAML is installed) with a route for each of `small`, `medium` and `large`:

```json
{
  "version": 1,
  "routes": [
    {"class": "small", "backend": "pi", "model": "minimax/MiniMax-M3.1-Flash", "effort": "low"},
    {"class": "medium", "backend": "pi", "model": "minimax/MiniMax-M3.1-Flash"},
    {"class": "large", "backend": "pi", "model": "minimax/MiniMax-M3.1-Flash"}
  ],
  "steps": [{"step": "reviewer", "backend": "codex", "model": "gpt-m", "effort": "high"}]
}
```

`effort` is optional: without it the size and difficulty matrix decides. `steps` is optional and names the
`implementer`, `reviewer` and `gaps` steps; the archive has no agent, so it has no route. A Pi model is
`provider/model`.

## Which file a new run reads

In order, the first one found is the only one read:

1. `--routes-file FILE`
2. the repository's `.orchestrate/routes.json`
3. the user's profile for the run's backend: `~/.config/orchestrate/routes.json` for Claude (the default),
   `routes-codex.json` for `--cli codex`, `routes-pi.json` for `--cli pi`

`--route CLASS=BACKEND/MODEL[@EFFORT]` and `--step STEP=BACKEND/MODEL[@EFFORT]` win over whichever file answered.
A run with no file, or whose selected file is invalid, stops before any worker is spawned and names the file it
expected; it never falls back to another file. The profile of one backend never answers another.

## Migrating

Write the models of your old table into a route file, one route per class, naming the backend each column was
for. A run that already started keeps the routes its ledger recorded, whatever the files now say; an explicit
argument that differs from the record stops the run, and a ledger with no complete record needs `--routes-file`,
which records the complete routes before the run resumes.
