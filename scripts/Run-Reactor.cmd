@echo off
setlocal
powershell.exe -NoLogo -ExecutionPolicy Bypass -File "%~dp0Run-Reactor.ps1" %*
exit /b %ERRORLEVEL%
