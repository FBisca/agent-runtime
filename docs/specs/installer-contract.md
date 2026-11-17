# Installer contract

The installer is one POSIX shell script, `install.sh`, that installs the runtime and the components a user selects (`runtime`, `orchestrator`, `claude`, `codex`, `pi`, `herdr`) from a local checkout or from a pinned, checksum-verified release. The runtime contract it installs lives in [agent-runtime.md](agent-runtime.md); the harness wiring it prints lives in [harness-adapters.md](harness-adapters.md). This capability covers how the installer selects, verifies, activates and removes what it installs, and what it never touches.

## Requirements

### Requirement: Selective install
The installer SHALL install and configure the components the user selected and their runtime dependency closure, and nothing else.

#### Scenario: a checkout installs the requested components
- WHEN a local checkout runs `sh install.sh --components runtime,pi`
- THEN the requested components alone are installed, the dependency closure is honoured and said out loud, and no unrelated tool config is overwritten; a name that is not a component is refused, naming the known ones

### Requirement: Piped bootstrap
A released bootstrap streamed through a shell SHALL NOT read piped script bytes as interactive answers.

#### Scenario: no terminal
- WHEN the bootstrap runs with no controlling terminal
- THEN it requires explicit `--components` and exits with actionable usage, and a stream that names no checkout and no release is told to pin one

#### Scenario: a terminal is present
- WHEN `/dev/tty` opens
- THEN the bootstrap presents the component menu on the terminal, accepts numbers and `all`, asks before it acts (`--yes` skips the ask), and quitting changes nothing

### Requirement: Release coherence
The runtime and the orchestrator SHALL install against one compatible release, never a mixture of branches or releases.

#### Scenario: a mixture is refused
- WHEN the orchestrator is selected, a checkout's members disagree, a release manifest mixes versions, or an install would leave a component at another release
- THEN the runtime requirement is made explicit where the orchestrator is selected, and every mixture is refused naming the reason

### Requirement: Existing configuration kept
The installer SHALL be idempotent over user-owned symlinks, Claude, Codex and Pi configuration and credentials, refuse or reconcile conflicts with a preview, and print no secret.

#### Scenario: a file the installer does not own
- WHEN a shim, a config file or a wired entry already exists that the installer did not write
- THEN it is kept, the refusal or preview names the reason and the way out, no config is rewritten, no credential is opened and no secret reaches stdout or logs

#### Scenario: a second run
- WHEN the install runs twice
- THEN the second run leaves the filesystem and its output as the first did

### Requirement: Installer dry run
`--dry-run` SHALL print the planned actions, dependencies, versions and paths without changing the filesystem or invoking any provider.

#### Scenario: a preview that tells the truth
- WHEN `--dry-run` runs over an existing install, an uninstall or a conflict
- THEN the home and the user's dotfiles are exactly as they were, a conflict is refused with its reason, and a stream names the release as still to be pinned

### Requirement: Verified atomic install
A failed, interrupted or unverifiable install SHALL leave no partial active installation and the prior version usable.

#### Scenario: verification fails
- WHEN a download is interrupted, a payload is tampered, an asset or checksum is missing, a manifest names another release or platform, or a payload escapes its directory
- THEN nothing runs, nothing stays staged, and the prior install, its manifest and its shims are as they were; an independent `--sha256` (or `AGENT_RUNTIME_SHA256`) outranks the release's own checksums

#### Scenario: activation fails
- WHEN activating fails or a signal arrives after the shims moved, for a release, a checkout or the legacy install
- THEN the rollback runs to the end, the prior shims are back and no half-made link or temporary file is left; a first install leaves a clean home

### Requirement: Rerun, upgrade and uninstall
Rerunning, upgrading or selectively uninstalling SHALL preserve unrelated components and user-owned data and keep the documented `agent-runtime*` shims working.

#### Scenario: a component leaves alone
- WHEN one adapter is uninstalled or the runtime is upgraded
- THEN the runtime, the other adapters, the runtime's state, the host policy and the harness dotfiles are untouched; the runtime stays while a component needs it and the way out is named; an upgrade that would strand a component at another release is refused

### Requirement: Hermetic installer tests
The installer's tests SHALL run on Linux and macOS under clean and existing HOME values with fake external binaries, needing no network or credentials.

#### Scenario: failure paths and shellcheck
- WHEN the installer tests run
- THEN every failure path names its reason and the way out, nothing reaches the network, and the installer and every script under `scripts/` are shellcheck clean

### Requirement: Pi prerequisite
Selecting the Pi adapter SHALL report a missing Pi and the official way to install it, and never install or authenticate Pi.

#### Scenario: Pi is missing or present
- WHEN the Pi adapter is selected and Pi is not on the PATH
- THEN the adapter installs, the prerequisite is named, the exit code stays 0, and Pi is never invoked; with Pi present the notice is gone

### Requirement: Herdr component
`sh install.sh --components herdr` SHALL install the agent-workbench mod and the opt-in `workbench` layout into Herdr without switching to it, and remove only what it installed.

#### Scenario: a checkout or a release
- WHEN the herdr component is installed from a checkout, then reinstalled or pulled
- THEN the mod and the layout are symlinks into the checkout (a release copies them), so a pull updates them, nothing switches to the layout, and the install prints how to make Herdr reload the mod

#### Scenario: a name that is not ours
- WHEN a mod or layout of that name exists that the installer did not install, or the person edited a copy
- THEN it is refused with a preview before anything else changes, or kept aside, and `--dry-run` changes nothing

#### Scenario: uninstall
- WHEN the component is uninstalled
- THEN the mod and the layout links go, the checkout's file stays, and an edited copy or layout is kept
