@echo off
REM KORG PA Manager v5 - Windows launcher
cd /d "%~dp0"
start "" pythonw korg_pa_manager_v5_gui.py %*
