# method-cost

What a ticket costs and how long it takes, driven by the orchestrator vs run manually as
implementer + independent-review subagents in one Lead session. A dated measurement, not a gate:
nothing here is a rule, a budget or a ceiling — it is evidence about one day, kept so the next
measurement has a baseline.

## The first record: `2026-11-19-mcp-writes.json`

A natural experiment in a private production TypeScript monorepo. One approved plan, one day, one
owner. The Lead session ran the plan's first two tickets through the orchestrator (09:32–12:16
UTC), then ran three follow-up tickets manually — a fresh implementer subagent and an "independent
review" subagent per ticket, no orchestrator (15:02–21:01). Same repo, same feature, same plan,
same person. Tickets are opaque labels: O1/O2 orchestrated, M1–M3 manual.

| | Orchestrated | Manual |
|---|---|---|
| Tickets | 2 | 3 |
| Merged at cutoff | 2/2 | 2/3 — M1 open, rework still running |
| Cost | **$9.65** | **$96.22** (lower bound) |
| Per ticket | $4.83 | $32.07 |
| Per added line | $0.0082 | $0.0364 |
| Wall | 164 min, both merged | 358 min elapsed, 259 active |
| Review cost | $1.57 total | $20.46 total |
| Escalations / interventions | 0 / 0 | owner present throughout |
| Diff | +1176 / −83 | +2644 / −369 |

**~6.6× per ticket, ~4.4× per added line.** The drivers, in order:

1. **Model routing.** The orchestrator routed medium tickets to sonnet workers *and* reviewers.
   The manual Lead picked opus for 2/3 implementers and every reviewer; those opus implementers
   burned 21–34M cache-read tokens in long loops — 2–3× the tokens of the sonnet workers at ~3×
   the rate. M2 alone cost $20 to implement vs $2.44 for the comparable orchestrated O2.
2. **Lead tax.** Manual coordination (PRs, issue tracker, choreography) cost $2–2.8 of opus
   main-thread per ticket; the driver does that plumbing, and the whole orchestrated run's Lead
   cost $1.98.
3. **Stale-main rework.** M2/M3 merged under M1; the rework cost $25.22 and was still not clean at
   the cutoff. The orchestrator's update-branch and scope rules exist for this.
4. **Review depth was comparable** — both methodologies caught real bugs (wrong `required` flags
   on five resources; seven findings resolved in a fix round; 8+8 blocking findings). The manual
   flow paid opus prices for a sonnet-level catch rate.

Shared planning (~$21: one opus planning session, one sonnet gather at $1.13) amortizes over all
five tickets and does not change the ratio. The gather is ~1% of the feature's cost; its judgment
(settled/open decisions, seams a test can drive) is the plan's only evidence, so it stays medium.

## Second record: `2026-11-19-planning-vs-lead.json`

Where the money goes in the plan-then-orchestrate workflow: a fresh session to plan, a fresh
session to Lead the driver. A private Rust repo, three plans executed plus one planned only,
Claude and Pi side by side. Execution = workers + reviewers (sonnet).

| Plan | Planning | Execution | Lead |
|---|---|---|---|
| P1 | $7.51 opus grill/write/approve + $0 pi pre-plan | $28.36 | **$15.02 opus** (incl. owner chat) |
| P2 | $0.79 gather | $31.09 | **$12.81 opus** (wakes + firefighting + amend) |
| P3 | unattributed (plan written earlier) | $6.65 | **$0 pi token-plan** — $25.97 at opus rates |
| P4 (planned only) | $2.60 opus + $0.55 pi metered | — | — |
| old loop, 3 plans | — | — | **$34.33 opus** overnight vs $0.78 pi deepseek-flash |

- **Planning is not the cost driver:** 2.5–26.5% of execution cost. It tracks decision novelty,
  not tokens — the $21 greenfield MCP plan in the first record is the outlier, not the norm.
- **The opus Lead is the sink.** Every wake and every owner message re-reads the whole Lead
  context at opus rates. The same driver with an absent owner cost $1.98 (first record); with a
  chatty owner, $15.02.
- **The harness choice on the Lead is the biggest lever:** P3's Lead burned 37.9M cache-read +
  196k output tokens — $0 marginal on a token plan, $25.97 at opus rates.
- Rules that follow: Lead on a token-plan or cheap model; don't talk to the Lead mid-run (ask the
  workbench); keep planning as a fresh session with opus only for the grill/write/approve pass.

## Method

- **Orchestrated workers:** the run's `attempts.jsonl` token totals per attempt (authoritative at
  attempt end). **Reviewers:** the last `usage` snapshot per reviewer assignment (may undercount
  the final poll interval). **Lead:** the Lead session's main-thread assistant usage, windowed
  09:31–12:33.
- **Manual:** each subagent transcript's assistant usage, attributed by label; the Lead main
  thread split by spawn-time windows. All usage deduped by message id.
- **Rates:** USD per M input tokens, solved from Claude Code's own `cost-state` snapshots
  (`costUSD` against token counts) with standard multipliers — output 5×, cache read 0.1×, cache
  write 1.25×: opus ≈ 4.978, sonnet ≈ 1.73, haiku = 0.1. ±5%.
- **Wall:** orchestrated = run start to finish (includes CI and merge); manual = spawn to the
  later of implementer/review end per ticket, summed; merge waits noted, not hidden.
- **Freeze:** the Lead session was still live at measurement time, so the raw usage lines are
  frozen in `data/` as gzipped jsonl, cut at `manual.as_of` (minute granularity). Message ids are
  sha256-truncated and timestamps minute-precision: enough for dedup and windows, no correlatable
  handles. Later activity in that session cannot move the figures, and they reproduce on any
  machine.

## Reproduce

```sh
python3 evals/method-cost/extract.py            # recompute from data/ and check every record
python3 evals/method-cost/extract.py --print    # show the recomputed tables
python3 evals/method-cost/extract.py --freeze   # rebuild data/ from live logs (measuring machine only)
```

The check exits non-zero on any figure that drifts beyond ±$0.06 / ±2 min. `--freeze` needs the
private sources file named in the record (`data.local_sources`): repo keys, session ids and the
label mapping, which never enter this repository. The frozen data and the record carry no repo
names, PR numbers, issue ids or paths; message ids are hashed.

## Caveats

- n=2 vs n=3; the ticket mix differs (M3 is the largest diff). Per-line normalization is
  approximate, not a controlled trial.
- M1's rework and re-review were still running at the 21:01Z cutoff: the manual cost is a lower
  bound and its merge wall is not counted.
- Manual Lead windows include chores the driver automates. That asymmetry is the methodology's
  point, not a measurement error.
- One repo, one day, one owner. Read the ratio as a direction, not a constant.
