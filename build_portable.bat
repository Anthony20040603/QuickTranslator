@echo off
setlocal
cd /d "%~dp0"
set PYTHONNOUSERSITE=1

python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onedir ^
  --name QuickTranslator ^
  --icon assets\app_icon.png ^
  --add-data "assets\app_icon.png;assets" ^
  --hidden-import pystray._win32 ^
  --exclude-module numpy ^
  --exclude-module psutil ^
  --exclude-module setuptools ^
  --exclude-module pkg_resources ^
  qt_app.py

if errorlevel 1 exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0package_release.ps1"
if errorlevel 1 exit /b 1
echo.
echo Portable build created at dist\QuickTranslator
