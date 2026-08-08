@echo off
REM ============================================================
REM  Build the standalone Windows executable (no Python needed
REM  on the target machine). Output: dist\BolaoOCR.exe
REM ============================================================
cd /d "%~dp0"

echo Installing build tool (PyInstaller)...
python -m pip install pyinstaller --quiet

echo.
echo Building BolaoOCR.exe (this can take a few minutes)...
python -m PyInstaller BolaoOCR.spec --noconfirm

echo.
if exist "dist\BolaoOCR.exe" (
    echo  Build OK  ->  dist\BolaoOCR.exe
) else (
    echo  Build FAILED - see messages above.
)
pause
