@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
call "venv\Scripts\activate.bat"
pip install -q openpyxl pydantic >nul 2>&1
python deploy\sync_journal_from_vps.py
set ERR=%ERRORLEVEL%
if %ERR% neq 0 (
  echo.
  echo Sync failed. Press any key...
  pause >nul
  exit /b %ERR%
)
echo.
echo Journal: C:\Python\Projects\Zerocode2md\ResponseJournal\journal.xlsx
explorer /select,"C:\Python\Projects\Zerocode2md\ResponseJournal\journal.xlsx"
timeout /t 3 >nul
endlocal
