"""Field types and error wording shared by the label shapes."""

from datetime import date
from typing import Annotated

from pydantic import AfterValidator, Field, StringConstraints, ValidationError

Unit = Annotated[float, Field(ge=0, le=1)]


def real_date(value: str) -> str:
    """The pattern fixes the shape; the calendar decides whether the day exists."""
    date.fromisoformat(value)
    return value


Date = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"), AfterValidator(real_date)]


def field_path(loc: tuple[int | str, ...]) -> str:
    """`dimensions.quality.findings[0].evidence[1].quote`: a list index in brackets."""
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else part
    return path


def describe(error: ValidationError) -> str:
    return "; ".join(
        f"{field_path(tuple(item['loc'])) or 'label'}: {item['msg']}" for item in error.errors()
    )
