# Adapter: herdmates

Herdmates hosts Claude's native team processes in Herdr panes (Claude only; Codex uses native). Claude owns tasks, messaging and shutdown. Do not close panes, sweep sessions, or assume obsolete `TeamCreate`/`TeamDelete` tools; tool schemas vary by version.

- `spawn`: `lookup` is the Agent tool without a name. `worker`: ask Claude to spawn a named native teammate with the `implementer` definition; put task and exact checkout in the prompt. Verify the returned agent ID and its Herdr pane; a shell pane without an agent is not a teammate.
- `message`: SendMessage to the teammate.
- `resume`: SendMessage to the teammate by its returned agent ID.
- `report`: the teammate replies with SendMessage to the lead.
- `wait`: end your turn; messages and task completions arrive as input.
- `collect`: the teammate's final message and native task result; verify files in the checkout. Sidebar states are presentation only.
- `stop`: ask Claude to shut the teammate down through its own handshake.
- `status`/`list`: `list` returns `[]` with a stderr note (hc-only); use the native task list and roster, `native` holds `pane`, `session`, `agent_status` and a read-only note.
