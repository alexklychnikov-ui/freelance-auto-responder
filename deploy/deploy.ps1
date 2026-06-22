@echo off
setlocal
set "ROOT=%~dp0.."
set "REMOTE=LightRAG_Naive"
set "DEST=/opt/freelance-responder"

echo === rsync project to VPS ===
ssh %REMOTE% "mkdir -p %DEST%/data/examples %DEST%/logs"
scp -r "%ROOT%\src" "%ROOT%\config" "%ROOT%\requirements.txt" "%ROOT%\deploy" %REMOTE%:%DEST%/
scp "%ROOT%\.env" %REMOTE%:%DEST%/.env

echo === copy journal + examples ===
scp "C:\Python\Projects\Zerocode2md\ResponseJournal\journal.xlsx" %REMOTE%:%DEST%/data/response_journal.xlsx
scp -r "C:\Python\Projects\Zerocode2md\Output\Отклики\*" %REMOTE%:%DEST%/data/examples/
ssh %REMOTE% "chmod +x %DEST%/deploy/install.sh && bash %DEST%/deploy/install.sh"

echo === run-test ===
ssh %REMOTE% "cd %DEST% && .venv/bin/python -m src.scheduler run-test"

endlocal
