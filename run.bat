@echo off
cd /d "%~dp0"
pythonw qt_app.py
if errorlevel 1 python qt_app.py
