#!/bin/sh
# Link the agent-runtime shims into BIN_DIR, and wire the components the user names.
# Idempotent: re-running refreshes the links and changes nothing else.
#
# The components are runtime, orchestrator, claude, codex, pi and herdr. The runtime is the base: the
# orchestrator and every harness adapter need it, so it always comes along with them.
#
# Run from a checkout without arguments it keeps the original behaviour: link the shims and print
# the Claude and Codex wiring. With --components only the named components are installed, and what
# they need. Streamed through a shell (`curl ... | sh -s -- ...`) the script is stdin and there is no
# checkout: it asks for the components on the terminal when there is one, and otherwise needs them
# named with --components.
#
# A streamed run installs one pinned release. It downloads the release's payload over https, checks
# it against its SHA-256 checksum before anything in it runs, stages it under the install directory
# and hands over to the installer inside it. A moving ref (main, a branch, latest) is refused: only
# a version tag names something that cannot change under the installer. The assets and their
# checksums are built by scripts/build-release.sh.
set -eu

# The release this installer belongs to. scripts/build-release.sh stamps it into the published
# installer asset; the source installer leaves it empty.
release_pin=

self=$0
while [ -L "$self" ]; do
  dir=$(cd "$(dirname "$self")" && pwd)
  self=$(readlink "$self")
  case $self in /*) ;; *) self=$dir/$self ;; esac
done
# A streamed script has no file of its own: $0 is the shell's name, and there is no checkout beside
# it to install from. Guessing one from the working directory would install whatever is there.
streamed=1
root=
if [ -f "$self" ]; then
  candidate=$(cd "$(dirname "$self")" && pwd)
  if [ -f "$candidate/bin/agent-runtime" ]; then
    streamed=0
    root=$candidate
  fi
fi

if [ -z "${HOME:-}" ]; then
  echo "HOME is not set: the installer needs a home directory to install into" >&2
  exit 1
fi
bin_dir=${BIN_DIR:-$HOME/.local/bin}
release_base=${AGENT_RUNTIME_RELEASE_URL:-https://github.com/FBisca/agent-runtime/releases/download}

command -v python3 >/dev/null 2>&1 || { echo "python3 (3.11+) is required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Python 3.11+ is required" >&2; exit 1; }

# The documented shims: the runtime's four entry points.
shims='agent-runtime agent-runtime-hook agent-runtime-mcp agent-runtime-profile'

# The install manifest: what this installer owns, so an uninstall removes exactly that and
# nothing else. It lives in the user's data directory, never in the checkout.
install_home=${AGENT_RUNTIME_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/agent-runtime}
manifest_path=$install_home/install-manifest.json
tab=$(printf '\t')
# Herdr reads its mods and layouts from here. The component adds to them and never edits Herdr's
# own settings, so the layout it adds is shown only when the person switches to it.
herdr_home=${HERDR_APP_HOME:-$HOME/.config/herdr-app}
herdr_mod=$herdr_home/mods/agent-workbench
herdr_layout=$herdr_home/layouts/workbench.json

usage() {
  cat <<EOT
usage: install.sh [install|upgrade|uninstall] [--components <list>] [--release <tag>] [--yes] [--dry-run]

commands:
  install     link and configure the named components, and what they need (the default)
  upgrade     install the named components again, from the release this checkout is
  uninstall   remove what the named components own, and nothing else

components:
EOT
  for component in $component_order; do
    printf '  %-13s %s\n' "$component" "$(component_summary "$component")"
  done
  cat <<EOT

options:
  --components <list>  the components to install, comma separated (the runtime comes with them)
  --yes                do not ask for confirmation: the headless run
  --dry-run            print what would be done, with the versions and paths, and change nothing
  --release <tag>      the release a streamed run installs, a version tag like v1.2.3 (a published
                       installer already names its release); a moving ref is refused
  --sha256 <hex>       the SHA-256 of the release payload, taken from somewhere other than the
                       download itself; the payload is installed only if it matches
  --version            print the release of this installer

The orchestrator and every harness adapter need the runtime, so it is installed with them; the
Herdr mod reads the orchestrator, so it brings that along.
Run from a checkout without --components the original behaviour runs: link the shims and print
the Claude and Codex wiring. Streamed through a shell there is no checkout: the installer asks
for the components on the terminal (/dev/tty) when there is one, and otherwise needs
--components, for example:
  curl -fsSL <installer url> | sh -s -- --components runtime,pi --yes
A pipe runs what the server sends before anything can check it. To verify first, download the
release's install.sh and SHA256SUMS, check them against a checksum you got elsewhere, then run
sh install.sh --components ... from the file.

environment:
  BIN_DIR                    where the shims are linked (default ~/.local/bin)
  HERDR_APP_HOME             where the herdr component installs the mod and layout (default ~/.config/herdr-app)
  AGENT_RUNTIME_INSTALL_DIR  where the install manifest lives (default ~/.local/share)
  AGENT_RUNTIME_RELEASE      the release this install must be (same as --release)
  AGENT_RUNTIME_SHA256       the trusted payload checksum (same as --sha256)
  AGENT_RUNTIME_RELEASE_URL  where releases are downloaded from, https only
                             (default https://github.com/FBisca/agent-runtime/releases/download)
  AGENT_RUNTIME_TTY          the terminal the menu is asked on (default /dev/tty)

exit codes: 0 done, 1 refused or declined (a failed download or check included), 2 the command line was wrong
EOT
}

# The component registry: the components the installer knows, in the order it handles them.
component_order='runtime orchestrator claude codex pi herdr'

# What a component is, for the menu and the usage text.
component_summary() {
  case $1 in
    runtime) printf 'the runtime and its documented shims; every other component needs it\n' ;;
    orchestrator) printf 'the orchestration engine\n' ;;
    claude) printf 'the Claude Code plugin wiring\n' ;;
    codex) printf 'the Codex shims and config wiring\n' ;;
    pi) printf 'the Pi integration\n' ;;
    herdr) printf 'the agent-workbench mod for Herdr and its opt-in workbench layout\n' ;;
  esac
}

# What each component needs installed with it: the runtime is the base, so the orchestrator and
# every harness adapter come with it.
component_dependencies() {
  case $1 in
    runtime) return 0 ;;
    orchestrator|claude|codex|pi) printf 'runtime\n' ;;
    herdr) printf 'orchestrator\n' ;;
    *) return 1 ;;
  esac
}

# A component the registry knows: one the dependency table can answer for.
known_component() {
  component_dependencies "$1" >/dev/null
}

# The components in the order the installer handles them.
canonical_components() {
  result=
  for component in $component_order; do
    case " $1 " in
      *" $component "*) result="$result $component" ;;
    esac
  done
  printf '%s\n' "$result"
}

# The same list, for a human: "runtime, pi".
comma_list() {
  result=
  for item in $1; do
    if [ -z "$result" ]; then result=$item; else result="$result, $item"; fi
  done
  printf '%s\n' "$result"
}

# The requested components plus everything they need: the fixpoint of the dependency table.
resolve_closure() {
  selected=$1
  changed=1
  while [ "$changed" -eq 1 ]; do
    changed=0
    for component in $selected; do
      for dependency in $(component_dependencies "$component"); do
        case " $selected " in
          *" $dependency "*) ;;
          *) selected="$selected $dependency"; changed=1 ;;
        esac
      done
    done
  done
  printf '%s\n' "$selected"
}

# The version a member of the workspace ships. The release an install is recorded as is the
# version the components agree on: a checkout that mixes versions is a mixture of releases.
member_version() {
  file=$1
  [ -n "$root" ] || return 0
  [ -f "$file" ] || return 0
  sed -n 's/^version *= *"\(.*\)"$/\1/p' "$file" | head -n 1
}

# The version this checkout ships: the runtime member's, or the workspace's own.
checkout_version() {
  version=$(member_version "$root/packages/agent-runtime/pyproject.toml")
  [ -n "$version" ] || version=$(member_version "$root/pyproject.toml")
  printf '%s\n' "$version"
}

# The release this checkout is: what every component of an install comes from.
checkout_release() {
  if [ -z "$root" ] && [ -n "$release_pin" ]; then
    printf '%s\n' "$release_pin"
    return 0
  fi
  version=$(checkout_version)
  if [ -n "$version" ]; then printf 'v%s\n' "$version"; else printf 'local\n'; fi
}

# The manifest: the record of what this installer owns.
manifest() {
  python3 - "$manifest_path" "$@" <<'PY'
import json
import os
import sys

path = sys.argv[1]
command = sys.argv[2] if len(sys.argv) > 2 else ""
arguments = sys.argv[3:]


def load():
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or data.get("schema") != 1:
        data = {}
    components = data.get("components")
    if not isinstance(components, dict):
        components = {}
    return {"schema": 1, "components": components}


def save(data):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except OSError as error:
        # A failed write leaves no half-written file beside the manifest.
        try:
            os.remove(temporary)
        except OSError:
            pass
        sys.stderr.write("cannot record the install manifest %s: %s\n" % (path, error.strerror or error))
        sys.exit(1)


if command == "dump":
    data = load()
    for name, entry in sorted(data["components"].items()):
        print("component\t%s\t%s\t%s" % (name, entry.get("release", ""), entry.get("source", "")))
elif command == "files":
    data = load()
    for owned in data["components"].get(arguments[0], {}).get("files", []):
        print(owned)
elif command == "source":
    data = load()
    print(data["components"].get(arguments[0], {}).get("source", ""))
elif command == "sources":
    data = load()
    for entry in data["components"].values():
        print(entry.get("source", ""))
elif command == "add":
    name, release, source = arguments[0], arguments[1], arguments[2]
    data = load()
    data["components"][name] = {"release": release, "source": source, "files": arguments[3:]}
    save(data)
elif command == "remove":
    data = load()
    data["components"].pop(arguments[0], None)
    save(data)
PY
}

# The release an installed component was recorded with: empty when it is not installed.
installed_component() {
  wanted=$1
  found=
  while IFS="$tab" read -r kind name release source; do
    [ "$kind" = component ] || continue
    [ "$name" = "$wanted" ] || continue
    found=$release
  done <<EOF
$(manifest dump)
EOF
  printf '%s\n' "$found"
}

# Whether a path is inside a directory. An empty directory is no directory: it would otherwise
# turn the pattern into /*, which every absolute path matches.
under() {
  [ -n "$2" ] || return 1
  case $1 in
    "$2"/*) return 0 ;;
  esac
  return 1
}

# A path this installer created before: it is inside a checkout the manifest recorded.
owned_by_the_installer() {
  target=$1
  found=1
  while IFS= read -r source; do
    if under "$target" "$source"; then found=0; break; fi
  done <<EOF
$(manifest sources)
EOF
  [ "$found" -eq 0 ]
}

# Refuse what linking the shims would clobber. The legacy mode replaces whatever symlink is
# there; the selective mode refuses a symlink this install does not own, so a shim of the user's
# own is never taken. A dry run asks the same question, so its preview is the answer.
check_shims() {
  mode=$1
  for shim in $shims; do
    path=$bin_dir/$shim
    if [ -e "$path" ] && [ ! -L "$path" ]; then
      echo "refusing to overwrite $path: not a symlink" >&2
      exit 1
    fi
    if [ -L "$path" ] && [ "$mode" = selective ]; then
      target=$(readlink "$path")
      if ! under "$target" "$root" && ! owned_by_the_installer "$target"; then
        echo "refusing to replace $path: it points at $target, which this install does not own" >&2
        echo "  remove it, or install into another directory with BIN_DIR" >&2
        exit 1
      fi
    fi
  done
}

# Point a symlink at a target in one step: the link is made beside the old one and renamed over it,
# so a reader sees the old shim or the new one and never a missing one. ln -sf would unlink first.
swap_link() {
  python3 - swap "$1" "$2" <<'PY'
import os
import sys

target, path = sys.argv[2], sys.argv[3]
temporary = "%s.new.%d" % (os.path.join(os.path.dirname(path), "." + os.path.basename(path)), os.getpid())
try:
    os.symlink(target, temporary)
    os.replace(temporary, path)
except OSError as error:
    try:
        os.remove(temporary)
    except OSError:
        pass
    sys.stderr.write("cannot link %s: %s\n" % (path, error.strerror or error))
    sys.exit(1)
PY
}

# Link the shims into BIN_DIR, once nothing in the way is left.
link_shims() {
  check_shims "$1"
  mkdir -p "$bin_dir"
  for shim in $shims; do
    swap_link "$root/bin/$shim" "$bin_dir/$shim" || exit 1
    echo "linked $bin_dir/$shim"
  done
}

# The topmost directory of a path that does not exist yet: what a run creates, it removes again.
topmost_missing() {
  missing=
  probe=$1
  while [ ! -d "$probe" ] && [ "$probe" != / ] && [ "$probe" != . ]; do
    missing=$probe
    probe=$(dirname "$probe")
  done
  printf '%s\n' "$missing"
}

# Remove the directories a run made, from the leaf up to the topmost one, and only while they are
# empty: anything that landed in them since is not this run's to remove.
remove_made_dirs() {
  leaf=$1
  top=$2
  [ -n "$top" ] || return 0
  while :; do
    rmdir "$leaf" 2>/dev/null || break
    [ "$leaf" != "$top" ] || break
    leaf=$(dirname "$leaf")
  done
  return 0
}

# The activation is a transaction. Before the first shim moves, what the run is about to change is
# recorded: where each shim pointed and the manifest as it was. A failure or a signal after that puts
# every one of them back, so the prior installation is the one still active and no half of the new
# one is. The record lives outside the install directory: it is not part of what it protects.
txn=
txn_bin_made=
txn_home_made=
begin_transaction() {
  txn=$(mktemp -d "${TMPDIR:-/tmp}/agent-runtime-install.XXXXXX") || {
    echo "cannot install: no temporary directory to keep the rollback record in" >&2
    exit 1
  }
  txn_bin_made=$(topmost_missing "$bin_dir")
  txn_home_made=$(topmost_missing "$install_home")
  for shim in $shims; do
    if [ -L "$bin_dir/$shim" ]; then readlink "$bin_dir/$shim" >"$txn/link.$shim"; fi
  done
  if [ -f "$manifest_path" ]; then cp -p "$manifest_path" "$txn/manifest"; fi
  trap rollback_transaction EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

# Runs when the install ends without committing, for whatever reason: undo, best effort and in the
# reverse of what was done, and keep going when one step cannot be undone so the rest still is.
rollback_transaction() {
  status=$?
  # Ignored, not reset: a signal in the middle would leave the shims half restored.
  trap - EXIT
  trap '' HUP INT TERM
  set +e
  for shim in $shims; do
    rm -f "$bin_dir"/."$shim".new.*
    if [ -f "$txn/link.$shim" ]; then
      swap_link "$(cat "$txn/link.$shim")" "$bin_dir/$shim" || echo "could not restore $bin_dir/$shim" >&2
    elif [ -L "$bin_dir/$shim" ]; then
      rm -f "$bin_dir/$shim"
    fi
  done
  rm -f "$manifest_path.tmp"
  if [ -f "$txn/manifest" ]; then
    cp -p "$txn/manifest" "$manifest_path.restore" && mv -f "$manifest_path.restore" "$manifest_path" ||
      echo "could not restore $manifest_path" >&2
  elif [ -f "$manifest_path" ]; then
    rm -f "$manifest_path"
  fi
  remove_made_dirs "$bin_dir" "$txn_bin_made"
  remove_made_dirs "$install_home" "$txn_home_made"
  rm -rf "$txn"
  echo "the install did not finish: the previous installation is as it was" >&2
  exit "$status"
}

commit_transaction() {
  trap - EXIT HUP INT TERM
  rm -rf "$txn"
  txn=
}

note_path() {
  case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) echo "note: $bin_dir is not on PATH" ;;
  esac
}

# TOML basic-string escaping for paths.
toml_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

# The files the harnesses keep their configuration in. The installer never writes one: what it
# prints is the block to add. It asks of each only whether agent-runtime is already in it. The
# credential files beside them (auth.json, .credentials.json) are never opened, and no line of a
# config reaches the output: a config can hold a key, and a key must never be printed.
codex_config=$HOME/.codex/config.toml
pi_config=$HOME/.pi/agent/mcp.json
pi_hosts=$HOME/.config/agent-runtime/hosts.json
claude_settings=$HOME/.claude/settings.json

# What a config says about agent-runtime: absent (no such file), wired (it names this install),
# elsewhere (it names agent-runtime and another install) or kept (it is there and nothing more is
# known). A file that cannot be read is kept: the answer is never a guess. An empty $3 is a
# location the run does not know yet (a streamed dry run), so nothing can be wired.
config_state() {
  if [ ! -e "$1" ]; then
    printf 'absent\n'
  elif [ ! -f "$1" ] || [ ! -r "$1" ]; then
    printf 'kept\n'
  elif ! grep -qF -e "$2" "$1" 2>/dev/null; then
    printf 'kept\n'
  elif [ -z "$3" ]; then
    printf 'kept\n'
  elif grep -qF -e "$3" "$1" 2>/dev/null; then
    printf 'wired\n'
  else
    printf 'elsewhere\n'
  fi
}

# One line of the dry run for a config that is already there ($2 is its path under the home): what is kept, and what is printed.
plan_config() {
  case $3 in
    wired) printf '%s: ~/%s already names this install: it is left as it is, the full block is printed to check it against\n' "$1" "$2" ;;
    elsewhere) printf '%s: ~/%s has an agent-runtime entry that points elsewhere: it is left as it is, the full block is printed\n' "$1" "$2" ;;
    *) printf '%s: ~/%s exists and is left as it is: what to add is printed\n' "$1" "$2" ;;
  esac
}

# Pi comes from its own installer. The installer looks for it on the PATH and nothing more: Pi is
# never run, not even for its version, and never installed or signed in.
pi_found() {
  command -v pi >/dev/null 2>&1
}

print_pi_prerequisite() {
  cat <<EOT

pi: not found on PATH. Pi comes from its official installer, and this installer does not install or sign in to Pi:
  curl -fsSL https://pi.dev/install.sh | sh
  or, with Node.js 22.19 or newer: npm install -g --ignore-scripts @earendil-works/pi-coding-agent
  https://pi.dev/docs/latest/quickstart
The integration below works once Pi is installed and you have signed in to a model provider yourself.
EOT
}

print_claude_wiring() {
  state=$(config_state "$claude_settings" agent-runtime@agent-runtime agent-runtime@agent-runtime)
  # A name in the settings is not the whole wiring (a plugin can be listed and switched off), so
  # the commands are printed whatever the file says.
  case $state in
    wired) printf '\n~/.claude/settings.json already names agent-runtime: it is left as it is. The commands are below; run them if the plugin is not enabled\n' ;;
    elsewhere|kept) printf '\n~/.claude/settings.json exists and is left as it is: run these in Claude Code, which adds the plugin itself\n' ;;
  esac
  cat <<EOT

Claude Code (public marketplace):
  /plugin marketplace add FBisca/agent-runtime
  /plugin install agent-runtime@agent-runtime

Claude Code (this clone, edits load in place):
  /plugin marketplace add $root
  /plugin install agent-runtime@agent-runtime
EOT
}

print_codex_wiring() {
  mcp=$(toml_escape "$bin_dir/agent-runtime-mcp")
  hook=$(toml_escape "$bin_dir/agent-runtime-hook")

  state=$(config_state "$codex_config" mcp_servers.agent-runtime "$bin_dir/agent-runtime-mcp")
  case $state in
    wired)
      printf '\n~/.codex/config.toml already names this install: it is left as it is.\nThe full block for this release is below; check it against yours, a line may be missing:\n\n'
      ;;
    elsewhere)
      printf '\n~/.codex/config.toml has an agent-runtime entry that points elsewhere: it is left as it is.\nThe block for this install is below; change your entry to match it (its command is "%s"):\n\n' "$mcp"
      ;;
    kept) printf '\nCodex: ~/.codex/config.toml exists and is left as it is: add this by hand:\n\n' ;;
    *) printf '\nCodex, add to ~/.codex/config.toml:\n\n' ;;
  esac
  print_codex_block

  # Lead-liveness hooks (Stop guard, SessionStart and UserPromptSubmit context). Opt-in: Codex runs a hook only
  # after it is trusted, and an untrusted one raises a "Hooks need review" prompt, so print them on request,
  # then approve them with /hooks (see docs/verification/codex-hooks.md).
  if [ "${CODEX_LEAD_HOOKS:-}" = 1 ]; then
    print_codex_lead_hooks
  else
    printf '\n# Optional lead hooks for Codex: re-run with CODEX_LEAD_HOOKS=1 to print the Stop, SessionStart and\n# UserPromptSubmit blocks.\n'
  fi
}

print_codex_block() {
  mcp=$(toml_escape "$bin_dir/agent-runtime-mcp")
  hook=$(toml_escape "$bin_dir/agent-runtime-hook")

  cat <<EOT
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

[[hooks.PreToolUse]]
matcher = "^apply_patch\$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "\\"$hook\\" --cli codex"
timeout = 5
statusMessage = "Checking file edits"
EOT

}

# Lead-liveness hooks (Stop guard, SessionStart and UserPromptSubmit context).
print_codex_lead_hooks() {
  hook=$(toml_escape "$bin_dir/agent-runtime-hook")
  cat <<EOT

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = "\\"$hook\\" stop --cli codex"
timeout = 5
statusMessage = "Checking for unseen worker results"

[[hooks.SessionStart]]

[[hooks.SessionStart.hooks]]
type = "command"
command = "\\"$hook\\" context --cli codex"
timeout = 5

[[hooks.UserPromptSubmit]]

[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = "\\"$hook\\" context --cli codex"
timeout = 5
EOT
}

print_pi_wiring() {
  server=$(toml_escape "$root/server/mcp_server.py")
  if ! pi_found; then print_pi_prerequisite; fi

  # Where this install's server is, unless this run has no checkout yet to say.
  wire=
  [ -z "$root" ] || wire=$root/server/mcp_server.py
  state=$(config_state "$pi_config" '"agent-runtime"' "$wire")
  case $state in
    wired) printf '\n~/.pi/agent/mcp.json already names this install: it is left as it is.\nThe full block for this release is below; check it against yours, a line may be missing:\n\n' ;;
    elsewhere) printf '\n~/.pi/agent/mcp.json has an agent-runtime entry that points elsewhere: it is left as it is.\nThe block for this install is below; change your entry to match it:\n\n' ;;
    kept) printf '\nPi (agent-runtime integration): ~/.pi/agent/mcp.json exists and is left as it is: add this by hand:\n\n' ;;
    *) printf '\nPi (agent-runtime integration), add to ~/.pi/agent/mcp.json:\n\n' ;;
  esac
  cat <<EOT
{
  "mcpServers": {
    "agent-runtime": {
      "command": "python3",
      "args": ["$server"],
      "env": { "AGENT_RUNTIME_DISPATCH": "1" },
      "exposure": "direct"
    }
  }
}
EOT

  wire=
  [ -z "$root" ] || wire=$root
  state=$(config_state "$pi_hosts" '"pi"' "$wire")
  case $state in
    wired) printf '\nA Pi lead needs the host policy too: ~/.config/agent-runtime/hosts.json already names a pi host for this install and is left as it is. The full entry is below; check it against yours:\n' ;;
    absent) printf '\nA Pi lead needs the host policy too, in ~/.config/agent-runtime/hosts.json:\n' ;;
    *) printf '\nA Pi lead needs the host policy too: ~/.config/agent-runtime/hosts.json exists and is left as it is, add the pi host to it by hand:\n' ;;
  esac
  cat <<EOT
{"hosts": {"pi": {"mutations": true, "backends": ["codex"], "paths": ["$root"]}}}
EOT
}

# The components, one by one. Each action installs what it owns and records it in the manifest.
install_runtime() {
  link_shims selective
  note_path
  manifest add runtime "$release" "$root" \
    "$bin_dir/agent-runtime" "$bin_dir/agent-runtime-hook" \
    "$bin_dir/agent-runtime-mcp" "$bin_dir/agent-runtime-profile"
}

install_orchestrator() {
  printf 'orchestrator: the engine ships with the release (%s); this checkout keeps the source in %s\n' \
    "$release" "$root/packages/agent-orchestrator"
  manifest add orchestrator "$release" "$root"
}

install_claude() {
  print_claude_wiring
  manifest add claude "$release" "$root"
}

install_codex() {
  print_codex_wiring
  manifest add codex "$release" "$root"
}

install_pi() {
  print_pi_wiring
  manifest add pi "$release" "$root"
}

# Whether the herdr component copies instead of linking: a staged release is the installer's own
# directory, so a link into it would point at something nobody edits and an uninstall deletes.
herdr_copies() {
  staged_release "$root"
}

herdr_verb() {
  if herdr_copies; then printf 'copy\n'; else printf 'link\n'; fi
}

# Whether this installer put the path there: it is listed in the manifest. The manifest is read
# once the component is installed; before that nothing is ours.
herdr_listed() {
  while IFS= read -r owned; do
    [ "$owned" != "$1" ] || return 0
  done <<EOF
$(manifest files herdr)
EOF
  return 1
}

# Refuse what the component would clobber, before anything is changed. A mod or layout of that name
# that this installer did not put there is the person's, and the refusal shows what it would do.
check_herdr() {
  if [ -z "$root" ] || [ ! -d "$root/herdr/agent-workbench" ] || [ ! -f "$root/herdr/layouts/workbench.json" ]; then
    echo "cannot install herdr: this checkout or release has no herdr/agent-workbench mod and herdr/layouts/workbench.json" >&2
    exit 1
  fi
  verb=$(herdr_verb)
  if [ -L "$herdr_mod" ]; then
    target=$(readlink "$herdr_mod")
    if ! under "$target" "$root" && ! owned_by_the_installer "$target" && ! herdr_listed "$herdr_mod"; then
      echo "refusing to replace $herdr_mod: it points at $target, which this install does not own" >&2
      echo "  would $verb $herdr_mod -> $root/herdr/agent-workbench" >&2
      echo "  move it away or remove it, then run the install again" >&2
      exit 1
    fi
  elif [ -e "$herdr_mod" ] && ! herdr_listed "$herdr_mod"; then
    echo "refusing to replace $herdr_mod: a mod of that name is there and this install did not put it there" >&2
    echo "  would $verb $herdr_mod -> $root/herdr/agent-workbench" >&2
    echo "  move it away or remove it, then run the install again" >&2
    exit 1
  fi
  if [ -e "$herdr_layout" ] || [ -L "$herdr_layout" ]; then
    if ! herdr_listed "$herdr_layout" && ! cmp -s "$herdr_layout" "$root/herdr/layouts/workbench.json"; then
      echo "refusing to replace $herdr_layout: a layout of that name is there and this install did not put it there" >&2
      echo "  would copy $root/herdr/layouts/workbench.json -> $herdr_layout" >&2
      echo "  move it away or remove it, then run the install again" >&2
      exit 1
    fi
  fi
}

# A copy is made beside its place and moved over it, so a reader sees the old one or the new one.
install_herdr() {
  mkdir -p "$herdr_home/mods" "$herdr_home/layouts"
  if herdr_copies; then
    temporary=$herdr_home/mods/.agent-workbench.new.$$
    rm -rf "$temporary"
    cp -R "$root/herdr/agent-workbench" "$temporary"
    rm -rf "$herdr_mod"
    mv "$temporary" "$herdr_mod"
    printf 'copied %s\n' "$herdr_mod"
  else
    # A copy from an earlier release is ours to replace; a link is swapped in one step.
    if [ -d "$herdr_mod" ] && [ ! -L "$herdr_mod" ]; then rm -rf "$herdr_mod"; fi
    swap_link "$root/herdr/agent-workbench" "$herdr_mod" || exit 1
    printf 'linked %s\n' "$herdr_mod"
  fi
  temporary=$herdr_home/layouts/.workbench.json.new.$$
  cp "$root/herdr/layouts/workbench.json" "$temporary"
  mv "$temporary" "$herdr_layout"
  printf 'added the workbench layout %s (not switched to: pick it with layout.switch)\n' "$herdr_layout"
  manifest add herdr "$release" "$root" "$herdr_mod" "$herdr_layout"
}

# Remove what the component put in Herdr's home, and only while it is still what it put there: a
# link into the install, a copy nobody edited, a layout nobody changed. Anything else stays.
uninstall_herdr() {
  source=$(manifest source herdr)
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    if [ -L "$file" ]; then
      target=$(readlink "$file")
      if under "$target" "$root" || under "$target" "$source"; then
        herdr_remove "$file"
      else
        printf 'kept %s: it points at %s, which this install does not own\n' "$file" "$target"
      fi
    elif [ -d "$file" ]; then
      if diff -r "$file" "$source/herdr/agent-workbench" >/dev/null 2>&1; then
        herdr_remove "$file"
      else
        printf 'kept %s: it is not the copy this installer made\n' "$file"
      fi
    elif [ -f "$file" ]; then
      if cmp -s "$file" "$source/herdr/layouts/workbench.json"; then
        herdr_remove "$file"
      else
        printf 'kept %s: it is not the file this installer created\n' "$file"
      fi
    else
      printf 'already gone: %s\n' "$file"
    fi
  done <<EOF
$(manifest files herdr)
EOF
  if [ "$dry_run" -eq 1 ]; then printf 'would uninstall herdr\n'; return 0; fi
  manifest remove herdr
  printf 'uninstalled herdr\n'
}

herdr_remove() {
  if [ "$dry_run" -eq 1 ]; then
    printf 'would remove %s\n' "$1"
  else
    rm -rf "$1"
    printf 'removed %s\n' "$1"
  fi
}

install_component() {
  case $1 in
    herdr) install_herdr ;;
    runtime) install_runtime ;;
    orchestrator) install_orchestrator ;;
    claude) install_claude ;;
    codex) install_codex ;;
    pi) install_pi ;;
  esac
}

# Remove what a component owns: the files the manifest recorded, and only while they are still
# the ones this installer created. Anything the user replaced stays.
uninstall_component() {
  component=$1
  if [ -z "$(installed_component "$component")" ]; then
    printf '%s: not installed\n' "$component"
    return 0
  fi
  if [ "$component" = herdr ]; then uninstall_herdr; return 0; fi
  source=$(manifest source "$component")
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    if [ -L "$file" ]; then
      target=$(readlink "$file")
      if under "$target" "$root" || under "$target" "$source"; then
        if [ "$dry_run" -eq 1 ]; then
          printf 'would remove %s\n' "$file"
        else
          rm -f "$file"
          printf 'removed %s\n' "$file"
        fi
      else
        printf 'kept %s: it points at %s, which this install does not own\n' "$file" "$target"
      fi
    elif [ -e "$file" ]; then
      printf 'kept %s: it is not the file this installer created\n' "$file"
    else
      printf 'already gone: %s\n' "$file"
    fi
  done <<EOF
$(manifest files "$component")
EOF
  if [ "$dry_run" -eq 1 ]; then
    if [ "$component" = runtime ] && staged_release "$source"; then printf 'would remove the staged release %s\n' "$source"; fi
    printf 'would uninstall %s\n' "$component"
    return 0
  fi
  manifest remove "$component"
  if [ "$component" = runtime ] && staged_release "$source"; then
    rm -rf "$source"
    rmdir "$install_home/releases" 2>/dev/null || true
    printf 'removed the staged release %s\n' "$source"
  fi
  printf 'uninstalled %s\n' "$component"
}

# A release this installer staged: a directory of its own under releases/, never a link, never a
# checkout. The runtime is what runs from it, and every component that needs the runtime leaves in
# the same call or first, so nothing is left running from it when the runtime goes.
staged_release() {
  [ -n "$1" ] || return 1
  [ -n "$install_home" ] || return 1
  case ${1#"$install_home"/releases/} in
    ""|*/*|.|..) return 1 ;;
  esac
  case $1 in
    "$install_home"/releases/*) ;;
    *) return 1 ;;
  esac
  [ -d "$1" ] && [ ! -L "$1" ]
}

# The original behaviour: link the shims and print the Claude and Codex wiring.
legacy_install() {
  if [ "$dry_run" -eq 1 ]; then
    check_shims legacy
    print_dry_run_header
    printf 'command: install (no components named: the original behaviour)\n'
    print_paths
    for shim in $shims; do
      printf 'link %s -> %s\n' "$bin_dir/$shim" "$root/bin/$shim"
    done
    printf 'print the Claude Code and Codex wiring\n'
    return 0
  fi
  begin_transaction
  link_shims legacy
  note_path
  print_claude_wiring
  print_codex_wiring
  commit_transaction
}

# The names the user asked for, checked one by one.
parse_components() {
  list=$1
  result=
  rest=$list
  while [ -n "$rest" ]; do
    case $rest in
      *,*) item=${rest%%,*}; rest=${rest#*,} ;;
      *) item=$rest; rest= ;;
    esac
    item=$(printf '%s' "$item" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    if [ -z "$item" ]; then
      echo "empty component name in --components '$list'" >&2
      usage >&2
      exit 2
    fi
    if ! known_component "$item"; then
      echo "unknown component: $item" >&2
      usage >&2
      exit 2
    fi
    result="$result $item"
  done
  if [ -z "$result" ]; then
    echo "--components needs at least one component" >&2
    usage >&2
    exit 2
  fi
  printf '%s\n' "$result"
}

# The terminal the menu is asked on. It is the terminal itself, never stdin: a streamed script
# is stdin, and its own bytes must not be read as answers. The variable is for tests, which
# stand a pseudo-terminal in for it.
tty_path=${AGENT_RUNTIME_TTY:-/dev/tty}

# A terminal that can be opened and is one. Opened in a subshell: a failed exec would end this shell.
tty_available() {
  ( exec 3<"$tty_path" && [ -t 3 ] ) 2>/dev/null
}

# One line from the terminal, which the menu opened as descriptor 3. End of input is an empty answer.
ask() {
  printf '%s' "$1" >&3
  reply=
  IFS= read -r reply <&3 || reply=
  reply=$(printf '%s' "$reply" | tr -d '\r')
}

# The menu: ask which components, and set $components from the answer. Nothing is changed here.
ask_components() {
  exec 3<>"$tty_path"
  {
    printf 'agent-runtime installer: choose what to install\n\n'
    position=0
    for component in $component_order; do
      position=$((position + 1))
      printf '  %s) %-12s %s\n' "$position" "$component" "$(component_summary "$component")"
    done
    printf '\nThe runtime comes with every other component: they need it.\n'
  } >&3
  ask 'Components (names or numbers, comma separated; "all" for every one; empty to quit): '
  chosen=
  # The answer is data: no glob in it may expand into file names.
  set -f
  for token in $(printf '%s' "$reply" | tr ',' ' '); do
    case $token in
      q|quit) chosen= ; break ;;
      all) chosen=$component_order ;;
      [0-9]*)
        position=0
        name=$token
        for component in $component_order; do
          position=$((position + 1))
          [ "$token" = "$position" ] && name=$component
        done
        chosen="$chosen $name"
        ;;
      *) chosen="$chosen $token" ;;
    esac
  done
  set +f
  if [ -z "$chosen" ]; then
    echo "no component chosen: nothing was changed" >&2
    exit 1
  fi
  components=$(comma_list "$chosen")
}

# After a menu the user confirms what is about to happen. --yes skips it, and a dry run changes
# nothing so there is nothing to confirm.
confirm_choice() {
  [ "$menu_used" -eq 1 ] && [ "$assume_yes" -eq 0 ] && [ "$dry_run" -eq 0 ] || return 0
  printf 'command: %s\n' "$command"
  print_selection
  ask 'Proceed? [y/N] '
  case $reply in
    y|Y|yes|YES|Yes) ;;
    *)
      echo "declined: nothing was changed" >&2
      exit 1
      ;;
  esac
}

print_dry_run_header() {
  printf 'dry run: nothing is changed, nothing is downloaded and no tool is run\n'
}

print_paths() {
  if [ -n "$root" ]; then printf 'source: %s\n' "$root"; else printf 'source: none, this run is streamed\n'; fi
  printf 'bin dir: %s\n' "$bin_dir"
  printf 'manifest: %s\n' "$manifest_path"
}

# The components, the ones brought in for them, and the release: what a run is about to do.
print_selection() {
  printf 'components: %s\n' "$(comma_list "$selection")"
  for component in $selection; do
    for dependency in $(component_dependencies "$component"); do
      case " $requested " in
        *" $dependency "*) ;;
        *) printf '%s: required by %s\n' "$dependency" "$component" ;;
      esac
    done
  done
  printf 'release: %s\n' "$release"
}

# One adapter's line in the dry run: what is printed for a config that is not there yet, and what
# is kept, and said, for one that is.
plan_adapter() {
  state=$(config_state "$3" "$4" "$5")
  if [ "$state" = absent ]; then
    printf '%s: %s\n' "$1" "$6"
  else
    plan_config "$1" "$2" "$state"
  fi
}

# The plan of an install or an upgrade: every action, the versions and the paths, as a preview.
print_plan() {
  print_dry_run_header
  printf 'command: %s\n' "$command"
  print_selection
  if [ "$streamed" -eq 1 ] && [ "$release" != unresolved ]; then
    printf 'versions: runtime and orchestrator as the release manifest of %s says, checked after the download\n' "$release"
  elif [ -n "$runtime_version" ]; then
    versions="runtime $runtime_version"
    case " $selection " in
      *" orchestrator "*) versions="$versions, orchestrator $orchestrator_version" ;;
    esac
    printf 'versions: %s\n' "$versions"
  else
    printf 'versions: unresolved until a release payload is fetched\n'
  fi
  print_paths
  if [ "$streamed" -eq 1 ] && [ "$release" != unresolved ]; then print_fetch_plan; fi
  for component in $selection; do
    case $component in
      runtime)
        for shim in $shims; do
          if [ -n "$root" ]; then
            target=$root/bin/$shim
          elif [ "$release" != unresolved ]; then
            target=$install_home/releases/$release/bin/$shim
          else
            target='the payload, pinned by the release'
          fi
          printf 'runtime: link %s -> %s\n' "$bin_dir/$shim" "$target"
        done
        ;;
      orchestrator) printf 'orchestrator: the engine ships with the release, nothing is linked\n' ;;
      claude) plan_adapter claude '.claude/settings.json' "$claude_settings" agent-runtime@agent-runtime agent-runtime@agent-runtime 'print the Claude Code plugin commands, no Claude config is written' ;;
      codex) plan_adapter codex '.codex/config.toml' "$codex_config" mcp_servers.agent-runtime "$bin_dir/agent-runtime-mcp" 'print the config for ~/.codex/config.toml, no Codex config is written' ;;
      pi)
        if ! pi_found; then print_pi_prerequisite; fi
        wire=
        [ -z "$root" ] || wire=$root/server/mcp_server.py
        plan_adapter pi '.pi/agent/mcp.json' "$pi_config" '"agent-runtime"' "$wire" 'print the config for ~/.pi/agent/mcp.json, no Pi config is written'
        wire=
        [ -z "$root" ] || wire=$root
        plan_adapter pi '.config/agent-runtime/hosts.json' "$pi_hosts" '"pi"' "$wire" 'print the Pi host policy for ~/.config/agent-runtime/hosts.json, no config is written'
        printf 'pi: Pi itself comes from its official installer; this one does not run it\n'
        ;;
      herdr)
        printf 'herdr: %s %s -> %s\n' "$(herdr_verb)" "$herdr_mod" "${root:-the release payload}/herdr/agent-workbench"
        printf 'herdr: copy %s from %s/herdr/layouts/workbench.json (opt-in, not switched to)\n' "$herdr_layout" "${root:-the release payload}"
        ;;
    esac
    printf '%s: record in the manifest\n' "$component"
  done
}

# The release every component of this install comes from, and the refusals that follow from it.
resolve_release() {
  runtime_version=$(checkout_version)
  orchestrator_version=$(member_version "$root/packages/agent-orchestrator/pyproject.toml")
  [ -n "$orchestrator_version" ] || orchestrator_version=$runtime_version
  case " $selection " in
    *" orchestrator "*)
      if [ -n "$orchestrator_version" ] && [ "$orchestrator_version" != "$runtime_version" ]; then
        echo "refusing to mix releases: the runtime is v$runtime_version and the orchestrator is v$orchestrator_version in this checkout" >&2
        echo "  check out one release, or install the components from separate checkouts" >&2
        exit 1
      fi
      ;;
  esac
  if [ -n "$runtime_version" ]; then
    release=v$runtime_version
  else
    release=local
  fi
  if [ -n "$pinned" ]; then
    if [ -z "$runtime_version" ]; then
      echo "refusing to install: this checkout has no version to check against $pinned" >&2
      exit 1
    fi
    if [ "$pinned" != "v$runtime_version" ]; then
      echo "refusing to install: this checkout is v$runtime_version, not the requested $pinned" >&2
      exit 1
    fi
  fi
}

# The installed set is one release: a component that stays behind at another release would be a
# mixture, so the installer refuses and names the way out.
refuse_a_mixed_set() {
  conflict=
  conflict_release=
  installed_names=
  installed=$(manifest dump)
  while IFS="$tab" read -r kind name old_release source; do
    [ "$kind" = component ] || continue
    installed_names="$installed_names $name"
    case " $selection " in
      *" $name "*) continue ;;
    esac
    if [ "$old_release" != "$release" ]; then
      conflict=$name
      conflict_release=$old_release
    fi
  done <<EOF
$installed
EOF
  if [ -n "$conflict" ]; then
    together=$(comma_list "$(canonical_components "$selection $installed_names")")
    echo "refusing to mix releases: $conflict is installed from $conflict_release and this run is $release" >&2
    echo "  install the whole set together: --components $together" >&2
    exit 1
  fi
}

# What a release is called. Only a version tag names something that cannot change under the
# installer: a branch, HEAD or `latest` is a moving ref, and is refused with the way out.
normalise_release() {
  want=${1#v}
  case $want in
    *[!0-9A-Za-z.-]*) want= ;;
  esac
  if [ -n "$want" ] && printf '%s\n' "$want" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?$'; then
    printf 'v%s\n' "$want"
    return 0
  fi
  echo "refusing release '$1': a release is a version tag like vX.Y.Z, and a moving ref (main, a branch, HEAD, latest) can change under the installer" >&2
  echo "  pin a version: --release v1.2.3" >&2
  return 1
}

# The one release this run names: the one stamped into a published installer, --release and
# AGENT_RUNTIME_RELEASE. They may be spelled differently but must be the same release.
note_release() {
  value=$(normalise_release "$2") || exit 1
  if [ -n "$pinned" ] && [ "$pinned" != "$value" ]; then
    echo "refusing to install: two releases named: $pinned (from $pinned_from) and $value (from $1)" >&2
    echo "  name one release" >&2
    exit 1
  fi
  pinned=$value
  pinned_from=$1
}

resolve_pin() {
  pinned=
  pinned_from=
  if [ -n "$release_pin" ]; then note_release "this installer" "$release_pin"; fi
  if [ -n "${AGENT_RUNTIME_RELEASE:-}" ]; then note_release AGENT_RUNTIME_RELEASE "$AGENT_RUNTIME_RELEASE"; fi
  if [ -n "$release_flag" ]; then note_release --release "$release_flag"; fi
}

# The release is fetched over https and nothing else: a plain http or file URL can be rewritten.
check_release_base() {
  release_base=${release_base%/}
  case $release_base in
    https://*[!A-Za-z0-9._~:/@%+=,-]*|https://) bad_base=1 ;;
    https://*) bad_base=0 ;;
    *) bad_base=1 ;;
  esac
  if [ "$bad_base" -eq 1 ]; then
    echo "refusing the release URL '$release_base': releases are downloaded over https only" >&2
    echo "  unset AGENT_RUNTIME_RELEASE_URL to use the default" >&2
    exit 1
  fi
}

# The checks and the unpacking of a release. Every refusal names what failed and exits 1; nothing
# here writes outside the directory it is given.
artifact() {
  python3 - "$@" <<'PY'
import hashlib
import json
import os
import re
import sys
import tarfile


def refuse(message):
    sys.stderr.write("%s\n" % message)
    sys.exit(1)


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def expected(sums, name):
    found = set()
    try:
        with open(sums, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, ValueError):
        refuse("refusing to install: SHA256SUMS cannot be read")
    for line in lines:
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64}) [ *]([^ /]+)", line)
        if match is None:
            refuse("refusing to install: SHA256SUMS is malformed, so no checksum can be trusted")
        if match.group(2) == name:
            found.add(match.group(1))
    if not found:
        refuse("refusing to install: there is no checksum for %s in SHA256SUMS" % name)
    if len(found) > 1:
        refuse("refusing to install: SHA256SUMS gives %s more than one checksum" % name)
    return found.pop()


def check_manifest(path, release, payload):
    def wrong(field, value, wanted):
        refuse(
            "refusing to install: the release manifest (release.json) names %s %s, not %s"
            % (field, value, wanted)
        )

    try:
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        refuse("refusing to install: the release manifest (release.json) is not valid JSON")
    if not isinstance(manifest, dict):
        refuse("refusing to install: the release manifest (release.json) is not an object")
    prefix = payload[: -len(".tar.gz")]
    for field, wanted in (
        ("contract", 1),
        ("release", release),
        ("platform", "any"),
        ("payload", payload),
        ("prefix", prefix),
    ):
        if manifest.get(field) != wanted:
            wrong(field, json.dumps(manifest.get(field)), json.dumps(wanted))
    components = manifest.get("components")
    if not isinstance(components, dict):
        refuse("refusing to install: the release manifest (release.json) lists no components")
    versions = {name: components.get(name) for name in ("runtime", "orchestrator")}
    version = release[1:]
    if any(value != version for value in versions.values()):
        refuse(
            "refusing to mix releases: the release manifest says the runtime is v%s and the "
            "orchestrator is v%s, and this is release %s" % (versions["runtime"], versions["orchestrator"], release)
        )


def extract(tarball, destination, prefix):
    try:
        archive = tarfile.open(tarball, "r:gz")
    except (OSError, tarfile.TarError):
        refuse("refusing to install: the payload is not a readable archive")
    with archive:
        try:
            members = archive.getmembers()
        except (OSError, EOFError, tarfile.TarError):
            refuse("refusing to install: the payload is not a readable archive")
        total = 0
        for member in members:
            parts = member.name.split("/")
            unsafe = None
            if member.name.startswith("/") or ".." in parts or parts[0] != prefix:
                unsafe = "its path leaves %s/" % prefix
            elif member.issym():
                target = os.path.normpath(os.path.join(os.path.dirname(member.name), member.linkname))
                if member.linkname.startswith("/") or not (target == prefix or target.startswith(prefix + "/")):
                    unsafe = "it links outside %s/" % prefix
            elif not (member.isreg() or member.isdir()):
                unsafe = "it is not a plain file, directory or link"
            total += max(member.size, 0)
            if total > 512 * 1024 * 1024:
                unsafe = "the payload is larger than a release can be"
            if unsafe:
                refuse("refusing to install: the payload holds an unsafe member, %s: %s" % (member.name, unsafe))
        if hasattr(tarfile, "data_filter"):
            archive.extractall(destination, members=members, filter="data")
        else:
            archive.extractall(destination, members=members)


command = sys.argv[1]
arguments = sys.argv[2:]
if command == "sum":
    print(digest(arguments[0]))
elif command == "verify":
    path, name, sums = arguments
    want = expected(sums, name)
    got = digest(path)
    if got != want:
        refuse(
            "refusing to install: the checksum of %s is %s, but SHA256SUMS says %s: the download is not what was published"
            % (name, got, want)
        )
elif command == "manifest":
    check_manifest(*arguments)
elif command == "extract":
    extract(*arguments)
PY
}

# One asset of the release, downloaded over https into the staging area. The tools see no stdin: a
# streamed script is stdin, and a program that reads it would eat the rest of the installer.
download_asset() {
  name=$1
  url=$release_base/$release/$name
  if ! curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 30 --max-time 600 --output "$stage/download/$name" "$url" </dev/null; then
    echo "refusing to $command: the download of $name did not finish ($url)" >&2
    echo "  nothing was installed and any installed version is unchanged; check the connection and run it again" >&2
    exit 1
  fi
}

# The staging directory and whatever this run created to hold it, removed when the run ends for
# any reason but a hand-over: an interrupted or refused download leaves nothing behind.
stage=
home_uncreated=
staged_new=0
releases_made=0
cleanup_stage() {
  if [ -n "$stage" ]; then
    rm -rf "$stage"
    stage=
  fi
  # A release this run staged is removed with the rest unless the install it was staged for
  # finished: a staged payload nobody activated is a leftover, and a release staged earlier stays.
  if [ "$staged_new" -eq 1 ]; then
    rm -rf "$staged"
    staged_new=0
    if [ "$releases_made" -eq 1 ]; then rmdir "$releases_dir" 2>/dev/null || true; fi
  fi
  remove_made_dirs "$install_home" "$home_uncreated"
  return 0
}

# The installer inside the payload runs as a child, so a refusal or a signal ends with this run
# cleaning up what it staged. A signal for this shell is passed on: the child rolls back its own
# activation, and this shell must not remove the release it is running from until it has.
relayed=0
relay() {
  relayed=$1
  kill -TERM "$inner" 2>/dev/null || true
}
run_inner() {
  sh "$staged/install.sh" "$command" --components "$components" </dev/null &
  inner=$!
  trap 'relay 129' HUP
  trap 'relay 130' INT
  trap 'relay 143' TERM
  status=0
  wait "$inner" || status=$?
  while kill -0 "$inner" 2>/dev/null; do
    status=0
    wait "$inner" || status=$?
  done
  if [ "$status" -ne 0 ] && [ "$relayed" -ne 0 ]; then status=$relayed; fi
  return 0
}

# Download the pinned release, verify it, stage its payload and hand over to the installer inside
# it. Nothing in the payload runs before its checksum matched, and nothing is activated here: the
# payload only becomes a release directory once it is verified and complete.
fetch_release() {
  check_release_base
  command -v curl >/dev/null 2>&1 || { echo "curl is required to download the release; install it, or run install.sh from a checkout" >&2; exit 1; }
  payload_name=agent-runtime-$release.tar.gz
  prefix=agent-runtime-$release
  releases_dir=$install_home/releases
  staged=$releases_dir/$release
  if [ -L "$releases_dir" ] || [ -L "$staged" ]; then
    echo "refusing to stage the release: $releases_dir is a symlink, and the payload is not written through a link" >&2
    echo "  replace it with a directory, or install into another directory with AGENT_RUNTIME_INSTALL_DIR" >&2
    exit 1
  fi

  trap cleanup_stage EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  if [ ! -d "$install_home" ]; then
    home_uncreated=$install_home
    parent=$(dirname "$install_home")
    while [ ! -d "$parent" ]; do
      home_uncreated=$parent
      parent=$(dirname "$parent")
    done
  fi
  mkdir -p "$install_home"
  stage=$(mktemp -d "$install_home/.staging.XXXXXX")
  mkdir "$stage/download" "$stage/payload"

  printf 'downloading release %s\n' "$release"
  download_asset SHA256SUMS
  download_asset release.json
  download_asset "$payload_name"

  artifact verify "$stage/download/release.json" release.json "$stage/download/SHA256SUMS" || exit 1
  artifact verify "$stage/download/$payload_name" "$payload_name" "$stage/download/SHA256SUMS" || exit 1
  payload_sha=$(artifact sum "$stage/download/$payload_name") || exit 1
  if [ -n "$trusted_sha" ]; then
    if [ "$payload_sha" != "$trusted_sha" ]; then
      echo "refusing to install: the checksum of $payload_name is $payload_sha, which does not match the checksum you gave ($trusted_sha)" >&2
      echo "  the download is not the payload you meant to install; nothing was installed" >&2
      exit 1
    fi
    printf 'verified: the payload sha256 matches the checksum you gave\n'
  else
    printf 'verified: the payload sha256 matches the checksum published with the release (SHA256SUMS)\n'
    printf 'note: that checksum comes from the same place as the payload; for full assurance take one from somewhere else and pass --sha256\n'
  fi
  artifact manifest "$stage/download/release.json" "$release" "$payload_name" || exit 1
  artifact extract "$stage/download/$payload_name" "$stage/payload" "$prefix" || exit 1
  if [ ! -f "$stage/payload/$prefix/install.sh" ] || [ ! -f "$stage/payload/$prefix/bin/agent-runtime" ]; then
    echo "refusing to install: the verified archive is not a release payload (no install.sh and bin/agent-runtime in $prefix/)" >&2
    exit 1
  fi
  printf '%s\n' "$payload_sha" >"$stage/payload/$prefix/.payload-sha256"

  [ -d "$releases_dir" ] || releases_made=1
  mkdir -p "$releases_dir"
  if [ -e "$staged" ]; then
    if [ "$(cat "$staged/.payload-sha256" 2>/dev/null)" != "$payload_sha" ]; then
      echo "refusing to stage the release: $release is already staged with a different payload" >&2
      echo "  the staged one stays as it is; remove $staged to stage this one, or install another release" >&2
      exit 1
    fi
    printf 'release %s is already staged: reusing %s\n' "$release" "$staged"
  else
    mv "$stage/payload/$prefix" "$staged"
    staged_new=1
    printf 'staged %s\n' "$staged"
  fi
  rm -rf "$stage"
  stage=

  # The payload is a checkout of the release: its own installer does the install, against the same
  # release, so the runtime and the orchestrator never come from two different ones.
  AGENT_RUNTIME_RELEASE=$release
  export AGENT_RUNTIME_RELEASE
  # What only the bootstrap reads stays behind: the inner run is a checkout install that downloads
  # nothing, and it refuses a checksum to verify as a usage error.
  unset AGENT_RUNTIME_SHA256 AGENT_RUNTIME_RELEASE_URL
  run_inner
  if [ "$status" -eq 0 ]; then
    staged_new=0
    home_uncreated=
  fi
  # Ignored once the child is done: this cleanup must run to the end, or the release it removes
  # and the shims the child restored would disagree.
  trap - EXIT
  trap '' HUP INT TERM
  [ "$status" -eq 0 ] || cleanup_stage
  exit "$status"
}

# What a streamed run would fetch, check and stage: the dry run's part of the download.
print_fetch_plan() {
  for asset in SHA256SUMS release.json "agent-runtime-$release.tar.gz"; do
    printf 'fetch: %s/%s/%s\n' "$release_base" "$release" "$asset"
  done
  printf 'verify: sha256 of every asset against SHA256SUMS, published with the release\n'
  if [ -n "$trusted_sha" ]; then
    printf 'verify: sha256 of the payload against the checksum you gave\n'
  else
    printf 'note: no --sha256 given: the checksums come from the release itself; pass --sha256 for an independent check\n'
  fi
  printf 'stage: %s/releases/%s\n' "$install_home" "$release"
}

command=install
components=
components_given=0
release_flag=
trusted_sha=${AGENT_RUNTIME_SHA256:-}
assume_yes=0
dry_run=0
menu_used=0
while [ $# -gt 0 ]; do
  case $1 in
    install|upgrade|uninstall) command=$1 ;;
    --components)
      shift
      [ $# -gt 0 ] || { echo "--components needs a list of components" >&2; usage >&2; exit 2; }
      components=$1
      components_given=1
      ;;
    --components=*) components=${1#--components=}; components_given=1 ;;
    --release)
      shift
      [ $# -gt 0 ] || { echo "--release needs a version tag like v1.2.3" >&2; usage >&2; exit 2; }
      release_flag=$1
      ;;
    --release=*) release_flag=${1#--release=} ;;
    --sha256)
      shift
      [ $# -gt 0 ] || { echo "--sha256 needs the SHA-256 checksum of the payload" >&2; usage >&2; exit 2; }
      trusted_sha=$1
      ;;
    --sha256=*) trusted_sha=${1#--sha256=} ;;
    --yes|-y) assume_yes=1 ;;
    --dry-run|-n) dry_run=1 ;;
    -h|--help) usage; exit 0 ;;
    --version) printf 'agent-runtime installer %s\n' "$(checkout_release)"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [ -n "$trusted_sha" ]; then
  trusted_sha=$(printf '%s' "$trusted_sha" | tr 'A-F' 'a-f')
  if ! printf '%s\n' "$trusted_sha" | grep -Eq '^[0-9a-f]{64}$'; then
    echo "the checksum must be 64 hexadecimal digits (a SHA-256), not '$trusted_sha'" >&2
    usage >&2
    exit 2
  fi
fi

if [ "$components_given" -eq 0 ]; then
  if [ "$streamed" -eq 1 ]; then
    # The script is stdin, so stdin is not a place to ask: the terminal is, when there is one.
    if tty_available; then
      ask_components
      menu_used=1
    else
      echo "no components named, and there is no terminal to ask on" >&2
      echo "  this run was streamed through a shell: stdin is the script and cannot answer" >&2
      echo "  name the components: curl -fsSL <installer url> | sh -s -- --components runtime,pi --yes" >&2
      usage >&2
      exit 2
    fi
  elif [ "$command" != install ]; then
    echo "$command needs --components" >&2
    usage >&2
    exit 2
  else
    legacy_install
    exit 0
  fi
fi

requested=$(parse_components "$components")
selected=$(resolve_closure "$requested")
selection=$(canonical_components "$selected")

if [ "$streamed" -eq 1 ] && [ "$command" != uninstall ]; then
  # A stream carries no payload: the release it installs is the one it is pinned to, and the
  # payload is fetched and verified once the run is confirmed. The versions come with the payload.
  runtime_version=
  orchestrator_version=
  resolve_pin
  if [ -z "$pinned" ]; then
    release=unresolved
    if [ "$dry_run" -eq 0 ]; then
      echo "cannot $command: this run names no release to install, and the installer does not guess one or follow a moving main" >&2
      echo "  pin a version: curl -fsSL <installer url> | sh -s -- --components runtime,pi --release vX.Y.Z --yes" >&2
      echo "  (the installer published with a release already names it)" >&2
      usage >&2
      exit 2
    fi
  else
    release=$pinned
    check_release_base
  fi
elif [ "$streamed" -eq 1 ]; then
  runtime_version=
  orchestrator_version=
  release=unresolved
else
  resolve_pin
  resolve_release
fi

if [ -n "$trusted_sha" ] && { [ "$streamed" -eq 0 ] || [ "$command" = uninstall ]; }; then
  echo "--sha256 checks a download, and this run downloads nothing" >&2
  echo "  it applies to a streamed install or upgrade of a release" >&2
  exit 2
fi

if [ "$command" = uninstall ]; then
  # A component that needs another cannot be left behind without it: the runtime is the base,
  # so removing it alone would leave the orchestrator and the adapters without their runtime.
  orphans=
  orphan_dependency=
  for component in $component_order; do
    case " $requested " in
      *" $component "*) continue ;;
    esac
    [ -n "$(installed_component "$component")" ] || continue
    for need in $(component_dependencies "$component"); do
      case " $requested " in
        *" $need "*) orphans="$orphans $component"; orphan_dependency=$need ;;
      esac
    done
  done
  if [ -n "$orphans" ]; then
    echo "refusing to uninstall $orphan_dependency: it is needed by $(comma_list "$orphans")" >&2
    echo "  uninstall them together: --components $(comma_list "$requested $orphans")" >&2
    exit 1
  fi
  if [ "$dry_run" -eq 1 ]; then
    print_dry_run_header
    printf 'command: uninstall\ncomponents: %s\n' "$(comma_list "$(canonical_components "$requested")")"
  fi
  confirm_choice
  # The Herdr mod is compared with the release it came from, so it goes before the runtime that
  # may own that release.
  case " $requested " in
    *" herdr "*) uninstall_component herdr ;;
  esac
  for component in $(canonical_components "$requested"); do
    [ "$component" != herdr ] || continue
    uninstall_component "$component"
  done
  exit 0
fi

[ "$release" = unresolved ] || refuse_a_mixed_set

if [ "$dry_run" -eq 1 ]; then
  # The preview asks the refusing questions too, so it never promises what the install would not do.
  case " $selection " in
    *" runtime "*) check_shims selective ;;
  esac
  case " $selection " in
    *" herdr "*) if [ "$streamed" -eq 0 ]; then check_herdr; fi ;;
  esac
  print_plan
  exit 0
fi

case " $selection " in
  *" herdr "*) if [ "$streamed" -eq 0 ]; then check_herdr; fi ;;
esac
confirm_choice
print_selection

if [ "$streamed" -eq 1 ]; then
  fetch_release
fi

begin_transaction
for component in $selection; do
  install_component "$component"
done
commit_transaction
printf 'recorded %s\n' "$manifest_path"
