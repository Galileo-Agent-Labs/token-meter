# Windows One-Command Bootstrap Design

## Status

Approved for implementation on 2026-08-14.

## Goal

Provide a single copy-and-paste Windows command that can install Token Meter on
a supported Windows 10 or Windows 11 machine even when Git and Python are not
already installed. The flow must remain per-user, local-first, rerunnable, and
safe around existing source and runtime directories.

## User Experience

README will lead with one PowerShell command. That command will:

1. download `scripts/bootstrap-windows.ps1` over HTTPS to a named file under
   `%TEMP%`;
2. run it in a child Windows PowerShell process with `-NoLogo`, `-NoProfile`,
   and process-scoped `-ExecutionPolicy Bypass`;
3. remove only that downloaded temporary file after the bootstrap returns.

The command will not pipe mutable remote content directly into `Invoke-Expression`.
The checked-in bootstrap remains readable in the repository, and a local
`scripts/install-windows.cmd` wrapper will support simple reinstalls without
requiring users to remember PowerShell flags or change directories.

On success, the bootstrap opens `http://127.0.0.1:8722` only after the existing
installer has verified server and tray readiness. It prints the source path,
runtime path, Python path, installed revision, automatic-start state, dashboard
URL, and uninstall command. A `-NoOpenDashboard` switch supports unattended
use without changing the default interactive experience.

## Components and Responsibilities

### `scripts/bootstrap-windows.ps1`

This self-contained script runs before the repository is available locally.
It owns the early Git discovery and installation needed to acquire the managed
source, then delegates the complete prerequisite pass and runtime installation
to the cloned repository. It does not duplicate
runtime staging, registry startup registration, server/tray startup, readiness
checks, or rollback logic.

The bootstrap accepts optional `-SourceRoot`, `-InstallRoot`, and
`-NoOpenDashboard` parameters. Defaults are:

- source: `%LOCALAPPDATA%\Token Meter\source`
- runtime: `%LOCALAPPDATA%\Token Meter\runtime`
- repository: `https://github.com/splunk/token-meter.git`
- branch: `main`

Repository URL and branch are fixed constants, not user-controlled remote input.
Existing `TOKEN_METER_SOURCE_ROOT`, `TOKEN_METER_INSTALL_ROOT`, and
`TOKEN_METER_PYTHON` environment overrides remain supported for advanced and
test scenarios.

### `scripts/install-windows.cmd`

This local wrapper resolves Windows PowerShell through the absolute
`%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe` path, launches
`install-windows.ps1` with process-scoped execution-policy bypass, forwards all
arguments, and returns the child exit code. The delegated installer performs
the automatic prerequisite pass, so the wrapper behaves consistently when a
checkout was obtained through Git, a ZIP download, or the remote bootstrap.

### `scripts/install-windows.ps1`

The existing installer remains the authority for validated Python selection,
manifest staging and parity, managed runtime swapping, startup registration,
service/tray launch, ownership verification, readiness, and rollback. It gains
the shared local prerequisite behavior: refresh discovery, install missing Git
or Python through WinGet, verify both executables, and then continue. Existing
compatible tools are reused.

## Prerequisite Policy

The bootstrap refreshes its process PATH from current machine, user, and
inherited values before discovery. It then performs executable probes rather
than treating a file or WinGet record as proof.

- Git passes only when `git.exe --version` exits successfully.
- Python passes only when an executable reports CPython 3.8 or newer.
- Existing compatible tools are never upgraded or replaced.

If Git is missing, the bootstrap or local installer installs exact package ID
`Git.MinGit` from the `winget` source. The bootstrap needs this early step before
it can clone; the delegated installer repeats only the executable probe and
therefore does not reinstall it. MinGit is the small portable Git-for-Windows
distribution intended for non-interactive third-party use, exposes a `git`
alias, supports x86, x64, and ARM64, and avoids installing the larger Git
desktop shell.

If Python is missing or older than 3.8, the delegated local installer installs
exact package ID `Python.Python.3.14` from the `winget` source with user scope.
WinGet selects the matching supported architecture. Token Meter continues to
accept any already installed compatible native Python rather than requiring
3.14 specifically.

Each package command uses:

- `install --exact --id <id> --source winget`
- `--silent`
- `--accept-package-agreements`
- `--accept-source-agreements`
- `--disable-interactivity`

The bootstrap never uses `--force`, never requests a machine scope, and never
installs both packages blindly. After each successful WinGet command it refreshes
PATH and reruns the real executable probe. A zero WinGet exit without a usable
executable is a failure.

WinGet itself is a Windows prerequisite. If it is unavailable, the bootstrap
stops before changing source or runtime state and prints a bounded instruction
to install or update Microsoft's App Installer. It does not fall back to
unverified direct package downloads.

## Managed Source Flow

After prerequisites pass, the bootstrap owns exactly the configured source
directory.

For a first installation, it clones into a unique sibling staging directory,
verifies the expected `origin`, branch, and revision, then atomically renames
the staging directory to the final source path. A failed clone removes only the
staging directory created by that invocation.

For an existing source directory, the bootstrap requires all of the following:

- it is a Git repository;
- `origin` is exactly the canonical Splunk Token Meter HTTPS repository;
- the current branch is `main` and tracks `origin/main`;
- the checkout has no tracked or untracked changes;
- the update can fast-forward.

It fetches `origin`, performs only a fast-forward merge, and refuses to reset,
clean, overwrite, or repurpose an unexpected directory. A dirty, diverged,
wrong-origin, non-Git, or wrong-branch source produces an actionable error and
leaves the existing source and runtime untouched.

The bootstrap then invokes the source checkout's
`scripts\install-windows.ps1`, passing the selected runtime path. The existing
installer's staging, previous-runtime backup, readiness checks, and rollback
remain unchanged.

## Failure and Recovery Contracts

Output uses short named phases: prerequisites, source, runtime, and readiness.
Errors identify the failed phase and one next action without printing arbitrary
package output, environment contents, credentials, source-controlled content,
or local trace data.

The bootstrap returns nonzero when WinGet, Git, Python, source validation,
clone/update, or the delegated installer fails. It does not open the browser on
failure. It never deletes an existing source or runtime directory. The existing
runtime stays active until the delegated installer reaches its atomic swap,
and that installer restores the previous runtime when post-swap validation
fails.

Rerunning the command is idempotent: valid prerequisites are reused, a clean
managed checkout fast-forwards or remains unchanged, and the transactional
installer replaces only its owned runtime.

## Packaging and Documentation

`runtime-manifest.txt` remains the sole staged-runtime inventory. The new local
wrapper and bootstrap must be included through the existing `tree scripts`
entry and covered by manifest parity tests.

README replaces the three-line Windows clone/install sequence with the one
bootstrap command, explains that missing Git and Python are installed
automatically through WinGet, documents the App Installer prerequisite, and
keeps a local-wrapper command for reinstalls and troubleshooting.
`specs/CONTRIBUTING.md` documents both the bootstrap smoke path and direct
installer path for maintainers.

## Test Strategy

Test-first implementation will add regression coverage for these observable
contracts:

- README exposes one primary Windows command with no `Set-Location` step or
  user-authored execution-policy setting change.
- The local CMD wrapper resolves the intended PowerShell executable, applies
  process-scoped bypass, forwards arguments, and preserves the exit code.
- Existing Git and Python skip WinGet.
- Missing Git installs only `Git.MinGit` with the exact non-interactive flags.
- Missing or incompatible Python installs only `Python.Python.3.14` with user
  scope and the exact non-interactive flags.
- A successful WinGet exit followed by a failed executable probe stops the
  bootstrap.
- First install uses a sibling staging clone and promotes it only after
  verification.
- Clean managed source fast-forwards; dirty, diverged, wrong-origin,
  wrong-branch, non-Git, and occupied paths fail without mutation.
- Delegation passes the chosen runtime path and propagates installer failure.
- Browser opening occurs only after successful installer completion and is
  suppressed by `-NoOpenDashboard`.
- The runtime manifest expands both new scripts and staged source matches the
  repository byte-for-byte.

Cross-platform contract tests run on every host. Windows-native tests parse all
PowerShell artifacts, exercise the CMD wrapper with controlled stubs, and test
bootstrap branches with temporary directories and fake prerequisite commands.
A release claim still requires a real Windows install covering WinGet, x64 or
ARM64 Python discovery, source acquisition, tray readiness, automatic startup,
dashboard health, rerun, update, and uninstall. macOS validation must not be
presented as proof of those Windows lifecycle behaviors.

## Security and Privacy

All downloads use HTTPS through PowerShell, WinGet, or Git. Package resolution
uses exact IDs and the named `winget` source. No arbitrary repository URL,
branch, package ID, installer URL, or shell fragment comes from user-controlled
remote content. The bootstrap makes no telemetry or analytics request and does
not read agent traces.

Execution-policy bypass applies only to child PowerShell processes and does not
change CurrentUser, LocalMachine, or Group Policy settings. Token Meter remains
bound to `127.0.0.1`, installed per user, and free of a Token Meter-specific
administrator requirement.

## References

- [Microsoft WinGet install command](https://learn.microsoft.com/en-us/windows/package-manager/winget/install)
- [Git.MinGit WinGet manifest](https://raw.githubusercontent.com/microsoft/winget-pkgs/master/manifests/g/Git/MinGit/2.55.0.3/Git.MinGit.installer.yaml)
- [Python.Python.3.14 WinGet manifest](https://raw.githubusercontent.com/microsoft/winget-pkgs/master/manifests/p/Python/Python/3/14/3.14.1/Python.Python.3.14.installer.yaml)
- [PowerShell execution policies](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies)
