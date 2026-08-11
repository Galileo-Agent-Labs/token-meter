import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from token_meter.packaging import load_manifest, manifest_source_files
from token_meter.quotas import anthropic as anthropic_quotas
from token_meter.quotas import openai as openai_quotas


ROOT = Path(__file__).resolve().parents[2]
WINDOWS_SCRIPTS = (
    "scripts/install-windows.ps1",
    "scripts/start-token-meter.ps1",
    "scripts/run-tray.ps1",
    "scripts/update-windows.ps1",
    "scripts/uninstall-windows.ps1",
    "scripts/run-token-meter-mcp.cmd",
)


class WindowsPackagingContracts(unittest.TestCase):
    def test_manifest_is_the_only_windows_staging_inventory(self):
        files = set(manifest_source_files(ROOT, load_manifest(ROOT / "runtime-manifest.txt")))
        self.assertTrue(set(WINDOWS_SCRIPTS).issubset(files))
        installer = (ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")
        self.assertIn("runtime-manifest.txt", installer)
        self.assertIn("token_meter.packaging manifest", installer)
        self.assertIn("token_meter.packaging parity", installer)
        self.assertNotIn("$RequiredPaths", installer)
        self.assertNotIn("$RuntimeDirectory", installer)

    def test_windows_lifecycle_is_per_user_owned_and_local_only(self):
        installer = (ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")
        starter = (ROOT / "scripts" / "start-token-meter.ps1").read_text(encoding="utf-8")
        uninstaller = (ROOT / "scripts" / "uninstall-windows.ps1").read_text(encoding="utf-8")
        for marker in (
            'Join-Path $env:LOCALAPPDATA "Token Meter\\runtime"',
            "HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run",
            '$InstallNonce = [Guid]::NewGuid().ToString("N")',
            "the runtime path contains files that do not belong to Token Meter",
            "Stop-InstalledServer",
            "Stop-InstalledTray",
        ):
            self.assertIn(marker, installer)
        self.assertIn("http://127.0.0.1:8722/health", starter)
        self.assertIn("Test-OwnedHealth", starter)
        self.assertIn("Remove-ItemProperty", uninstaller)
        self.assertIn("belongs to a different", uninstaller)
        self.assertNotIn("sudo", installer.lower())

    def test_windows_tray_uses_runtime_catalog_for_known_and_unknown_labels(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")
        self.assertIn("function Get-RuntimeLabel", tray)
        self.assertIn('Get-Value $State "runtime_catalog"', tray)
        self.assertIn('Get-Value $Catalog "unknown-runtime"', tray)
        self.assertNotIn('switch ($Provider)', tray)
        self.assertIn("System.Windows.Forms.NotifyIcon", tray)
        self.assertIn("known_runtime_label", tray)
        self.assertIn("unknown_runtime_label", tray)

    def test_windows_extension_does_not_add_os_dispatch_to_runtime_parsers(self):
        branch = re.compile(
            r"\b(?:if|elif|match|case)\b[^\n]{0,160}\b(?:win32|windows|os\.name\s*==\s*['\"]nt)",
            re.IGNORECASE,
        )
        for path in (ROOT / "token_meter" / "runtimes").glob("*.py"):
            self.assertNotRegex(path.read_text(encoding="utf-8"), branch, path)

    def test_provider_cli_processes_receive_no_window_flag_when_available(self):
        subprocess_module = mock.Mock()
        subprocess_module.CREATE_NO_WINDOW = 0x08000000
        subprocess_module.TimeoutExpired = subprocess.TimeoutExpired
        subprocess_module.PIPE = subprocess.PIPE
        subprocess_module.DEVNULL = subprocess.DEVNULL
        subprocess_module.run.return_value = mock.Mock(stdout="{}", returncode=0)

        anthropic_quotas.auth_status(
            lambda name: r"C:\bin\claude.exe", lambda path: {}, subprocess_module,
        )

        self.assertEqual(
            subprocess_module.run.call_args.kwargs["creationflags"], 0x08000000
        )

        process = mock.Mock()
        process.stdin = mock.Mock()
        process.stdout = mock.Mock()
        process.poll.return_value = 0
        subprocess_module.Popen.return_value = process
        selector = mock.Mock()
        selectors_module = mock.Mock()
        selectors_module.DefaultSelector.return_value = selector
        selectors_module.EVENT_READ = 1
        openai_quotas.app_server_rate_limits(
            lambda name: r"C:\bin\codex.exe", lambda path: {},
            lambda process, selector, request_id, timeout: {},
            subprocess_module, selectors_module, 1,
        )
        self.assertEqual(
            subprocess_module.Popen.call_args.kwargs["creationflags"], 0x08000000
        )

    @unittest.skipUnless(os.name == "nt", "Windows-native PowerShell validation")
    def test_powershell_scripts_parse_and_tray_smoke_on_windows(self):
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        self.assertTrue(shell)
        for relative in WINDOWS_SCRIPTS[:-1]:
            command = [
                shell, "-NoLogo", "-NoProfile", "-Command",
                "[void][scriptblock]::Create([IO.File]::ReadAllText($args[0]))",
                str(ROOT / relative),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
        smoke = subprocess.run(
            [shell, "-NoLogo", "-NoProfile", "-File",
             str(ROOT / "scripts" / "run-tray.ps1"), "-SmokeTest"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(smoke.returncode, 0, smoke.stderr)
        self.assertIn('"known_runtime_label":"Kiro"', smoke.stdout)
        self.assertIn('"unknown_runtime_label":"Unknown Runtime"', smoke.stdout)


if __name__ == "__main__":
    unittest.main()
