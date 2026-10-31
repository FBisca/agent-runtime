# Plan: agent runtime follow-ups

Slug: `agent-runtime-followups`. Living spec: `docs/specs/agent-runtime.md`. Status: ARCHIVED 2026-10-31.

## Why now

The hardening plan's final gap review left four findings:
- A failed spawn can keep its linked task when a fast blocked/decision report landed first.
- Diagnostics check the store first, while hardening D3 said hc first.
- Tracked Claude settings hard-code `/Users/fbisca/...` for the `dotfiles` marketplace. This is the user's priority.
- The hook's deny of unparseable subagent input is untested.

The user also asked whether the runtime belongs in dotfiles or in its own repo. No PRD; the request and the archived plans are the sources.

## Sources

| Source | What it settles |
|---|---|
| User, 2026-10-30: "all, specially the 3" + repo-placement question | Scope: gaps 1–4, gap 3 first, placement evaluated |
| User, grilling round 1, 2026-10-30: "Yes to all" | D1–D9 below |
| [Hardening plan](2026-10-30-agent-runtime-hardening.plan.md) D3, D4, D9, D12, D14 | hc-first wording; every post-run spawn failure fails the assignment; every failure path releases tasks; version bump "so caches refresh" |
| [Tools plan](2026-10-30-agent-runtime-tools.plan.md) D4 | Plugin lives in dotfiles through a local marketplace; its T7/T8 were superseded by later plans |
| [Living spec](../specs/agent-runtime.md) R64, R69, R70–R72, R74, R75, R81 | Failed spawn inactive; task release; diagnostics fallback and authorization; subagent guard; tracked settings load the plugin; docs lists |
| Read-only gather, 2026-10-30 | `undo` → `fail(inactive=True)` updates only pending/working, so `_release` is skipped after a fast report (`dispatch_store.py:263-279`, `hc_tools.py:293-299`); `test_stop_failures.py:158` asserts that behaviour; `pre_tool_use.py:170-176` denies non-object subagent input with no test; diagnostics authorize in the store, then fall back to hc (`hc_tools.py:630-720`) |
| Gather: install mechanism | `~/.claude/settings.json` is a stow symlink to tracked `agents/.claude/settings.json`; the marketplace is not stowed; Claude loads `directory`-marketplace plugins in place from the checkout (running server `CLAUDE_PLUGIN_ROOT` = repo path), so cache copies under `~/.claude/plugins/cache/dotfiles/agent-runtime/` are unused; Codex runs `~/bin` relative links |
| Scratch-HOME run of Claude 2.1.314, 2026-10-30 | A `directory` marketplace with `path: "~/developer/dotfiles/agents"` in user settings registers at the expanded `$HOME` path on startup |
| Gather: repo-placement facts | 26 of 61 recent commits change the runtime together with its consumers (implementer agents, orchestrate skill, settings, Codex config); no consumer outside dotfiles; no CI; a `github` source runs a cached copy that needs version bumps and `plugin update` |
| Gather: other absolute paths | agent-fleet `install.sh` writes absolute hook paths into tracked settings; Codex writes machine state into tracked `config.toml`; `default.rules` holds literal argv patterns that Codex records |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | The runtime stays in dotfiles. Revisit on a second machine, a second user, or a consumer outside dotfiles; `git filter-repo` can extract it with history then | Own repo with a `github` marketplace source; own repo cloned at a fixed path with a `directory` source | Most runtime changes touch its consumers too, so one PR covers them. In-place loading applies edits at the next session. A `github` source runs a cached copy, needs a version bump plus `plugin update` per change, and lets Claude and Codex run different versions; a fixed-path clone keeps the absolute path | User Q1 |
| D2 | The `dotfiles` marketplace path becomes `~/developer/dotfiles/agents`. The settings test asserts the `~/` form and that no runtime entry in tracked settings holds an absolute home path | Settings template rendered by `install.sh`; `github` source; keep the absolute path | Claude expands `~` at startup (verified). A template fights the stow symlink and agent-fleet's merge into the same file | User Q2; scratch-HOME run |
| D3 | Other absolute paths stay out of scope: agent-fleet hook paths, the herdr hook script path, Codex machine state in `config.toml`, and `default.rules` (literal argv patterns, so `~` would not match) | Rewrite them here | agent-fleet re-writes its paths on install; Codex re-writes its state; rules match literal arguments | User Q3; gather |
| D4 | When spawn fails after `hc run` and the terminal is removed, the assignment fails in every state except complete, including after a fast blocked or decision report. Its unfinished linked task returns to `pending` with no owner, and the lead gets one `failed` message. When `hc run` times out or is cancelled and the terminal is discarded, the worker becomes inactive. A failed resume keeps today's rule: a report that landed first stands | Keep "report landed first wins" for spawn; leave timeout-path workers active | The terminal is gone, so no worker can finish the task (hardening D9/D12, R69). The resume rule exists because that worker is still alive | User Q4; gather |
| D5 | Diagnostics stay store-first: authorize in the store, then fall back to hc-only state with `delivery_error`. The spec intro records this order; hardening D3's "hc first" wording is superseded | Reorder to fetch hc first | Same calls when the store is healthy. Fetching hc before authorization would fetch data that must be discarded on a rejected call, a new risk to R71 | User Q5; gather |
| D6 | The hook keeps denying a native subagent's runtime call whose string `tool_input` is not a JSON object. Unparseable hook stdin keeps failing open. Both get tests | Allow unparseable subagent input; make stdin fail closed | Subagent restrictions must not be bypassable by malformed input; a broken hook payload must not block every tool | User Q6 |
| D7 | The README install section says Claude loads the plugin in place from the checkout, so edits apply at the next session with no `marketplace update`/`plugin update`. Version bumps are for the record only. Unused cache dirs are deleted, keeping the version `installed_plugins.json` references | Keep the update step; delete every cache dir | The step is a no-op for a `directory` marketplace (loading docs; running server path). The referenced cache dir may be checked by Claude, so it stays | User Q7; gather |
| D8 | Archive the `agent-runtime-tools` plan as superseded by the dispatch and hardening plans. Delete the ignored leftover `agents/.agents/skills/agent-runtime/` (only `__pycache__`), which fails `test_orchestrate` locally | Leave both | An APPROVED plan with stale TO-DO tickets misleads `/orchestrate`; the leftover dir breaks a local test | User Q8; gather |
| D9 | Merge each PR once converge, review and checks pass | Ask before each merge | Same policy as the hardening plan | User Q9 |
| D10 | Spawn cleanup treats a terminal as its own only when its own assignment bound it, so a concurrent spawn of the same name keeps its live worker and a respawned finished worker is still cleaned up; a store error in that check keeps the original error and still fails the assignment | Compare against the address read after the failure | The first fix discarded a concurrent spawn's live terminal; the second broke cleanup on name reuse | T2 review rounds, 2026-10-30 |
| D11 | When `hc run` times out after a fast complete report, the spawn error says the worker's result already landed | Plain timeout error | The result stays in the store, so the lead should read it rather than respawn | T2 review, 2026-10-30 |
| D12 | The spec intro drops the removed startup pointer and `agent-runtime contract` and names the SubagentStart notice and PreToolUse guard | Leave the stale sentence | The tools plan removed both; found by the docs review | T4 review, 2026-10-30 |

## Scenarios

- **S1** WHEN spawn fails after `hc run` and removes the terminal after the worker already reported blocked or decision, THEN the assignment is failed, the linked task returns to `pending` with no owner, and the lead receives one `failed` message.
- **S2** WHEN `hc run` times out or is cancelled after a fast report bound the worker's address, and the terminal is discarded, THEN the worker is inactive and messages or resumes addressed to it fail explicitly.
- **S3** WHEN a resume fails after the resumed worker's report already landed, THEN that report stands and the linked task stays with the worker's assignment.
- **S4** WHEN the tracked Claude settings are read, THEN the `dotfiles` marketplace is a `directory` source with path `~/developer/dotfiles/agents`, and no marketplace, plugin or allow entry for the runtime contains an absolute home path.
- **S5** WHEN Claude starts with a HOME whose `~/developer/dotfiles/agents` holds the marketplace and the tracked settings, THEN the `dotfiles` marketplace registers at that expanded HOME path.
- **S6** WHEN a native Claude or Codex subagent calls a runtime tool whose string `tool_input` is not a JSON object, THEN the hook denies it; the same input from a lead passes the hook.
- **S7** WHEN the hook receives stdin that is not valid JSON, THEN it emits no decision and exits successfully.
- **S8** WHEN users read the README agent-runtime install section, THEN it says Claude loads the plugin in place so edits apply at the next session, lists no `marketplace update` or `plugin update` step, and says version bumps are record-only.
- **S9** WHEN users read the spec intro, THEN it says diagnostics authorize against the dispatch store first and fall back to hc-only state with `delivery_error`.
- **S10** WHEN main is pulled into the installed checkout and Claude and Codex restart, THEN the runtime loads from the `~` marketplace path, the plugin suite and `test_orchestrate` pass in that checkout, and only the referenced plugin cache version remains.
- **S11** WHEN a timed-out or cancelled spawn cleans up while another spawn of the same name has bound its live terminal, or the ownership check hits a store error, THEN the other worker's terminal is kept and the failed spawn still fails with its original error.
- **S12** WHEN `hc run` times out after the worker's complete report already landed, THEN the spawn error says the worker's result already landed.

## Out of scope

- Moving the runtime to its own repo (D1 names when to revisit).
- agent-fleet hook paths, Codex `config.toml` machine state and `default.rules` (D3).
- Reordering diagnostics to hc first (D5).
- Committing the local `agents/.codex/config.toml` edits. They hold a plaintext API key, and the repo is public.

## Risks

- **Security:** D4 releases a task only when its terminal is gone. D6 keeps subagent input fail-closed. D5 keeps authorization before any hc read (R71). The local API key stays uncommitted.
- **Performance:** n/a. D4 adds at most one message per failed spawn.
- **Migration:** after merge, `~/.claude/settings.json` (a stow symlink) changes to the `~` path. `known_marketplaces.json` re-registers it at the same expanded path at the next start. The main checkout is rebased with its local `config.toml` edits kept.
- **Rollback:** revert the PRs. The absolute path still works on this machine. Deleted cache versions are unused by in-place loading.

## Seams

- Subprocess JSON-RPC harness with fake hc and temporary SQLite stores (`test_stop_failures.py`, `test_spawn_cleanup.py`, `test_task_links.py`): S1–S3.
- Tracked-settings test `test_settings.py`: S4.
- Opt-in live test (`AGENT_RUNTIME_LIVE=1`) with a scratch HOME: S5; its scratch settings hold only the tracked `dotfiles` marketplace and plugin entries, because the github marketplaces need network. Startup with the full settings is checked by S10.
- Hook unit tests `test_hooks.py`: S6, S7.
- Docs assertions in `test_activation.py`: S8, S9.
- Install check in the main checkout: S10.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Owner | Status |
|---|---|---|---|---|---|---|
| T1 | Portable marketplace: `~` path in tracked settings, a settings test with no absolute home path, an opt-in scratch-HOME startup test, and the README install section rewritten for in-place loading | S4, S5, S8 | none | agents/.claude/settings.json, agents/plugins/agent-runtime/tests/test_settings.py, agents/plugins/agent-runtime/tests/test_live_acceptance.py, agents/plugins/agent-runtime/tests/test_activation.py, README.md | agent | COMPLETE (PR47 104c7b5; converge/review PASS; live S5 passed; CI n/a) |
| T2 | A failed spawn never keeps its task: fail in every non-complete state once the terminal is removed, task release, one `failed` message to the lead, inactive worker on the timeout path; failed resume unchanged | S1–S3, S11, S12 | none | agents/plugins/agent-runtime/server/, agents/plugins/agent-runtime/tests/ | agent | COMPLETE (PR49 53b4f08; converge PASS; review blockers (name-reuse regression) fixed and re-checked; CI n/a) |
| T3 | Hook input tests: subagent non-object input denied, lead input passes, unparseable stdin fails open | S6, S7 | none | agents/plugins/agent-runtime/hooks/, agents/plugins/agent-runtime/tests/test_hooks.py | agent | COMPLETE (PR48 325612b; converge/review PASS; CI n/a) |
| T4 | Docs and housekeeping: spec intro records store-first diagnostics, plugin version 0.2.4 for the record, `agent-runtime-tools` plan archived as superseded | S9 | T1, T2, T3 | docs/specs/agent-runtime.md, docs/plans/2026-10-30-agent-runtime-tools.plan.md, agents/plugins/agent-runtime/.claude-plugin/plugin.json, agents/plugins/agent-runtime/server/mcp_server.py, agents/plugins/agent-runtime/tests/test_activation.py | agent | COMPLETE (PR50 174b8f4; converge/review PASS; CI n/a) |
| T5 | Install: pull main into the installed checkout keeping local `config.toml` edits, delete the leftover `agents/.agents/skills/agent-runtime/` and unused cache versions, restart sessions, run the plugin suite, `test_orchestrate` and the opt-in S5 test | S10 | T4 | none (local checkout and `~/.claude/plugins/cache/`) | Felippe | COMPLETE (user-approved install 2026-10-30: main rebased with local config kept, leftover skill dir and plugin cache 0.1.0–0.2.2 deleted, suite, test_orchestrate and live startup test passed; Claude restarted 2026-10-31 and loads the runtime 0.2.4 in place from the checkout) |
