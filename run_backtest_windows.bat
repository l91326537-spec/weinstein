@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"
set PY=
py -3 --version >nul 2>nul && set PY=py -3
if not defined PY (
  python --version >nul 2>nul && set PY=python
)
if not defined PY (
  echo [!] Python not found. Install from https://www.python.org/downloads/ and check "Add python.exe to PATH".
  pause
  exit /b
)
echo Using: %PY%
%PY% --version
echo.
echo [1/2] Installing libraries. Progress is shown below (first time 1-5 min)...
%PY% -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
  echo [!] Library install failed. Take a screenshot of this window.
  pause
  exit /b
)
echo.
echo [2/2] Running backtest (10-20 min on first run)...
%PY% backtest.py %*
echo.
echo Done. If something went wrong, take a screenshot of this window.
pause
