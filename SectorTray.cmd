@echo off
rem Double-click to start SectorTray. Close this window to stop it.
title SectorTray
cd /d "%~dp0"
python -m sectortray %*
if errorlevel 1 pause
