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
