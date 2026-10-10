@echo off
REM KORG PA Manager - Windows launcher
cd /d "%~dp0"
start "" pythonw korg_pa_manager_gui.py %*
