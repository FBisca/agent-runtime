# Plan: lead delivery gaps (hc exit notes, unread reports, startup dialogs)

Slug: `lead-delivery-gaps`. Living spec: `docs/specs/agent-runtime.md`. Status: ARCHIVED 2026-11-04 (approved 2026-11-03 by Codex as owner, standing in for Felippe).

## Why now

Two live-run bugs hit the lead side of dispatch. #5: after `wait(all)` already returned a worker's `complete`, hc typed `<hc>t5k judge-codex exited (exit 0). Read: hc read t5k</hc>` into the lead pane twice, as fake user turns. #3: a lead that `resume`d a blocked worker and armed no new `wait(all)` never saw three later `complete` reports until the user asked. #7: a Codex worker spawned into an untrusted folder sits at "Trust this folder?" forever while every `wait` returns `timeout` with `runtime_state=unknown`. All three break the spec's promise that reports reach the lead as tool results, never as injected input (R34), and that the lead keeps one `wait(all)` per work period (R57).

## Sources

| Source | What it settles |
|---|---|
| GitHub issue #5 | The symptom, repro and expectation: no exit note for an already-complete worker, never as user input, at most once |
| GitHub issue #3 | The symptom; four options (push, sticky wait, nudge in results, docs); the `unknown task` papercut is separate |
| `docs/specs/agent-runtime.md` R34, R47, R56/R81, R57, R68, R83 | Reports arrive as tool results, never injected; idle recipients get messages on their next call; tool descriptions ≤400 words; one `wait(all)` per work period; exit before complete → one `failed`; read tools piggyback only with a receipt |
| `docs/plans/2026-10-30-agent-runtime-dispatch.plan.md` D1, D2, D10, D12, D13, line 79 | Pull-only delivery; hc injection rejected; no hc upstream changes; "this plan does not promise unsolicited model wakeups" |
| hc binary (`herdr-core`, read with `strings`) | The `<hc>… exited …</hc>` note is hc's own parent note, typed for events the parent has not "seen" (read/get/wait/send/keys/kill); `hc ls` does not count as seen; `hc run --no-notify` turns notes off per terminal |
| `server/hc_tools.py:277` | Spawn runs `hc run --bg --name N --cwd D --parent CALLER --json <program>` with no `--no-notify` |
| `server/hc_tools.py:685-744` (`call_dispatch`) | Non-read calls already attach pending `inbox` (incl. worker reports); the gap is reports landing after the call |
| `server/dispatch_events.py` | No per-recipient record of a pending wait exists |
| GitHub issue #7 | Trust-dialog hang: symptom, live repro, expectation (actionable next step, no endless wait) |
| Live probe 2026-11-03 (codex 0.166, untrusted tmp dir) | Stuck worker: `hc get` shows `status:null, reported:false, status_ms:null, turn:0`, `created_ms` set; `hc ls` rows lack `reported`/`created_ms`; screen captured |
| `server/hc_tools.py` `observe` in `wait`; `server/dispatch_wait.py` `reason` | Null hc status maps to `unknown`, treated as still working; only `blocked` gives attention; hc `error` also maps to `unknown` |
| Tools plan D21; `hooks/pre_tool_use.py` `HC_ASKED` | A lead's raw `hc keys` gets a PreToolUse `ask`, so a person approves clearing a worker's trust dialog |
| Grilling 2026-11-03 (Codex as owner, Q1–Q15) | D1–D15 below; concerns: describe the limit and promise no automatic delivery; exit precedence; `unreported` stays generic |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | Every `hc run` that `spawn` issues carries `--no-notify` | Document `notes = false` in user config; call `hc get` on exit to mark it seen | Per spawn, no user config, matches R34 (no injected user messages) | Grilling Q1 |
| D2 | Accept losing hc's "is blocked" and turn-ended notes; blocked workers still surface as `attention` through `wait` | Keep notes for blocked only | hc offers no per-event switch; #3 nudges the lead to re-arm `wait`, and reports arriving afterward still need a new `wait` | Grilling Q2 |
| D3 | No runtime-initiated push into the lead session | Push a notification through hc typing or a host channel | Contradicts D1/R34 and the no-unsolicited-wakeups rule | Grilling Q3, dispatch plan D1, line 79 |
| D4 | No sticky `wait(rearm=true)` | Server keeps a wait armed across returns | One MCP call returns once; re-arming is the model's job | Grilling Q4 |
| D5 | A lead's `spawn`, `resume` and `message` results carry `unfinished: N` (the count of the lead's unfinished assignments, same set as `wait`'s `unfinished`) and `next: "call wait(target=all) to receive their reports"` when N > 0 | Add a `no wait pending` flag | The flag needs new per-recipient wait tracking; the count is store-only | Grilling Q5 |
| D6 | No new host hook (UserPromptSubmit/Stop) surfacing unread counts in this plan | Add the hook now | New hook surface and Codex parity unknown; follow-up | Grilling Q6 |
| D7 | `wait` and `resume` descriptions say delivery is pull-only and the lead re-arms `wait(all)` after `spawn`/`resume`; they promise no automatic delivery; all descriptions stay ≤400 words | Leave descriptions as they are | Makes the gap visible at the call that creates it | Grilling Q7 + Codex concern |
| D8 | One plan, independent tickets, one PR each; no version bump in any | One PR for all; chained tickets | The issues touch different code; release is separate | Grilling Q8, Q15 |
| D9 | Detect, don't bypass: a live worker with no status report ever (`reported:false`, `status_ms:null`, `turn:0`) more than a grace period after `created_ms` makes `wait` return `attention` with `runtime_state: unreported` | Pre-trust the cwd before spawn; fail spawn after checking each CLI's trust config | Auto-trust lets a folder's hooks and settings run without consent, and writing `~/.claude.json` races running Claude; trust rules drift between CLI versions | Grilling Q9, issue #7 |
| D10 | Grace period: fixed 30 s from `created_ms`, a module constant | Env var or config | Nothing needs tuning yet | Grilling Q10 |
| D11 | The `unreported` next step says the worker never reported a status and may be at a folder-trust or startup dialog: collect it, ask the user, then clear it with the user-approved hc keys or stop it. `status(name)` shows the same state and next rows. It never claims a confirmed trust block | Name it `trust_dialog` | The same signal covers trust, hook-trust, login and update prompts | Grilling Q11, Q12 + Codex concern |
| D12 | No trust-specific screen marker | A `trust` key in `markers.json` | Generic detection already covers it | Grilling Q12 |
| D13 | hc status `error` maps to attention, not `unknown` | Keep `unknown` | Same hang class | Grilling Q13 |
| D14 | `observe` keeps `hc ls`, and runs `hc get <name> --json` only for live members whose ls status is null, to read `created_ms`/`reported`. It may skip that get while the assignment's own store start time is under 30 s old | Run `hc get` for every member each poll; threshold from ls alone | `hc ls` lacks `created_ms`; this bounds the cost to stuck candidates | Grilling Q14 (Codex override) |
| D15 | Exit precedence stays: an exited, interrupted or replaced never-reported worker is `failed` (R68), never `unreported` | Unreported wins | Exit is the stronger fact | Codex concern |
| D16 | A worker that spawns its own children is a lead of its own team: its `spawn`/`resume` results and its `message` to its own children get the nudge; a worker messaging its own lead or a peer does not | Nudge only top-level leads | S2 says "a lead"; otherwise issue #3 recurs one level down | PR review 2026-11-03 |
| D17 | The nudge count is exactly `wait(all)`'s membership (snapshot without acking, not complete): stopped or failed workers whose failure was already delivered drop out | Count every current non-complete assignment | Otherwise the lead is told to `wait` on workers that are gone and `wait` returns `empty` | PR review 2026-11-03 |
| D18 | `status` and `wait` both show hc `error` as `error` (not `unknown`), with a next hint; R12's state list gains `error` | `status` keeps `unknown` | One state per fact across tools; review of D13 | PR review 2026-11-03 |

## Scenarios

- **S1** WHEN `spawn(kind=worker)` starts a claude or codex worker, THEN the `hc run` argv carries `--no-notify` before the program.
- **S2** WHEN a lead with N > 0 unfinished assignments gets a `spawn`, `resume` or `message` result, THEN the result carries `unfinished: N` and `next` telling it to call `wait(target=all)`; the count includes the assignment that call just created.
- **S3** WHEN a caller with no unfinished assignments of its own as lead (a lead whose workers all finished, or a worker messaging its lead or a peer) gets a `message` result, THEN the result carries no `unfinished` or `next` field.
- **S4** WHEN `tools/list` runs, THEN the `wait` and `resume` descriptions say delivery is pull-only and that reports landing after a call need a new `wait(all)`, and all descriptions together stay ≤400 words.
- **S5** WHEN a live worker has never reported a status (`reported:false`, `status_ms:null`, `turn:0`) and its `created_ms` is more than 30 s old, THEN `wait` returns `attention` for it with `runtime_state: unreported` and a next step naming collect, asking the user, user-approved hc keys, or stop.
- **S6** WHEN a never-reported worker is younger than 30 s, THEN `wait` keeps treating it as working, with no attention.
- **S7** WHEN a worker that has reported a status at least once later shows a null status, THEN it is not flagged `unreported`.
- **S8** WHEN a never-reported worker's terminal has exited, been interrupted or replaced, THEN `wait` returns its one `failed` message (R68), not `unreported`.
- **S9** WHEN hc reports a worker's status as `error`, THEN `wait` returns `attention` for it instead of a timeout.
- **S10** WHEN `status(name)` targets a never-reported worker past the grace period, THEN it shows state `unreported` with the same next rows as S5.
- **S11** WHEN `wait` polls a team whose members all have a non-null hc status, THEN it runs no `hc get`.

## Out of scope

- Push delivery, sticky wait, a `no wait pending` flag and a host hook (D3, D4, D5, D6).
- Changing hc upstream or its config (`notes = false`).
- The `unknown task for this team` error on `spawn`/`resume` with a plan-local `task_id` (issue #3 notes it as separate).
- Why hc typed the note twice: moot once notes are off.
- Pre-trusting folders or answering the trust dialog automatically (D9).
- A trust-specific screen marker (D12).

## Risks

- **Security:** no new input paths or permission changes. T3 reports the dialog and never answers it; clearing it stays behind the user-approved `hc keys` ask (D21).
- **Performance:** T3 adds `hc get` only for null-status candidates (D14). T2 adds one count query on the lead's assignments per `spawn`/`resume`/`message`; indexed by team, negligible.
- **Migration:** n/a; no schema change. Workers spawned before the change keep hc notes until they exit.
- **Rollback:** revert either PR on its own; each is independent.

## Seams

- `tests/test_tools.py`: real MCP server over stdio with a fake `hc` on PATH logging argv (S1; the exact `argv[:9]` assertion at line ~97 changes).
- `tests/test_peer_dispatch.py`: dispatch results over stdio for `spawn`/`resume`/`message` (S2, S3).
- `tests/test_activation.py`: tool descriptions and the 400-word budget (S4).
- `tests/test_event_wait.py`: wait loop with a mocked `observe` (S5–S9); `tests/test_tools.py` fake hc `ls`/`get` replies for observe's `hc get` gating (S11); `tests/test_status.py` (S10).

## Tickets

Each ticket adds its own scenarios to the living spec under pre-assigned R-numbers, so the three PRs never collide: T1 adds R96 (S1), T2 adds R97–R99 (S2–S4), T3 adds R100–R106 (S5–S11).

| # | What it delivers | Satisfies | Blocked by | Paths | Owner | Status |
|---|---|---|---|---|---|---|
| T1 | Spawned workers never type hc notes into the lead (#5) | S1 | none | docs/specs/agent-runtime.md, server/hc_tools.py, tests/test_tools.py, tests/test_spawn_cleanup.py | agent | COMPLETE (PR #8; converge PASS; review no blocking; CI green; unittest 293 OK) |
| T2 | Lead results nudge to re-arm `wait(all)` and descriptions state pull-only delivery (#3) | S2, S3, S4 | none | docs/specs/agent-runtime.md, server/hc_tools.py, server/dispatch_store.py, server/mcp_server.py, README.md, tests/test_peer_dispatch.py, tests/test_dispatch.py, tests/test_tools.py, tests/test_activation.py | agent | COMPLETE (PR #10; converge PASS; review: 1 blocking fixed in round 2, D16/D17; CI green; unittest 297 OK) |
| T3 | A worker stuck before its first status report surfaces as `attention`/`unreported` instead of an endless timeout (#7) | S5–S11 | none | docs/specs/agent-runtime.md, server/hc_tools.py, server/dispatch_wait.py, server/agent_runtime.py, tests/test_event_wait.py, tests/test_tools.py, tests/test_status.py, tests/test_dispatch.py, tests/test_agent_runtime.py | agent | COMPLETE (PR #9; converge PASS; review no blocking, 2 follow-up rounds; CI green; unittest 314 OK) |
