"""Local CLI profiles; credentials are resolved only by the worker-side launcher."""

import json
import os
import re
from pathlib import Path

BUILTINS = ("claude", "codex")
PROVIDER_VARIABLES = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
                      "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                      "ANTHROPIC_DEFAULT_HAIKU_MODEL")


class ProfileError(ValueError):
    """Invalid profile configuration or selection."""


def config_path(env):
    home = env.get("HOME") or os.path.expanduser("~")
    override = env.get("AGENT_RUNTIME_PROFILES")
    if override == "~" or (override and override.startswith("~/")):
        override = home + override[1:]
    return Path(override or
                Path(env.get("XDG_CONFIG_HOME") or Path(home) / ".config") / "agent-runtime" / "profiles.json").absolute()


def load(env, path=None):
    path = Path(path) if path is not None else config_path(env)
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        # Do not echo file contents or env values, including malformed credentials.
        raise ProfileError("invalid profile config %s: cannot read JSON (%s)" % (path, type(error).__name__)) from error
    problem = None
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
        problem = "expected a profiles object"
    else:
        for name, profile in data["profiles"].items():
            if not name or "\0" in name or name in BUILTINS:
                problem = "profile names must be nonempty and cannot replace built-ins"
                break
            if not isinstance(profile, dict) or not isinstance(profile.get("cli"), str):
                problem = "each profile needs a CLI family"
                break
            overlay = profile.get("env")
            if not isinstance(overlay, dict):
                problem = "each profile needs an env object"
                break
            for key, value in overlay.items():
                if key.startswith(("HC_", "HERDR_")) or key in ("AGENT_RUNTIME_DISPATCH", "PATH"):
                    problem = "reserved environment variable %s" % key
                    break
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or not isinstance(value, str) or "\0" in value:
                    problem = "env requires variable names and string values without NUL"
                    break
                if value.startswith("$") and not re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", value):
                    problem = "env references must be $NAME"
                    break
                if key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY") and not value.startswith("$"):
                    problem = "%s requires a $NAME credential reference" % key
                    break
            if problem:
                break
    if problem:
        raise ProfileError("invalid profile config %s: %s" % (path, problem))
    return data["profiles"]


def cli_names(env):
    try:
        return list(BUILTINS) + list(load(env))
    except ProfileError:
        return list(BUILTINS)


def scrubbed_variables(configured):
    """Provider variables plus every key any profile sets: a worker inherits none of them, only its profile's."""
    keys = set(PROVIDER_VARIABLES)
    for profile in configured.values():
        keys.update(profile.get("env", {}))
    return sorted(keys)


def scrub_prefix(env):
    """`env -u NAME...` for a built-in spawn, so it runs on the CLI's own login whatever the lead or pane carries."""
    try:
        configured = load(env)
    except ProfileError:
        configured = {}  # the provider variables still go
    return ["env"] + [arg for key in scrubbed_variables(configured) for arg in ("-u", key)]


def launch(argv, env):
    """Resolve references here, immediately before exec; never pass values through argv."""
    if len(argv) < 4 or argv[2] != "--":
        raise ProfileError("usage: agent-runtime-profile <profile> <config-path> -- <program> <args...>")
    name, program = argv[0], argv[3:]
    configured = load(env, argv[1])
    if name not in configured:
        raise ProfileError("unknown CLI profile %r" % name)
    family = configured[name]["cli"]
    if family not in BUILTINS:
        raise ProfileError("profile %r has unsupported CLI family %r" % (name, family))
    overlay = {}
    for key, value in configured[name].get("env", {}).items():
        if value.startswith("$"):
            reference = value[1:]
            if reference not in env:
                raise ProfileError("profile %r requires environment variable %s" % (name, reference))
            value = env[reference]
        overlay[key] = value
    scrubbed = set(scrubbed_variables(configured))
    worker_env = {key: value for key, value in env.items() if key not in scrubbed}
    worker_env.update(overlay)
    try:
        os.execvpe(program[0], program, worker_env)
    except (OSError, ValueError) as error:
        # execvpe's filename may come from PATH and contain a resolved credential.
        raise ProfileError("cannot exec %s" % program[0]) from error


if __name__ == "__main__":
    import sys

    try:
        launch(sys.argv[1:], os.environ)
    except ProfileError as error:
        print("agent-runtime-profile: %s" % error, file=sys.stderr)
        sys.exit(1)
