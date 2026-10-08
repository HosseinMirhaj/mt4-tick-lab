@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0mt4-tick-lab.ps1" all %*
if errorlevel 1 (
  echo.
  echo MT4 Tick Lab stopped with an error. Read the message above.
  pause
)
