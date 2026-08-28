@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\Scripts\python.exe" ( echo Run setup.bat first. & exit /b 1 )
"runtime\Scripts\python.exe" -m pip install -q pytest >nul 2>&1
set "PYTHONUTF8=1"
"runtime\Scripts\python.exe" -m pytest -q %*
endlocal
