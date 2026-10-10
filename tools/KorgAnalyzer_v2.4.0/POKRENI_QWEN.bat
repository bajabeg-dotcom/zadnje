@echo off
setlocal EnableExtensions
title Korg Binary Analyzer - Qwen asistent
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem  One-time setup + check for the local Qwen assistant.
rem
rem  The assistant runs entirely on this computer through Ollama. Nothing is
rem  sent to the internet: the model file is downloaded once, then every answer
rem  is produced locally.
rem
rem  This script does NOT install anything silently. It checks what is present
rem  and tells you the exact command to run if something is missing.
rem ---------------------------------------------------------------------------

echo ============================================================
echo   Korg Analyzer - lokalni Qwen asistent (Ollama)
echo ============================================================
echo.

where ollama >nul 2>nul
if errorlevel 1 goto no_ollama

echo Ollama je nadena. Provjeravam da li radi...
ollama list >nul 2>nul
if not errorlevel 1 goto ollama_ready

echo Ollama ne odgovara. Pokrecem je...
start "" /min ollama serve
timeout /t 5 /nobreak >nul
ollama list >nul 2>nul
if not errorlevel 1 goto ollama_ready

echo.
echo Ollama se nije pokrenula. Pokreni rucno: ollama serve
pause
exit /b 1

:ollama_ready
echo.
echo Instalirani modeli:
ollama list
echo.

ollama list | findstr /I "qwen" >nul
if not errorlevel 1 goto have_model

echo Nema Qwen modela. Preporuceni izbor:
echo.
echo   qwen2.5:7b    - preporuceno, trazi oko 8 GB RAM-a, 4.7 GB download
echo   qwen2.5:3b    - ako imas 8 GB RAM-a ili manje, 1.9 GB download
echo   qwen2.5:14b   - ako imas 16 GB+ RAM-a, 9 GB download
echo.
set "IZBOR="
set /p "IZBOR=Ukucaj tag modela pa Enter (Enter = qwen2.5:7b): "
if not defined IZBOR set "IZBOR=qwen2.5:7b"
echo.
echo Preuzimam %IZBOR% ... ovo traje nekoliko minuta.
echo.
ollama pull %IZBOR%
if errorlevel 1 goto pull_failed
goto after_pull

:have_model
echo Qwen model je vec instaliran - spremno.

:after_pull
echo.
echo ============================================================
echo   Provjera kroz program
echo ============================================================
echo.

set "PYEXE="
if exist "runtime\python\python.exe" set "PYEXE=runtime\python\python.exe"
if not defined PYEXE where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE goto no_python

%PYEXE% -X utf8 -m korg_binary_analyzer ai-status
echo.
echo ============================================================
echo   Gotovo.
echo   Otvori KorgAnalyzer.bat, ucitaj fajl, tab "AI (Qwen)".
echo ============================================================
echo.
pause
endlocal
exit /b 0

:no_ollama
echo Ollama nije instalirana.
echo.
echo Uradi ovo, po redu:
echo.
echo   1. Otvori https://ollama.com/download/windows
echo   2. Skini OllamaSetup.exe i pokreni ga ^(obicna instalacija, Next-Next^)
echo   3. ZATVORI ovaj prozor i pokreni POKRENI_QWEN.bat ponovo
echo.
echo Ollama se poslije instalacije pokrece sama i radi u pozadini.
echo.
pause
endlocal
exit /b 1

:pull_failed
echo.
echo Preuzimanje nije uspjelo. Provjeri internet vezu pa probaj ponovo.
pause
endlocal
exit /b 1

:no_python
echo Python nije pronaden - pokreni install.bat prvo.
pause
endlocal
exit /b 1
