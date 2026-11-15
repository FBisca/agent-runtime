"""The ledger's shape for a worker's report: the fields `checkpoint` reads from it and the one value a check
outcome can be.

It lives here rather than beside `checkpoint` because the driver module is over its line ceiling at the base
revision and may not grow, while the report gate needs these rules before anything writes a report: a module of
its own lets both call one definition and adds no line to the driver.
"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def require_report(report):
    """The ledger's shape for a worker's report, whatever its status. `checkpoint` and the report gate share it, so
    a report the ledger cannot accept is refused in the same words wherever it lands.

    `review_rounds` is not here: it is the driver's own count, and the driver overwrites whatever the worker claims
    before the ledger stores it, so a claim about it is never the shape of the report."""
    require(isinstance(report, dict), "report must be an object")
    require(report.get("status") in ("PARTIAL", "BLOCKED", "COMPLETE"), "invalid result status")
    require(
        isinstance(report.get("summary"), str) and report["summary"].strip(), "summary is required"
    )
    for key in ("files", "decisions", "interfaces", "remaining", "checks"):
        require(isinstance(report.get(key), list), key + " must be a list")
    for check in report["checks"]:
        require(isinstance(check, dict) and isinstance(check.get("command"), str), "invalid check")
        require(
            check.get("outcome") in ("passed", "failed", "not_run"),
            "invalid check outcome: checks[].outcome must be `passed`, `failed` or `not_run`",
        )
        require(
            check.get("outcome") == "passed" or check.get("reason"),
            "failed or omitted check needs a reason",
        )
