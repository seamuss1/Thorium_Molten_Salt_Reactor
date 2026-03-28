@echo off
setlocal
powershell.exe -NoLogo -NoExit -ExecutionPolicy Bypass -File "%~dp0Enter-PytbknShell.ps1" %*
