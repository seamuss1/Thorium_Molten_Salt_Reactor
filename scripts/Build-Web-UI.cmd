@echo off
setlocal
powershell.exe -NoLogo -ExecutionPolicy Bypass -File "%~dp0Build-Web-UI.ps1" %*
exit /b %ERRORLEVEL%
