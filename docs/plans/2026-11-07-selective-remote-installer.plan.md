# Plan: Selectable, Release-Pinned Agent Runtime Installer

Slug: `selective-remote-installer`. Living spec: `docs/specs/installer-contract.md`. Status: **APPROVED — READY FOR EXECUTION**.

## Why

The present `agent-runtime/install.sh` is a local-checkout POSIX shell script that links runtime CLI shims and prints Claude/Codex wiring. It assumes its own `$0` is a repository file path. It does not yet implement a remote `curl | sh` bootstrap, selectable components, Pi wiring, or an independent installed orchestrator. Extend this *same* installer after P4b packages are defined; do not build a separate conflicting dotfiles installer.

## Sources

- https://github.com/FBisca/agent-runtime/blob/main/install.sh (existing link-and-print semantics; refuse non-symlink overwrite)
- https://pi.dev/docs/latest/quickstart (Pi binary's official installer owns Pi itself)
- https://docs.astral.sh/uv/concepts/projects/workspaces/ (independent workspace packages/commands)
- https://docs.astral.sh/uv/getting-started/installation/ (uv bootstrap/versioning practices)
- Accepted user constraint Q48; existing design decisions Q36, Q41, Q42, Q43.

## Proposed decisions

| # | Choice | Rejected | Why | Status |
|---|---|---|---|---|
| D1 | Reuse `agent-runtime/install.sh`; support local checkout and remote stdin bootstrap. | Create another installer; assume `$0` exists as a checkout. | One installation contract. | Requested |
| D2 | Select `runtime`, `orchestrator`, `claude`, `codex`, `pi`; resolve `orchestrator → runtime`, integrations → runtime. | All-or-nothing install. | Minimal user footprint. | Requested |
| D3 | Interactive menu only if `/dev/tty` available; explicit `--components` and `--yes` for non-interactive execution. | Read prompt from pipeline stdin. | `curl | sh` stdin is not a terminal. | Recommended |
| D4 | Pin release/tag and verify fetched payloads; stage install transactionally, support dry-run, rollback and upgrade. | Run unverified, moving `main` code with unchecked shell execution. | Remote installer has high privileges in user's account. | Recommended |
| D5 | Default to preserving all existing files; print config instructions when conflict exists. No secret creation/printing. | Silent rewrites of Claude/Codex/Pi config. | Respect existing user tools and provider credentials. | Requested |
| D6 | Install Pi binary only via official Pi distribution, optionally print its installation steps; own only agent-runtime integration. | Install a second custom Pi binary or own Pi credentials. | Avoid competing installers and auth state. | Recommended |

## Binding P4c distribution and security contract — Q48, Q50–Q52

**First-stage trust caveat:** `curl ... | sh` executes whatever bytes HTTPS returns. An integrity check *inside that same downloaded script* cannot authenticate the script itself. A secure recommended path is downloading a **version-pinned release asset** plus checksum/signature whose trusted reference was obtained independently, verifying before executing. A convenience pipe may be documented as lower-assurance after publication of an immutable, audited release; do not label it fully verified or assume GitHub tag names are intrinsically immutable.

The install choices are `runtime`, `orchestrator`, `claude`, `codex`, `pi`. Dependency closure: orchestrator → runtime; harness adapters → runtime; no choice auto-installs or authenticates Pi/Claude/Codex binaries. Root-owned/system paths and other users' configs are out of scope. Stage into user-owned versioned destination, activate after verification, and roll back atomically when possible. Use explicit `--components`/`--yes` for headless installs; interactive menu requires `/dev/tty`; `--dry-run` must do no network writes or installs. Never silently rewrite arbitrary existing user JSON or insert credentials.

**Command semantics:** `install`, `upgrade`, `uninstall`, `--dry-run` and `--version` need documented parse/exit-code contracts. Uninstall removes only files recorded in its manifest and preserves user state and other components; install is repeatable and refuses unowned conflicts.

## Scenarios

- **S1** WHEN a local checkout runs `sh install.sh --components runtime,pi`, THEN the requested components alone are installed/configured, the runtime dependency closure is honoured, and no unrelated tool configs are overwritten.
- **S2** WHEN a released bootstrap is streamed through a shell, THEN it does not interpret piped script bytes as interactive answers. With an interactive `/dev/tty`, it can present the component menu; without one, it requires explicit `--components` and exits with actionable usage.
- **S3** WHEN the orchestrator is selected, THEN the runtime requirement is made explicit and both install against a compatible released version, not a mixture of branches.
- **S4** WHEN user-installed symlinks, Claude/Codex/Pi configuration or credentials already exist, THEN install is idempotent and refuses or safely reconciles conflicts with a preview; no secrets enter stdout/logs.
- **S5** WHEN `--dry-run` is supplied, THEN it prints planned actions, dependencies, versions and paths without changing the filesystem or invoking any provider.
- **S6** WHEN a download is interrupted or verification fails, THEN no partial active installation is left and any prior version remains usable.
- **S7** WHEN install is rerun, upgraded or selectively uninstalled, THEN it preserves unrelated components and user-owned data, and maintains compatibility with documented `agent-runtime*` shims.
- **S8** WHEN Linux/macOS installer tests run under clean/existing HOME values, with fake external binaries, THEN no network or credentials are needed, and shellcheck plus all failure-path cases pass.
- **S9** WHEN Pi is not installed, THEN selecting its adapter reports the missing prerequisite and gives the official Pi installation method; it must not silently install or authenticate Pi.

## Interface proposal (examples; **NOT IMPLEMENTED**)

```sh
# Local checkout
sh install.sh --components runtime,orchestrator,pi --dry-run

# Future version-tagged remote bootstrap (after release and checksum strategy)
curl -fsSL https://raw.githubusercontent.com/FBisca/agent-runtime/vX.Y.Z/install.sh | sh -s -- --components runtime,orchestrator,pi --yes
```

The proposed URL is illustrative: `vX.Y.Z` is a placeholder, not an existing release. Before publishing, choose how the bootstrap validates a versioned release payload, checksums and compatibility across packages. Do not recommend piping mutable `main` into a shell as the default.

## Out of scope

- Installing Pi's own binary, asking for or persisting provider credentials, managing user dotfiles, changing existing worker state, auto-enabling unsafe MCP mutations, destructive uninstall of application data, creating a public release during planning.

## Risks / rollback

- If `curl | sh` runs code as the current user, an upstream compromise is consequential. Prefer transparent versioned source, checksum verification of secondary payloads, no `sudo`, and document reviewable download-then-run alternative.
- Preserve original local installer behaviour and its existing symlink refusal; add remote-specific path handling instead of breaking `$0` resolution.
- Use executable-independent, temporary-home shell tests and transaction staging to validate partial failure and repeated install.
- Keep installer decisions separate from the Pi bootstrap; roll back only files created by this installer.

## Tickets

| ID | Vertical deliverable | Scenarios | Depends | Paths | Size | Difficulty | Status |
|---|---|---|---|---|---|---|---|
| T1 | Snapshot legacy `install.sh` behaviour and add regression tests; define component dependency closure and install manifest. | S1,S3,S4,S7,S8 | P4b package interface | `install.sh`, `tests/installer/`, `docs/specs/installer-contract.md` | regular | complex | TO-DO |
| T2 | POSIX-compatible menu `/dev/tty` and headless flags, with dry-run and automated matrix tests. | S1,S2,S5,S8 | T1 | `install.sh`, `tests/installer/` | regular | complex | TO-DO |
| T3 | Release-pinned remote bootstrap, integrity verification, atomic staging and rollback tests. | S2,S3,S6,S8 | T2 + release artifact contract | `install.sh`, `scripts/`, `.github/workflows/`, `tests/installer/` | large | complex | TO-DO |
| T4 | Wire requested Claude/Codex/Pi adapters without secret access or unsafe permissions; verify upgrade/uninstall, docs and backward compatibility. | S4,S7,S9 | T3, P2 for Pi runtime support | `install.sh`, `hooks/`, `tests/installer/`, `README.md` | regular | complex | TO-DO |

**Owner-approved for implementation on 2026-11-07.** P0/dotfiles and P4b ownership/dependencies are reconciled in this plan. Independent pre-implementation review was waived by the owner; technical preflight and safety checks remain mandatory.


## Installer additional adversarial cases

- Tampered tarball, missing signature, wrong platform, interrupted download and mismatched release manifest leave previous install intact.
- Inputs containing whitespace, shell metacharacters, unexpected component IDs, absent `/dev/tty` and symlinked install directories are rejected or safely handled without eval or command injection.
- Local checkout install remains supported for contributors and never implies remote release has already been published.
- Test a genuine clean HOME, existing dotfiles HOME and CI no-TTY environment on macOS/Linux; no provider credentials or network required in fixtures.

**Status: APPROVED — READY FOR EXECUTION. Installer not yet implemented or published.**
