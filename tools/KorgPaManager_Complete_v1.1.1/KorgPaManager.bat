@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

if not exist "logs" mkdir "logs" >nul 2>&1
set "SELFTEST_LOG=%~dp0logs\last-self-test.json"
set "INSTALL_LOG=%~dp0logs\python-install.log"
set "LOCAL_ROOT=%~dp0runtime\python"
set "LOCAL_PY=%LOCAL_ROOT%\python.exe"
set "LOCAL_PYW=%LOCAL_ROOT%\pythonw.exe"
set "INSTALLER=%~dp0runtime_installer\python-3.12.10-amd64.exe"
set "PY_MODE="
set "PY_CONSOLE="
set "PY_WINDOWED="
set "CONSOLE_TEST=0"
if /I "%~1"=="--console-self-test" set "CONSOLE_TEST=1"

call :find_python
if not defined PY_MODE call :install_runtime
if not defined PY_MODE goto :fatal_runtime

call :self_test
if errorlevel 1 goto :fatal_selftest
if "%CONSOLE_TEST%"=="1" (
    echo.
    type "%SELFTEST_LOG%"
    echo.
    echo Ugradjena provjera je zavrsena bez greske.
    pause
    exit /b 0
)

if /I "%PY_MODE%"=="PYLAUNCHER" (
    start "" pyw -3 -m korg_pa_manager %*
) else (
    start "" "%PY_WINDOWED%" -m korg_pa_manager %*
)
exit /b 0

:find_python
if exist "%LOCAL_PY%" if exist "%LOCAL_PYW%" (
    "%LOCAL_PY%" -c "import tkinter, mido, korg_pa_manager" >nul 2>&1
    if not errorlevel 1 (
        set "PY_MODE=LOCAL"
        set "PY_CONSOLE=%LOCAL_PY%"
        set "PY_WINDOWED=%LOCAL_PYW%"
        goto :eof
    )
)

where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import tkinter, mido, korg_pa_manager" >nul 2>&1
    if not errorlevel 1 (
        where pyw >nul 2>&1
        if not errorlevel 1 (
            set "PY_MODE=PYLAUNCHER"
            set "PY_CONSOLE=py -3"
            set "PY_WINDOWED=pyw -3"
            goto :eof
        )
    )
)

where python >nul 2>&1
if not errorlevel 1 (
    python -c "import tkinter, mido, korg_pa_manager" >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%P in ('where python') do if not defined PY_MODE (
            for %%Q in ("%%P") do if exist "%%~dpQpythonw.exe" (
                set "PY_MODE=SYSTEM"
                set "PY_CONSOLE=%%P"
                set "PY_WINDOWED=%%~dpQpythonw.exe"
            )
        )
        if defined PY_MODE goto :eof
    )
)
goto :eof

:install_runtime
echo.
echo KorgPaManager - prvo pokretanje
echo Pripremam ugradjeni Python i Tkinter. Internet i admin prava nisu potrebni.
echo Ovo se radi samo jednom i moze potrajati nekoliko minuta.
echo.
if not exist "%INSTALLER%" goto :eof
if /I "%PROCESSOR_ARCHITECTURE%"=="x86" if not defined PROCESSOR_ARCHITEW6432 (
    echo Ovaj kompletni paket zahtijeva 64-bitni Windows 8.1, 10 ili 11.
    goto :eof
)
if not exist "%~dp0runtime" mkdir "%~dp0runtime" >nul 2>&1
start /wait "Python runtime setup" "%INSTALLER%" /passive InstallAllUsers=0 TargetDir="%LOCAL_ROOT%" Include_core=1 Include_exe=1 Include_dev=0 Include_lib=1 Include_test=0 Include_doc=0 Include_tools=0 Include_tcltk=1 Include_pip=0 Include_launcher=0 AssociateFiles=0 Shortcuts=0 PrependPath=0 AppendPath=0 CompileAll=0 SimpleInstall=1 /log "%INSTALL_LOG%"
if exist "%LOCAL_PY%" if exist "%LOCAL_PYW%" (
    "%LOCAL_PY%" -c "import tkinter, mido, korg_pa_manager" >nul 2>&1
    if not errorlevel 1 (
        set "PY_MODE=LOCAL"
        set "PY_CONSOLE=%LOCAL_PY%"
        set "PY_WINDOWED=%LOCAL_PYW%"
    )
)
goto :eof

:self_test
if /I "%PY_MODE%"=="PYLAUNCHER" (
    py -3 -m korg_pa_manager --self-test >"%SELFTEST_LOG%" 2>&1
) else (
    "%PY_CONSOLE%" -m korg_pa_manager --self-test >"%SELFTEST_LOG%" 2>&1
)
if errorlevel 1 (exit /b 1) else (exit /b 0)

:fatal_runtime
echo.
echo GRESKA: Nije moguce pokrenuti kompletni Python/Tk runtime.
echo Pogledaj: %INSTALL_LOG%
echo Paket mora biti prvo raspakovan u folder u koji smijes pisati,
echo na primjer Desktop ili Documents. Nemoj ga pokretati direktno iz ZIP-a.
echo.
pause
exit /b 1

:fatal_selftest
echo.
echo GRESKA: Ugradjena provjera programa nije prosla.
echo Nista nije mijenjano na tvojim MIDI fajlovima.
echo Detalji su zapisani u:
echo %SELFTEST_LOG%
echo.
type "%SELFTEST_LOG%"
echo.
pause
exit /b 1
