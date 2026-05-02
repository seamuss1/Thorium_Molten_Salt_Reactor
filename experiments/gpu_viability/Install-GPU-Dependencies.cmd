@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"

if not exist "%REPO_ROOT%\.runtime-env\python.exe" (
  echo Missing repo-local runtime: %REPO_ROOT%\.runtime-env\python.exe
  echo Bootstrap the repository runtime before installing GPU dependencies.
  exit /b 1
)

set "PATH=%REPO_ROOT%\.runtime-env\Library\bin;%REPO_ROOT%\.runtime-env;%REPO_ROOT%\.runtime-env\Scripts;%PATH%"

echo Installing official PyTorch XPU wheel into repo-local .runtime-env...
"%REPO_ROOT%\.runtime-env\python.exe" -m pip install --upgrade --force-reinstall torch --index-url https://download.pytorch.org/whl/xpu
if errorlevel 1 exit /b %ERRORLEVEL%

echo.
echo Checking torch.xpu availability...
"%REPO_ROOT%\.runtime-env\python.exe" -c "import torch; print('torch', torch.__version__); print('xpu available', torch.xpu.is_available()); print('xpu devices', torch.xpu.device_count()); print('xpu device 0', torch.xpu.get_device_name(0) if torch.xpu.is_available() else 'n/a')"
exit /b %ERRORLEVEL%
