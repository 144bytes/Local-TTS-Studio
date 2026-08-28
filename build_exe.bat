@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\Scripts\python.exe" ( echo Run setup.bat first. & exit /b 1 )

"runtime\Scripts\python.exe" -m pip show pyinstaller >nul 2>&1 || "runtime\Scripts\python.exe" -m pip install "pyinstaller>=6"

echo Building LocalTTS.exe (launcher only; ~5-10 MB) ...
"runtime\Scripts\python.exe" -m PyInstaller --noconfirm --clean LocalTTS.spec || exit /b 1

if exist "dist\LocalTTS.exe" (
  copy /y "dist\LocalTTS.exe" "LocalTTS.exe" >nul
  echo.
  echo Done.  LocalTTS.exe is in the project root. Double-click it to launch.
) else (
  echo [ERROR] build did not produce dist\LocalTTS.exe
  exit /b 1
)
endlocal
