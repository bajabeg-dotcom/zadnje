@echo off
setlocal EnableExtensions
title Korg Binary Analyzer - provjera
cd /d "%~dp0"

rem Diagnostics only: verifies the runtime and the codec without opening the GUI.
rem Output is also useful to paste into a bug report.

echo ============================================================
echo   Korg Binary Analyzer - dijagnostika
echo ============================================================
echo.

set "PYEXE="
if exist "runtime\python\python.exe" set "PYEXE=runtime\python\python.exe"
if not defined PYEXE (
    where py >nul 2>nul && set "PYEXE=py -3"
)
if not defined PYEXE (
    where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    echo Python nije pronaden.
    pause
    exit /b 1
)

echo Python:
%PYEXE% -X utf8 -c "import sys; print('  ', sys.version)"
echo.
echo Tkinter:
%PYEXE% -X utf8 -c "import tkinter; print('   Tk', tkinter.TkVersion)" 2>nul || echo    NIJE DOSTUPAN (GUI nece raditi, CLI hoce)
echo.
echo Samotest codeca:
%PYEXE% -X utf8 -m korg_binary_analyzer selftest
echo.
echo Regressioni testovi:
%PYEXE% -X utf8 -m pytest -q 2>nul || echo   (pytest nije instaliran - nije obavezno)
echo.
pause
endlocal
