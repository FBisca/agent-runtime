# Codex hook contract probe

Probed 2026-11-04 with `codex-cli 0.166.0` (model `gpt-6.1-sol`), one `codex exec` turn, in a throwaway
`CODEX_HOME` (copied `auth.json`, since deleted) with `HC_*`/`HERDR_*` unset and `XDG_STATE_HOME` in a temp
dir. Nothing under `~/.codex` was read for write or changed. Source notes: Codex `hooks` crate schemas and
`engine/{discovery,output_parser}.rs`, `events/{stop,session_start,user_prompt_submit}.rs`.

## Result: contract confirmed, adapter can be built

| Question | Finding | How |
| --- | --- | --- |
| Stop payload | `session_id`, `turn_id`, `transcript_path`, `cwd`, `hook_event_name:"Stop"`, `model`, `permission_mode`, `stop_hook_active` (bool), `last_assistant_message`. **No `background_tasks`.** | live |
| SessionStart payload | `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `model`, `permission_mode`, `source` | live (`source:"startup"`) |
| SessionStart `source` values | `startup`, `resume`, `clear`, `compact`, `fork` (same set as Claude) | schema enum + `SessionStartSource`; only `startup` seen live |
| UserPromptSubmit payload | `session_id`, `turn_id`, `transcript_path`, `cwd`, `hook_event_name`, `model`, `permission_mode`, `prompt` | live |
| Exit 0 with empty stdout | accepted ("hook: Stop Completed") | live |
| Exit 0 with `{}` | accepted ("hook: UserPromptSubmit Completed") | live |
| Block shape | `{"decision":"block","reason":"…"}` on stdout, exit 0; Codex logs "hook: Stop Blocked" and runs another model turn with the reason | live |
| `stop_hook_active` | `false` on the first Stop of a turn, `true` on the Stop after a block, same `turn_id` | live |
| SessionStart context | `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"…"}}` reaches the model (it echoed the token) | live |
| UserPromptSubmit context | same shape with `hookEventName:"UserPromptSubmit"` | output parser source (`additionalContext` field on that event), not live |
| `/new` and `/clear` as a source | a new thread in the same process; expected to report `clear` | source only, not live (needs the TUI) |
| Env seen by a hook | the full process env (no MCP allowlist), so `HC_*` reach hooks | live (with the vars unset by the probe) |

## Trust gate (relevant to hc-spawned workers)

- Hooks without a persisted `trusted_hash` do **not run**: in the first probe run no hook fired and `codex
  exec` printed nothing about it. In the TUI an untrusted hook raises the "Hooks need review" prompt, which is
  what D16 worries about for hc-spawned workers (S21, owner sign-off).
- `--dangerously-bypass-hook-trust` exists but was not used.
- Trust state is `[hooks.state."<abs config path>:<event>:<group>:<handler>"]` with `trusted_hash =
  "sha256:<hex>"`. The hash was reproduced against an existing entry in `~/.codex/config.toml`: SHA-256 of
  compact, key-sorted JSON of the normalised handler, e.g.
  `{"event_name":"pre_tool_use","hooks":[{"async":false,"command":"…","statusMessage":"…","timeout":5,"type":"command"}],"matcher":"^Bash$"}`
  (`event_name` in snake case; `matcher` omitted when there is none). That let the probe pre-trust its own
  throwaway hooks. The real hooks are trusted by the owner through `/hooks`.

## Design consequences

- Claude and Codex share one output shape for Stop (`decision:"block"`), SessionStart and
  UserPromptSubmit (`hookSpecificOutput.additionalContext`), so one entrypoint serves both.
- No D10 exemption on Codex: the Stop payload has no `background_tasks`. The 3-block budget bounds the guard;
  `stop_hook_active` is ignored (it is `true` on every re-check inside a blocked turn).
- The budget is reset by Codex UserPromptSubmit exactly as on Claude.

## S21 manual check (owner, after trusting the hooks)

1. Add the printed `[[hooks.*]]` blocks (`CODEX_LEAD_HOOKS=1 ./install.sh`) to `~/.codex/config.toml`, start
   Codex, run `/hooks` and trust the three new hooks.
2. From a Claude or Codex lead, `spawn(kind="worker", cli="codex", …)` with a trivial task.
3. Pass if the worker starts the task with no "Hooks need review" prompt and its `report` arrives. Fail: run
   `hc get <name>` and check the terminal for the review modal; untrust or remove the hooks.
