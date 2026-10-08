@echo off
setlocal
set "PROJECT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT%prepare-mt4-backtest.ps1" %*
if errorlevel 1 (
  echo.
  echo Backtest preparation stopped with an error. Read the message above.
  pause
)
