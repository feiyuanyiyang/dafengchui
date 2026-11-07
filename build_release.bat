@echo off
setlocal
cd /d "%~dp0"

set "PY=D:\WorkTools\python3.11.7\python.exe"
if not exist "%PY%" set "PY=python"

echo ================================================
echo   dafengchui  -  onedir package
echo   python: %PY%
echo ================================================
echo.

"%PY%" -m PyInstaller --noconfirm --clean dafengchui.spec 2>&1
if errorlevel 1 goto fail

echo.
echo [OK] output: dist\dafengchui\dafengchui.exe
echo      done - report back to the assistant
pause
exit /b 0

:fail
echo.
echo [FAIL] build failed - copy the errors above into the chat
pause
exit /b 1
