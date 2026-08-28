@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   LocalTTS Studio - setup   (run once, needs internet)
echo ============================================================
echo   Flags:  --cpu         force CPU-only PyTorch
echo           --build-exe   also build LocalTTS.exe (PyInstaller)
echo.

set "FORCE_CPU="
set "BUILD_EXE="
for %%A in (%*) do (
  if /i "%%A"=="--cpu" set "FORCE_CPU=1"
  if /i "%%A"=="--build-exe" set "BUILD_EXE=1"
)

echo %CD% | findstr /I "\\Downloads\\" >nul && (
  echo [!] Папка внутри Downloads. Windows Storage Sense может её удалить.
  echo     Перенесите LocalTTS\ в C:\Users\%USERNAME%\LocalTTS
  echo.
)

REM -- Python 3.12 --
set "PYEXE="
for %%V in (3.12 3.11 3.13) do (
  if not defined PYEXE ( py -%%V -c "import sys" >nul 2>&1 && set "PYEXE=py -%%V" )
)
if not defined PYEXE ( python --version >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE ( echo [ERROR] Нужен Python 3.11-3.13 (python.org). & exit /b 1 )
echo Python: !PYEXE!
!PYEXE! --version

REM -- venv --
if not exist "runtime\Scripts\python.exe" (
  echo Создаю runtime\ ...
  !PYEXE! -m venv runtime || (echo [ERROR] venv & exit /b 1)
)
set "VPY=runtime\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip "setuptools<81" wheel || exit /b 1

REM -- PyTorch --
set "TORCH_INDEX=https://download.pytorch.org/whl/cpu"
if not defined FORCE_CPU (
  nvidia-smi >nul 2>&1
  if !errorlevel! == 0 ( echo NVIDIA GPU -^> CUDA 12.4 PyTorch. & set "TORCH_INDEX=https://download.pytorch.org/whl/cu124" ) ^
  else ( echo Нет GPU -^> CPU PyTorch. )
)
"%VPY%" -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url !TORCH_INDEX! || exit /b 1

REM -- app deps (qwen-tts pins transformers==4.57.3) --
"%VPY%" -m pip install -r requirements.txt || exit /b 1
if defined BUILD_EXE "%VPY%" -m pip install "pyinstaller>=6"

REM -- model weights (into models\qwen-1.7b\, ~4.6 GB) --
echo.
echo Скачиваю веса Qwen3-TTS ...
"%VPY%" -m scripts.download_models || (echo [ERROR] download & exit /b 1)

REM -- starter voice + workspace --
echo.
"%VPY%" -m scripts.make_voices
"%VPY%" -m scripts.check_env --make-demo-project >nul 2>&1

REM -- report + offline self-test --
echo.
"%VPY%" -m scripts.check_env
echo.
echo Офлайн self-test ...
"%VPY%" -m scripts.check_env --offline-selftest

if defined BUILD_EXE ( echo. & call build_exe.bat )

echo.
echo ============================================================
echo   Готово. Запуск:  LocalTTS.bat   (или run.bat)
echo ============================================================
endlocal
