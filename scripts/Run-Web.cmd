@echo off
setlocal
powershell.exe -NoLogo -ExecutionPolicy Bypass -File "%~dp0Run-Web.ps1" %*
exit /b %ERRORLEVEL%
