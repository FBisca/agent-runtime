"""The runtime's verdict on an assignment, and the words the Lead reads for it.

`wait` answers an assignment with `attention` and `next` once it has decided the attempt needs a person: a resume
whose worker produced nothing (`not_started`), or a turn with no progress for 15 minutes (`stalled`). The idle tick
asks the same question from screen freshness and answers it two ticks (20 minutes) later, so the drive reads the
verdict here instead of asking a slower question of its own. Only `not_started` is acted on: One idle watchdog keeps a running
turn waiting `--working-minutes` however quiet the runtime finds it, so `stalled` is not the tick's to defer to;
a resume that never started has no turn to wait on.
"""

SILENT = (
    "idle",
    "done",
)  # runtime_state of a `working` assignment whose turn ended: a live agent, maybe waiting on a background job
TURN_ENDED = SILENT + (
    "unreported",
    "error",
)  # runtime_state of a `working` assignment that needs attention
ATTENTION = {
    "blocked": "reported blocked",
    "decision": "asked for a decision",
    "exited": "ended without a report",
    # The way out is the driver's, because the runtime never resends the task: the worker's terminal is the only
    # place the attempt still exists, and a fresh worker on the round is what replaces a pane that has finished.
    "not_started": (
        "was resumed but produced nothing: look at its terminal and, if it has finished, stop it and put a "
        "fresh worker on the round"
    ),
}
VERDICTS = ("not_started",)


def attention(assignments):
    """(assignment id, kind, what the runtime says next, whether the agent is a silent live one) for each
    assignment that needs a person, in one pass. A verdict the runtime already reached is not re-asked from
    screen text: it is the answer to the idle tick's question, delivered on the read the drive already made."""
    for assignment in assignments:
        assignment_id = assignment.get("assignment_id")
        state, runtime = assignment.get("state"), assignment.get("runtime_state")
        verdict = assignment.get("attention")
        if not assignment_id:
            continue
        if verdict in VERDICTS:
            yield assignment_id, verdict, assignment.get("next") or "", False
        elif state in ("blocked", "decision"):
            yield assignment_id, state, "", False
        elif state == "failed" or (state == "working" and runtime in TURN_ENDED):
            yield (
                assignment_id,
                "exited",
                "runtime state " + runtime if runtime else "",
                state == "working" and runtime in SILENT,
            )
