@echo off
REM Launches the Clawd animation daemon detached from any console.
REM Place a shortcut to this file in shell:startup to start it on Windows login.
start "" /B pythonw "%~dp0clawd_daemon.py"
