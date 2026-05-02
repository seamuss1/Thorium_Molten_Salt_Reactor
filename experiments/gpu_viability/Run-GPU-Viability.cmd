@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"

if not exist "%REPO_ROOT%\.runtime-env\python.exe" (
  echo Missing repo-local runtime: %REPO_ROOT%\.runtime-env\python.exe
  echo Bootstrap the repository runtime before running GPU viability probes.
  exit /b 1
)

set "PYTHONPATH=%REPO_ROOT%\src;%PYTHONPATH%"
set "PYTHONUNBUFFERED=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "TMP=%REPO_ROOT%\.tmp"
set "TEMP=%REPO_ROOT%\.tmp"
set "PIP_CACHE_DIR=%REPO_ROOT%\.pip-cache"
set "PATH=%REPO_ROOT%\.runtime-env\Library\bin;%REPO_ROOT%\.runtime-env;%REPO_ROOT%\.runtime-env\Scripts;%PATH%"

if not defined SYCL_CACHE_PERSISTENT set "SYCL_CACHE_PERSISTENT=1"
if not defined ZE_ENABLE_PCI_ID_DEVICE_ORDER set "ZE_ENABLE_PCI_ID_DEVICE_ORDER=1"
if not defined KMP_DUPLICATE_LIB_OK set "KMP_DUPLICATE_LIB_OK=TRUE"
if not defined PYTORCH_ENABLE_XPU_FALLBACK set "PYTORCH_ENABLE_XPU_FALLBACK=0"

"%REPO_ROOT%\.runtime-env\python.exe" "%SCRIPT_DIR%gpu_viability_bench.py" %*
exit /b %ERRORLEVEL%
