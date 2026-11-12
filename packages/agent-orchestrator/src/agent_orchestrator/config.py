"""The repo config, `<repo>/.orchestrate/config.yaml`: where plans live and how they archive.

Only these nodes are read. A missing file means the defaults; reading never creates anything."""

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_PATH = Path(".orchestrate") / "config.yaml"
ARCHIVE_MODES = ("promote", "discard")
# node -> its keys: the whole accepted shape, so an unknown key is refused by its dotted path.
NODES = {"plans": ("dir",), "acceptance": ("dir",), "archive": ("mode",)}


class ConfigError(ValueError):
    """The repo config is refused; the text names the dotted path and the fix."""


@dataclass(frozen=True)
class RepoConfig:
    plans_dir: Path
    acceptance_dir: str
    archive_mode: str


def _path(root, value, dotted):
    if not isinstance(value, str) or not value:
        raise ConfigError(
            f"{CONFIG_PATH}: {dotted} must be a non-empty path — set it to a directory"
        )
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


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
    return RepoConfig(plans, acceptance, mode)
