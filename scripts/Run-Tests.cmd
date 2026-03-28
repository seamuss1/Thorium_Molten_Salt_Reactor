@echo off
setlocal
powershell.exe -NoLogo -ExecutionPolicy Bypass -File "%~dp0Run-Tests.ps1" %*
exit /b %ERRORLEVEL%
