@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"

if not exist "%REPO_ROOT%\.runtime-env\python.exe" (
  echo Missing repo-local runtime: %REPO_ROOT%\.runtime-env\python.exe
  echo Bootstrap the repository runtime before running GPU viability probes.
  exit /b 1
)

set "PYTHONUNBUFFERED=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "TMP=%REPO_ROOT%\.tmp"
set "TEMP=%REPO_ROOT%\.tmp"
set "PATH=%REPO_ROOT%\.runtime-env\Library\bin;%REPO_ROOT%\.runtime-env;%REPO_ROOT%\.runtime-env\Scripts;%PATH%"

"%REPO_ROOT%\.runtime-env\python.exe" "%SCRIPT_DIR%opencl_arc_b70_stress.py" %*
exit /b %ERRORLEVEL%

