# Adapter: hc

`$HC_TERMINAL_ID` is your address; terminals you start are your children.

- `spawn`: `lookup`: Claude's Agent tool without a name, or Codex's MultiAgent V2 spawn tool. `worker`: always `--bg`, a unique name (a-z 0-9 - _, max 32), task inside `run`.
  - Claude: `hc run --bg --name <n> --cwd <dir> claude --agent implementer "<task>"`
  - Codex: `hc run --bg --name <n> --cwd <dir> codex --no-daemon "<developer_instructions of ~/.codex/agents/implementer.toml + task>"`.
  - If `first_message.sent` is false, the task was not sent: `hc read <n>`; for a trust dialog `hc keys <n> down enter`, then send it. No blind resend.
  - Exact scope; report via `hc send $HC_TERMINAL_ID`.
- `message`: `hc send <n> "<text>"`. Never `--force` past a permission dialog.
- `resume`: `hc send --wait <n> "<task>"`, or `hc send` then `wait`.
- `report`: `hc send <parent> "<result or blocker>"`.
- `wait`: `hc wait --children any|all`, `hc wait <n>`. Message or timeout wake-ups are not completion.
- `collect`: `hc read --lines N <n>`, plus result files.
- `stop`: `hc kill <n>`, then `hc rm <n>`; own terminals only.
- `status`/`list`: `list` is hc-only. Wrap `hc get <n>` (`status` is an object `{app, state}`; `native` keeps address, name, state, status, turn, exit_code, reason, argv) and `hc ls` (`status` is a string; it lists the user's other terminals too, so filter to your children by `hc get` parent). `idle`, `working`, `done`, `blocked` map directly; finished is `exited`; `error` or anything else is `unknown`. A `blocked` worker's `next` includes the raw `hc keys` row as a gap.
