"""The ledger's state invariants: checked where the driver writes it, readable as `ledger check`.

Three states stalled a run and each was found by hand afterwards: two open attempts naming one review file, a park
whose release key was lost, and a gate the driver does not know. Each is refused by name — the invariant, the task
and the way out — at the write and on a captured ledger, so the next spelling is refused instead of discovered. A
`LedgerInvariant` is a `ValueError`, so a gate step's own handler turns it into a request that names the way out.
Standard library only: the plugin ships without dependencies."""

AMENDING = "awaiting-amendment"  # the Lead decided `amend`: the unit waits for the driver to adopt the new plan
GATES = (
    "reported",
    "converged",
    "reviewing",
    "reviewed",
    "fix-pending",
    "requested",
    "decided",
    "redo-pending",
    "ready-for-ci",
    "awaiting-merge",
    "cleanup",
)  # ledger `gate` stages the loop acts on
PARKED = (
    "attention",
    "blocked",
    "skipped",
    AMENDING,
)  # nothing runs for the unit until the user acts (`attention`: runs of the old driver) or, for AMENDING, until the
# driver adopts an amendment
SETTLED = (
    "done",
    "superseded",
)  # the unit has left the gates: a later attempt owns whatever comes next

REVIEW_FILE_SHARED = "review-file-shared"
PARK_WITHOUT_KEY = "park-without-key"
UNKNOWN_GATE = "unknown-gate"

WAY_OUT = {
    REVIEW_FILE_SHARED: "give each open attempt its own review file: the rework writes the next free "
    "<unit>/review.<n>.json, and the attempt that no longer runs is released",
    PARK_WITHOUT_KEY: "record the approval digest the park is released by on the task (`decide amend` writes it as "
    "`amend_from`), or release the unit instead of parking it",
    UNKNOWN_GATE: "name a gate the driver knows (%s), or release the task and let the drive write it again"
    % ", ".join(GATES + PARKED + SETTLED),
}


class LedgerInvariant(ValueError):
    """A ledger state the driver refuses: its name, the task that holds it and the way out, so the message alone can
    be turned into a request a reader can act on."""

    def __init__(self, invariant, task, detail):
        self.invariant = invariant
        self.task = task
        self.way_out = WAY_OUT[invariant]
        self.detail = detail
        super().__init__(
            "ledger invariant %s: task %s %s; way out: %s" % (invariant, task, detail, self.way_out)
        )

    @property
    def output(self):
        """The one JSON object a refused command prints: the invariant, the task and the way out, by name."""
        return {
            "error": "the ledger breaks an invariant",
            "invariant": self.invariant,
            "task": self.task,
            "reason": self.detail,
            "way_out": self.way_out,
        }


def record(task):
    """One task of the ledger as a mapping: anything else is a corrupt record, refused by the invariants that read it."""
    return task if isinstance(task, dict) else {}


def review_file(task):
    """The review file this attempt names, or None when it names none."""
    review = record(task).get("review")
    return review.get("path") if isinstance(review, dict) else None


def open_attempt(task):
    """True while the attempt can still act: neither released nor settled leaves a review file to a later attempt."""
    task = record(task)
    return task.get("status") not in ("COMPLETE", "RELEASED") and task.get("gate") not in SETTLED


def shape(data):
    """What the invariants read, as one comparable value: a writer compares it before and after its block, so a block
    that changed none of it costs a comparison instead of a scan."""
    return tuple(
        (
            task_id,
            record(task).get("gate"),
            record(task).get("status"),
            review_file(task),
            record(task).get("amend_from"),
        )
        for task_id, task in sorted((data.get("tasks") or {}).items())
    )


def violations(data):
    """Every illegal state the ledger holds, in a stable order: one review file per open attempt, a park that names
    the approval that releases it, a gate the driver knows."""
    tasks = sorted((data.get("tasks") or {}).items())
    found, holders = [], {}
    for task_id, task in tasks:
        path = review_file(task)
        if not path or not open_attempt(task):
            continue
        if path in holders:
            found.append(
                LedgerInvariant(
                    REVIEW_FILE_SHARED,
                    task_id,
                    "names the review file %s that %s holds open too" % (path, holders[path]),
                )
            )
        else:
            holders[path] = task_id
    for task_id, task in tasks:
        task, gate = record(task), record(task).get("gate")
        if gate == AMENDING and not task.get("amend_from"):
            found.append(
                LedgerInvariant(
                    PARK_WITHOUT_KEY,
                    task_id,
                    "waits for an amendment with no `amend_from`: nothing can release it",
                )
            )
        elif gate is not None and gate not in GATES + PARKED + SETTLED:
            found.append(LedgerInvariant(UNKNOWN_GATE, task_id, "names the gate %r" % (gate,)))
    return found


def check(data):
    """Refuse the first illegal state the ledger holds; a coherent ledger returns."""
    found = violations(data)
    if found:
        raise found[0]
