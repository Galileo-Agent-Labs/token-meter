# Windows One-Command Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one-command Windows installation that automatically installs missing Git and Python, safely acquires the managed source, and delegates to Token Meter's transactional per-user installer.

**Architecture:** A self-contained remote bootstrap installs portable MinGit only when Git is unavailable, safely clones or fast-forwards the canonical managed checkout, then invokes a local CMD wrapper. The wrapper applies process-scoped PowerShell bypass and delegates to `install-windows.ps1`, which performs the complete Git/Python prerequisite pass before retaining authority for staging, lifecycle registration, readiness, and rollback.

**Tech Stack:** Windows PowerShell 5.1, Windows batch, WinGet, Git, Python standard-library contracts, `unittest`, Token Meter runtime-manifest parity.

## Global Constraints

- Support Windows 10 and Windows 11 with Windows PowerShell 5.1 syntax.
- Keep Token Meter per-user under `%LOCALAPPDATA%\Token Meter`; request no Token Meter-specific administrator access.
- Install only missing prerequisites: exact `Git.MinGit` and user-scoped `Python.Python.3.14` from source `winget`.
- Use `--silent --accept-package-agreements --accept-source-agreements --disable-interactivity`; never use `--force` or machine scope.
- Verify `git.exe --version` and native Python 3.8+ after discovery or installation.
- Never reset, clean, overwrite, or delete an existing source or runtime directory.
- Preserve manifest staging, byte parity, startup ownership checks, readiness, and rollback.
- Never present macOS checks as native Windows lifecycle proof.

---

### Task 0: Preserve the Verified Windows Reliability Baseline

**Files:**
- Modify: `README.md`
- Modify: `scripts/install-windows.ps1`
- Modify: `specs/CONTRIBUTING.md`
- Modify: `tests/contracts/test_platform_services.py`
- Modify: `tests/contracts/test_windows_packaging.py`
- Modify: `token_meter/platforms/windows.py`

**Interfaces:**
- Consumes: the already completed execution-policy and Python-discovery fix in the working tree.
- Produces: a clean committed baseline where process-scoped bypass and refreshed PATH/launcher/registry/system Python discovery are independently tested.

- [ ] **Step 1: Re-run the already established regression suite**

```bash
python3 -m unittest tests.contracts.test_platform_services tests.contracts.test_windows_packaging -v
python3 -m unittest discover -s tests -v
git diff --check
```

Expected: 498 tests pass with two host-native skips and no whitespace errors.

- [ ] **Step 2: Commit only the verified baseline files**

```bash
git add README.md scripts/install-windows.ps1 specs/CONTRIBUTING.md tests/contracts/test_platform_services.py tests/contracts/test_windows_packaging.py token_meter/platforms/windows.py
git commit -m "fix: harden Windows installer discovery"
```

### Task 1: Add the Local CMD Entry Point

**Files:**
- Create: `scripts/install-windows.cmd`
- Modify: `tests/contracts/test_windows_packaging.py`

**Interfaces:**
- Consumes: `scripts/install-windows.ps1` and optional arguments supplied by the caller.
- Produces: `scripts/install-windows.cmd`, which returns the delegated PowerShell process exit code unchanged.

- [ ] **Step 1: Write the failing wrapper contracts**

Add `scripts/install-windows.cmd` to `WINDOWS_SCRIPTS`, split PowerShell and CMD artifact lists, and add a cross-host contract plus Windows-native execution check:

```python
WINDOWS_POWERSHELL_SCRIPTS = (
    "scripts/install-windows.ps1",
    "scripts/start-token-meter.ps1",
    "scripts/run-tray.ps1",
    "scripts/update-windows.ps1",
    "scripts/uninstall-windows.ps1",
)
WINDOWS_CMD_SCRIPTS = (
    "scripts/install-windows.cmd",
    "scripts/run-token-meter-mcp.cmd",
)
WINDOWS_SCRIPTS = WINDOWS_POWERSHELL_SCRIPTS + WINDOWS_CMD_SCRIPTS

def test_local_windows_wrapper_applies_bypass_and_forwards_exit_status(self):
    wrapper = (ROOT / "scripts" / "install-windows.cmd").read_text(encoding="utf-8")
    self.assertIn(r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe", wrapper)
    self.assertIn("-ExecutionPolicy Bypass", wrapper)
    self.assertIn('"%~dp0install-windows.ps1" %*', wrapper)
    self.assertIn("exit /b", wrapper)
```

Extend the Windows-native test to parse only `WINDOWS_POWERSHELL_SCRIPTS` and execute a temporary copy of the CMD wrapper against a stub `install-windows.ps1` that exits `23`; assert the wrapper returns `23` and forwards a sentinel argument.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m unittest tests.contracts.test_windows_packaging.WindowsPackagingContracts.test_local_windows_wrapper_applies_bypass_and_forwards_exit_status -v
```

Expected: FAIL because `scripts/install-windows.cmd` is absent.

- [ ] **Step 3: Implement the minimal wrapper**

Create:

```bat
@echo off
setlocal
set "TOKEN_METER_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%TOKEN_METER_POWERSHELL%" (
  echo Token Meter installation failed: Windows PowerShell is unavailable. 1>&2
  exit /b 1
)
"%TOKEN_METER_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" %*
set "TOKEN_METER_EXIT=%ERRORLEVEL%"
endlocal & exit /b %TOKEN_METER_EXIT%
```

- [ ] **Step 4: Run the focused contracts and verify GREEN**

Run:

```bash
python3 -m unittest tests.contracts.test_windows_packaging -v
```

Expected: all cross-host contracts pass; Windows-native checks skip on non-Windows hosts.

- [ ] **Step 5: Commit the wrapper task**

```bash
git add scripts/install-windows.cmd tests/contracts/test_windows_packaging.py
git commit -m "feat: add simple Windows installer wrapper"
```

### Task 2: Make the Local Installer Acquire Missing Prerequisites

**Files:**
- Modify: `scripts/install-windows.ps1`
- Modify: `tests/contracts/test_windows_packaging.py`

**Interfaces:**
- Consumes: `winget.exe`, exact package IDs, refreshed machine/user PATH, existing `TOKEN_METER_PYTHON` override.
- Produces: `Get-UsableGit() -> CommandInfo|null`, `Find-CompatiblePython() -> string|null`, and `Install-Prerequisite([string]$PackageId, [switch]$UserScope) -> void` inside the installer.

- [ ] **Step 1: Write failing prerequisite behavior contracts**

Add contracts that name the break each branch catches:

```python
def test_windows_installer_acquires_only_missing_exact_prerequisites(self):
    installer = (ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")
    self.assertIn('Install-Prerequisite "Git.MinGit"', installer)
    self.assertIn('Install-Prerequisite "Python.Python.3.14" -UserScope', installer)
    for flag in (
        '"--exact"', '"--source"', '"winget"', '"--silent"',
        '"--accept-package-agreements"', '"--accept-source-agreements"',
        '"--disable-interactivity"',
    ):
        self.assertIn(flag, installer)
    self.assertNotIn('"--force"', installer)
    self.assertIn("Get-UsableGit", installer)
    self.assertIn("Find-CompatiblePython", installer)
    self.assertIn("could not be verified after WinGet reported success", installer)
```

On a Windows host, run an isolated prerequisite harness with temporary fake
`git.exe`, `python.exe`, and `winget.exe` commands. Record invocations to a temp
file and assert: valid Git/Python make zero WinGet calls; missing Git requests
only `Git.MinGit`; incompatible Python requests only `Python.Python.3.14` with
`--scope user`; a zero WinGet exit without a new usable command returns nonzero.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m unittest tests.contracts.test_windows_packaging.WindowsPackagingContracts.test_windows_installer_acquires_only_missing_exact_prerequisites -v
```

Expected: FAIL because prerequisite installation is not implemented.

- [ ] **Step 3: Refactor discovery into repeatable probes**

Move existing Git/Python discovery behind these functions without changing the
candidate precedence:

```powershell
function Get-UsableGit {
    $Command = Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $Command) { return $null }
    try {
        & $Command.Source --version *> $null
        if ($LASTEXITCODE -eq 0) { return $Command }
    } catch { }
    return $null
}

function Find-CompatiblePython {
    $Candidates = Get-PythonCandidates
    return $Candidates | Select-Object -Unique |
        Where-Object { Test-Python $_ } | Select-Object -First 1
}
```

`Get-PythonCandidates` contains the current override, PATH, launcher inventory,
PEP 514 registry, `%LOCALAPPDATA%`, and Program Files enumeration.

- [ ] **Step 4: Implement exact WinGet acquisition and post-install verification**

Add:

```powershell
function Get-WinGet {
    return Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue
}

function Install-Prerequisite([string]$PackageId, [switch]$UserScope) {
    $WinGet = Get-WinGet
    if (-not $WinGet) {
        Fail "WinGet is required to install $PackageId. Install or update Microsoft App Installer and rerun this command."
    }
    $Arguments = @(
        "install", "--exact", "--id", $PackageId, "--source", "winget",
        "--silent", "--accept-package-agreements", "--accept-source-agreements",
        "--disable-interactivity"
    )
    if ($UserScope) { $Arguments += @("--scope", "user") }
    & $WinGet.Source @Arguments
    if ($LASTEXITCODE -ne 0) { Fail "WinGet could not install $PackageId." }
    Refresh-ProcessPath
}
```

Call `Refresh-ProcessPath`, probe Git, install `Git.MinGit` only when missing,
and fail if the second Git probe is unusable. Probe Python, install
`Python.Python.3.14 -UserScope` only when missing, and fail if the second Python
probe is unusable. Persist the verified absolute Python path as before.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.contracts.test_windows_packaging tests.contracts.test_platform_services -v
```

Expected: all focused cross-host tests pass with only target-native skips.

- [ ] **Step 6: Commit prerequisite behavior**

```bash
git add scripts/install-windows.ps1 tests/contracts/test_windows_packaging.py
git commit -m "feat: install missing Windows prerequisites"
```

### Task 3: Add the Remote Bootstrap and Safe Managed Checkout

**Files:**
- Create: `scripts/bootstrap-windows.ps1`
- Modify: `tests/contracts/test_windows_packaging.py`

**Interfaces:**
- Consumes: fixed repository `https://github.com/splunk/token-meter.git`, branch `main`, WinGet, Git, and `scripts/install-windows.cmd` from the acquired source.
- Produces: bootstrap parameters `-SourceRoot <string>`, `-InstallRoot <string>`, and `-NoOpenDashboard`; exit `0` only after delegated installation succeeds.

- [ ] **Step 1: Write failing bootstrap contracts**

Add tests for the fixed source and safe state machine:

```python
def test_windows_bootstrap_owns_prerequisites_source_and_delegation(self):
    bootstrap = (ROOT / "scripts" / "bootstrap-windows.ps1").read_text(encoding="utf-8")
    for marker in (
        'https://github.com/splunk/token-meter.git',
        'Install-GitPrerequisite',
        '"Git.MinGit"',
        'git.exe',
        'status --porcelain',
        'fetch',
        'merge',
        '--ff-only',
        'install-windows.cmd',
        'NoOpenDashboard',
        'http://127.0.0.1:8722',
    ):
        self.assertIn(marker, bootstrap)
    for forbidden in ('reset --hard', 'clean -f', 'Invoke-Expression', '--force'):
        self.assertNotIn(forbidden, bootstrap)
```

Add `scripts/bootstrap-windows.ps1` to `WINDOWS_POWERSHELL_SCRIPTS` in this task,
when the artifact exists, so the manifest and native parse tests remain green
at every earlier commit boundary.

Add a native temporary-repository harness that uses real Git with local bare
remotes and a stub `install-windows.cmd`. Assert first install stages then
promotes, clean source fast-forwards, dirty/wrong-origin/diverged/wrong-branch
sources fail unchanged, delegated exit is propagated, and the dashboard opener
runs only after exit `0` unless `-NoOpenDashboard` is set.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m unittest tests.contracts.test_windows_packaging.WindowsPackagingContracts.test_windows_bootstrap_owns_prerequisites_source_and_delegation -v
```

Expected: FAIL because `scripts/bootstrap-windows.ps1` is absent.

- [ ] **Step 3: Implement bootstrap prerequisite and path guards**

Create a PowerShell 5.1 script with:

```powershell
[CmdletBinding()]
param(
    [string]$SourceRoot = "",
    [string]$InstallRoot = "",
    [switch]$NoOpenDashboard
)

$RepositoryUrl = "https://github.com/splunk/token-meter.git"
$RepositoryBranch = "main"
```

Resolve defaults below `%LOCALAPPDATA%\Token Meter`, reject a source root named
`runtime`, reject a runtime root not named `runtime`, refresh PATH, probe Git,
install `Git.MinGit` with the Task 2 exact flags only when absent, and verify the
post-install command before touching source state.

- [ ] **Step 4: Implement atomic first clone and guarded fast-forward**

For a missing source, clone into `$SourceRoot.installing-<guid>`, verify origin,
branch, clean state, and nonempty `HEAD`, then use `Move-Item` to promote it. In
`finally`, remove only that invocation's staging directory.

For an existing source, require `.git`, exact origin URL, `main`, upstream
`origin/main`, and empty `status --porcelain`. Run:

```powershell
& $Git.Source -C $SourceRoot fetch --quiet --prune --no-tags origin
& $Git.Source -C $SourceRoot merge --quiet --ff-only origin/main
```

Check each exit code and recheck clean state. Never call reset or clean.

- [ ] **Step 5: Delegate installation and open only after success**

Invoke:

```powershell
$Wrapper = Join-Path $SourceRoot "scripts\install-windows.cmd"
& $Wrapper -InstallRoot $InstallRoot
if ($LASTEXITCODE -ne 0) { Fail "the Token Meter runtime installer failed." }
if (-not $NoOpenDashboard) {
    Start-Process "http://127.0.0.1:8722" | Out-Null
}
```

Print the managed source before delegation. Let the existing installer print
runtime, revision, readiness, and uninstall details.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.contracts.test_windows_packaging -v
```

Expected: cross-host tests pass with Windows-native integration skipped only on
non-Windows hosts.

- [ ] **Step 7: Commit the bootstrap**

```bash
git add scripts/bootstrap-windows.ps1 tests/contracts/test_windows_packaging.py
git commit -m "feat: add one-command Windows bootstrap"
```

### Task 4: Publish the Minimal Command and Verify the Shipped Runtime

**Files:**
- Modify: `README.md`
- Modify: `specs/CONTRIBUTING.md`
- Modify: `tests/contracts/test_windows_packaging.py`

**Interfaces:**
- Consumes: raw bootstrap URL and local wrapper from Tasks 1-3.
- Produces: one primary Windows command, one local rerun command, and maintainer validation instructions.

- [ ] **Step 1: Write failing documentation and manifest behavior tests**

Add assertions that README's Windows Quick Start contains exactly one primary
PowerShell command which downloads to `%TEMP%`, invokes the file with
process-scoped bypass, cleans up the temp file, contains no `git clone` or
`Set-Location` in that block, and documents automatic Git/Python plus App
Installer. Assert README and contributor instructions include:

```powershell
.\scripts\install-windows.cmd
```

Extend manifest inventory assertions so both new files appear in
`manifest_source_files(...)` through `tree scripts`.

- [ ] **Step 2: Run the documentation contracts and verify RED**

Run:

```bash
python3 -m unittest tests.contracts.test_windows_packaging -v
```

Expected: FAIL because README and contributor instructions still expose the
old multi-command flow.

- [ ] **Step 3: Update README and contributor guidance**

Replace Windows Quick Start with a one-line command shaped as:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command '$p=Join-Path $env:TEMP "token-meter-bootstrap.ps1"; try { Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/splunk/token-meter/main/scripts/bootstrap-windows.ps1" -OutFile $p; & $p } finally { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }'
```

Explain automatic exact-package installation, WinGet/App Installer, per-user
paths, no persistent policy change, rerun command, and `-NoOpenDashboard`.
Keep direct `install-windows.ps1` parsing and native tray smoke instructions for
maintainers.

- [ ] **Step 4: Run focused and full source validation**

Run:

```bash
python3 -m unittest tests.contracts.test_windows_packaging tests.contracts.test_platform_services -v
PYTHONPYCACHEPREFIX=/private/tmp/token-meter-pycache python3 -m py_compile meter.py token_meter_mcp.py $(find token_meter -type f -name '*.py' -print | LC_ALL=C sort)
python3 -m unittest discover -s tests -v
bash -n scripts/install scripts/install-linux scripts/install-launch-agent scripts/install-systemd-user scripts/run-menubar scripts/run-token-meter-mcp scripts/start-token-meter scripts/uninstall-launch-agent scripts/uninstall-systemd-user scripts/update
node -e "const fs=require('fs'); const html=fs.readFileSync('page.html','utf8'); const m=html.match(/<script>([\\s\\S]*)<\\/script>/); new Function(m[1]); console.log('js ok')"
git diff --check
```

Expected: all cross-host checks pass; only host-native Windows/Linux skips remain.

- [ ] **Step 5: Stage and verify the exact runtime**

Run:

```bash
./scripts/install
PYTHONPATH=. python3 -m token_meter.packaging parity "$PWD" "$HOME/Library/Application Support/Token Meter/runtime" runtime-manifest.txt
curl -fsS http://127.0.0.1:8722/health
curl -fsS http://127.0.0.1:8722/menubar
launchctl print gui/$UID/com.token-meter.server
launchctl print gui/$UID/com.token-meter.menubar
cmp scripts/bootstrap-windows.ps1 "$HOME/Library/Application Support/Token Meter/runtime/scripts/bootstrap-windows.ps1"
cmp scripts/install-windows.cmd "$HOME/Library/Application Support/Token Meter/runtime/scripts/install-windows.cmd"
```

Expected: install completes; health is ready; both LaunchAgents run; manifest
parity and both new-file comparisons pass. Report that these macOS staging
checks do not prove WinGet, Windows PowerShell, registry startup, or NotifyIcon.

- [ ] **Step 6: Commit documentation and verification contracts**

```bash
git add README.md specs/CONTRIBUTING.md tests/contracts/test_windows_packaging.py
git commit -m "docs: publish one-command Windows install"
```
