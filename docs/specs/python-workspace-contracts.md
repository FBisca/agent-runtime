# Python workspace contracts

The runtime and the orchestrator are two installable members of one uv workspace. The runtime stays standard-library only in production; the orchestrator owns the typed, Pydantic-backed contracts and the YAML route policy. The runtime's behaviour lives in [agent-runtime.md](agent-runtime.md) and the harnesses in [harness-adapters.md](harness-adapters.md); this capability covers the packaging that carries them, the toolchain gates that guard it, how the test suite stays cheap, and the orchestrator driver's module layout. The workspace is a packaging change, not a rewrite: entrypoints, CLI subcommands, the MCP tool surface, the durable store format and each harness's permissions are unchanged. `docs/compat/compatibility-baseline.json` pins the pre-workspace inventory the contract tests assert on.

## Requirements

### Requirement: Reproducible workspace lock
The workspace SHALL resolve both members from the committed lockfile and fail loudly when the lockfile is stale.

#### Scenario: sync and check
- WHEN a developer runs `uv sync --locked` or `uv lock --check`
- THEN both members install reproducibly under the declared Python range and the lockfile is current and names both members

### Requirement: Member dependency isolation
Each member SHALL declare only its own dependencies, so the runtime wheel works on its own.

#### Scenario: the runtime alone
- WHEN the runtime wheel is installed on its own
- THEN its CLI and MCP server work without Pydantic or PyYAML, neither leaks into that environment, and the wheel needs neither the orchestrator member nor its dependencies

#### Scenario: manifests
- WHEN each member's manifest is read
- THEN the runtime declares none and the orchestrator declares Pydantic and PyYAML

### Requirement: Workspace CI gates
CI SHALL run the workspace's toolchain as plain commands that fail the build on their own exit codes, with no stored count or budget.

#### Scenario: the matrix
- WHEN CI runs on Linux or macOS across the supported Python versions
- THEN the workspace resolves, the whole suite runs (the orchestrator member's own suite included), `ruff check .`, `ruff format --check .` and `ty check packages/ ...` run, both member wheels build, and the runtime wheel installs in isolation

### Requirement: Harness behaviour unchanged by the workspace
The workspace SHALL leave the entrypoints, CLI subcommands and harness permissions of `agent-runtime`, `agent-runtime-mcp` and `agent-runtime-hook` as documented.

#### Scenario: shims and installer
- WHEN a Claude, Codex or Pi session runs one of the entrypoints
- THEN its shim exists, is executable and resolves to its Python entrypoint, the recorded entrypoints match the bin directory, the installer links every one and wires the hooks, the hook shim still fails open, and the CLI keeps its subcommands

### Requirement: Orchestrator reaches the runtime through its public surface
The orchestrator SHALL reach the runtime only through its public CLI or API.

#### Scenario: no private access
- WHEN the orchestrator dispatches, resumes, collects or cancels a worker
- THEN no member source imports a runtime module or opens the store's tables, the client returns the runtime CLI's answer, and a failing runtime is reported rather than swallowed

### Requirement: Typed boundaries refuse invalid fields
A task packet or worker report with an unknown or invalid field SHALL be refused with a deterministic error naming the field.

#### Scenario: an invalid report
- WHEN a report lacks its task id or carries an unknown field
- THEN the typed boundary refuses it naming the field, and the same input always produces the same message

### Requirement: YAML route policy
The orchestrator's route policy SHALL be YAML, and an unknown model class or field SHALL be an error rather than a silent fall back to another backend.

#### Scenario: an unknown class or field
- WHEN a route policy names an unknown class or carries an unknown field
- THEN it is refused, while a valid YAML policy loads

### Requirement: Rollback keeps the store
A rollback of the workspace change SHALL leave the legacy shims answering and the durable store usable in both trees without a migration.

#### Scenario: both trees open one store
- WHEN the workspace change is rolled back
- THEN the legacy CLI shims and MCP server still answer, and a store written by either tree opens in the other with the same schema version, tables and rows

### Requirement: Orchestrator keeps no runtime state
The orchestrator member SHALL write nothing into the runtime's store.

#### Scenario: a member run
- WHEN the orchestrator member runs
- THEN the runtime's store holds none of its state

### Requirement: Unit tests start no process
A test marked `unit` SHALL start no process (git included) and open no network socket.

#### Scenario: the unit tier
- WHEN the tests marked `unit` run
- THEN none starts a process or opens a socket

### Requirement: Fast suite cost
The fast suite SHALL cost under three seconds of test call time with no unit test at half a second or more.

#### Scenario: the fast suite
- WHEN `uv run pytest` runs the fast suite
- THEN its summed call time is under three seconds and no unit test costs half a second or more, the build failing only at half again those numbers so a shared runner is not the gate

### Requirement: Option-only pytest runs the fast suite
`uv run pytest` given only options SHALL select the same tests as bare `uv run pytest`.

#### Scenario: an option value
- WHEN pytest gets options only, including an option's value such as `-p no:cacheprovider`
- THEN it selects the fast suite, and naming a path or a node id still runs exactly what was named

### Requirement: Per-test timeout
A single test running past the configured timeout SHALL fail, naming the timeout.

#### Scenario: a slow test
- WHEN a single test runs past 18 seconds
- THEN pytest fails it by the `timeout = 18` of its configuration, so no per-module row and no stored ceiling exists

### Requirement: Tests wait on signals, not clocks
A test SHALL wait on an injected clock or a barrier and assert a signal the code produced; a real wait remains only where the assertion is about elapsed time.

#### Scenario: no real wait
- WHEN a test would wait for a deadline, a wake or an installer fake
- THEN it advances an injected clock or waits on a signal the code produced

### Requirement: Test fixtures are built once
Expensive read-only fixtures SHALL be built once per class or session, and a test that changes one SHALL take a copy.

#### Scenario: shared fixtures
- WHEN orchestrator tests need an approved plan, a delivered plan, or installer tests need the v0.1.0 release
- THEN the plan is approved and the snapshot or release built once, readers share it, every test still starts from the same fresh world and one PATH, and a test that changes it takes a copy

### Requirement: Tests start only what they exercise
A test SHALL seed the state it does not exercise through the store or a fixture and start a worker session or a server only when it talks to one.

#### Scenario: seeded state, lazy sessions
- WHEN a runtime test needs a full team, a long-lived lead, a spent budget or a backlog, or talks to no worker or server
- THEN the state is seeded, only the step under test drives the public path, and no session or server is started for it

### Requirement: Pure logic is tested in process
A test of pure logic (validation, precedence, parsing, classification) SHALL run it in process, with one test per boundary showing the result crossing it.

#### Scenario: a refusal in process and across the boundary
- WHEN logic such as an unknown capture ref or a spawn refusal is proved
- THEN it runs in process without a subprocess, and one test still shows the refusal crossing the subprocess or server boundary

### Requirement: Ledger busy deadline is configurable
The ledger's busy deadline SHALL be read from `AGENT_ORCHESTRATOR_LEDGER_DEADLINE`, so a test need not wait a minute.

#### Scenario: the deadline seam
- WHEN the variable is set to a usable value it reaches the lock; unset, unreadable or non-positive
- THEN the deadline stays sixty seconds

### Requirement: Driver facade and entry points
The `agent_orchestrator.driver` package SHALL expose every public name its modules define and reach the same CLI from every entry point.

#### Scenario: names resolve
- WHEN a published name of the package is used by the package, the suite or an entry point
- THEN it resolves, and a test comparing the sets of defined and reachable public names fails if a split drops one

#### Scenario: one CLI
- WHEN the CLI is asked for its subcommands, or the package entry and `python -m agent_orchestrator` run
- THEN they offer the same commands and subcommands and reach the same `run_cli`

### Requirement: One UTC timestamp definition
The orchestrator package SHALL define the current UTC timestamp once, in `timeutil.py`.

#### Scenario: call sites keep their type
- WHEN the package's modules are read for a current UTC timestamp
- THEN the definition exists once and every call site keeps the type it had: an ISO string or a datetime

### Requirement: Pre-split ledgers open
A ledger written before the driver was split SHALL open in the split engine without a migration.

#### Scenario: an old ledger
- WHEN a ledger written before the split is read after it
- THEN the record keeps its version, kind marker and tasks, and the archive, gates and book each continue from it

### Requirement: Reviewer packet names the attempt under review
The reviewer's packet SHALL name the exact report of the attempt under review.

#### Scenario: a re-dispatched unit
- WHEN a unit is re-dispatched and its second attempt is reviewed
- THEN the packet names that attempt's report with its mtime and claimed head rather than a glob over the unit directory, and the trail's newest report is chosen by the order the record holds, so a stale file is never read as the change under review

### Requirement: Ruff size limits
CI SHALL fail a function or class that exceeds a ruff PLR09 limit, with each existing offender carrying its own `# noqa`.

#### Scenario: over a limit
- WHEN a function or class exceeds 12 branches, 7 arguments, 50 statements or 25 public methods
- THEN `uv run ruff check .` fails naming its rule code, so no number is stored
