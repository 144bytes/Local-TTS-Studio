# PyInstaller spec for the LocalTTS Studio launcher.
#
# Freezes ONLY launcher.py (stdlib) into a small LocalTTS.exe. The Python
# runtime, heavy libraries, and model weights are NOT bundled — they stay as
# runtime/ and models/ next to the EXE. Build with:  build_exe.bat
#
# Layout after build:
#   LocalTTS/
#       LocalTTS.exe          <- from this spec
#       runtime/              <- created by setup.bat
#       app/  web/  config/  models/  voices/  projects/  output/  logs/

block_cipher = None

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "numpy", "flask", "transformers", "chatterbox",
              "scipy", "librosa", "soundfile"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="LocalTTS",
    console=True,          # keep the console: it shows status + is the stop button
    disable_windowed_traceback=False,
    icon=None,
    onefile=True,
)
