# Adapter: native

The agent's own subagent tools; `status` has `native: {}`; its `next` rows point to the return value or SendMessage.

## Claude

- `spawn`: `lookup` is the Agent tool without a name. `worker` is a named teammate, `subagent_type` = role (`implementer`); task and exact checkout in the prompt.
- `message`: SendMessage to the named agent.
- `resume`: SendMessage to the returned agent ID.
- `report`: a subagent returns its result as its final message.
- `wait`: the Agent tool returns on finish; for background agents end your turn and let notifications arrive.
- `collect`: the Agent result; verify files in the checkout.
- `stop`: let it finish, or use the runtime's stop tool.
- `status`/`list`: `list` returns `[]` with a stderr note (hc-only); use the runtime's agent list.

## Codex / MultiAgent V2

`spawn` maps `lookup` and `worker` to MultiAgent V2 (a worker is a named, long-lived agent). Keep runtime IDs in the ledger. `task_name` is only a label: select the role with `agent_type: implementer` and `fork_turns: none` when available. Existing sessions may keep old role definitions; report a mismatch. If role or model selection is missing, report the limitation instead of using an inherited expensive model.

- `message`: `send_message` steers a running worker.
- `resume`: `followup_task` starts another turn.
- Codex workers stay visible in Codex's own agent UI. Never start a second worker just to display an existing one.
