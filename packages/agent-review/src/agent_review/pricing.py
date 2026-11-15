"""The declared price table: what a screening cost, or that nobody knows.

A screening's cost is read from a table the caller declares, against the model the provider answered with,
never against the alias the bank asked for (D24, D26): an alias names what was requested, and what answered
is what was charged for. The report carries the cost with its basis - the table, its version, the model and
both per-million rates - so a frozen report can be priced again and a benchmark can say what it spent.

A served model the table does not name is unknown, never zero, and so is a run that declared no table at
all: a zero reads as free and would hide a paid screening from the totals. A table a rule cannot read is
refused by its field path before anything is charged for; the shape is validated here, and `--pricing`
names the file.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent_review.errors import ReviewError
from agent_review.numbers import number

# The unit a table states its rates in: a price per million tokens.
PER_MILLION = 1_000_000
TOP_LEVEL = ("version", "models")
RATES = ("input_per_million", "output_per_million")
NO_TABLE = "no price table was declared, so this screening's cost is unknown; pass --pricing <path> to price it"


@dataclass(frozen=True)
class Price:
    """What one model charges per million input and output tokens, as the table declares it."""

    input_per_million: float
    output_per_million: float

    def as_dict(self) -> dict[str, float]:
        """The rates as the report's basis carries them, in the table's own terms."""
        return {
            "input_per_million": self.input_per_million,
            "output_per_million": self.output_per_million,
        }


@dataclass(frozen=True)
class Cost:
    """One screening's cost: the dollars when the table priced the served model, and why when it did not."""

    usd: float | None = None
    basis: Mapping[str, Any] | None = None
    unknown: str | None = None

    def as_usage(self) -> dict[str, Any]:
        """The cost as `report.json`'s `usage` holds it: the dollars, their basis, or the reason neither."""
        return {"cost_usd": self.usd, "cost_basis": self.basis, "cost_unknown": self.unknown}


@dataclass(frozen=True)
class PriceTable:
    """The table one invocation prices with: its version, the rates per served model, and where it was read."""

    version: int | str
    models: Mapping[str, Price]
    source: str = ""

    def cost(self, served: str | None, *, input_tokens: int, output_tokens: int) -> Cost:
        """What this table charges for `served`'s tokens, or why the cost is unknown (D24, D26)."""
        if served is None:
            return Cost(
                unknown=(
                    "the provider named no served model, so the cost is unknown; a model the answer does "
                    "not name cannot be priced"
                )
            )
        price = self.models.get(served)
        if price is None:
            return Cost(
                unknown=(
                    f"the price table {self.source} (version {self.version}) names no price for the "
                    f"served model {served}; add its rates to the table - an unpriced model is unknown, "
                    "never zero"
                )
            )
        usd = (
            input_tokens * price.input_per_million + output_tokens * price.output_per_million
        ) / PER_MILLION
        return Cost(
            usd=usd,
            basis={
                "pricing": self.source,
                "version": self.version,
                "model": served,
                **price.as_dict(),
            },
        )


def no_table() -> Cost:
    """The cost of a run that declared no table: unknown, so it is never read as free."""
    return Cost(unknown=NO_TABLE)


def load(path: Path | str) -> PriceTable:
    """One declared price table, or a refusal naming the field by its path before anything is charged for."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReviewError(
            f"cannot read the price table {path}: {error.strerror}; point --pricing at a YAML file"
        ) from None
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ReviewError(f"the price table {path} is not YAML: {error}") from None
    if not isinstance(data, dict):
        raise ReviewError(f"the price table {path} must be a mapping of version and models")
    unknown = sorted(set(data) - set(TOP_LEVEL))
    if unknown:
        raise ReviewError(
            f"the price table {path} has an unknown field {unknown[0]!r}; it takes {', '.join(TOP_LEVEL)}"
        )
    return PriceTable(version=_version(data, path), models=_models(data, path), source=str(path))


def _version(data: dict[str, Any], path: Path) -> int | str:
    """The table's version, so a frozen report says which table priced it (D26)."""
    if "version" not in data:
        raise ReviewError(
            f"the price table {path} names no version: add `version: <n>` so a frozen report says which "
            "table priced it"
        )
    version = data["version"]
    if isinstance(version, bool) or not isinstance(version, (int, str)) or not str(version).strip():
        raise ReviewError(
            f"{path}: version must be a whole number or a non-empty name, not {version!r}; it is what a "
            "frozen report names beside the cost"
        )
    return version


def _models(data: dict[str, Any], path: Path) -> Mapping[str, Price]:
    """The rate per served model, refused by path when a name or an entry is not one a rule can read."""
    if "models" not in data:
        raise ReviewError(
            f"the price table {path} names no models: add `models: {{<served model>: "
            "{input_per_million: <number>, output_per_million: <number>}}}`"
        )
    declared = data["models"]
    if not isinstance(declared, dict):
        raise ReviewError(
            f"{path}: models must be a mapping of served model to its rates, not "
            f"{type(declared).__name__}"
        )
    models: dict[str, Price] = {}
    for name, entry in declared.items():
        if not isinstance(name, str) or not name.strip():
            raise ReviewError(
                f"{path}: models names {name!r}, which is not a served model name; name the model the "
                "provider answers with"
            )
        models[name] = _price(entry, f"models[{name!r}]", path)
    return models


def _price(entry: Any, field_name: str, path: Path) -> Price:
    """One model's rates, each required and read as the number it is."""
    if not isinstance(entry, dict):
        raise ReviewError(
            f"{path}: {field_name} must be a mapping with {', '.join(RATES)}, not "
            f"{type(entry).__name__}"
        )
    unknown = sorted(set(entry) - set(RATES))
    if unknown:
        raise ReviewError(
            f"{path}: {field_name} has an unknown field {unknown[0]!r}; it takes {', '.join(RATES)}"
        )
    rates: dict[str, float] = {}
    for rate in RATES:
        if rate not in entry:
            raise ReviewError(
                f"{path}: {field_name}.{rate} is missing: add the per-million price this model is "
                "charged at"
            )
        rates[rate] = number(entry[rate], f"{field_name}.{rate}", path)
    return Price(**rates)
