@echo off
setlocal
cd /d "%~dp0"

if not exist "runtime\Scripts\python.exe" (
  echo [ERROR] Not set up yet. Run setup.bat first.
  pause
  exit /b 1
)

set "HF_HOME=%~dp0models\hf_cache"
set "HUGGINGFACE_HUB_CACHE=%~dp0models\hf_cache"
set "HF_HUB_DISABLE_TELEMETRY=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

"runtime\Scripts\python.exe" launcher.py %*

endlocal
