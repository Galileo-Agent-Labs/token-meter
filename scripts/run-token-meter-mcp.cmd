@echo off
setlocal
set "TOKEN_METER_ROOT=%~dp0.."
set /p "TOKEN_METER_PYTHON="<"%TOKEN_METER_ROOT%\PYTHON_EXECUTABLE"
if not exist "%TOKEN_METER_PYTHON%" (
  echo Token Meter MCP failed: configured Python executable is unavailable. 1>&2
  exit /b 1
)
"%TOKEN_METER_PYTHON%" -X utf8 "%TOKEN_METER_ROOT%\token_meter_mcp.py"
