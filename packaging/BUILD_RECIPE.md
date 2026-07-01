# Token Meter Package Build Recipe

Use this checklist every time you build a distributable macOS installer package.
Run commands from the repository root unless a step says otherwise.

## 1. Preflight

Confirm the working tree state so the package is built from the intended files:

```bash
git status --short
```

Required local tools:

```bash
python3 --version
swiftc --version
command -v pkgbuild pkgutil mkbom cpio gzip gzcat
```

Expected:

- Python is 3.8 or newer.
- `swiftc` is available from Xcode Command Line Tools.
- `pkgbuild`, `pkgutil`, `mkbom`, `cpio`, `gzip`, and `gzcat` are available on macOS.

## 2. Validate Source

Compile the Python server:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/token-meter-pycache python3 -m py_compile meter.py token_meter_mcp.py
python3 -m unittest discover -s tests -v
```

Parse the dashboard JavaScript:

```bash
node -e 'const fs=require("fs"); const html=fs.readFileSync("page.html","utf8"); const m=html.match(/<script>([\s\S]*)<\/script>/); if(!m) throw new Error("script not found"); new Function(m[1]); console.log("js ok")'
```

Compile the macOS menu bar binary:

```bash
swiftc menubar/TokenMeterMenuBar.swift -o /private/tmp/token-meter-menubar
```

Run the non-GUI menu bar smoke test:

```bash
TOKEN_METER_MENUBAR_SMOKE=1 /private/tmp/token-meter-menubar
```

Validate both MCP launchers and a protocol transcript:

```bash
bash -n scripts/run-token-meter-mcp packaging/payload/bin/token-meter-mcp
./scripts/run-token-meter-mcp <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
EOF
```

Check for whitespace errors:

```bash
git diff --check
```

## 3. Build

Build the default unsigned package:

```bash
./packaging/build-pkg
```

Expected output path:

```text
dist/TokenMeter-0.1.0.pkg
```

To override the version:

```bash
TOKEN_METER_VERSION=0.1.1 ./packaging/build-pkg
```

To build with Developer ID signing identities:

```bash
TOKEN_METER_CODESIGN_IDENTITY="Developer ID Application: Your Name" \
TOKEN_METER_INSTALLER_SIGN_IDENTITY="Developer ID Installer: Your Name" \
./packaging/build-pkg
```

Unsigned local builds are expected to report `Status: no signature`.

## 4. Verify Package

Check the package exists and has a plausible size:

```bash
ls -lh dist/TokenMeter-0.1.0.pkg
```

List the payload:

```bash
pkgutil --payload-files dist/TokenMeter-0.1.0.pkg
```

Expected payload:

```text
.
./Library
./Library/Application Support
./Library/Application Support/Token Meter
./Library/Application Support/Token Meter/LICENSE
./Library/Application Support/Token Meter/README.md
./Library/Application Support/Token Meter/VERSION
./Library/Application Support/Token Meter/bin
./Library/Application Support/Token Meter/bin/start-token-meter
./Library/Application Support/Token Meter/bin/token-meter-mcp
./Library/Application Support/Token Meter/bin/token-meter-menubar
./Library/Application Support/Token Meter/bin/uninstall-token-meter
./Library/Application Support/Token Meter/meter.py
./Library/Application Support/Token Meter/page.html
./Library/Application Support/Token Meter/token_meter_mcp.py
```

Check that no AppleDouble metadata files are in the payload:

```bash
pkgutil --payload-files dist/TokenMeter-0.1.0.pkg | rg '/\._|^\._'
```

Expected: no output. `rg` should exit with status `1` because it found nothing.

Check package signature status:

```bash
pkgutil --check-signature dist/TokenMeter-0.1.0.pkg
```

Expected for local unsigned builds:

```text
Package "TokenMeter-0.1.0.pkg":
   Status: no signature
```

Expected for signed builds: Apple certificate chain details instead of `no signature`.

## 5. Optional Local Install Smoke Test

Install the package locally:

```bash
sudo installer -pkg dist/TokenMeter-0.1.0.pkg -target /
```

Confirm installed files:

```bash
ls -la "/Library/Application Support/Token Meter"
ls -la "/Library/Application Support/Token Meter/bin"
```

Confirm the server comes up:

```bash
curl -s --max-time 5 http://127.0.0.1:8722/health
```

Confirm the menu bar endpoint is live:

```bash
curl -s --max-time 5 http://127.0.0.1:8722/menubar
```

Uninstall the local package install:

```bash
sudo "/Library/Application Support/Token Meter/bin/uninstall-token-meter"
```

## Troubleshooting

### Payload Contains `._` Files

If `pkgutil --payload-files` shows paths like `._page.html`, the package contains
AppleDouble metadata entries. `packaging/build-pkg` post-processes the flat
package to remove these entries by expanding the package, extracting the
payload, deleting `._*`, rebuilding the `Payload` and `Bom`, and flattening the
package again.

If this check regresses, inspect `clean_flat_pkg_metadata` in
`packaging/build-pkg` first.

### Universal Menu Bar Build Fails

The build script first tries to compile both `arm64` and `x86_64` menu bar
binaries and combine them with `lipo`. If that fails, it falls back to the
current host architecture and prints a warning.

Confirm toolchain availability:

```bash
swiftc --version
lipo -info /private/tmp/token-meter-menubar 2>/dev/null || true
```

### Package Is Unsigned

This is expected unless `TOKEN_METER_INSTALLER_SIGN_IDENTITY` is set. Public
distribution should use Developer ID signing and notarization.
