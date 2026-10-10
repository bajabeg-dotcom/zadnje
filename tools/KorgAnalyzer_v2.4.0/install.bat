@echo off
setlocal EnableExtensions
title Korg Binary Analyzer - instalacija
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem  One-time setup. Prepares a PRIVATE Python + Tk runtime in runtime\python
rem  so the analyzer never depends on anything already installed.
rem
rem  It does NOT change PATH, does NOT register file associations and does NOT
rem  require administrator rights. Everything stays inside this folder.
rem ---------------------------------------------------------------------------

echo ============================================================
echo   Korg Binary Analyzer - jednokratna priprema okruzenja
echo ============================================================
echo.

if exist "runtime\python\python.exe" (
    echo Privatno Python okruzenje vec postoji.
    echo   runtime\python\python.exe
    goto :verify
)

where py >nul 2>nul
if not errorlevel 1 (
    echo Pronaden sistemski Python. Provjeravam Tkinter...
    py -3 -c "import tkinter, sys; sys.exit(0 if tkinter.TkVersion >= 8.6 else 3)" >nul 2>nul
    if not errorlevel 1 (
        echo Sistemski Python je spreman - privatno okruzenje nije potrebno.
        goto :verify
    )
    echo Sistemski Python postoji, ali bez Tkinter/Tk 8.6.
)

where python >nul 2>nul
if not errorlevel 1 (
    python -c "import tkinter, sys; sys.exit(0 if tkinter.TkVersion >= 8.6 else 3)" >nul 2>nul
    if not errorlevel 1 (
        echo Sistemski Python je spreman - privatno okruzenje nije potrebno.
        goto :verify
    )
)

if exist "runtime_installer\python-3.12.10-amd64.exe" (
    echo.
    echo Prvenstveno: instaliram ISKLJUCIVO privatno Python 3.12 okruzenje
    echo u mapu runtime\python (bez izmjene PATH-a, bez registracije tipova fajlova).
    echo.
    echo Ovo traje 1-3 minute i ne zahtijeva administratorska prava.
    echo.
    pause
    "runtime_installer\python-3.12.10-amd64.exe" /quiet InstallAllUsers=0 TargetDir="%CD%\runtime\python" PrependPath=0 Include_launcher=0 Include_test=0 Include_tcltk=1 Include_pip=0 SimpleInstall=1
    if exist "runtime\python\python.exe" (
        echo.
        echo Privatno okruzenje je spremno.
        goto :verify
    )
    echo.
    echo Automatska instalacija nije uspjela. Preuzmi Python 3.10+ sa:
    echo   https://www.python.org/downloads/windows/
    echo i oznaci "tcl/tk and IDLE" tijekom instalacije.
    echo.
    pause
    exit /b 1
)

echo.
echo Nema ni sistemskog Pythona ni prilozenog instalatera (runtime_installer\).
echo Instaliraj Python 3.10 ili noviji sa https://www.python.org/downloads/windows/
echo i oznaci opciju "tcl/tk and IDLE".
echo.
pause
exit /b 1

:verify
echo.
echo Provjera okruzenja...
set "PYEXE="
if exist "runtime\python\python.exe" set "PYEXE=runtime\python\python.exe"
if not defined PYEXE (
    where py >nul 2>nul && set "PYEXE=py -3"
)
if not defined PYEXE set "PYEXE=python"

%PYEXE% -X utf8 -c "import sys, tkinter; print('Python', sys.version.split()[0]); print('Tk', tkinter.TkVersion)"
if errorlevel 1 (
    echo.
    echo Okruzenje nije ispravno pripremljeno.
    pause
    exit /b 1
)

echo.
echo Pokrecem samotest (self-test)...
%PYEXE% -X utf8 -m korg_binary_analyzer selftest
if errorlevel 1 (
    echo Samotest nije prosao.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Gotovo. Od sada samo dvoklikni KorgAnalyzer.bat
echo ============================================================
echo.
pause
endlocal
