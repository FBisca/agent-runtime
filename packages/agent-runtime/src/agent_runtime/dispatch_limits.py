"""The store's limits and the words it refuses with: one home for every number a payload may not pass.

A family reads them the way the store always did, and `dispatch_store` republishes them, so a test
that imports `MAX_RESPONSE_BYTES` from the store measures the same number it measured before."""

MAX_WORKERS = MAX_MESSAGES = 32
# How many times a registration reads the terminal's teams again when another session registers at
# the same time: the read and the insert are separate transactions, so the picture can change.
TEAM_ATTEMPTS = 3
TEAM_LIMIT = "team limit is 32 active workers: stop an active worker, then retry"
MAX_TEXT_BYTES, MAX_BATCH_BYTES = 65536, 131072
# D22: the canonical compact-JSON result (every user-visible field, before the host's text +
# structuredContent copies) stays under MAX_RESPONSE_BYTES. A base payload (tool result before an
# inbox piggyback) stays under MAX_BASE_BYTES, so a legal 64 KiB report always fits beside it.
MAX_RESPONSE_BYTES, MAX_BASE_BYTES, MAX_PAGE_BYTES = 196608, 98304, 65536
# `wait` attaches a usage dict to each returned row after this store read. Every figure it can carry,
# at a width no real count reaches, is budgeted per row, so the attached response stays under its cap.
WORST_USAGE = {
    **dict.fromkeys(
        (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "output_tokens",
            "reasoning_tokens",
            "model_calls",
            "tool_calls",
        ),
        10**20 - 1,
    ),
    "cost_usd": 1234567890.1234567,
}

REPORT_KINDS = ("progress", "complete", "blocked", "decision")
HC_RESTARTED = (
    "the hc server restarted or this key predates server-instance tracking; "
    "inspect the earlier work, then use a new operation_id"
)
