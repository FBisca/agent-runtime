"""The repo config, `<repo>/.orchestrate/config.yaml`: where plans live, how they archive, and its check tiers.

Only these nodes are read. A missing file means the defaults; reading never creates anything."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_PATH = Path(".orchestrate") / "config.yaml"
ARCHIVE_MODES = ("promote", "discard")
# node -> its keys: the whole accepted shape, so an unknown key is refused by its dotted path.
NODES = {
    "plans": ("dir",),
    "acceptance": ("dir",),
    "archive": ("mode",),
    "checks": ("fast", "affected", "full"),
    "tests": ("patterns",),
}
# file-name globs that mark a test file; a directory named `tests` marks one whatever these say
DEFAULT_TEST_PATTERNS = ("test_*", "*_test.*", "*.test.*", "*.spec.*")
# the placeholders `checks.affected` may name: the run's base ref and the ticket's paths
AFFECTED_PLACEHOLDERS = ("base", "paths")
FULL_KEYS = ("run", "verify")
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


class ConfigError(ValueError):
    """The repo config is refused; the text names the dotted path and the fix."""


@dataclass(frozen=True)
class Checks:
    """The declared check tiers; each is optional, and `None` means the repo did not declare it."""

    fast: str | None
    affected: str | None
    full_run: str | None
    full_verify: bool


@dataclass(frozen=True)
class RepoConfig:
    plans_dir: Path
    acceptance_dir: str
    archive_mode: str
    checks: Checks | None = None
    test_patterns: tuple = DEFAULT_TEST_PATTERNS


def _path(root, value, dotted):
    if not isinstance(value, str) or not value:
        raise ConfigError(
            f"{CONFIG_PATH}: {dotted} must be a non-empty path — set it to a directory"
        )
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def _command(body, key, dotted, placeholders=False):
    """A check command: a non-empty string, its placeholders limited to `placeholders` when given."""
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"{CONFIG_PATH}: {dotted} must be a non-empty command string — write the command to run"
        )
    if placeholders:
        for name in PLACEHOLDER.findall(value):
            if name not in AFFECTED_PLACEHOLDERS:
                raise ConfigError(
                    f"{CONFIG_PATH}: {dotted}: placeholder {{{name}}} is not one of "
                    f"{', '.join('{' + p + '}' for p in AFFECTED_PLACEHOLDERS)} — "
                    f"use only those, or remove it"
                )
    return value


def _checks(body):
    """The `checks` node, its nested `full` mapping checked key by key."""
    full = body.get("full", {})
    if full is None:
        full = {}
    if not isinstance(full, dict):
        raise ConfigError(
            f"{CONFIG_PATH}: checks.full must be a mapping — write full: {{run: ..., verify: ...}}"
        )
    for key in full:
        if key not in FULL_KEYS:
            raise ConfigError(
                f"{CONFIG_PATH}: checks.full.{key}: unknown key — remove it (known: {', '.join(FULL_KEYS)})"
            )
    verify = full.get("verify", True)
    if not isinstance(verify, bool):
        raise ConfigError(
            f"{CONFIG_PATH}: checks.full.verify must be true or false — write verify: true or verify: false"
        )
    return Checks(
        fast=_command(body, "fast", "checks.fast"),
        affected=_command(body, "affected", "checks.affected", placeholders=True),
        full_run=_command(full, "run", "checks.full.run"),
        full_verify=verify,
    )


def _test_patterns(body):
    value = body.get("patterns", list(DEFAULT_TEST_PATTERNS))
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(p, str) and p.strip() for p in value)
    ):
        raise ConfigError(
            f"{CONFIG_PATH}: tests.patterns must be a non-empty list of file-name globs — "
            "write patterns: ['test_*', '*.spec.*']"
        )
    return tuple(value)


def load(root):
    """The repo's config under `root`, defaults filled in; ConfigError for an unknown key or a bad value."""
    root = Path(root)
    file = root / CONFIG_PATH
    data = {}
    if file.is_file():
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise ConfigError(f"{CONFIG_PATH}: cannot be read ({error}) — fix the YAML") from error
        data = {} if data is None else data
    if not isinstance(data, dict):
        raise ConfigError(
            f"{CONFIG_PATH}: must be a mapping of nodes — use plans, acceptance, archive"
        )
    for node, body in data.items():
        if node not in NODES:
            raise ConfigError(
                f"{CONFIG_PATH}: {node}: unknown key — remove it (known: {', '.join(NODES)})"
            )
        if not isinstance(body, dict):
            raise ConfigError(
                f"{CONFIG_PATH}: {node} must be a mapping — write {node}: {{{NODES[node][0]}: ...}}"
            )
        for key in body:
            if key not in NODES[node]:
                raise ConfigError(
                    f"{CONFIG_PATH}: {node}.{key}: unknown key — remove it (known: {', '.join(NODES[node])})"
                )
    plans = _path(root, data.get("plans", {}).get("dir", "docs/plans"), "plans.dir")
    acceptance = data.get("acceptance", {}).get("dir", "acceptance")
    if not isinstance(acceptance, str) or not acceptance:
        raise ConfigError(f"{CONFIG_PATH}: acceptance.dir must be a non-empty directory name")
    mode = data.get("archive", {}).get("mode", "promote")
    if mode not in ARCHIVE_MODES:
        raise ConfigError(
            f"{CONFIG_PATH}: archive.mode: {mode!r} is not a mode — use one of {' | '.join(ARCHIVE_MODES)}"
        )
    checks = _checks(data["checks"]) if "checks" in data else None
    patterns = _test_patterns(data.get("tests", {}))
    return RepoConfig(plans, acceptance, mode, checks, patterns)
