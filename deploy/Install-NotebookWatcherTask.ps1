# Install / repair local notebook sync automation (current user, no admin).
# - Freelance-NotebookWatcher: every 10 min keep-alive (mutex = single instance)
# - Freelance-NotebookDailySync: daily 21:00 apply sync
# - Startup\Freelance-NotebookWatcher.cmd: start at Windows logon
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$WatcherPs1 = Join-Path $PSScriptRoot "NotebookWatcher.ps1"
$SyncBat = Join-Path $PSScriptRoot "Sync-Notebook.bat"
$DailyLog = Join-Path $RepoRoot "logs\notebook_daily.log"
New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot "logs") | Out-Null

if (-not (Test-Path -LiteralPath $WatcherPs1)) { throw "Missing $WatcherPs1" }
if (-not (Test-Path -LiteralPath $SyncBat)) { throw "Missing $SyncBat" }

$VbsLauncher = Join-Path $PSScriptRoot "Start-NotebookWatcherHidden.vbs"
$watcherTr = 'wscript.exe //B //Nologo \"' + $VbsLauncher + '\"'
$dailyTr = 'cmd.exe /c \"cd /d \"' + $RepoRoot + '\" && call \"' + $SyncBat + '\" --apply --days 10 --pull-vps >> \"' + $DailyLog + '\" 2>&1\"'

Write-Host "Create Freelance-NotebookWatcher (every 10 min keep-alive, no console)..."
schtasks.exe /Create /F /TN "Freelance-NotebookWatcher" /SC MINUTE /MO 10 /RL LIMITED /TR $watcherTr
if ($LASTEXITCODE -ne 0) { throw "schtasks watcher failed: $LASTEXITCODE" }

Write-Host "Create Freelance-NotebookDailySync (daily 21:00)..."
schtasks.exe /Create /F /TN "Freelance-NotebookDailySync" /SC DAILY /ST 21:00 /RL LIMITED /TR $dailyTr
if ($LASTEXITCODE -ne 0) { throw "schtasks daily failed: $LASTEXITCODE" }

foreach ($name in @("Freelance-NotebookWatcher", "Freelance-NotebookDailySync")) {
    $t = Get-ScheduledTask -TaskName $name
    $t.Settings.ExecutionTimeLimit = "PT0S"
    $t.Settings.AllowDemandStart = $true
    Set-ScheduledTask -InputObject $t | Out-Null
}

$startup = [Environment]::GetFolderPath("Startup")
$oldCmd = Join-Path $startup "Freelance-NotebookWatcher.cmd"
if (Test-Path $oldCmd) { Remove-Item $oldCmd -Force }
$vbsPath = Join-Path $startup "Freelance-NotebookWatcher.vbs"
Copy-Item $VbsLauncher $vbsPath -Force
Write-Host "Startup (hidden): $vbsPath"

schtasks.exe /Run /TN "Freelance-NotebookWatcher" | Out-Host
Start-Sleep -Seconds 5

Get-ScheduledTask -TaskName "Freelance-NotebookWatcher", "Freelance-NotebookDailySync" |
    ForEach-Object {
        $info = $_ | Get-ScheduledTaskInfo
        [PSCustomObject]@{
            TaskName = $_.TaskName
            State    = $_.State
            NextRun  = $info.NextRunTime
        }
    } | Format-Table -AutoSize

Write-Host "OK. From TG: /notebook (PC on + logged in)."
Write-Host "Fallback: daily 21:00 without TG."
Write-Host "Log: $(Join-Path $RepoRoot 'logs\notebook_watcher.log')"
