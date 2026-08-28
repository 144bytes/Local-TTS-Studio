@echo off
REM ============================================================
REM  LocalTTS Studio - double-click launcher
REM  Works from anywhere (Explorer, a Desktop shortcut, etc.)
REM ============================================================
setlocal
cd /d "%~dp0"
title LocalTTS Studio

if not exist "runtime\Scripts\python.exe" (
  echo First run - installing LocalTTS. This needs internet and takes a while...
  echo.
  call setup.bat
  if errorlevel 1 (
    echo.
    echo Setup failed. See the messages above.
    pause
    exit /b 1
  )
)

echo Starting LocalTTS Studio - your browser will open at http://127.0.0.1:8765
echo Close this window (or press Ctrl+C) to stop the service.
echo.

"runtime\Scripts\python.exe" launcher.py

echo.
echo LocalTTS Studio has stopped.
pause
endlocal
