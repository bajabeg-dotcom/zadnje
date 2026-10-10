@echo off
setlocal EnableExtensions
title PA800 MIDI Optimizer / Restorator
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem  PA800 MIDI Optimizer / Restorator
rem
rem  Deterministicki, dokazima vodjen MIDI optimizator.
rem  NE MIJENJA tick ni pitch postojecih nota. NE prepisuje original.
rem ---------------------------------------------------------------------------

set "PYEXE="
if exist "runtime\python\python.exe" set "PYEXE=runtime\python\python.exe"
if not defined PYEXE where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE goto no_python

:menu
cls
echo ============================================================
echo   PA800 MIDI OPTIMIZER / RESTORATOR
echo ============================================================
echo.
echo   1  Analiziraj MIDI fajl (nista se ne mijenja)
echo   2  Napravi plan (samo pogledaj sta bi se uradilo)
echo   3  Optimizuj MIDI (primijeni dokazano)
echo   4  Optimizuj CIJELI FOLDER (tvoj corpus)
echo   5  Pitaj lokalnog Qwena o zadnjem izvjestaju
echo   6  Provjeri da sve radi (selftest)
echo   7  Napravi GOLD indeks iz mog materijala
echo   8  Prikazi pravila opsega (Key Range + GOLD dokaz)
echo   0  Izlaz
echo.
set "IZBOR="
set /p "IZBOR=Izaberi (0-8): "

if "%IZBOR%"=="1" goto analyze
if "%IZBOR%"=="2" goto plan
if "%IZBOR%"=="3" goto optimize
if "%IZBOR%"=="4" goto batch
if "%IZBOR%"=="5" goto ask
if "%IZBOR%"=="6" goto selftest
if "%IZBOR%"=="7" goto gold
if "%IZBOR%"=="8" goto keyrange
if "%IZBOR%"=="0" exit /b 0
goto menu

:analyze
echo.
echo Prepisi ovu liniju putanjom do svog MIDI fajla:
set "TARGET="
set /p "TARGET=Put do MIDI fajla: "
if not defined TARGET goto menu
%PYEXE% -X utf8 -m pa800_midi analyze "%TARGET%"
echo.
pause
goto menu

:plan
echo.
set "TARGET="
set /p "TARGET=Put do MIDI fajla: "
if not defined TARGET goto menu
%PYEXE% -X utf8 -m pa800_midi plan "%TARGET%"
echo.
pause
goto menu

:optimize
echo.
set "TARGET="
set /p "TARGET=Put do MIDI fajla: "
if not defined TARGET goto menu
echo.
echo Primjer pravila:  velocity=-15     cc7=100     cc10=70
echo (ostavi prazno ako ne zelis pravilo)
set "RULE="
set /p "RULE=Pravilo (Enter = bez): "
echo.
echo Primjer opsega:  guitar=D2-E6     bass=E1-C4
echo (ostavi prazno = pravilo po tipu instrumenta + GOLD dokaz)
set "KR="
set /p "KR=Key Range (Enter = po tipu): "
echo.
if defined RULE (
    if defined KR (
        %PYEXE% -X utf8 -m pa800_midi optimize "%TARGET%" --rule %RULE% --key-range "%KR%"
    ) else (
        %PYEXE% -X utf8 -m pa800_midi optimize "%TARGET%" --rule %RULE%
    )
) else (
    if defined KR (
        %PYEXE% -X utf8 -m pa800_midi optimize "%TARGET%" --key-range "%KR%"
    ) else (
        %PYEXE% -X utf8 -m pa800_midi optimize "%TARGET%"
    )
)
echo.
pause
goto menu

:batch
echo.
set "FOLDER="
set /p "FOLDER=Put do foldera sa MIDI fajlovima: "
if not defined FOLDER goto menu
echo.
%PYEXE% -X utf8 -m pa800_midi batch "%FOLDER%"
echo.
pause
goto menu

:ask
echo.
set "REPORT="
set /p "REPORT=Put do report.json (Enter = zadnji iz workspace): "
if not defined REPORT (
    for /f "delims=" %%i in ('dir /b /o-d "workspace\pa800_midi\reports" 2^>nul') do (
        set "ZADNJI=%%i"
        goto nasao
    )
    :nasao
    set "REPORT=workspace\pa800_midi\reports\%ZADNJI%\report.json"
)
echo.
set "PITANJE="
set /p "PITANJE=Pitanje: "
if not defined PITANJE set "PITANJE=Objasni mi ukratko sta je uradjeno i ima li konflikata."
echo.
echo (Ako Ollama nije pokrenuta, pokreni je: ollama serve)
%PYEXE% -X utf8 -m pa800_midi ask --report "%REPORT%" "%PITANJE%"
echo.
pause
goto menu

:selftest
echo.
%PYEXE% -X utf8 -m pa800_midi selftest
echo.
pause
goto menu

:gold
echo.
set "FOLDER="
set /p "FOLDER=Put do foldera sa tvojim provjerenim MIDI fajlovima: "
if not defined FOLDER goto menu
echo.
%PYEXE% -X utf8 -m pa800_midi index gold "%FOLDER%"
echo.
pause
goto menu

:keyrange
echo.
%PYEXE% -X utf8 -m pa800_midi key-range
echo.
pause
goto menu

:no_python
echo Python nije pronaden - pokreni install.bat prvo.
pause
endlocal
exit /b 1
