@echo off
pwsh -NoProfile -File "%~dp0scripts\run-workbench.ps1" %*
if errorlevel 1 pause
