@echo off
setlocal
cd /d "%~dp0\.."
echo Sync-Notebook: Journal.xlsx -^> Google Sheet (dry-run default)
echo Usage: Sync-Notebook.bat [--apply] [--days N]
echo Requires: GOOGLE_SERVICE_ACCOUNT_FILE in .env, sheet shared to SA email
echo.

set DAYS=10
set MODE=--dry-run
set PULL=
:parse
if "%~1"=="" goto run
if /I "%~1"=="--apply" (
  set MODE=--apply
  shift
  goto parse
)
if /I "%~1"=="--days" (
  set DAYS=%~2
  shift
  shift
  goto parse
)
if /I "%~1"=="--pull-vps" (
  set PULL=--pull-vps
  shift
  goto parse
)
echo Unknown arg: %~1
exit /b 1

:run
python -m src.journal.notebook_cli sync %PULL% --days %DAYS% %MODE%
exit /b %ERRORLEVEL%
