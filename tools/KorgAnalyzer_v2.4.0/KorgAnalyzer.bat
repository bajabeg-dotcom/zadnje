@echo off
setlocal EnableExtensions
title Korg Binary Analyzer
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem  Korg Binary Analyzer - one-click Windows launcher
rem
rem  Finds a usable Python in this order:
rem    1. a private runtime prepared by install.bat  (runtime\python\python.exe)
rem    2. the official "py" launcher
rem    3. python.exe on PATH
rem  Nothing is installed silently and PATH is never modified.
rem ---------------------------------------------------------------------------

set "PYEXE="

if exist "runtime\python\python.exe" (
    set "PYEXE=runtime\python\python.exe"
    goto :found
)

where py >nul 2>nul
if not errorlevel 1 (
    set "PYEXE=py -3"
    goto :found
)

where python >nul 2>nul
if not errorlevel 1 (
    set "PYEXE=python"
    goto :found
)

echo.
echo ============================================================
echo   Python nije pronaden na ovom racunaru.
echo   Python was not found on this computer.
echo.
echo   Pokreni install.bat jednom (bez administratorskih prava),
echo   pa zatim ponovo dvoklikni KorgAnalyzer.bat
echo.
echo   Run install.bat once (no administrator rights needed),
echo   then double-click KorgAnalyzer.bat again.
echo ============================================================
echo.
pause
exit /b 1

:found
where ollama >nul 2>nul
if errorlevel 1 (
    echo Napomena: Ollama nije instalirana - tab "AI ^(Qwen^)" nece raditi.
    echo            Za lokalnog asistenta pokreni POKRENI_QWEN.bat jednom.
    echo.
)

echo Korg Binary Analyzer se pokrece...
%PYEXE% -X utf8 -m korg_binary_analyzer gui %*
if errorlevel 1 (
    echo.
    echo Program je zavrsio s greskom. Snimljeni dijagnosticki podaci:
    echo   %CD%\workspace\logs\
    echo.
    pause
)
endlocal
