@echo off
setlocal
cd /d "%~dp0\.."
echo Watch-Notebook: supervised Bridge A (or use Scheduled Task Freelance-NotebookWatcher)
echo Ctrl+C stops only this console; Task continues if installed.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0NotebookWatcher.ps1"
exit /b %ERRORLEVEL%
