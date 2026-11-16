"""Recover worker reports when a runtime turn ends without a terminal message."""

from .. import contracts


def recover_worker_report(driver, task_id, assignment_id):
    """Accept only a valid report belonging to the current task attempt."""
    if driver.report_landed(task_id):
        driver.log(
            "report-landed", unit=driver.unit_of(task_id), task=task_id, assignment_id=assignment_id
        )
        driver.mark(task_id, gate="reported")
        return True
    path = driver.report_file(task_id)
    try:
        report = contracts.load(path, contracts.validate_report)
    except (contracts.ContractError, OSError, ValueError):
        return False
    if (
        report["task_id"] != task_id
        or report["status"] not in ("PARTIAL", "BLOCKED")
        or not driver.belongs_to_attempt(driver.task(task_id), path)
    ):
        return False
    reason = "%s: %s" % (report["status"], report["summary"])
    if report.get("remaining"):
        reason += "; remaining: " + "; ".join(report["remaining"])
    driver.request(task_id, "blocked", reason)
    return True
