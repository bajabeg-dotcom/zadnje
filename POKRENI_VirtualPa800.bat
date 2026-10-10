@echo off
REM ============================================================
REM  VirtualPa800 NATIVE 0.2 -- glavni pokretac (Windows)
REM  Pokrece PA800 Manager Web v0.2 (read-only forenzicki manager)
REM  na http://127.0.0.1:8765
REM ============================================================
chcp 65001 >nul
title VirtualPa800 NATIVE 0.2
cd /d "%~dp0app\pa800-manager-v0.2"

where python >nul 2>nul
if errorlevel 1 (
  echo [GRESKA] Python nije pronadjen u PATH-u.
  echo Instaliraj Python 3.10+ sa https://www.python.org/downloads/
  echo Uz instalaciju obavezno cekiraj "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo  VirtualPa800 NATIVE 0.2 -- PA800 Manager Web v0.2
echo  Otvaram http://127.0.0.1:8765 u browseru...
echo  Za gasenje: zatvori ovaj prozor ili pritisni CTRL+C
echo ============================================================
start "" "http://127.0.0.1:8765"
python server.py --host 127.0.0.1 --port 8765
pause
