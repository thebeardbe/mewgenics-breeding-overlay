@echo off
REM Build a self-contained Windows .exe (no Python install needed on target).
REM Requires: Python 3.10+, PySide6-Essentials, lz4, pyinstaller in the active venv.
REM
REM   py -m venv .venv && .venv\Scripts\activate
REM   pip install -r requirements.txt PySide6-Essentials pyinstaller
REM   packaging\build_windows.bat
REM
REM Output: dist\MewgenicsOverlay.exe  (double-click to run)

setlocal
cd /d "%~dp0\.."

pyinstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name MewgenicsOverlay ^
  --paths src ^
  --collect-submodules mewgenics_overlay ^
  packaging\entry.py

echo.
echo Built: dist\MewgenicsOverlay.exe
echo Copy the .exe to any Windows machine and double-click it.
endlocal
