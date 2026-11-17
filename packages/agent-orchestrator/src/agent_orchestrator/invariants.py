"""The ledger's state invariants: checked where the driver writes it, readable as `ledger check`.

A state stalled a run and was found by hand afterwards: a gate the driver does not know. It is refused by name — the
invariant, the task and the way out — at the write and on a captured ledger, so the next spelling is refused instead
of discovered. A
`LedgerInvariant` is a `ValueError`, so a gate step's own handler turns it into a request that names the way out.
Standard library only: the plugin ships without dependencies."""

from contextlib import contextmanager

from . import ledger as ledgers

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
)  # nothing runs for the unit until the user acts (`attention`: runs of the old driver)
SETTLED = (
    "done",
    "superseded",
)  # the unit has left the gates: a later attempt owns whatever comes next

UNKNOWN_GATE = "unknown-gate"

WAY_OUT = {
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


def next_path(directory, stem, suffix):
    """The first write-once file name free in `directory`: <stem><suffix>, <stem>.2<suffix>, ..."""
    for index in range(1, 1000):
        path = directory / (stem + ("" if index == 1 else ".%d" % index) + suffix)
        if not path.exists():
            return path, index
    raise ValueError("too many %s files in %s" % (stem, directory))


def shape(data):
    """What the invariants read, as one comparable value: a writer compares it before and after its block, so a block
    that changed none of it costs a comparison instead of a scan."""
    return tuple(
        (
            task_id,
            record(task).get("gate"),
            record(task).get("status"),
        )
        for task_id, task in sorted((data.get("tasks") or {}).items())
    )


def violations(data):
    """Every illegal state the ledger holds, in a stable order: a gate the driver does not know."""
    found = []
    for task_id, task in sorted((data.get("tasks") or {}).items()):
        gate = record(task).get("gate")
        if gate is not None and gate not in GATES + PARKED + SETTLED:
            found.append(LedgerInvariant(UNKNOWN_GATE, task_id, "names the gate %r" % (gate,)))
    return found


def check(data):
    """Refuse the first illegal state the ledger holds; a coherent ledger returns."""
    found = violations(data)
    if found:
        raise found[0]


@contextmanager
def checked_write(path, **options):
    """The ledger under its lock, through the module the evidence runner shares: a busy ledger is waited out under one
    deadline, and a ledger without the kind marker gets it before the block runs. A block that changed a state the
    invariants read is checked before the write, so an illegal state is refused where it is written and never stored;
    a read-only block compares one fingerprint and writes nothing."""
    with ledgers.locked(path, **options) as data:
        before = shape(data)
        yield data
        if shape(data) != before:
            check(data)


def check_command(path):
    """`ledger check`: a captured ledger the invariants hold on, or the first state they refuse, by name, with the
    task and the way out (`LedgerInvariant`: exit 2, nothing written). A read: it takes no lock, so a live run is not
    held up while its ledger is diagnosed."""
    data = ledgers.load(path)
    if data is None:
        raise ValueError(
            "no ledger at %s: pass the ledger a run wrote (`show --state` names it)" % path
        )
    data = ledgers.validate(data, path)
    check(data)
    return {"ok": True, "tasks": len(data["tasks"])}


def ledger_commands(commands):
    """`ledger check` on the CLI the driver builds: the read side of the invariants, for a captured ledger."""
    ledger = commands.add_parser(
        "ledger", help="Read a captured ledger the way the driver checks it where it writes"
    ).add_subparsers(dest="ledger_command", required=True)
    checked = ledger.add_parser(
        "check",
        help="Refuse the states the driver's invariants forbid (a park with no release key, an unknown gate); exit 2 names the invariant, the task and the way out",
    )
    checked.add_argument("path", help="A captured ledger (<state>/<slug>.json)")
