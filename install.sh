#!/bin/sh
# Link the agent-runtime shims into BIN_DIR and print the Claude and Codex wiring.
# Idempotent: re-running refreshes the links and changes nothing else.
set -eu

root=$(cd "$(dirname "$0")" && pwd)
bin_dir=${BIN_DIR:-$HOME/.local/bin}

command -v python3 >/dev/null 2>&1 || { echo "python3 (3.11+) is required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Python 3.11+ is required" >&2; exit 1; }

mkdir -p "$bin_dir"
for shim in agent-runtime agent-runtime-hook agent-runtime-mcp; do
  ln -sfn "$root/bin/$shim" "$bin_dir/$shim"
  echo "linked $bin_dir/$shim"
done

case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "note: $bin_dir is not on PATH" ;;
esac

cat <<EOT

Claude Code (public marketplace):
  /plugin marketplace add FBisca/agent-runtime
  /plugin install agent-runtime@agent-runtime

Claude Code (this clone, edits load in place):
  /plugin marketplace add $root
  /plugin install agent-runtime@agent-runtime

Codex, add to ~/.codex/config.toml:

  [mcp_servers.agent-runtime]
  command = "$bin_dir/agent-runtime-mcp"
  env_vars = ["HC_TERMINAL_ID", "HC_SCOPE", "HC_TERMINAL_SOCKET", "HERDR_PANE_ID", "HERDR_ENV", "HERDR_SOCKET_PATH", "HERDR_SESSION"]
  tool_timeout_sec = 600
  default_tools_approval_mode = "auto"
  [mcp_servers.agent-runtime.env]
  AGENT_RUNTIME_DISPATCH = "1"
  [mcp_servers.agent-runtime.tools.spawn]
  approval_mode = "approve"
  # Repeat approval_mode = "approve" for message, resume, stop, report and task.

  [[hooks.PreToolUse]]
  matcher = "^Bash\$"
  [[hooks.PreToolUse.hooks]]
  type = "command"
  command = "$bin_dir/agent-runtime-hook --cli codex"

  [[hooks.PreToolUse]]
  matcher = "^mcp__agent-runtime__.*\$"
  [[hooks.PreToolUse.hooks]]
  type = "command"
  command = "$bin_dir/agent-runtime-hook --cli codex"
EOT
