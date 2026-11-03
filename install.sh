#!/bin/sh
# Link the agent-runtime shims into BIN_DIR and print the Claude and Codex wiring.
# Idempotent: re-running refreshes the links and changes nothing else.
set -eu

self=$0
while [ -L "$self" ]; do
  dir=$(cd "$(dirname "$self")" && pwd)
  self=$(readlink "$self")
  case $self in /*) ;; *) self=$dir/$self ;; esac
done
root=$(cd "$(dirname "$self")" && pwd)
bin_dir=${BIN_DIR:-$HOME/.local/bin}

command -v python3 >/dev/null 2>&1 || { echo "python3 (3.11+) is required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Python 3.11+ is required" >&2; exit 1; }

mkdir -p "$bin_dir"
for shim in agent-runtime agent-runtime-hook agent-runtime-mcp; do
  if [ -e "$bin_dir/$shim" ] && [ ! -L "$bin_dir/$shim" ]; then
    echo "refusing to overwrite $bin_dir/$shim: not a symlink" >&2
    exit 1
  fi
done
for shim in agent-runtime agent-runtime-hook agent-runtime-mcp; do
  ln -sfn "$root/bin/$shim" "$bin_dir/$shim"
  echo "linked $bin_dir/$shim"
done

case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "note: $bin_dir is not on PATH" ;;
esac

# TOML basic-string escaping for paths.
toml_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
mcp=$(toml_escape "$bin_dir/agent-runtime-mcp")
hook=$(toml_escape "$bin_dir/agent-runtime-hook")

cat <<EOT

Claude Code (public marketplace):
  /plugin marketplace add FBisca/agent-runtime
  /plugin install agent-runtime@agent-runtime

Claude Code (this clone, edits load in place):
  /plugin marketplace add $root
  /plugin install agent-runtime@agent-runtime

Codex, add to ~/.codex/config.toml:

[mcp_servers.agent-runtime]
command = "$mcp"
env_vars = ["HC_TERMINAL_ID", "HC_SCOPE", "HC_TERMINAL_SOCKET", "HERDR_PANE_ID", "HERDR_ENV", "HERDR_SOCKET_PATH", "HERDR_SESSION"]
default_tools_approval_mode = "auto"
tool_timeout_sec = 600

[mcp_servers.agent-runtime.env]
AGENT_RUNTIME_DISPATCH = "1"
EOT

for tool in spawn message resume stop report wait status list collect task; do
  printf '\n[mcp_servers.agent-runtime.tools.%s]\noutput_token_limit = 524288\n' "$tool"
  case $tool in
    spawn|message|resume|stop|report|task) printf 'approval_mode = "approve"\n' ;;
  esac
done

cat <<EOT

[[hooks.PreToolUse]]
matcher = "^Bash\$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "\\"$hook\\" --cli codex"
timeout = 5
statusMessage = "Checking hc command"

[[hooks.PreToolUse]]
matcher = "^mcp__agent-runtime__.*\$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "\\"$hook\\" --cli codex"
timeout = 5
statusMessage = "Checking agent-runtime caller"
EOT
