@echo off
REM KORG PA Konverter - Windows launcher
cd /d "%~dp0"
start "" pythonw korg_pa_converter_gui.py %*
