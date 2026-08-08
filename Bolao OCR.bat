@echo off
REM ============================================================
REM  Bolao OCR - one-click launcher (requires Python installed)
REM  Double-click this file to start the desktop app.
REM ============================================================
cd /d "%~dp0"

REM Launch without a console window
start "" pythonw "main.py"

REM If pythonw is unavailable, fall back to python
if errorlevel 9009 (
    echo pythonw nao encontrado, usando python...
    python "main.py"
    pause
)
