"""Numbers a rule reads: a finite, non-negative declaration, refused by the field path it was declared at.

A screening never starts from a declaration no stage can use, and the rule is the same wherever a number
decides something - a calibrated threshold, a question's weight, a rate a price table charges. A boolean, a
text, a negative or an infinity is refused by the path it was declared at, so a long configuration is
fixable, and a band is a probability, so it is refused above one as well (D48).
"""

from typing import Any

from agent_review.errors import ReviewError


def number(value: Any, field_name: str, path: Any) -> float:
    """`value` as a finite number of zero or more, or a refusal naming the field by its path."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ReviewError(
            f"{path}: {field_name} must be a number of zero or more, not {value!r}; a number a rule "
            "cannot read would decide nothing"
        )
    declared = float(value)
    if declared != declared or declared in (float("inf"), float("-inf")):
        raise ReviewError(f"{path}: {field_name} must be a finite number, not {value!r}")
    return declared


def band(value: Any, field_name: str, path: Any) -> float:
    """A confidence band: a probability, so a number between zero and one (D48)."""
    declared = number(value, field_name, path)
    if declared > 1:
        raise ReviewError(
            f"{path}: {field_name} must be a confidence between 0 and 1, not {value!r}; a band above "
            "one would abstain on every answer"
        )
    return declared
