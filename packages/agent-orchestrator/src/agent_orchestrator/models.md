# The driver's model table

## Model classes

The driver's own class→model table. It answers a run whose `--models-file` has no column for its backend:
a Pi run reads the Pi column here. A column in the `--models-file` (the `## Model classes` table of
`~/.claude/CLAUDE.md`) wins over this one.

| Class | Pi |
|---|---|
| small | `minimax/MiniMax-M3.1-Flash` |
| medium | `minimax/MiniMax-M3.1-Flash` |
| large | `minimax/MiniMax-M3.1-Flash` |

The Pi column names a Pi model: the runtime spawns `pi --print --model <model>`. The Codex and Claude columns
stay in the canonical table (`~/.claude/CLAUDE.md`), so the driver reads them from there.
