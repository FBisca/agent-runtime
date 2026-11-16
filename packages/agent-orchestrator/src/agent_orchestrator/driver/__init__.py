"""Small orchestration records and the `run` dispatch driver.

No model calls and no shell evaluation. Only `run` starts workers, and only through `agent-runtime call`.

The package is a facade: every engine collaborator lives in the module beside it - the state and its
locks in `state.py`, the round in `lifecycle.py`, a worker's report and the Lead's answer in
`answers.py` and `findings.py`, the routing in `routing.py`, the evidence in `evidence_runs.py`, what
a unit delivered in `coverage.py`, the Lead's attention in `attention.py`, the handlers in
`commands.py` - and the names below are re-exported so a caller, a test and the entry points keep
asking `driver.<name>`.
"""

__all__ = [
    "ACCEPTED_STATES",
    "ACCEPT_READS",
    "AMENDING",
    "ARCHIVE",
    "ARCHIVE_MENU",
    "ARCHIVE_REBUILDS",
    "ATTEMPT_LETTERS",
    "ATTENTION",
    "Answers",
    "Any",
    "Archive",
    "ArchiveSurface",
    "Attention",
    "BACKENDS",
    "BUILDERS",
    "Book",
    "BookSurface",
    "CHECKS_WARNING",
    "CHECK_PREFIXES",
    "CI_EXCERPT_CHARS",
    "CONTRACT",
    "Coverage",
    "DECISION_KEYS",
    "DRIVER_ID",
    "DRIVER_MARKS",
    "Driver",
    "Evidence",
    "FAILURE_LINE",
    "Findings",
    "Gates",
    "IntentUnsettled",
    "JsonRefusal",
    "Lifecycle",
    "MATRIX",
    "MUTATION_ATTEMPTS",
    "NO_TEST_MENU",
    "PACKET_TEXT",
    "PARKED",
    "PARK_ASK_EVERY",
    "PARK_ASK_FIRST",
    "PAYLOAD_GRACE",
    "PAYLOAD_WARNINGS",
    "PROXY_INSTRUCTION",
    "Path",
    "PlanError",
    "Pull",
    "PullSurface",
    "ROUTE",
    "Refused",
    "Routes",
    "SILENT",
    "SPEC_HEADING",
    "SPEC_MARKER",
    "SPEC_REQUIREMENT",
    "SPEC_ROW",
    "STEPS",
    "SpawnSurface",
    "Spawning",
    "SpecSections",
    "TDD_SKILL_ENV",
    "TDD_SKILL_PLACES",
    "TREE_TIMEOUT",
    "TURN_ENDED",
    "USAGE_COLUMNS",
    "Unreadable",
    "VERDICTS",
    "YAML_ARCHIVE_ACCEPTANCE",
    "accept_item",
    "acceptance",
    "acceptance_context",
    "accepted",
    "add_accept_command",
    "add_await_command",
    "add_complete_command",
    "add_context_command",
    "add_decide_command",
    "add_evidence_command",
    "add_inspect_command",
    "add_launch_command",
    "add_ledger_command",
    "add_metrics_command",
    "add_plan_commands",
    "add_plans_commands",
    "add_run_command",
    "add_status_command",
    "add_task_commands",
    "add_verify_command",
    "add_workbench_commands",
    "approval_recovery",
    "approval_refusal",
    "archive_retry_allowed",
    "archive_retry_refusal",
    "assignment_of",
    "attempt_pattern",
    "attempt_trail",
    "attention",
    "await_lock_path",
    "await_wake",
    "bind",
    "build_parser",
    "cell",
    "check_commit",
    "checked_free",
    "checked_packet",
    "checked_route",
    "checked_texts",
    "checkout_snapshot",
    "checkpoint",
    "claim",
    "collection",
    "complete_person_ticket",
    "context_verdict",
    "contracts",
    "copy",
    "crash_line",
    "decide",
    "decided",
    "decision_version",
    "describe",
    "dispatch",
    "drive",
    "drive_locked",
    "driver_gone",
    "enter_plan_state",
    "events",
    "evidence",
    "failure_line",
    "foreign_attempt",
    "forwarded",
    "gh_process",
    "head_of",
    "import_old_ledger",
    "invariants",
    "is_agent",
    "is_done",
    "is_flaky",
    "is_yaml",
    "item_text",
    "job_log",
    "last_mark",
    "launch",
    "ledger",
    "ledgers",
    "legacy_imports",
    "legacy_markdown_view",
    "live_terminals",
    "load_approved",
    "locate_state",
    "lock_held",
    "log_tail",
    "main",
    "merge_requirements",
    "merge_state",
    "merged_snapshot",
    "merged_snapshot_of",
    "metrics",
    "metrics_table",
    "named_collection",
    "narrate",
    "newest_attempts",
    "next_path",
    "normalise_check",
    "now",
    "numbered",
    "old_ledger_of",
    "owned_collections",
    "owned_paths",
    "owned_scope",
    "owns_ledger",
    "pane_name",
    "passed_checks",
    "payload_waits",
    "plan_home",
    "plan_slug",
    "plan_state_of",
    "plan_status",
    "re",
    "read_decision",
    "read_json",
    "read_ledger_file",
    "read_repo_config",
    "readable_request",
    "ready",
    "rebuild_evidence",
    "rebuild_metrics",
    "recover_worker_report",
    "reference",
    "release",
    "render_status",
    "repo_config",
    "repo_key",
    "repo_root",
    "report_crash",
    "report_reader",
    "require",
    "require_completable",
    "require_report",
    "require_yaml",
    "resolve_runtime",
    "review_brief",
    "routed",
    "routes",
    "run",
    "run_cli",
    "run_dir_of",
    "run_finished",
    "run_lock",
    "run_lock_path",
    "runner",
    "runtime_call",
    "runtime_process",
    "satisfied",
    "scenario_number",
    "settle_merged",
    "short",
    "show",
    "snapshot_dir",
    "snapshot_of",
    "spec_defects",
    "split_row",
    "split_status",
    "state_layout",
    "step_failed",
    "subprocess",
    "sum_usage",
    "take_wake",
    "task_summary",
    "task_text",
    "tdd_instruction",
    "tdd_skill_path",
    "terminal_gone",
    "time",
    "timeutil",
    "tree_in",
    "unique_checks",
    "unit_summary",
    "unreadable_decision",
    "verify",
    "verify_plan",
    "whole",
    "without_coverage",
    "workbench",
    "worker_name",
    "write_atomic",
    "yaml_plan",
]

import copy
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .. import approval_recovery as approval_recovery
from .. import config as repo_config
from .. import contracts as contracts
from .. import events as events
from .. import evidence as evidence
from .. import invariants as invariants
from .. import ledger as ledgers
from .. import narrate as narrate
from .. import plan as yaml_plan
from .. import routes as routes
from .. import runner as runner
from .. import timeutil as timeutil
from .. import verify as verify
from .. import workbench as workbench
from ..invariants import AMENDING as AMENDING
from ..invariants import PARKED as PARKED
from ..invariants import checked_write as ledger
from ..invariants import next_path as next_path
from ..plan import PlanError as PlanError
from ..report_rules import require as require
from ..report_rules import require_report as require_report
from ..runtime_client import approval_refusal as approval_refusal
from .answers import ARCHIVE_MENU as ARCHIVE_MENU
from .answers import ARCHIVE_REBUILDS as ARCHIVE_REBUILDS
from .answers import ATTEMPT_LETTERS as ATTEMPT_LETTERS
from .answers import DECISION_KEYS as DECISION_KEYS
from .answers import FAILURE_LINE as FAILURE_LINE
from .answers import NO_TEST_MENU as NO_TEST_MENU
from .answers import Answers as Answers
from .answers import IntentUnsettled as IntentUnsettled
from .answers import Unreadable as Unreadable
from .answers import attempt_pattern as attempt_pattern
from .answers import decide as decide
from .answers import decided as decided
from .answers import decision_version as decision_version
from .answers import failure_line as failure_line
from .answers import foreign_attempt as foreign_attempt
from .answers import read_decision as read_decision
from .answers import readable_request as readable_request
from .answers import unreadable_decision as unreadable_decision
from .archive import DRIVER_ID as DRIVER_ID
from .archive import PARK_ASK_EVERY as PARK_ASK_EVERY
from .archive import PARK_ASK_FIRST as PARK_ASK_FIRST
from .archive import YAML_ARCHIVE_ACCEPTANCE as YAML_ARCHIVE_ACCEPTANCE
from .archive import Archive as Archive
from .archive import ArchiveSurface as ArchiveSurface
from .archive import crash_line as crash_line
from .archive import report_crash as report_crash
from .book import ARCHIVE as ARCHIVE
from .book import BACKENDS as BACKENDS
from .book import PACKET_TEXT as PACKET_TEXT
from .book import Book as Book
from .book import BookSurface as BookSurface
from .book import bind as bind
from .book import checked_free as checked_free
from .book import checked_packet as checked_packet
from .book import checked_route as checked_route
from .book import checked_texts as checked_texts
from .book import checkpoint as checkpoint
from .book import claim as claim
from .book import merged_snapshot_of as merged_snapshot_of
from .book import owned_paths as owned_paths
from .book import release as release
from .book import settle_merged as settle_merged
from .book import snapshot_of as snapshot_of
from .book import task_summary as task_summary
from .book import unit_summary as unit_summary
from .briefs import Briefs as Briefs
from .cli import BUILDERS as BUILDERS
from .cli import add_accept_command as add_accept_command
from .cli import add_await_command as add_await_command
from .cli import add_complete_command as add_complete_command
from .cli import add_context_command as add_context_command
from .cli import add_decide_command as add_decide_command
from .cli import add_evidence_command as add_evidence_command
from .cli import add_inspect_command as add_inspect_command
from .cli import add_launch_command as add_launch_command
from .cli import add_ledger_command as add_ledger_command
from .cli import add_metrics_command as add_metrics_command
from .cli import add_plan_commands as add_plan_commands
from .cli import add_plans_commands as add_plans_commands
from .cli import add_run_command as add_run_command
from .cli import add_status_command as add_status_command
from .cli import add_task_commands as add_task_commands
from .cli import add_verify_command as add_verify_command
from .cli import add_workbench_commands as add_workbench_commands
from .cli import build_parser as build_parser
from .cli import main as main
from .cli import run_cli as run_cli
from .commands import ACCEPT_READS as ACCEPT_READS
from .commands import accept_item as accept_item
from .commands import accepted as accepted
from .commands import await_wake as await_wake
from .commands import complete_person_ticket as complete_person_ticket
from .commands import dispatch as dispatch
from .commands import drive as drive
from .commands import drive_locked as drive_locked
from .commands import launch as launch
from .commands import legacy_markdown_view as legacy_markdown_view
from .commands import plan_status as plan_status
from .commands import rebuild_evidence as rebuild_evidence
from .commands import rebuild_metrics as rebuild_metrics
from .commands import verify_plan as verify_plan
from .coverage import Coverage as Coverage
from .engine import Driver as Driver
from .evidence_runs import MUTATION_ATTEMPTS as MUTATION_ATTEMPTS
from .evidence_runs import Evidence as Evidence
from .evidence_runs import acceptance as acceptance
from .evidence_runs import acceptance_context as acceptance_context
from .evidence_runs import tree_in as tree_in
from .findings import Findings as Findings
from .gates import STEPS as STEPS
from .gates import Gates as Gates
from .gates import step_failed as step_failed
from .handoff import recover_worker_report as recover_worker_report
from .lifecycle import Lifecycle as Lifecycle
from .lifecycle import metrics as metrics
from .lifecycle import sum_usage as sum_usage
from .pointers import collection as collection
from .pointers import named_collection as named_collection
from .pointers import owned_collections as owned_collections
from .pointers import owned_scope as owned_scope
from .pointers import reference as reference
from .pointers import task_text as task_text
from .pointers import tdd_instruction as tdd_instruction
from .pull import CI_EXCERPT_CHARS as CI_EXCERPT_CHARS
from .pull import Pull as Pull
from .pull import PullSurface as PullSurface
from .pull import job_log as job_log
from .pull import log_tail as log_tail
from .records import CHECK_PREFIXES as CHECK_PREFIXES
from .records import PROXY_INSTRUCTION as PROXY_INSTRUCTION
from .records import ROUTE as ROUTE
from .records import assignment_of as assignment_of
from .records import check_commit as check_commit
from .records import context_verdict as context_verdict
from .records import is_agent as is_agent
from .records import is_done as is_done
from .records import is_flaky as is_flaky
from .records import merge_state as merge_state
from .records import newest_attempts as newest_attempts
from .records import normalise_check as normalise_check
from .records import passed_checks as passed_checks
from .records import ready as ready
from .records import report_reader as report_reader
from .records import require_completable as require_completable
from .records import routed as routed
from .records import satisfied as satisfied
from .records import scenario_number as scenario_number
from .records import split_row as split_row
from .records import terminal_gone as terminal_gone
from .records import unique_checks as unique_checks
from .records import whole as whole
from .report import USAGE_COLUMNS as USAGE_COLUMNS
from .report import cell as cell
from .report import describe as describe
from .report import item_text as item_text
from .report import metrics_table as metrics_table
from .report import render_status as render_status
from .report import short as short
from .report import show as show
from .review import attempt_trail as attempt_trail
from .review import review_brief as review_brief
from .routing import CHECKS_WARNING as CHECKS_WARNING
from .routing import CONTRACT as CONTRACT
from .routing import MATRIX as MATRIX
from .routing import TDD_SKILL_ENV as TDD_SKILL_ENV
from .routing import TDD_SKILL_PLACES as TDD_SKILL_PLACES
from .routing import Routes as Routes
from .routing import forwarded as forwarded
from .routing import is_yaml as is_yaml
from .routing import live_terminals as live_terminals
from .routing import load_approved as load_approved
from .routing import pane_name as pane_name
from .routing import read_repo_config as read_repo_config
from .routing import repo_key as repo_key
from .routing import repo_root as repo_root
from .routing import require_yaml as require_yaml
from .routing import snapshot_dir as snapshot_dir
from .routing import tdd_skill_path as tdd_skill_path
from .routing import worker_name as worker_name
from .runtime import TREE_TIMEOUT as TREE_TIMEOUT
from .runtime import checkout_snapshot as checkout_snapshot
from .runtime import gh_process as gh_process
from .runtime import head_of as head_of
from .runtime import merged_snapshot as merged_snapshot
from .runtime import resolve_runtime as resolve_runtime
from .runtime import run as run
from .runtime import runtime_call as runtime_call
from .runtime import runtime_process as runtime_process
from .spawn import Spawning as Spawning
from .spawn import SpawnSurface as SpawnSurface
from .spec import SPEC_HEADING as SPEC_HEADING
from .spec import SPEC_MARKER as SPEC_MARKER
from .spec import SPEC_REQUIREMENT as SPEC_REQUIREMENT
from .spec import SPEC_ROW as SPEC_ROW
from .spec import SpecSections as SpecSections
from .spec import merge_requirements as merge_requirements
from .spec import spec_defects as spec_defects
from .spec import without_coverage as without_coverage
from .state import DRIVER_MARKS as DRIVER_MARKS
from .state import JsonRefusal as JsonRefusal
from .state import Refused as Refused
from .state import await_lock_path as await_lock_path
from .state import driver_gone as driver_gone
from .state import enter_plan_state as enter_plan_state
from .state import import_old_ledger as import_old_ledger
from .state import last_mark as last_mark
from .state import legacy_imports as legacy_imports
from .state import locate_state as locate_state
from .state import lock_held as lock_held
from .state import numbered as numbered
from .state import old_ledger_of as old_ledger_of
from .state import owns_ledger as owns_ledger
from .state import plan_home as plan_home
from .state import plan_slug as plan_slug
from .state import read_ledger_file as read_ledger_file
from .state import run_dir_of as run_dir_of
from .state import run_finished as run_finished
from .state import run_lock as run_lock
from .state import run_lock_path as run_lock_path
from .state import split_status as split_status
from .state import state_layout as state_layout
from .state import take_wake as take_wake
from .state import write_atomic as write_atomic
from .verdict import ATTENTION as ATTENTION
from .verdict import SILENT as SILENT
from .verdict import TURN_ENDED as TURN_ENDED
from .verdict import VERDICTS as VERDICTS
from .verdict import attention as attention
from .watch import ACCEPTED_STATES as ACCEPTED_STATES
from .watch import PAYLOAD_GRACE as PAYLOAD_GRACE
from .watch import PAYLOAD_WARNINGS as PAYLOAD_WARNINGS
from .watch import Attention as Attention
from .watch import archive_retry_allowed as archive_retry_allowed
from .watch import archive_retry_refusal as archive_retry_refusal
from .watch import payload_waits as payload_waits
from .watch import plan_state_of as plan_state_of
from .watch import read_json as read_json

now = timeutil.iso
