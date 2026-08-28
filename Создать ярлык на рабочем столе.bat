@echo off
REM Creates a "LocalTTS Studio" shortcut on your Desktop that points to LocalTTS.bat.
setlocal
cd /d "%~dp0"
set "TARGET=%CD%\LocalTTS.bat"
set "LNK=%USERPROFILE%\Desktop\LocalTTS Studio.lnk"

powershell -NoProfile -Command ^
  "$s=New-Object -ComObject WScript.Shell;" ^
  "$l=$s.CreateShortcut('%LNK%');" ^
  "$l.TargetPath='%TARGET%';" ^
  "$l.WorkingDirectory='%CD%';" ^
  "$l.IconLocation='%SystemRoot%\System32\SHELL32.dll,227';" ^
  "$l.Description='Local offline TTS studio';" ^
  "$l.Save()"

if exist "%LNK%" (
  echo Done. "LocalTTS Studio" is on your Desktop - double-click it to launch.
) else (
  echo Could not create the shortcut.
)
pause
endlocal
