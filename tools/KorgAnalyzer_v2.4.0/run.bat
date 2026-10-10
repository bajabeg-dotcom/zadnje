@echo off
setlocal EnableExtensions
title Korg Binary Analyzer - CLI
cd /d "%~dp0"

rem Command-line entry point. Use this when you want scripted runs:
rem   run.bat decode "C:\path\to\USER01.STY"
rem   run.bat scan   "C:\path\to\SET"
rem   run.bat selftest

set "PYEXE="
if exist "runtime\python\python.exe" set "PYEXE=runtime\python\python.exe"
if not defined PYEXE (
    where py >nul 2>nul && set "PYEXE=py -3"
)
if not defined PYEXE (
    where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    echo Python nije pronaden. Pokreni install.bat jednom, pa probaj ponovo.
    pause
    exit /b 1
)

if "%~1"=="" (
    echo.
    echo Korg Binary Analyzer - komandna linija
    echo.
    echo   run.bat analyze  file.sty     prepoznaj i ispitaj fajl
    echo   run.bat decode   file.sty     dekodiraj resurse
    echo   run.bat scan     folder\      obradi cijeli folder
    echo   run.bat compare  a.sty b.sty  uporedi dva fajla
    echo   run.bat report   workspace\   citaj snimljeni izvjestaj
    echo   run.bat hex      file.sty 0x7ac
    echo   run.bat selftest              provjeri da codec radi
    echo   run.bat ai-status             provjeri lokalni Qwen asistent
    echo   run.bat ask file.sty "pitanje"  pitaj Qwen o fajlu
    echo   run.bat manuals --search pa5x    pregled prirucnika
    echo   run.bat gui                   otvori prozor
    echo.
    pause
    exit /b 0
)

%PYEXE% -X utf8 -m korg_binary_analyzer %*
set RC=%ERRORLEVEL%
if not "%RC%"=="0" (
    echo.
    echo Zavrsilo s kodom %RC%.
    pause
)
endlocal & exit /b %RC%
