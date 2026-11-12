# Installer-Contract
Paths: install.sh, scripts/, tests/installer/, herdr/layouts/

The installer is one POSIX shell script, `install.sh`, that installs the runtime and the components a user selects
(`runtime`, `orchestrator`, `claude`, `codex`, `pi`, `herdr`) from a local checkout or from a pinned, checksum-verified release.
The runtime contract it installs lives in [agent-runtime.md](agent-runtime.md); the harness wiring it prints lives in
[harness-adapters.md](harness-adapters.md). This spec covers how the installer selects, verifies, activates and
removes what it installs, and what it must never touch.

## Requirements

- **R1** WHEN a local checkout runs `sh install.sh --components runtime,pi`, THEN the requested components alone are installed/configured, the runtime dependency closure is honoured, and no unrelated tool configs are overwritten. (from selective-remote-installer/S1)
- **R2** WHEN a released bootstrap is streamed through a shell, THEN it does not interpret piped script bytes as interactive answers. With an interactive `/dev/tty`, it can present the component menu; without one, it requires explicit `--components` and exits with actionable usage. (from selective-remote-installer/S2)
- **R3** WHEN the orchestrator is selected, THEN the runtime requirement is made explicit and both install against a compatible released version, not a mixture of branches. (from selective-remote-installer/S3)
- **R4** WHEN user-installed symlinks, Claude/Codex/Pi configuration or credentials already exist, THEN install is idempotent and refuses or safely reconciles conflicts with a preview; no secrets enter stdout/logs. (from selective-remote-installer/S4)
- **R5** WHEN `--dry-run` is supplied, THEN it prints planned actions, dependencies, versions and paths without changing the filesystem or invoking any provider. (from selective-remote-installer/S5)
- **R6** WHEN a download is interrupted or verification fails, THEN no partial active installation is left and any prior version remains usable. (from selective-remote-installer/S6)
- **R7** WHEN install is rerun, upgraded or selectively uninstalled, THEN it preserves unrelated components and user-owned data, and maintains compatibility with documented `agent-runtime*` shims. (from selective-remote-installer/S7)
- **R8** WHEN Linux/macOS installer tests run under clean/existing HOME values, with fake external binaries, THEN no network or credentials are needed, and shellcheck plus all failure-path cases pass. (from selective-remote-installer/S8)
- **R9** WHEN Pi is not installed, THEN selecting its adapter reports the missing prerequisite and gives the official Pi installation method; it must not silently install or authenticate Pi. (from selective-remote-installer/S9)
- **R10** WHEN `sh install.sh --components herdr` runs, THEN from a checkout it symlinks `herdr/agent-workbench` into `~/.config/herdr-app/mods/agent-workbench` (a release copies it) and adds the opt-in `workbench` layout without switching to it; `--dry-run` changes nothing; a rerun is idempotent; an existing mod or layout of that name that it did not install is refused with a preview, before anything else is changed; uninstall removes only what it installed, and keeps a copy or layout the person edited. The component needs the orchestrator, and so the runtime. (from agent-workbench/S19)
