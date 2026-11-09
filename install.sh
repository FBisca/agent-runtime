#!/bin/sh
# Link the agent-runtime shims into BIN_DIR, and wire the components the user names.
# Idempotent: re-running refreshes the links and changes nothing else.
#
# The components are runtime, orchestrator, claude, codex and pi. The runtime is the base: the
# orchestrator and every harness adapter need it, so it always comes along with them.
#
# Without arguments this keeps the original behaviour: link the shims and print the Claude and
# Codex wiring. With --components only the named components are installed, and what they need.
set -eu

self=$0
while [ -L "$self" ]; do
  dir=$(cd "$(dirname "$self")" && pwd)
  self=$(readlink "$self")
  case $self in /*) ;; *) self=$dir/$self ;; esac
done
root=$(cd "$(dirname "$self")" && pwd)

if [ -z "${HOME:-}" ]; then
  echo "HOME is not set: the installer needs a home directory to install into" >&2
  exit 1
fi
bin_dir=${BIN_DIR:-$HOME/.local/bin}

command -v python3 >/dev/null 2>&1 || { echo "python3 (3.11+) is required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Python 3.11+ is required" >&2; exit 1; }

# The documented shims: the runtime's four entry points.
shims='agent-runtime agent-runtime-hook agent-runtime-mcp agent-runtime-profile'

# The install manifest: what this installer owns, so an uninstall removes exactly that and
# nothing else. It lives in the user's data directory, never in the checkout.
install_home=${AGENT_RUNTIME_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/agent-runtime}
manifest_path=$install_home/install-manifest.json
tab=$(printf '\t')

usage() {
  cat <<EOT
usage: install.sh [install|upgrade|uninstall] [--components <list>]

commands:
  install     link and configure the named components, and what they need (the default)
  upgrade     install the named components again, from the release this checkout is
  uninstall   remove what the named components own, and nothing else

components:
  runtime       the runtime and its documented shims; every other component needs it
  orchestrator  the orchestration engine
  claude        the Claude Code plugin wiring
  codex         the Codex shims and config wiring
  pi            the Pi integration

The orchestrator and every harness adapter need the runtime, so it is installed with them.
Without --components the original behaviour runs: link the shims and print the Claude and
Codex wiring.

environment:
  BIN_DIR                    where the shims are linked (default ~/.local/bin)
  AGENT_RUNTIME_INSTALL_DIR  where the install manifest lives (default ~/.local/share)
  AGENT_RUNTIME_RELEASE      the release this install must be; the checkout's own version

exit codes: 0 done, 1 refused, 2 the command line was wrong
EOT
}

# The component registry: the components the installer knows, in the order it handles them.
component_order='runtime orchestrator claude codex pi'

# What each component needs installed with it: the runtime is the base, so the orchestrator and
# every harness adapter come with it.
component_dependencies() {
  case $1 in
    runtime) return 0 ;;
    orchestrator|claude|codex|pi) printf 'runtime\n' ;;
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
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


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

# A path this installer created before: it is inside a checkout the manifest recorded.
owned_by_the_installer() {
  target=$1
  found=1
  while IFS= read -r source; do
    [ -n "$source" ] || continue
    case $target in
      "$source"/*) found=0; break ;;
    esac
  done <<EOF
$(manifest sources)
EOF
  [ "$found" -eq 0 ]
}

# Link the shims into BIN_DIR. The legacy mode replaces whatever symlink is there; the selective
# mode refuses a symlink this install does not own, so a shim of the user's own is never taken.
link_shims() {
  mode=$1
  mkdir -p "$bin_dir"
  for shim in $shims; do
    path=$bin_dir/$shim
    if [ -e "$path" ] && [ ! -L "$path" ]; then
      echo "refusing to overwrite $path: not a symlink" >&2
      exit 1
    fi
    if [ -L "$path" ] && [ "$mode" = selective ]; then
      target=$(readlink "$path")
      case $target in
        "$root"/*) ;;
        *)
          if ! owned_by_the_installer "$target"; then
            echo "refusing to replace $path: it points at $target, which this install does not own" >&2
            echo "  remove it, or install into another directory with BIN_DIR" >&2
            exit 1
          fi
          ;;
      esac
    fi
  done
  for shim in $shims; do
    ln -sfn "$root/bin/$shim" "$bin_dir/$shim"
    echo "linked $bin_dir/$shim"
  done
}

note_path() {
  case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) echo "note: $bin_dir is not on PATH" ;;
  esac
}

# TOML basic-string escaping for paths.
toml_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

print_claude_wiring() {
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

  cat <<EOT

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

  # Lead-liveness hooks (Stop guard, SessionStart and UserPromptSubmit context). Opt-in: Codex runs a hook only
  # after it is trusted, and an untrusted one raises a "Hooks need review" prompt, so print them on request,
  # then approve them with /hooks (see docs/verification/codex-hooks.md).
  if [ "${CODEX_LEAD_HOOKS:-}" = 1 ]; then
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
  else
    printf '\n# Optional lead hooks for Codex: re-run with CODEX_LEAD_HOOKS=1 to print the Stop, SessionStart and\n# UserPromptSubmit blocks.\n'
  fi
}

print_pi_wiring() {
  server=$(toml_escape "$root/server/mcp_server.py")
  cat <<EOT

Pi (agent-runtime integration), add to ~/.pi/agent/mcp.json:

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

A Pi lead needs the host policy too, in ~/.config/agent-runtime/hosts.json:
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

install_component() {
  case $1 in
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
  source=$(manifest source "$component")
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    if [ -L "$file" ]; then
      target=$(readlink "$file")
      case $target in
        "$root"/*|"$source"/*)
          rm -f "$file"
          printf 'removed %s\n' "$file"
          ;;
        *)
          printf 'kept %s: it points at %s, which this install does not own\n' "$file" "$target"
          ;;
      esac
    elif [ -e "$file" ]; then
      printf 'kept %s: it is not the file this installer created\n' "$file"
    else
      printf 'already gone: %s\n' "$file"
    fi
  done <<EOF
$(manifest files "$component")
EOF
  manifest remove "$component"
  printf 'uninstalled %s\n' "$component"
}

# The original behaviour: link the shims and print the Claude and Codex wiring.
legacy_install() {
  link_shims legacy
  note_path
  print_claude_wiring
  print_codex_wiring
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

command=install
components=
components_given=0
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
    -h|--help) usage; exit 0 ;;
    --version) printf 'agent-runtime installer %s\n' "$(checkout_release)"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [ "$components_given" -eq 0 ]; then
  if [ "$command" != install ]; then
    echo "$command needs --components" >&2
    usage >&2
    exit 2
  fi
  legacy_install
  exit 0
fi

requested=$(parse_components "$components")
selected=$(resolve_closure "$requested")
selection=$(canonical_components "$selected")

# The release every component of this install comes from.
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
if [ -n "${AGENT_RUNTIME_RELEASE:-}" ]; then
  wanted=${AGENT_RUNTIME_RELEASE#v}
  if [ -z "$runtime_version" ]; then
    echo "refusing to install: this checkout has no version to check against $AGENT_RUNTIME_RELEASE" >&2
    exit 1
  fi
  if [ "$wanted" != "$runtime_version" ]; then
    echo "refusing to install: this checkout is v$runtime_version, not the requested $AGENT_RUNTIME_RELEASE" >&2
    exit 1
  fi
fi

if [ "$command" = uninstall ]; then
  for component in $(canonical_components "$requested"); do
    uninstall_component "$component"
  done
  exit 0
fi

# The installed set is one release: a component that stays behind at another release would be a
# mixture, so the installer refuses and names the way out.
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

for component in $selection; do
  install_component "$component"
done
printf 'recorded %s\n' "$manifest_path"
