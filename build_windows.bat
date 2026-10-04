@echo off
setlocal
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --windowed --name SekiroSaveSlotCopier src\SekiroSaveSlotCopier.py
if errorlevel 1 exit /b 1
echo.
echo Build complete: dist\SekiroSaveSlotCopier.exe
pause
