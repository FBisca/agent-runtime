# Brief: a pointer names the thing, not how to find it

> **2026-11-17:** plan/1 no longer carries scenario evidence: the `evidence:` blocks and the plan folder's
> `acceptance/` files were removed. Each ticket's own tests are its evidence: a scenario it satisfies needs a
> covering test the ticket changes, mapped in its report's `covers`. Read any evidence or acceptance-file
> guidance below as the tests the implementing ticket writes. Its stale approval was dropped: run `plan approve`.

Slug: `locator-invariants`. Contract: `plan.yaml`. Lane: Full, regular·complex over three tickets;
it also repeals a numbered requirement another plan's frozen acceptance test proved, which a person
should see before approving.

## Why

A pointer that says *how to find* a thing instead of *what it is* fails silently for exactly the case
it was built for: the search still finds something, so nothing raises. Three instances appeared in
three components tonight, two of them already fixed (`#409` decides a package's files from git
instead of a filesystem walk; `#411` measures growth from `git merge-base <rev> HEAD` and reports the
revision it measured).

The third cost a real gate round. The driver's review packet told the reviewer to read *the newest
`report*.json` in `<run_dir>/<unit>`* (`driver/__init__.py:2289`), while a re-dispatched unit's report
lives in the per-attempt directory (`t1d1/report.json`). The reviewer could only ever find the first
attempt's file and judged an 08:45 `BLOCKED` report against a branch green at 11:44, so the gate
recorded a concern about the file rather than about the code. Beside it, `unit_trail`
(`driver/__init__.py:2570`) answers "the newest report over the unit's attempts" by dict order, and
the brief answers the same question by telling the reviewer to search: one question, two answers.

The rule that follows from this is stated in full in **The invariant** below: it is the contract of
this plan, the sentence its tickets are held to, and the requirement the last ticket writes into the
living spec.

## The invariant

**Every path the engine hands a reader is a path the engine holds.** It is printed as a path, resolved
once; where the environment already knows the answer — the ledger, git, the name the writer was given —
it is read there rather than reconstructed by search; and a pointer that resolves to nothing, or would
resolve to more than one thing, is a refusal that names the pointer and the way out.

Two clauses, because a search fails in two directions. A pointer that finds *nothing* is loud: the
reader says the file is not there and the driver asks again. A pointer that finds *the wrong thing* is
silent, and tonight's was silent — the reviewer read a real file and reported it honestly.

Three exemptions, stated so the rule cannot decay into a per-file allowance:

- **A collection the reader must enumerate** may be named, printed with its count (`6 files in <dir>`),
  never bare: the count is a fact the engine holds and the reader does not (D9).
- **A pattern the reader must expand** — the affected tier's map, a test's own sweep of `docs/specs` —
  is a rule about a set, not a pointer to a thing.
- **"The first name not taken"** — `report.2.json`, `task.v2.md` — asks for an absence, and the
  filesystem is the authority on absences.

**Where it is enforced.** Not by a list of approved pointers, which rots, but by a property over
rendered text: T2 renders every artifact a run produces — each attempt's task, the review brief, a fix
round's task, the answers task, the archive task, the hand-over, the `status` and `verify` renderings —
and checks every path any of them prints. That decides the brief nobody has written yet; a list would
only decide tonight's.

**Where it is written down.** As a numbered requirement in `docs/specs/orchestrator-engine.md`, beside
R145 and R155, with a coverage row naming the guard — because a brief is not a contract and the spec is
(D11). R145 is that requirement applied to the report pointer; R155 was it violated.

**Why this generalises rather than invents.** The repository already holds the rule twice: a ticket's
`paths` may hold no glob (`plan.py:65` refuses one — "use a literal repo-relative path without
globs"), and `structure_budget.py --paid` refuses a path that measures nothing rather than passing it.
Both are this sentence. What never got it is the text the engine *says* to an agent — and that is where
tonight's three failures live: the same brief that named a glob also pointed at `report.md` (a file that
may not exist), at "the first review's file is in `<run>/t1`" (a directory to go and search), and at a
bare `receipts/` directory.

## What changes

- `driver/` — the review packet's report line: `the newest report*.json in <run_dir>/<unit>` → the
  `report_path` the ledger recorded for the attempt under review, with that file's mtime in UTC seconds
  and the head its report claims (T1, S1).
- `driver/` — the review instruction: `the file to write is X; a file written under another name inside
  the run directory is read too` → `the file to write is X` (T1, S4).
- `driver/` — `announced_path`, `adopt_review`, `note_announced` and the `review.announced` field:
  present → deleted, so the gate reads the file it named and asks the reviewer for it when it is
  missing (T1, S2, S4).
- `driver/` — `unit_trail`, the merge's reported checks, the open findings and the open-attempt
  selection: four loops over `data["tasks"]` → one reader over the ledger's recorded attempt order and
  the record's own open attempt (T3, S5).
- `tests/test_driver_review_pointers.py` (new) — no guard → every path token a rendered brief prints is
  a file that exists or the file the reviewer must write, and `newest`, a glob or a bare run directory
  fails naming the token (T2, S3).
- `driver/` — the rest of the brief's pointers: `beside report.md` (a name that may not exist), `none;
  the first review's file is in <run>/<unit>` (a directory to search for the earlier review) and
  `Evidence already in the run: <dir>` (a bare directory) → the file named when it exists, the earlier
  review named when there is one, and the receipts printed with how many there are (T1, S3).
- `driver/` — a pointer that resolves to nothing or to several: `read_payload` takes the first alternate
  that exists and `unit_trail` falls back to an earlier attempt's file → a refusal naming the pointer,
  what was expected there and the way out (T2, S7).
- `tests/test_driver_pointers.py` (new) — no guard → the property holds over *every* instruction the
  engine renders for a reader: each attempt's task, the review brief, a fix round's task, the answers
  task, the archive task, the hand-over, and the `status` and `verify` renderings (T2, S3).
- `docs/specs/orchestrator-engine.md` — R145 names the newest `report*.json` in a directory and R155
  adopts a review written under another name → R145 names the report path the ledger recorded, R155 and
  its coverage row are gone, and the invariant itself becomes a numbered requirement with a coverage row
  naming the guard (T1, T2, S6, S8).
- `AGENTS.md` — no such rule → one paragraph in the repository's voice — name the file, do not describe
  how to find it, refuse when a pointer resolves to nothing or to several — naming the requirement and
  the guard that hold it, so the rule meets the next author before the guard does (T2, S9).

## Decisions

- **D1** The packet names the `report_path` the ledger recorded for the attempt under review, over
  "the newest `report*.json` in the unit's directory": the record already holds the path the writer was
  given, and a directory plus a glob is what let a reviewer read an earlier attempt's file (the gate
  round that judged an 08:45 report against an 11:44 branch).
- **D2** The line prints the file's mtime as UTC whole seconds — `2026-11-16T08:45:12Z` — and the head
  the report claims, over a relative age ("2 h ago") or the file's mtime alone: the incident is a
  comparison between two times, so the number the reader compares has to be the number, and the head is
  what makes staleness visible against the commit under review (the brief's own order: "with its mtime
  and claimed head").
- **D3** The announced-path reader and `adopt_review` are deleted together with the sentence, and R155
  is repealed, over keeping the mechanism and dropping only the clause: a tolerance nobody is told
  about is a second answer that fires only on a mistake, which is the class this plan exists to end
  (the brief's order; R155 as it stands in `docs/specs/orchestrator-engine.md:510`).
- **D4** The path comes from `task["report_path"]`, written by `record_report`, over re-deriving it with
  `report_file()`/`next_report()`: `report_file` answers "the newest file in the task directory", which
  is the same search one directory down, and `next_report` answers the file a worker owes next, not the
  one it wrote.
- **D5** The guard (T2) checks the *rendered brief's* tokens, over a source-level regex over the
  driver's code: the brief is the artifact that carries the rule, and a source regex enumerates today's
  spellings of the defect while a token check decides the pointer nobody has written yet.
- **D6** One reader answers "the unit's newest report / findings / open attempt" from the ledger's
  recorded attempt order and the record's own open attempt, over the four per-caller loops over
  `data["tasks"]` (`unit_trail`, `pull._reported_checks`, `pull._open_findings`, `pull.units`): four
  loops are four answers to one question, which is how the two packets came to disagree.
- **D12** `AGENTS.md` carries the rule as one paragraph that names the spec requirement, over a full
  restatement there: two copies drift and the spec is where the test lives, but `AGENTS.md` is where an
  agent meets the rule before it writes anything — and the review brief already hands `AGENTS.md` to
  every reviewer as the rules its change is judged by (`driver/__init__.py:2281`), so the paragraph
  reaches the reader the sentence is about (S9, and the repository's own "an invariant, not a point
  fix" section is where it belongs).
- **D11** The invariant is written as a numbered requirement in the living spec with a coverage row
  naming the guard, over living only in this brief: a brief is read once at approval, the spec is the
  ledger every later change is held to, and a rule that only a plan folder holds is the enumeration the
  brief itself argues against (S8, and the spec's own R209 discipline that a change updates its rows).
- **D9** A collection is printed as `"<n> files in <dir>"`, over a bare directory the reader must
  enumerate: the count is a fact the engine already holds and the reader does not, and it turns "go and
  look" into "there are six, here is where" — the rule the guard enforces for the receipts line and for
  any directory a later instruction adds (S3).
- **D10** A pointer that resolves to nothing, or to more than one file, is a refusal naming the pointer
  and the way out, over a fallback to an earlier attempt's file or the first match that exists: both
  fallbacks are exactly how a search that found the wrong thing passed as an answer — in `unit_trail`'s
  accumulation and in `read_payload`'s alternates (D1's incident, and S7).
- **D7** The ticket fence names the driver package directory, over naming the module each ticket will
  edit: the structure-budget drive's T10–T12 decide where the brief and the trail land, so a fence that
  guesses `routes.py` or `book.py` today would block this plan's own first ticket.
- **D8** The dependency on that drive is stated here rather than in the contract, over inventing a
  ticket id that names another plan: `blocked_by` resolves only inside one contract, and the approval
  is where a person sequences two plans.

## Out of scope

- The two instances fixed today (`#409`, `#411`): the survey records them as `(a)` so the class is not
  reported twice, and no ticket touches `plan.py`, `scripts/structure_budget.py` or `scripts/suite_budget.py`.
- Every search in this tree whose result the environment verifies before use, or that is the answer by
  construction — the full inventory with the reason for each is in the appendix. They are honest `(c)`
  cases, not this plan's work.
- `packages/agent-orchestrator/src` is being refactored by an active drive (T10–T12 of the
  structure-budget plan): this plan reads it and never edits it, and its own first ticket lands after
  that drive.
- The other packages' file discovery (`agent-eval` capture, `agent-runtime` session logs): the files are
  named by a CLI whose layout nobody records, and each is matched by its content before use.

## Risks

- `state` (T3): the ordering decides which attempt's report the merge trusts; verified by the ticket's
  own tests over the engine's real book (claim, report, release), with a control that a unit whose
  newest attempt has not reported answers nothing rather than an earlier attempt's file.
- **A frozen acceptance case is deleted**: `packages/agent-orchestrator/acceptance/driver/test_review_payload_path.py`
  was frozen by the `review-payload-path` plan and proves R155. Repealing R155 deletes its `S2` case and
  keeps its controls; the row in the spec's coverage table goes with the requirement.
- **Sequencing**: T1–T3 land after the structure-budget drive's T12 (the driver split). Their fence is
  the driver package directory, and a split that leaves the review brief somewhere other than
  `driver/routes.py` changes no ticket: the fence covers wherever it landed.
- **The acceptance file reads as two unresolved imports inside the plan folder**: `from fixtures import
  …` and `from test_gates import GateFixture` are resolved by pytest's `pythonpath` and ty's
  `extra-paths`, which name the suites and not `docs/plans`. At its destination
  (`packages/agent-orchestrator/acceptance/driver/`) it collects and runs — proven by running it there:
  three cases red at base for the reasons above, both controls green — and the frozen sibling
  `test_review_payload_path.py` in that same directory is clean under an LSP probe.
  The same analyzer flags this contract's long lines at 80 columns; the repository's own width is 100
  for Python and the approved plan it copies runs to 121, and CI gates neither.

## Appendix: the survey

Every pointer in this repository that could be a search, decided as `(a)` it names the thing already,
`(b)` the search is the defect, `(c)` it searches but cannot be wrong because the environment
guarantees one answer. Two `(a)` entries are tonight's fixes, listed so the plan covers the rest.

| file:line | | evidence |
|---|---|---|
| `agent_orchestrator/plan.py:993` | a | `package_files` asks git (`ls-files --cached --others`) which files a package holds; fixed today by #409 |
| `scripts/structure_budget.py:143` | a | `tracked`/`resolve` decide membership from git and `--since` resolves through `git merge-base`; fixed today by #411 |
| `driver/__init__.py:2289` | b | `review_text` names a directory and a `report*.json` glob, so a re-dispatched attempt's report is unfindable |
| `driver/__init__.py:2290` | b | the same line also names `report.md` beside it: a file that may not exist, so the reviewer goes looking for a second one |
| `driver/__init__.py:2302` | b | a re-review with no earlier findings is told "the first review's file is in `<run>/<unit>`": a directory to search |
| `driver/__init__.py:2294` | b | "evidence already in the run: `<run>/receipts`" names a directory and leaves the enumeration to the reader |
| `driver/__init__.py:2570` | b | `unit_trail` answers "newest report/findings" by dict order, the second answer to the brief's question |
| `driver/__init__.py:221` | b | `adopt_review` copies a review from a name the reviewer chose, so the review path has two answers (R155) |
| `driver/__init__.py:246` | b | `announced_path` scans a report's prose for a `.json` name inside the run directory |
| `driver/__init__.py:2394` | b | the brief promises that second answer in words, beside the path it names |
| `driver/__init__.py:1926` | b | `read_payload` takes the first candidate that exists, so a payload has as many answers as names it was given |
| `driver/pull.py:522` | b | `_reported_checks` orders attempts `[task_id] + reversed(attempts)` to guess which report is newest |
| `driver/pull.py:565` | b | `_open_findings` walks `reversed(tasks)` by dict order to guess the newest fix round |
| `driver/pull.py:202` | b | `units()` takes `group[-1]`, the last attempt inserted, as "the task still open" |
| `driver/__init__.py:4814` | b | `next(iter(open_.items()))` takes the first open attempt by dict order when several could be open |
| `docs/specs/orchestrator-engine.md:475` | b | R145 states the rule to search in the living spec ("the newest `report*.json` in the unit's run directory") |
| `docs/specs/orchestrator-engine.md:510` | b | R155 states the second answer as a requirement |
| `driver/__init__.py:1898` | c | `report_file` asks the filesystem for the newest write-once report name; the names are dense from 1, so absence decides |
| `driver/__init__.py:1908` | c | `next_report` asks for the first name not taken; the property asked for *is* absence |
| `driver/__init__.py:906` | c | `task.v%d.md` is allocated the same way: the first unused version |
| `driver/__init__.py:6278` | c | `last_mark` takes `marks[-1]` from an append-only event log; the log's order is the fact |
| `driver/invariants.py:103` | a | `review_paths` walks a chain the record itself names (`review.earlier`), newest first |
| `driver/pull.py:139` | a | `pr_url` reads each attempt's recorded report for `pr` and excludes the one the ledger recorded closed |
| `driver/spawn.py:139` | c | `reconcile_intent` sorts open intents by their recorded `at`; the record carries the order |
| `workbench.py:356`, `evidence.py:183` | c | attempts are ordered by `(started_at, task_id)`, a stated rule with a deterministic tiebreak |
| `agent_runtime/usage.py:358,364,373,704,716` | c | a session log is found by glob and then matched to the worker's `session_id`/`session_log`, and the resolved path is cached in the metadata |
| `agent-runtime/dispatch_events.py:34` | c | `*.sock` notification: the set to notify *is* every socket in the directory, and a refused one is unlinked |
| `agent-runtime/hc_tools.py:548` | c | `profile_launcher` walks up for `bin/agent-runtime-profile` because the checkout root is not knowable from the member |
| `agent-runtime/agent_runtime.py:110` | c | `ancestor_cli` walks the process tree; the nearest harness ancestor is only knowable by walking |
| `hooks/hook_lib.py:107` | c | the owning team is "the newest row that is neither dead nor empty", and the store's `dispatch.owning_team` decides it |
| `hooks/hook_lib.py:264` | c | `prune_budgets` deletes every file in one directory older than a week; the set is the directory |
| `hooks/pre_tool_use.py:130–160` | a | argv parsing reads positions it has already fixed, not a search |
| `acceptance/suite/test_spec_citations.py:22,91` | c | the sweep's domain is by definition the specs and the test trees |
| `tests/test_usage.py:431,493` | c | the tests assert the glob is *not* re-run once the path is cached |
| `install.sh:306,1120` | c | the installer walks its own staged tree, the set it just wrote |
| `scripts/build-release.sh:153` | c | the release lists the staging directory it just filled |
| `agent-eval/*` (`capture.py`, `claude.py`, `codex.py`, `pi.py`, `listing.py`) | c | the transcripts are named by another CLI's layout, and each candidate is bound to a session id before use |

Not in this plan: the `agent-eval` and `agent-runtime` entries are `(c)` because the environment
verifies each result; the two `(a)` fixes are already merged; the review brief's own four pointers are
T1's and T2's, because they are the sentence the incident came from, and everything else `(b)` is T3's
four readers, because they are the same question the packet got wrong.
